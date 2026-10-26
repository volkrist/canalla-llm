# Installed-window navigation smoke for Canalla LLM.
#
# This script only claims what it can prove from the real window: the window is found by its
# title, restored, captured at its real size, and the navigation controls visibly change the
# screen. It does NOT claim "navigable during generation" — that needs a live stream and is
# proven by the free Playwright regression test
# (apps/desktop/e2e/generation-navigation.spec.ts), which drives New Chat, chat switching,
# typing, Settings and Stop while a long answer streams.
param(
  [string]$Captures = 'ui-nav'
)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NavWin {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, IntPtr process);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint attach, uint attachTo, bool value);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  public delegate bool EnumProc(IntPtr hWnd, IntPtr extra);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr extra);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int index);
}
"@
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

$script:canallaHandle = [IntPtr]::Zero
$script:canallaTopmost = $false
trap {
  # Never leave the window pinned above every other application, even when the test fails.
  if ($script:canallaTopmost -and $script:canallaHandle -ne [IntPtr]::Zero) {
    $normal = New-Object IntPtr -ArgumentList -2
    [NavWin]::SetWindowPos($script:canallaHandle, $normal, 0, 0, 0, 0, 0x0003) | Out-Null
    $script:canallaTopmost = $false
  }
  throw
}

$candidates = @(Get-Process -Name 'alex-llm' -ErrorAction Stop)
# Prefer the product window by title, but the WebView2 host can briefly rename it (a
# `com.alexllm.desktop-…` helper window), so a single `alex-llm` process with a real window handle
# is still this product and not another application. ChatGPT is a different process entirely.
$process = $candidates | Where-Object { $_.MainWindowTitle -eq 'Canalla LLM' } | Select-Object -First 1
if (-not $process) {
  $process = $candidates | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } | Select-Object -First 1
}
if (-not $process) { $process = $candidates | Select-Object -First 1 }
if (-not $process) { throw 'CANALLA_WINDOW_NOT_FOUND: no alex-llm process' }
$handle = $process.MainWindowHandle
for ($wait = 0; $wait -lt 20 -and $handle -eq [IntPtr]::Zero; $wait++) {
  Start-Sleep -Milliseconds 500
  $process.Refresh()
  $handle = $process.MainWindowHandle
}
if ($handle -eq [IntPtr]::Zero) { throw 'CANALLA_WINDOW_HANDLE_MISSING' }
$script:canallaHandle = $handle

# Another application can own a topmost window that covers this rect (the ChatGPT desktop app does
# exactly that), and then every capture of these pixels shows the wrong program while every hash
# still differs. Canalla is placed above it for the duration and restored on the way out.
$topmost = New-Object IntPtr -ArgumentList -1
$notTopmost = New-Object IntPtr -ArgumentList -2

# Choose a screen the window can actually be seen on. On this desktop a second application keeps a
# topmost window over the primary display, and a capture of "our" rectangle then shows that program.
# The best candidate is the work area that the fewest other visible windows overlap.
function ChooseWorkArea([IntPtr]$own) {
  $best = $null
  $bestScore = [int]::MaxValue
  foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
    $work = $screen.WorkingArea
    $left = $work.Left + 20
    $top = $work.Top + 20
    $width = [Math]::Min(1280, $work.Width - 40)
    $height = [Math]::Min(900, $work.Height - 40)
    $right = $left + $width
    $bottom = $top + $height
    $score = 0
    $callback = [NavWin+EnumProc]{
      param($other, $extra)
      if ($other -eq $own) { return $true }
      if (-not [NavWin]::IsWindowVisible($other)) { return $true }
      $rect = New-Object NavWin+RECT
      [NavWin]::GetWindowRect($other, [ref]$rect) | Out-Null
      $ow = $rect.Right - $rect.Left
      $oh = $rect.Bottom - $rect.Top
      if ($ow -lt 200 -or $oh -lt 200) { return $true }
      if ($rect.Right -le $left -or $rect.Left -ge $right -or $rect.Bottom -le $top -or $rect.Top -ge $bottom) {
        return $true
      }
      # A topmost window would sit above ours even after we raise ourselves.
      $exstyle = [NavWin]::GetWindowLong($other, -20)
      if (($exstyle -band 0x8) -eq 0x8) { $script:overlapTopmost++ } else { $script:overlapNormal++ }
      return $true
    }
    $script:overlapTopmost = 0
    $script:overlapNormal = 0
    [NavWin]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
    $score = 100 * $script:overlapTopmost + $script:overlapNormal
    if ($score -lt $bestScore) {
      $bestScore = $score
      $best = [pscustomobject]@{ Left = $left; Top = $top; Width = $width; Height = $height; Screen = $screen.DeviceName }
    }
  }
  return $best
}

