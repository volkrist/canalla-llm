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
}
"@
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

$process = Get-Process -Name 'alex-llm' -ErrorAction Stop |
  Where-Object { $_.MainWindowTitle -eq 'Canalla LLM' } | Select-Object -First 1
if (-not $process) { throw 'CANALLA_WINDOW_NOT_FOUND: no alex-llm window titled Canalla LLM' }
$handle = $process.MainWindowHandle
if ($handle -eq [IntPtr]::Zero) { throw 'CANALLA_WINDOW_HANDLE_MISSING' }

# Restore first, then raise: a minimized window reports rect -32000,... and every capture is junk.
[NavWin]::ShowWindow($handle, 9) | Out-Null
[NavWin]::SetWindowPos($handle, [IntPtr]::Zero, 0, 0, 0, 0, 3) | Out-Null
[Microsoft.VisualBasic.Interaction]::AppActivate($process.Id) | Out-Null
[NavWin]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 900

$rect = New-Object NavWin+RECT
[NavWin]::GetWindowRect($handle, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($rect.Left -le -30000 -or $rect.Top -le -30000) { throw 'WINDOW_IS_MINIMIZED' }
if ($width -lt 400 -or $height -lt 300) { throw "WINDOW_TOO_SMALL ${width}x${height}" }
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

$base = Capture '1-base'
# Sidebar proportions from the real 1216x859 layout: New Chat at the top, history below it,
# Settings above the account block, composer across the bottom. Proportional coordinates keep the
# script valid when the window is a different size.
$newChatX = [int]($width * 0.07)
$newChatY = [int]($height * 0.176)
$chatX = [int]($width * 0.115)
$chatY = [int]($height * 0.439)
$settingsX = [int]($width * 0.07)
$settingsY = [int]($height * 0.853)
$composerX = [int]($width * 0.55)
$composerY = [int]($height * 0.88)

ClickRel $chatX $chatY
$switched = Capture '2-switched-chat'
if ($switched.Hash -eq $base.Hash) { throw 'CHAT_SWITCH_DID_NOT_CHANGE_THE_SCREEN' }

ClickRel $newChatX $newChatY
$new = Capture '3-new-chat'
if ($new.Hash -eq $switched.Hash) { throw 'NEW_CHAT_DID_NOT_CHANGE_THE_SCREEN' }

ClickRel $settingsX $settingsY
$settings = Capture '4-settings'
if ($settings.Hash -eq $new.Hash) { throw 'SETTINGS_DID_NOT_OPEN' }
[System.Windows.Forms.SendKeys]::SendWait('{ESC}')
Start-Sleep -Milliseconds 600

ClickRel $composerX $composerY
[System.Windows.Forms.SendKeys]::SendWait('Canalla UI navigation check')
Start-Sleep -Milliseconds 500
$typed = Capture '5-typed'
if ($typed.Hash -eq $new.Hash) { throw 'COMPOSER_DID_NOT_ACCEPT_TEXT' }
[System.Windows.Forms.SendKeys]::SendWait('^a')
[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}')

Write-Output 'installed-window-navigation: PASS (New Chat, chat switching, Settings, typing)'
Write-Output 'during-generation navigation: proven separately by e2e/generation-navigation.spec.ts'
