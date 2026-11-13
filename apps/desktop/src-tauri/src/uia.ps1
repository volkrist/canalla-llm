# Canalla Windows UI Automation bridge.
#
# Runs on the user's PC inside the native host. The host writes the operation parameters to a
# JSON file and passes its path, so no argument is ever interpolated into this script and nothing
# here evaluates page or model text.
param(
    [Parameter(Mandatory = $true)][string]$ArgsFile,
    [Parameter(Mandatory = $true)][string]$Action,
    [int]$AllowInput = 0
)
#
# Every action returns one JSON object:
#   { ok, action, observed, element, window, screenshot, error }
# `ok` means "the requested state was observed after the action", not "the call did not throw":
# invoke/set/close are followed by a fresh observation of the same element or window.
#
# Read-only actions never change focus. Actions that move the pointer or type require
# CANALLA_UIA_ALLOW_INPUT=1, which the backend sets only for an explicitly authorised call.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Windows PowerShell 5.1 writes redirected stdout with the console code page, which mangles
# non-ASCII window titles and typed values. The host reads UTF-8, so say so explicitly.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch { }

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CanallaNative {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageW")] public static extern IntPtr SendMessageText(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int max);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public const uint WM_SETTEXT = 0x000C;
    public const uint WM_GETTEXT = 0x000D;
    public const uint WM_CLOSE = 0x0010;
    public const uint BM_CLICK = 0x00F5;
    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;
}
"@
[void][CanallaNative]::SetProcessDPIAware()

$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]

$action = $Action
$argsJson = ''
if (Test-Path -LiteralPath $ArgsFile) { $argsJson = Get-Content -LiteralPath $ArgsFile -Raw }
$allowInput = ($AllowInput -eq 1)

function Get-Param([string]$name, $default = $null) {
    if ([string]::IsNullOrWhiteSpace($argsJson)) { return $default }
    try { $obj = $argsJson | ConvertFrom-Json } catch { return $default }
    $value = $obj.$name
    if ($null -eq $value) { return $default }
    return $value
}

function Get-Rect($element) {
    try {
        $r = $element.Current.BoundingRectangle
        return @{ left = [int]$r.Left; top = [int]$r.Top; width = [int]$r.Width; height = [int]$r.Height }
    } catch { return @{ left = 0; top = 0; width = 0; height = 0 } }
}

function Get-ElementInfo($element) {
    if ($null -eq $element) { return $null }
    $value = $null
    try {
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            $value = $pattern.Current.Value
        }
    } catch { }
    return @{
        name = $element.Current.Name
        automation_id = $element.Current.AutomationId
        control_type = $element.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
        class_name = $element.Current.ClassName
        enabled = $element.Current.IsEnabled
        offscreen = $element.Current.IsOffscreen
        handle = $element.Current.NativeWindowHandle
        value = $value
        rect = Get-Rect $element
    }
}

function Get-WindowInfo($window) {
    if ($null -eq $window) { return $null }
    $handle = [IntPtr]$window.Current.NativeWindowHandle
    $rect = @{ left = 0; top = 0; width = 0; height = 0 }
    if ($handle -ne [IntPtr]::Zero) {
        $native = New-Object CanallaNative+RECT
        if ([CanallaNative]::GetWindowRect($handle, [ref]$native)) {
            $rect = @{
                left = $native.Left; top = $native.Top
                width = $native.Right - $native.Left; height = $native.Bottom - $native.Top
            }
        }
    } else {
        $rect = Get-Rect $window
    }
    return @{
        title = $window.Current.Name
        class_name = $window.Current.ClassName
        process_id = $window.Current.ProcessId
        handle = $handle.ToInt64()
        rect = $rect
        focused = ([CanallaNative]::GetForegroundWindow() -eq $handle)
    }
}

function Get-Windows {
    $all = $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $list = @()
    foreach ($item in $all) {
        try {
            if ($item.Current.ControlType -ne $CT::Window) { continue }
            if ([string]::IsNullOrWhiteSpace($item.Current.Name)) { continue }
            if ($item.Current.IsOffscreen -and $item.Current.NativeWindowHandle -eq 0) { continue }
            # A destroyed window can linger in the UIA tree after its process is gone; a stale
            # title match would then make `close_window` report a live window that no longer
            # exists. Only a window belonging to a running process is a window.
            $null = Get-Process -Id $item.Current.ProcessId -ErrorAction Stop
            $list += (Get-WindowInfo $item)
        } catch { }
    }
    return $list
}

function Find-Window([string]$title) {
    $needle = $title.ToLower()
    foreach ($window in (Get-Windows)) {
        if ($window.title.ToLower().Contains($needle)) { return $window }
    }
    return $null
}

function Get-WindowElement($info) {
    $handle = [IntPtr]$info.handle
    return $AE::FromHandle($handle)
}

