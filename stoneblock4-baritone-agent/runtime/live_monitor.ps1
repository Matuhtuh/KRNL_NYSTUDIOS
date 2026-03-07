param(
  [switch]$StopBotAtEnd
)

$ErrorActionPreference = 'Stop'
Set-Location 'C:/Users/suret/stoneblock4-baritone-agent'
New-Item -ItemType Directory -Force -Path 'runtime' | Out-Null

$liveOut = 'runtime/live_run.log'
$liveErr = 'runtime/live_run.err'
$monLog = 'runtime/live_run_monitor.log'
if (Test-Path $liveOut) { Remove-Item $liveOut -Force }
if (Test-Path $liveErr) { Remove-Item $liveErr -Force }
if (Test-Path $monLog) { Remove-Item $monLog -Force }

$proc = Start-Process -FilePath '.venv/Scripts/python.exe' -ArgumentList @('main.py', '--config', 'config.yaml', '--verbose', 'arm-run') -RedirectStandardOutput $liveOut -RedirectStandardError $liveErr -PassThru -NoNewWindow
$botPid = $proc.Id

$statusPath = 'C:/Users/suret/curseforge/minecraft/Instances/FTB StoneBlock 4/config/sb4_baritone_bridge/status.json'
$stayedAlive = $true

for ($i = 1; $i -le 5; $i++) {
  Start-Sleep -Seconds 60
  $alive = $false
  try {
    $alive = (Get-Process -Id $botPid -ErrorAction Stop) -ne $null
  } catch {
    $alive = $false
    $stayedAlive = $false
  }

  $inWorld = ''
  $baritoneLoaded = ''
  $isPathing = ''
  $lastCommand = ''
  $lastCommandResult = ''
  $lastCommandError = ''

  if (Test-Path $statusPath) {
    try {
      $raw = Get-Content -Raw -Path $statusPath
      if ($raw -and $raw.Trim().Length -gt 0) {
        $js = $raw | ConvertFrom-Json
        $inWorld = $js.inWorld
        $baritoneLoaded = $js.baritoneLoaded
        $isPathing = $js.isPathing
        $lastCommand = $js.lastCommand
        $lastCommandResult = $js.lastCommandResult
        $lastCommandError = $js.lastCommandError
      }
    } catch {}
  }

  $ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
  $line = "timestamp=$ts processAlive=$alive inWorld=$inWorld baritoneLoaded=$baritoneLoaded isPathing=$isPathing lastCommand=$lastCommand lastCommandResult=$lastCommandResult lastCommandError=$lastCommandError"
  Add-Content -Path $monLog -Value $line
}

$aliveAfter = $false
try {
  $aliveAfter = (Get-Process -Id $botPid -ErrorAction Stop) -ne $null
} catch { $aliveAfter = $false }
$stopResult = $null
$stopError = ''
if ($StopBotAtEnd -and $aliveAfter) {
  try {
    $stopJson = & '.venv/Scripts/python.exe' 'main.py' '--config' 'config.yaml' 'stop-baritone' '--reason' 'live_monitor_stop_at_end' '--source' 'runtime.live_monitor.ps1'
    if ($stopJson) {
      $stopResult = $stopJson | ConvertFrom-Json
    }
  } catch {
    $stopError = $_.Exception.Message
  }
  try { Stop-Process -Id $botPid -Force -ErrorAction Stop } catch {}
}

$warnErrOut = @()
if (Test-Path $liveOut) {
  $warnErrOut = Select-String -Path $liveOut -Pattern 'WARN|WARNING|ERROR|ERR|EXCEPTION|Traceback|CRITICAL|FATAL' | Select-Object -First 40 | ForEach-Object { $_.Line }
}
$warnErrErr = @()
if (Test-Path $liveErr) {
  $warnErrErr = Select-String -Path $liveErr -Pattern 'WARN|WARNING|ERROR|ERR|EXCEPTION|Traceback|CRITICAL|FATAL' | Select-Object -First 40 | ForEach-Object { $_.Line }
}

$stateJson = ''
if (Test-Path 'runtime/state.json') {
  $stateJson = Get-Content -Raw -Path 'runtime/state.json'
}
$monitorText = ''
if (Test-Path $monLog) {
  $monitorText = Get-Content -Raw -Path $monLog
}

$result = [ordered]@{
  pid = $botPid
  stopResult = $stopResult
  stopError = $stopError
  processStayedAliveForWholeSession = $stayedAlive
  stdoutWarningsErrors = $warnErrOut
  stderrWarningsErrors = $warnErrErr
  finalStateJson = $stateJson
  fullMonitorLog = $monitorText
}
$result | ConvertTo-Json -Depth 6
