$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $workspace '.venv\Scripts\python.exe'
Set-Location $workspace

$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python(\.exe)?$' -and
  $_.CommandLine -and
  $_.CommandLine -like '*stoneblock4-baritone-agent*' -and
  $_.CommandLine -like '*main.py*' -and
  ($_.CommandLine -like '* run*' -or $_.CommandLine -like '* arm-run*' -or $_.CommandLine -match '(^|\s)dashboard(\s|$)')
}

$agentTargets = @($targets | Where-Object { $_.CommandLine -like '* run*' -or $_.CommandLine -like '* arm-run*' })
$stopResult = $null
$stopError = ''
if (@($agentTargets).Count -gt 0 -and (Test-Path $python)) {
  try {
    $stopJson = & $python 'main.py' '--config' 'config.yaml' 'stop-baritone' '--reason' 'stop_live_automation' '--source' 'runtime.stop_live_automation.ps1'
    if ($stopJson) {
      $stopResult = $stopJson | ConvertFrom-Json
    }
  } catch {
    $stopError = $_.Exception.Message
  }
}

$killed = @()
foreach ($p in $targets) {
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    $killed += $p.ProcessId
  } catch {}
}

[ordered]@{
  stopResult = $stopResult
  stopError = $stopError
  killedPids = $killed
  count = @($killed).Count
} | ConvertTo-Json -Depth 3