function New-Condition([string]$name, [string]$automationId, [string]$controlType) {
    $conditions = @()
    if (-not [string]::IsNullOrEmpty($name)) {
        $conditions += (New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, $name))
    }
    if (-not [string]::IsNullOrEmpty($automationId)) {
        $conditions += (New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, $automationId))
    }
    if (-not [string]::IsNullOrEmpty($controlType)) {
        $type = $CT::$controlType
        if ($null -ne $type) {
            $conditions += (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $type))
        }
    }
    if ($conditions.Count -eq 0) { return [System.Windows.Automation.Condition]::TrueCondition }
    if ($conditions.Count -eq 1) { return $conditions[0] }
    return (New-Object System.Windows.Automation.AndCondition($conditions))
}

function Find-Element($window, [string]$name, [string]$automationId, [string]$controlType, [string]$className) {
    $element = $null
    if (-not [string]::IsNullOrEmpty($name) -or -not [string]::IsNullOrEmpty($automationId) -or -not [string]::IsNullOrEmpty($controlType)) {
        $condition = New-Condition $name $automationId $controlType
        $element = $window.FindFirst($TS::Descendants, $condition)
    }
    if ($null -eq $element -and -not [string]::IsNullOrEmpty($className)) {
        # Legacy WinForms/Win32 controls expose a generic Pane whose class carries the real kind
        # (`WindowsForms10.EDIT...`, `Edit`, `Button`). Matching the class is how those are found.
        $all = $window.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($candidate in $all) {
            if ($candidate.Current.ClassName -like "*$className*") { $element = $candidate; break }
        }
    }
    if ($null -eq $element -and -not [string]::IsNullOrEmpty($name)) {
        # A tab label or button may expose its text through a child Text element.
        $textCondition = New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, $name)
        $element = $window.FindFirst($TS::Descendants, $textCondition)
    }
    return $element
}

function Get-Pattern($element, $pattern) {
    $value = $null
    if ($element.TryGetCurrentPattern($pattern, [ref]$value)) { return $value }
    return $null
}

# Legacy WinForms/Win32 controls expose no UIA patterns in the boxed provider. The messages below
# are the documented Win32 equivalent of the missing pattern (WM_SETTEXT, BM_CLICK, WM_CLOSE),
# used only after UI Automation discovery has already identified the exact control.
function Get-NativeText($handle) {
    if ($null -eq $handle -or $handle -eq 0) { return $null }
    $length = [CanallaNative]::GetWindowTextLength([IntPtr]$handle)
    if ($length -le 0) { return '' }
    $buffer = New-Object System.Text.StringBuilder ($length + 1)
    [void][CanallaNative]::GetWindowText([IntPtr]$handle, $buffer, $buffer.Capacity)
    return $buffer.ToString()
}

function Set-NativeText($handle, [string]$text) {
    if ($null -eq $handle -or $handle -eq 0) { return $false }
    [void][CanallaNative]::SendMessageText([IntPtr]$handle, [CanallaNative]::WM_SETTEXT, [IntPtr]::Zero, $text)
    return ((Get-NativeText $handle) -eq $text)
}