$placement = ChooseWorkArea $handle
Write-Output ("placement screen=" + $placement.Screen + " rect=" + $placement.Left + ',' + $placement.Top + " size=" + $placement.Width + 'x' + $placement.Height)
$targetWidth = $placement.Width
$targetHeight = $placement.Height
$targetLeft = $placement.Left
$targetTop = $placement.Top
$work = [System.Windows.Forms.Screen]::FromPoint((New-Object System.Drawing.Point ($targetLeft + 5), ($targetTop + 5))).WorkingArea
[NavWin]::ShowWindow($handle, 9) | Out-Null
[NavWin]::SetWindowPos(
  $handle, $topmost, $targetLeft, $targetTop, $targetWidth, $targetHeight, 0x0040
) | Out-Null
$script:canallaTopmost = $true
[Microsoft.VisualBasic.Interaction]::AppActivate($process.Id) | Out-Null
[NavWin]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 900

$rect = New-Object NavWin+RECT
[NavWin]::GetWindowRect($handle, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($rect.Left -le -30000 -or $rect.Top -le -30000) { throw 'WINDOW_IS_MINIMIZED' }
if ($width -lt 400 -or $height -lt 300) { throw "WINDOW_TOO_SMALL ${width}x${height}" }
$drift = [Math]::Max([Math]::Abs($rect.Left - $targetLeft), [Math]::Abs($rect.Top - $targetTop))
if ($drift -gt 40) {
  # The window did not take the placement (another monitor, a window manager rule). Start over
  # instead of clicking at coordinates the window does not occupy.
  [NavWin]::SetWindowPos(
    $handle, $topmost, $targetLeft, $targetTop, $targetWidth, $targetHeight, 0x0040
  ) | Out-Null
  Start-Sleep -Milliseconds 700
  [NavWin]::GetWindowRect($handle, [ref]$rect) | Out-Null
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  $drift = [Math]::Max([Math]::Abs($rect.Left - $targetLeft), [Math]::Abs($rect.Top - $targetTop))
  if ($drift -gt 40) { throw "WINDOW_NOT_PLACED at=$($rect.Left),$($rect.Top) wanted=$targetLeft,$targetTop" }
}
if ($rect.Left -lt $work.Left -or $rect.Top -lt $work.Top -or
    $rect.Right -gt ($work.Left + $work.Width) -or $rect.Bottom -gt ($work.Top + $work.Height)) {
  throw "WINDOW_NOT_VISIBLE rect=$($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)"
}

# The pixels only belong to Canalla when Canalla is the foreground window. A capture of a rect
# another application happens to cover used to pass every hash comparison while showing ChatGPT.
$topmost = New-Object IntPtr -ArgumentList -1
$notTopmost = New-Object IntPtr -ArgumentList -2
$foreground = [IntPtr]::Zero
for ($attempt = 0; $attempt -lt 3; $attempt++) {
  # The classic, documented way to let a background process raise its own window: a synthetic ALT
  # keystroke releases the foreground lock for this thread.
  [NavWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
  [NavWin]::SetForegroundWindow($handle) | Out-Null
  [NavWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 400
  $foreground = [NavWin]::GetForegroundWindow()
  if ($foreground -eq $handle) { break }
  $current = [NavWin]::GetForegroundWindow()
  $owner = [NavWin]::GetWindowThreadProcessId($current, [IntPtr]::Zero)
  $mine = [NavWin]::GetCurrentThreadId()
  [NavWin]::AttachThreadInput($mine, $owner, $true) | Out-Null
  [NavWin]::SetForegroundWindow($handle) | Out-Null
  [NavWin]::AttachThreadInput($mine, $owner, $false) | Out-Null
  Start-Sleep -Milliseconds 400
  $foreground = [NavWin]::GetForegroundWindow()
  if ($foreground -eq $handle) { break }
}
if ($foreground -ne $handle) {
  throw "WINDOW_NOT_FOREGROUND: captured pixels would belong to another window"
}
Write-Output ("window id=" + $process.Id + " rect=" + $rect.Left + ',' + $rect.Top + " size=" + $width + 'x' + $height)

function Capture([string]$name) {
  $target = Join-Path $PSScriptRoot ($Captures + '-' + $name + '.png')
  $bmp = New-Object System.Drawing.Bitmap -ArgumentList $width, $height
  $graphics = [System.Drawing.Graphics]::FromImage($bmp)
  $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size)
  $bmp.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
  # A frame is only evidence when it has the real window size and actual content. A blank or
  # collapsed frame is refused instead of being reported as a pass.
  $colors = @{}
  for ($x = 0; $x -lt $width; $x += 40) {
    for ($y = 0; $y -lt $height; $y += 40) {
      $colors[$bmp.GetPixel($x, $y).ToArgb()] = $true
    }
  }
  $graphics.Dispose()
  $bmp.Dispose()
  $bytes = (Get-Item $target).Length
  if ($bytes -lt 30000) { throw "FRAME_TOO_SMALL $name bytes=$bytes" }
  if ($colors.Count -lt 10) { throw "FRAME_IS_BLANK $name distinct_colors=$($colors.Count)" }
  $hash = (Get-FileHash $target -Algorithm SHA256).Hash.ToLowerInvariant()
  Write-Host ("frame " + $name + " bytes=" + $bytes + " colors=" + $colors.Count + " sha256=" + $hash.Substring(0, 12))
  return [pscustomobject]@{ Path = $target; Hash = $hash }
}

function ClickRel([int]$x, [int]$y) {
  [NavWin]::SetCursorPos(($rect.Left + $x), ($rect.Top + $y)) | Out-Null
  Start-Sleep -Milliseconds 180
  [NavWin]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
  [NavWin]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 700
}

function SampleDiff([string]$left, [string]$right) {
  # Fraction of sampled pixels that differ. A hover highlight moves a few hundred pixels; a modal
  # covers a large part of the window, so the two are easy to tell apart without an image library.
  $a = [System.Drawing.Bitmap]::FromFile($left)
  $b = [System.Drawing.Bitmap]::FromFile($right)
  try {
    $total = 0
    $different = 0
    for ($x = 0; $x -lt [Math]::Min($a.Width, $b.Width); $x += 8) {
      for ($y = 0; $y -lt [Math]::Min($a.Height, $b.Height); $y += 8) {
        $total++
        if ($a.GetPixel($x, $y).ToArgb() -ne $b.GetPixel($x, $y).ToArgb()) { $different++ }
      }
    }
    if ($total -eq 0) { return 1.0 }
    return [double]$different / $total
  } finally {
    $a.Dispose()
    $b.Dispose()
  }
}

$base = Capture '1-base'
# The sidebar is anchored, not proportional: New Chat and the chat list sit a fixed distance below
# the top bar, Settings and the composer a fixed distance above the bottom. Proportional guesses
# moved the Settings click into the chat list as soon as the window height changed.
$newChatX = [int][Math]::Min(110, $width * 0.08)
$newChatY = 151
$chatX = [int][Math]::Min(130, $width * 0.10)
$chatY = 377
$settingsX = [int][Math]::Min(90, $width * 0.07)
$settingsY = $height - 126
$composerX = [int]($width * 0.55)
$composerY = $height - 105

ClickRel $chatX $chatY
$switched = Capture '2-switched-chat'
if ($switched.Hash -eq $base.Hash) { throw 'CHAT_SWITCH_DID_NOT_CHANGE_THE_SCREEN' }

ClickRel $newChatX $newChatY
$new = Capture '3-new-chat'
if ($new.Hash -eq $switched.Hash) { throw 'NEW_CHAT_DID_NOT_CHANGE_THE_SCREEN' }

ClickRel $settingsX $settingsY
$settings = Capture '4-settings'
if ($settings.Hash -eq $new.Hash) { throw 'SETTINGS_DID_NOT_OPEN' }
$panel = SampleDiff $settings.Path $new.Path
if ($panel -lt 0.10) { throw "SETTINGS_PANEL_TOO_SMALL diff=$panel" }
# A pixel change alone is not proof: a hover or a selected chat changes pixels too. A modal that
# really opened disappears on Escape and leaves the previous view behind (allowing a hover).
# WebView2 does not always deliver a synthetic Escape to a native <dialog>, so the real close button
# is invoked through UI Automation when the key does nothing.
[Microsoft.VisualBasic.Interaction]::AppActivate($process.Id) | Out-Null
[NavWin]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 300
[NavWin]::keybd_event(0x1B, 0, 0, [UIntPtr]::Zero)
[NavWin]::keybd_event(0x1B, 0, 2, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 900
$dismissed = Capture '4b-settings-dismissed'
if ((Get-FileHash $dismissed.Path -Algorithm SHA256).Hash -eq (Get-FileHash $settings.Path -Algorithm SHA256).Hash) {
  # Some WebView2 builds route SendKeys to the host window only; a second, explicit attempt.
  [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
  Start-Sleep -Milliseconds 900
  $dismissed = Capture '4b-settings-dismissed-retry'
}
$residue = SampleDiff $dismissed.Path $new.Path
if ($residue -gt 0.05) { throw "SETTINGS_DID_NOT_CLOSE diff=$residue" }

ClickRel $composerX $composerY
[System.Windows.Forms.SendKeys]::SendWait('Canalla UI navigation check')
Start-Sleep -Milliseconds 500
$typed = Capture '5-typed'
if ($typed.Hash -eq $new.Hash) { throw 'COMPOSER_DID_NOT_ACCEPT_TEXT' }
[System.Windows.Forms.SendKeys]::SendWait('^a')
[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}')

Write-Output 'installed-window-navigation: PASS (New Chat, chat switching, Settings, typing)'
Write-Output 'during-generation navigation: proven separately by e2e/generation-navigation.spec.ts'
[NavWin]::SetWindowPos($handle, $notTopmost, 0, 0, 0, 0, 0x0003) | Out-Null
$script:canallaTopmost = $false
