param([string]$Captures = 'ui-nav-debug')
try {
  & (Join-Path $PSScriptRoot 'ui-nav-during-gen.ps1') -Captures $Captures
} catch {
  Write-Output ("ERR: " + $_.Exception.Message)
  Write-Output ("AT: " + $_.InvocationInfo.PositionMessage)
  Write-Output ($_.ScriptStackTrace)
}