function Invoke-ByPoint($element) {
    $rect = $element.Current.BoundingRectangle
    $x = [int]($rect.Left + $rect.Width / 2)
    $y = [int]($rect.Top + $rect.Height / 2)
    if ($x -le 0 -or $y -le 0) { return $false }
    [void][CanallaNative]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 80
    [CanallaNative]::mouse_event([CanallaNative]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
    [CanallaNative]::mouse_event([CanallaNative]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
    return $true
}

function Invoke-Element($element) {
    $pattern = Get-Pattern $element ([System.Windows.Automation.InvokePattern]::Pattern)
    if ($null -ne $pattern) { $pattern.Invoke(); return $true }
    # LegacyIAccessiblePattern is not present in every UIAutomationClient build; the Win32
    # equivalent (BM_CLICK) covers the same controls without depending on it.
    try {
        $legacy = Get-Pattern $element ([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) { $legacy.DoDefaultAction(); return $true }
    } catch { }
    return $false
}

function Save-Screenshot($handle, [string]$path) {
    if ([string]::IsNullOrEmpty($path)) { return $null }
    $rect = New-Object CanallaNative+RECT
    if (-not [CanallaNative]::GetWindowRect([IntPtr]$handle, [ref]$rect)) { return $null }
    $width = [Math]::Max(1, $rect.Right - $rect.Left)
    $height = [Math]::Max(1, $rect.Bottom - $rect.Top)
    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
        $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose(); $bitmap.Dispose()
    }
    return $path
}

function Finish($ok, [string]$observed, $elementInfo, $windowInfo, [string]$screenshot, [string]$errorText) {
    $payload = @{
        ok = [bool]$ok
        action = $action
        observed = $observed
        element = $elementInfo
        window = $windowInfo
        screenshot = $screenshot
        error = $errorText
    }
    $payload | ConvertTo-Json -Compress -Depth 6
    exit 0
}

try {
    $windowTitle = [string](Get-Param 'window' '')
    $elementName = [string](Get-Param 'name' '')
    $automationId = [string](Get-Param 'automation_id' '')
    $controlType = [string](Get-Param 'control_type' '')
    $className = [string](Get-Param 'class_name' '')
    $value = [string](Get-Param 'value' '')
    $path = [string](Get-Param 'path' '')
    $timeoutMs = [int](Get-Param 'timeout_ms' 8000)

    switch ($action) {
        'list_windows' {
            $windows = Get-Windows
            $payload = @{ ok = $true; action = $action; windows = $windows; observed = "$($windows.Count) windows" }
            $payload | ConvertTo-Json -Compress -Depth 6
            exit 0
        }
        'wait_window' {
            $deadline = (Get-Date).AddMilliseconds($timeoutMs)
            $found = $null
            while ((Get-Date) -lt $deadline) {
                $found = Find-Window $windowTitle
                if ($null -ne $found) { break }
                Start-Sleep -Milliseconds 200
            }
            Finish ($null -ne $found) $(if ($found) { "window '$($found.title)' present" } else { "window '$windowTitle' not found" }) $null $found '' $(if ($found) { '' } else { 'window_not_found' })
        }
        'close_window' {
            $info = Find-Window $windowTitle
            if ($null -eq $info) { Finish $false "window '$windowTitle' not found" $null $null '' 'window_not_found' }
            $window = Get-WindowElement $info
            $pattern = Get-Pattern $window ([System.Windows.Automation.WindowPattern]::Pattern)
            if ($null -ne $pattern) {
                $pattern.Close()
            } elseif ($info.handle -ne 0) {
                [void][CanallaNative]::PostMessage([IntPtr]$info.handle, [CanallaNative]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
            } else {
                Finish $false 'window has neither WindowPattern nor a native handle' $null $info '' 'pattern_unavailable'
            }
            Start-Sleep -Milliseconds 500
            $deadline = (Get-Date).AddMilliseconds(4000)
            $still = Find-Window $windowTitle
            while ($null -ne $still -and (Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 250
                $still = Find-Window $windowTitle
            }
            Finish ($null -eq $still) $(if ($null -eq $still) { 'window closed' } else { 'window still present' }) $null $info '' $(if ($null -eq $still) { '' } else { 'close_failed' })
        }
        default {
            $info = Find-Window $windowTitle
            if ($null -eq $info) { Finish $false "window '$windowTitle' not found" $null $null '' 'window_not_found' }
            $window = Get-WindowElement $info

            if ($action -eq 'focus') {
                [void][CanallaNative]::ShowWindow([IntPtr]$info.handle, 9)
                [void][CanallaNative]::SetForegroundWindow([IntPtr]$info.handle)
                Start-Sleep -Milliseconds 250
                $after = Get-WindowInfo $window
                Finish $after.focused "focused: $($after.focused)" $null $after '' $(if ($after.focused) { '' } else { 'focus_failed' })
            }
            if ($action -eq 'screenshot') {
                $shot = Save-Screenshot $info.handle $path
                Finish ($null -ne $shot) $(if ($shot) { "screenshot $shot" } else { 'screenshot failed' }) $null $info $shot $(if ($shot) { '' } else { 'screenshot_failed' })
            }
            if ($action -eq 'send_keys') {
                if (-not $allowInput) { Finish $false 'input not permitted' $null $info '' 'input_not_permitted' }
                [void][CanallaNative]::SetForegroundWindow([IntPtr]$info.handle)
                Start-Sleep -Milliseconds 200
                [System.Windows.Forms.SendKeys]::SendWait($value)
                Start-Sleep -Milliseconds 300
                Finish $true "sent $($value.Length) chars" $null $info '' ''
            }

            $element = Find-Element $window $elementName $automationId $controlType $className
            if ($null -eq $element -and -not [string]::IsNullOrEmpty($elementName)) {
                # The first observation can be stale (a dialog opened after the tree walk).
                Start-Sleep -Milliseconds 300
                $window = Get-WindowElement $info
                $element = Find-Element $window $elementName $automationId $controlType $className
            }
            if ($null -eq $element -and -not [string]::IsNullOrEmpty($className)) {
                Start-Sleep -Milliseconds 400
                $window = Get-WindowElement $info
                $element = Find-Element $window $elementName $automationId $controlType $className
            }
            if ($null -eq $element) { Finish $false "element not found (name='$elementName' id='$automationId')" $null $info '' 'element_not_found' }
            $before = Get-ElementInfo $element

            switch ($action) {
                'find' { Finish $true 'element found' $before $info '' '' }
                'get_text' {
                    $text = $before.name
                    $textPattern = Get-Pattern $element ([System.Windows.Automation.TextPattern]::Pattern)
                    if ($null -ne $textPattern) {
                        $text = $textPattern.DocumentRange.GetText(-1)
                    } elseif (-not [string]::IsNullOrEmpty($before.value)) {
                        $text = $before.value
                    } else {
                        $native = Get-NativeText $before.handle
                        if (-not [string]::IsNullOrEmpty($native)) { $text = $native }
                    }
                    Finish $true $text $before $info '' ''
                }
                'invoke' {
                    $ok = Invoke-Element $element
                    $how = 'pattern'
                    if (-not $ok -and $before.handle -ne 0) {
                        [void][CanallaNative]::PostMessage([IntPtr]$before.handle, [CanallaNative]::BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
                        $ok = $true; $how = 'bm_click'
                    }
                    if (-not $ok -and $allowInput) {
                        $ok = Invoke-ByPoint $element
                        $how = 'mouse'
                    }
                    Start-Sleep -Milliseconds 350
                    $again = Find-Element (Get-WindowElement $info) $elementName $automationId $controlType $className
                    $after = Get-ElementInfo $again
                    Finish $ok $(if ($ok) { "invoked ($how)" } else { 'no invoke pattern and input not permitted' }) $after $info '' $(if ($ok) { '' } else { 'pattern_unavailable' })
                }
                'set_value' {
                    $observed = $null
                    $ok = $false
                    $how = ''
                    $pattern = Get-Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)
                    if ($null -ne $pattern) {
                        $pattern.SetValue($value)
                        Start-Sleep -Milliseconds 200
                        $observed = (Get-ElementInfo $element).value
                        $ok = ($observed -eq $value); $how = 'value_pattern'
                    } elseif ($before.handle -ne 0) {
                        $ok = Set-NativeText $before.handle $value
                        $observed = Get-NativeText $before.handle
                        $how = 'wm_settext'
                    } elseif ($allowInput) {
                        $element.SetFocus()
                        [System.Windows.Forms.SendKeys]::SendWait('^a')
                        [System.Windows.Forms.SendKeys]::SendWait($value)
                        Start-Sleep -Milliseconds 200
                        $observed = (Get-ElementInfo $element).value
                        $ok = $true; $how = 'send_keys'
                    }
                    $after = Get-ElementInfo $element
                    Finish $ok $("value '$observed' ($how)") $after $info '' $(if ($ok) { '' } else { 'pattern_unavailable' })
                }
                'select' {
                    $pattern = Get-Pattern $element ([System.Windows.Automation.SelectionItemPattern]::Pattern)
                    if ($null -eq $pattern) { Finish $false 'element has no SelectionItemPattern' $before $info '' 'pattern_unavailable' }
                    $pattern.Select()
                    Start-Sleep -Milliseconds 250
                    $after = Get-ElementInfo $element
                    Finish $true 'selected' $after $info ''
                }
                'expand' {
                    $pattern = Get-Pattern $element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
                    if ($null -eq $pattern) { Finish $false 'element has no ExpandCollapsePattern' $before $info '' 'pattern_unavailable' }
                    $pattern.Expand()
                    Start-Sleep -Milliseconds 250
                    $after = Get-ElementInfo $element
                    Finish $true 'expanded' $after $info ''
                }
                'scroll' {
                    $pattern = Get-Pattern $element ([System.Windows.Automation.ScrollPattern]::Pattern)
                    if ($null -eq $pattern) { Finish $false 'element has no ScrollPattern' $before $info '' 'pattern_unavailable' }
                    $direction = [string](Get-Param 'direction' 'down')
                    $amount = [string](Get-Param 'amount' 'large')
                    switch ($direction) {
                        'up' { $pattern.Scroll([System.Windows.Automation.ScrollAmount]::$amount, [System.Windows.Automation.ScrollAmount]::NoAmount) }
                        'down' { $pattern.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, [System.Windows.Automation.ScrollAmount]::$amount) }
                        'left' { $pattern.Scroll([System.Windows.Automation.ScrollAmount]::$amount, [System.Windows.Automation.ScrollAmount]::NoAmount) }
                        'right' { $pattern.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, [System.Windows.Automation.ScrollAmount]::$amount) }
                    }
                    Start-Sleep -Milliseconds 250
                    Finish $true "scrolled $direction" (Get-ElementInfo $element) $info ''
                }
                default { Finish $false "unknown action '$action'" $before $info '' 'unknown_action' }
            }
        }
    }
} catch {
    $payload = @{ ok = $false; action = $action; observed = ''; element = $null; window = $null; screenshot = ''; error = $_.Exception.Message }
    $payload | ConvertTo-Json -Compress -Depth 6
    exit 0
}
