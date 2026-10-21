Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WinEnum {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr extra);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int max);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
  Write-Output ("screen " + $screen.DeviceName + " bounds=" + $screen.Bounds + " work=" + $screen.WorkingArea)
}

$items = New-Object System.Collections.ArrayList
$callback = [WinEnum+EnumProc]{
  param($hWnd, $lParam)
  if (-not [WinEnum]::IsWindowVisible($hWnd)) { return $true }
  $length = [WinEnum]::GetWindowTextLength($hWnd)
  if ($length -lt 1) { return $true }
  $builder = New-Object System.Text.StringBuilder ($length + 2)
  [WinEnum]::GetWindowText($hWnd, $builder, $builder.Capacity) | Out-Null
  $rect = New-Object WinEnum+RECT
  [WinEnum]::GetWindowRect($hWnd, [ref]$rect) | Out-Null
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($width -lt 200 -or $height -lt 200) { return $true }
  [void]$items.Add([pscustomobject]@{
    title = $builder.ToString();
    x = $rect.Left; y = $rect.Top; w = $width; h = $height
  })
  return $true
}
[WinEnum]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
$items | Format-Table -AutoSize | Out-String -Width 200 | Write-Output
