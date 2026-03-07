param(
  [double]$BridgeTimeoutSeconds = 180,
  [switch]$NoDashboard,
  [switch]$DashboardInNewWindow,
  [double]$DashboardIntervalSeconds = 1.0,
  [switch]$KillDashboards,
  [switch]$NoAutoCloseDuplicateClients
)

$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$runtimeDir = Join-Path $workspace 'runtime'
$outLog = Join-Path $runtimeDir 'live_run.log'
$errLog = Join-Path $runtimeDir 'live_run.err'
$auditScript = Join-Path $runtimeDir 'audit_bridge_runtime.ps1'
$duplicateClientCleanup = [ordered]@{
  attempted = $false
  keptPid = $null
  closedPids = @()
  error = ''
}

if (-not (Test-Path $python)) {
  throw "Python venv not found at $python"
}

if (Test-Path $auditScript) {
  $audit = & powershell -ExecutionPolicy Bypass -File $auditScript | ConvertFrom-Json
  if (-not $NoAutoCloseDuplicateClients -and $audit.bridgeRuntimeAssessment -and $audit.bridgeRuntimeAssessment.preferredClientPid) {
    $duplicatePids = @($audit.bridgeRuntimeAssessment.duplicateClientPids)
    if (@($duplicatePids).Count -gt 0) {
      $duplicateClientCleanup.attempted = $true
      $duplicateClientCleanup.keptPid = [int]$audit.bridgeRuntimeAssessment.preferredClientPid
      foreach ($pid in $duplicatePids) {
        try {
          Stop-Process -Id ([int]$pid) -Force -ErrorAction Stop
          $duplicateClientCleanup.closedPids += [int]$pid
        } catch {
          $duplicateClientCleanup.error = $_.Exception.Message
        }
      }
      Start-Sleep -Seconds 2
      $audit = & powershell -ExecutionPolicy Bypass -File $auditScript | ConvertFrom-Json
    }
  }
  $blockingBridgeIssues = @(
    'expected_bridge_jar_missing',
    'multiple_forgeclient_processes',
    'multiple_clients_without_confident_keeper',
    'conflicting_legacy_bridge_mod_present',
    'legacy_bridge_mod_loaded',
    'in_world_but_stale_bridge_status',
    'running_client_started_before_bridge_jar_write',
    'loaded_bridge_jar_name_differs_from_deployed_mod_jar',
    'death_screen_active'
  )
  $matchedBridgeIssues = @($audit.readinessIssues | Where-Object { $blockingBridgeIssues -contains $_ })
  if (@($matchedBridgeIssues).Count -gt 0) {
    [ordered]@{
      workspace = $workspace
      blocked = $true
      reason = 'bridge_preflight_failed'
      duplicateClientCleanup = $duplicateClientCleanup
      readinessIssues = $matchedBridgeIssues
      recommendedActions = @($audit.recommendedActions)
      auditSummary = $audit
    } | ConvertTo-Json -Depth 8
    exit 2
  }
}

Set-Location $workspace
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$stale = Get-CimInstance Win32_Process | Where-Object {
  if (-not ($_.Name -match '^python(\.exe)?$') -or -not $_.CommandLine) {
    return $false
  }
  if ($_.CommandLine -notlike '*stoneblock4-baritone-agent*' -or $_.CommandLine -notlike '*main.py*') {
    return $false
  }
  $isAgentRun = $_.CommandLine -like '* run*' -or $_.CommandLine -like '* arm-run*'
  $isDashboard = $_.CommandLine -match '(^|\s)dashboard(\s|$)'
  return $isAgentRun -or ($KillDashboards -and $isDashboard)
}

$staleAgentRuns = @($stale | Where-Object { $_.CommandLine -like '* run*' -or $_.CommandLine -like '* arm-run*' })
$preKillStopResult = $null
$preKillStopError = ''
if (@($staleAgentRuns).Count -gt 0) {
  try {
    $stopJson = & $python 'main.py' '--config' 'config.yaml' 'stop-baritone' '--reason' 'start_live_automation_stale_cleanup' '--source' 'runtime.start_live_automation.ps1'
    if ($stopJson) {
      $preKillStopResult = $stopJson | ConvertFrom-Json
    }
  } catch {
    $preKillStopError = $_.Exception.Message
  }
}

$killed = @()
foreach ($p in $stale) {
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    $killed += $p.ProcessId
  } catch {}
}

if (Test-Path $outLog) { Remove-Item $outLog -Force }
if (Test-Path $errLog) { Remove-Item $errLog -Force }

$agentArgs = @(
  'main.py',
  '--config', 'config.yaml',
  '--verbose',
  'arm-run',
  '--bridge-timeout', [string]$BridgeTimeoutSeconds
)

$proc = Start-Process -FilePath $python -ArgumentList $agentArgs -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WorkingDirectory $workspace -PassThru

$dashboardPid = $null
if (-not $NoDashboard) {
  if ($DashboardInNewWindow) {
    $dash = Start-Process -FilePath $python -ArgumentList @(
      'main.py', '--config', 'config.yaml', 'dashboard', '--interval', [string]$DashboardIntervalSeconds
    ) -WorkingDirectory $workspace -PassThru
    $dashboardPid = $dash.Id
  } else {
    Write-Host "Agent started (PID=$($proc.Id)). Attaching live dashboard in this terminal..."
    & $python 'main.py' '--config' 'config.yaml' 'dashboard' '--interval' ([string]$DashboardIntervalSeconds)
  }
}

[ordered]@{
  workspace = $workspace
  duplicateClientCleanup = $duplicateClientCleanup
  preKillStopResult = $preKillStopResult
  preKillStopError = $preKillStopError
  killedPids = $killed
  agentPid = $proc.Id
  dashboardPid = $dashboardPid
  stdoutLog = $outLog
  stderrLog = $errLog
} | ConvertTo-Json -Depth 4
