$ErrorActionPreference='Stop'
$workspace = 'C:/Users/suret/stoneblock4-baritone-agent'
Set-Location $workspace

$stale = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python(\.exe)?$' -and
  $_.CommandLine -like '*stoneblock4-baritone-agent*' -and
  $_.CommandLine -like '*main.py*' -and
  $_.CommandLine -like '* run*'
}
$killed = @()
foreach($p in $stale){
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    $killed += $p.ProcessId
  } catch {}
}

$logOut = Join-Path $workspace 'runtime/full_auto_run.log'
$logErr = Join-Path $workspace 'runtime/full_auto_run.err'
New-Item -ItemType Directory -Force -Path (Join-Path $workspace 'runtime') | Out-Null
if(Test-Path $logOut){ Remove-Item $logOut -Force }
if(Test-Path $logErr){ Remove-Item $logErr -Force }

$proc = Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList @('main.py','--config','config.yaml','--verbose','run') -RedirectStandardOutput $logOut -RedirectStandardError $logErr -PassThru -WorkingDirectory $workspace
$agentPid = $proc.Id

$statusPath = 'C:/Users/suret/curseforge/minecraft/Instances/FTB StoneBlock 4/config/sb4_baritone_bridge/status.json'
$pathingBecameTrue = $false
$lastObserved = [ordered]@{ isPathing=$null; currentGoal=''; lastCommand=''; lastCommandResult=''; lastCommandError=''; ts='' }
$snapshots = @()
$warnErrLines = New-Object System.Collections.Generic.List[string]
$errOffset = 0

for($i=0; $i -lt 9; $i++){
  Start-Sleep -Seconds 10

  $alive = $false
  try { $alive = -not (Get-Process -Id $agentPid -ErrorAction Stop).HasExited } catch { $alive = $false }

  if(Test-Path $statusPath){
    try {
      $raw = Get-Content -Raw -Path $statusPath -ErrorAction Stop
      if($raw){
        $js = $raw | ConvertFrom-Json
        $isPathing = [bool]$js.isPathing
        if($isPathing){ $pathingBecameTrue = $true }
        $lastObserved = [ordered]@{
          isPathing = $js.isPathing
          currentGoal = [string]$js.currentGoal
          lastCommand = [string]$js.lastCommand
          lastCommandResult = [string]$js.lastCommandResult
          lastCommandError = [string]$js.lastCommandError
          ts = (Get-Date).ToString('o')
        }
      }
    } catch {}
  }

  if(Test-Path $logErr){
    try {
      $allErr = Get-Content -Path $logErr -ErrorAction Stop
      $count = @($allErr).Count
      if($count -gt $errOffset){
        $new = $allErr[$errOffset..($count-1)]
        foreach($line in $new){
          if($line -match '(?i)warning|error|exception|traceback|fatal'){
            $warnErrLines.Add($line) | Out-Null
          }
        }
        $errOffset = $count
      }
    } catch {}
  }

  $snapshots += [pscustomobject]@{
    ts = (Get-Date).ToString('o')
    alive = $alive
    isPathing = $lastObserved.isPathing
    currentGoal = $lastObserved.currentGoal
    lastCommand = $lastObserved.lastCommand
    lastCommandResult = $lastObserved.lastCommandResult
    lastCommandError = $lastObserved.lastCommandError
  }
}

$finalAlive = $false
try { $finalAlive = -not (Get-Process -Id $agentPid -ErrorAction Stop).HasExited } catch { $finalAlive = $false }

$result = [ordered]@{
  killedPids = $killed
  pid = $agentPid
  alive = $finalAlive
  pathingBecameTrue = $pathingBecameTrue
  lastObserved = $lastObserved
  warningsErrors = @($warnErrLines)
  snapshots = $snapshots
}

$result | ConvertTo-Json -Depth 6

