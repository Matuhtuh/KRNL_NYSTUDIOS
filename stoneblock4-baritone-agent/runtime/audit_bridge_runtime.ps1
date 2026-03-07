param(
  [string]$InstanceRoot = 'C:\Users\suret\curseforge\minecraft\Instances\FTB StoneBlock 4',
  [double]$StaleAfterSeconds = 4.0
)

$ErrorActionPreference = 'Stop'

$modsPath = Join-Path $InstanceRoot 'mods'
$statusPath = Join-Path $InstanceRoot 'config\sb4_baritone_bridge\status.json'
$latestLogPath = Join-Path $InstanceRoot 'logs\latest.log'

function Get-ForgeClientProcessInfo {
  param([string]$TargetInstanceRoot)

  $normalizedTargetRoot = [System.IO.Path]::GetFullPath($TargetInstanceRoot).TrimEnd('\')
  $matching = @()

  $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^javaw?\.exe$' -and $_.CommandLine -and $_.CommandLine -like '*--launchTarget forgeclient*'
  }

  foreach ($proc in $processes) {
    $gameDir = $null
    if ($proc.CommandLine -match '--gameDir\s+"([^"]+)"') {
      try {
        $gameDir = [System.IO.Path]::GetFullPath($Matches[1]).TrimEnd('\')
      } catch {
        $gameDir = $Matches[1].TrimEnd('\')
      }
    }
    if (-not $gameDir -or $gameDir -ne $normalizedTargetRoot) {
      continue
    }

    $liveProcess = $null
    $windowTitle = ''
    try {
      $liveProcess = Get-Process -Id $proc.ProcessId -ErrorAction Stop
      $windowTitle = [string]$liveProcess.MainWindowTitle
    } catch {}

    $creationIso = $null
    if ($proc.CreationDate) {
      try {
        $creationIso = ([System.Management.ManagementDateTimeConverter]::ToDateTime($proc.CreationDate)).ToString('o')
      } catch {}
    }
    if (-not $creationIso -and $liveProcess) {
      try {
        $creationIso = $liveProcess.StartTime.ToString('o')
      } catch {}
    }

    $matching += [pscustomobject]@{
      processId = [int]$proc.ProcessId
      parentProcessId = [int]$proc.ParentProcessId
      creationTime = $creationIso
      windowTitle = $windowTitle
      hasWindow = -not [string]::IsNullOrWhiteSpace($windowTitle)
      gameDir = $gameDir
    }
  }

  $matching = @($matching | Sort-Object creationTime, processId)
  return [pscustomobject]@{
    count = @($matching).Count
    pids = @($matching | Select-Object -ExpandProperty processId)
    activeWriterPid = $null
    processes = $matching
    issue = if (@($matching).Count -gt 1) { 'multiple forgeclient processes detected for instance' } else { $null }
  }
}

function Get-BridgeJarInfo {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return [pscustomobject]@{
      count = 0
      names = @()
      activeName = $null
      activeVersion = $null
      issue = "mods path missing"
    }
  }
  $jars = Get-ChildItem -Path $Path -Filter 'sb4baritonebridge-*.jar' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
  $active = $jars | Select-Object -First 1
  $activeVersion = $null
  if ($active) {
    $m = [regex]::Match($active.Name, 'sb4baritonebridge-([0-9]+\.[0-9]+\.[0-9]+)')
    if ($m.Success) {
      $activeVersion = $m.Groups[1].Value
    }
  }
  return [pscustomobject]@{
    count = @($jars).Count
    names = @($jars | Select-Object -ExpandProperty Name)
    activeName = if ($active) { $active.Name } else { $null }
    activePath = if ($active) { $active.FullName } else { $null }
    activeVersion = $activeVersion
    activeLastWriteTime = if ($active) { $active.LastWriteTime.ToString('o') } else { $null }
    issue = if (@($jars).Count -eq 0) { "no active bridge jar in mods" } else { $null }
  }
}

function Get-ConflictingBridgeJarInfo {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return [pscustomobject]@{
      count = 0
      names = @()
      activeName = $null
      modIdHints = @()
      issue = "mods path missing"
    }
  }

  $patterns = @(
    @{ Pattern = 'stoneblock4-client-bridge-*.jar'; ModId = 'stoneblock4bridge' }
  )

  $matches = @()
  foreach ($entry in $patterns) {
    $found = Get-ChildItem -Path $Path -Filter $entry.Pattern -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      ForEach-Object {
        [pscustomobject]@{
          Name = $_.Name
          LastWriteTime = $_.LastWriteTime
          ModIdHint = $entry.ModId
        }
      }
    $matches += @($found)
  }

  $matches = @($matches | Sort-Object LastWriteTime -Descending)
  $active = $matches | Select-Object -First 1
  return [pscustomobject]@{
    count = @($matches).Count
    names = @($matches | Select-Object -ExpandProperty Name)
    activeName = if ($active) { $active.Name } else { $null }
    modIdHints = @($matches | Select-Object -ExpandProperty ModIdHint -Unique)
    issue = $null
  }
}

function Get-StatusInfo {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return [pscustomobject]@{
      exists = $false
      parseOk = $false
      bridgeVersion = $null
      timestampIso = $null
      bridgeWriterPid = 0
      bridgeWriterSessionId = $null
      bridgeWriterStatusSeq = 0
      inWorld = $false
      baritoneLoaded = $false
      bridgeOk = $false
      windowActive = $null
      currentScreen = $null
      screenName = $null
      screenClass = $null
      screenTitle = $null
      screenPausesGame = $null
      clientStateHint = $null
      playerName = $null
      modpackName = $null
      stageHint = $null
      issue = "status file missing"
    }
  }
  try {
    $status = Get-Content -Path $Path -Raw | ConvertFrom-Json
  } catch {
    return [pscustomobject]@{
      exists = $true
      parseOk = $false
      bridgeVersion = $null
      timestampIso = $null
      bridgeWriterPid = 0
      bridgeWriterSessionId = $null
      bridgeWriterStatusSeq = 0
      inWorld = $false
      baritoneLoaded = $false
      bridgeOk = $false
      windowActive = $null
      currentScreen = $null
      screenName = $null
      screenClass = $null
      screenTitle = $null
      screenPausesGame = $null
      clientStateHint = $null
      playerName = $null
      modpackName = $null
      stageHint = $null
      issue = "status parse failed: $($_.Exception.Message)"
    }
  }
  return [pscustomobject]@{
    exists = $true
    parseOk = $true
    bridgeVersion = [string]$status.bridgeVersion
    timestampIso = [string]$status.timestampIso
    bridgeWriterPid = if ($null -eq $status.bridgeWriterPid) { 0 } else { [int64]$status.bridgeWriterPid }
    bridgeWriterSessionId = [string]$status.bridgeWriterSessionId
    bridgeWriterStatusSeq = if ($null -eq $status.bridgeWriterStatusSeq) { 0 } else { [int64]$status.bridgeWriterStatusSeq }
    inWorld = [bool]$status.inWorld
    baritoneLoaded = [bool]$status.baritoneLoaded
    bridgeOk = [bool]$status.bridgeOk
    windowActive = if ($null -eq $status.windowActive) { $null } else { [bool]$status.windowActive }
    currentScreen = [string]$status.currentScreen
    screenName = [string]$status.screenName
    screenClass = [string]$status.screenClass
    screenTitle = [string]$status.screenTitle
    screenPausesGame = if ($null -eq $status.screenPausesGame) { $null } else { [bool]$status.screenPausesGame }
    clientStateHint = [string]$status.clientStateHint
    playerName = [string]$status.playerName
    modpackName = [string]$status.modpackName
    stageHint = [string]$status.stoneblockStageHint
    issue = $null
  }
}

function Get-LoadedBridgeRuntimeInfo {
  param([string]$LogPath)

  if (-not (Test-Path $LogPath)) {
    return [pscustomobject]@{
      found = $false
      loadedJarName = $null
      mode = $null
      issue = "latest.log missing"
    }
  }

  $bridgeLines = Get-Content -Path $LogPath -Tail 4000 -ErrorAction SilentlyContinue | Where-Object {
    $_ -match 'Found mod file "(sb4baritonebridge-[^"]+\.jar|stoneblock4-client-bridge-[^"]+\.jar|stoneblock4bridge-[^"]+\.jar)"'
  }
  $lastBridgeLine = $bridgeLines | Select-Object -Last 1
  if (-not $lastBridgeLine) {
    return [pscustomobject]@{
      found = $false
      loadedJarName = $null
      mode = $null
      issue = "bridge jar not found in latest.log"
    }
  }

  $loadedJarName = $null
  if ($lastBridgeLine -match 'Found mod file "([^"]+)"') {
    $loadedJarName = $Matches[1]
  }

  $mode = $null
  if ($loadedJarName -like 'sb4baritonebridge-*') {
    $mode = 'file_bridge'
  } elseif ($loadedJarName -like 'stoneblock4-client-bridge-*' -or $loadedJarName -like 'stoneblock4bridge-*') {
    $mode = 'legacy_http_bridge'
  }

  return [pscustomobject]@{
    found = [bool]$loadedJarName
    loadedJarName = $loadedJarName
    mode = $mode
    issue = $null
  }
}

function Get-LiveContextInfo {
  param(
    [string]$LogPath,
    [object]$Status,
    [double]$StaleAfterSeconds = 4.0
  )

  $worldEvidence = $null
  $worldEvidenceInLog = $false
  if (Test-Path $LogPath) {
    $tail = Get-Content -Path $LogPath -Tail 4000 -ErrorAction SilentlyContinue
    $worldLine = $tail | Where-Object {
      $_ -match 'Baritone world data dir:' -or $_ -match 'Logged in with entity id'
    } | Select-Object -Last 1
    if ($worldLine) {
      $worldEvidence = $worldLine.Trim()
      $worldEvidenceInLog = $true
    }
  }

  $statusAgeSeconds = $null
  $statusFresh = $false
  if ($Status -and $Status.parseOk -and $Status.timestampIso) {
    try {
      $statusTimestamp = [datetimeoffset]::Parse($Status.timestampIso)
      $statusAgeSeconds = ([datetimeoffset]::UtcNow - $statusTimestamp.ToUniversalTime()).TotalSeconds
      $statusFresh = $statusAgeSeconds -le [Math]::Max(0.5, $StaleAfterSeconds)
    } catch {}
  }

  $classification = 'unknown'
  if ($Status -and $Status.parseOk -and $statusFresh -and $Status.inWorld -and $Status.bridgeOk -and $Status.baritoneLoaded) {
    $classification = 'in_world_and_bridge_ready'
  } elseif ($Status -and $Status.parseOk -and $statusFresh -and -not $Status.inWorld) {
    $classification = 'not_connected_or_loading'
  } elseif ($worldEvidenceInLog -and -not $statusFresh) {
    $classification = 'in_world_but_stale_bridge_status'
  } elseif ($Status -and $Status.parseOk -and -not $statusFresh -and -not $worldEvidenceInLog) {
    $classification = 'bridge_status_stale_without_world_evidence'
  }

  return [pscustomobject]@{
    statusAgeSeconds = $statusAgeSeconds
    statusFresh = $statusFresh
    worldEvidenceInLog = $worldEvidenceInLog
    worldEvidence = $worldEvidence
    classification = $classification
  }
}

function Get-BridgeRuntimeAssessment {
  param(
    [object]$BridgeJar,
    [object]$ForgeClients,
    [object]$Status,
    [object]$LoadedBridgeRuntime,
    [object]$LiveContext
  )

  $jarWriteTime = $null
  if ($BridgeJar -and $BridgeJar.activeLastWriteTime) {
    try {
      $jarWriteTime = [datetimeoffset]::Parse([string]$BridgeJar.activeLastWriteTime)
    } catch {}
  }

  $processes = @($ForgeClients.processes)
  $preferred = $null
  $preferredReason = ''

  if ($Status -and $Status.parseOk -and $LiveContext.statusFresh -and $Status.bridgeWriterPid -gt 0) {
    $preferred = @($processes | Where-Object { $_.processId -eq [int64]$Status.bridgeWriterPid } | Select-Object -First 1)[0]
    if ($preferred) {
      $preferredReason = 'fresh_bridge_writer_pid'
    }
  }

  if (-not $preferred -and $jarWriteTime) {
    $afterJarWrite = @()
    foreach ($proc in $processes) {
      if (-not $proc.creationTime) {
        continue
      }
      try {
        $procTime = [datetimeoffset]::Parse([string]$proc.creationTime)
      } catch {
        continue
      }
      if ($procTime.ToUniversalTime() -ge $jarWriteTime.ToUniversalTime()) {
        $afterJarWrite += $proc
      }
    }
    if (@($afterJarWrite).Count -eq 1) {
      $preferred = $afterJarWrite[0]
      $preferredReason = 'only_client_started_after_bridge_jar_write'
    }
  }

  $runningClientStartedAfterJarWrite = $null
  if ($preferred -and $preferred.creationTime -and $jarWriteTime) {
    try {
      $preferredTime = [datetimeoffset]::Parse([string]$preferred.creationTime)
      $runningClientStartedAfterJarWrite = $preferredTime.ToUniversalTime() -ge $jarWriteTime.ToUniversalTime()
    } catch {}
  }

  $duplicatePids = @()
  if ($preferred) {
    $duplicatePids = @($processes | Where-Object { $_.processId -ne $preferred.processId } | Select-Object -ExpandProperty processId)
  }

  $loadedJarMatchesDeployedJar = $false
  if ($BridgeJar.activeName -and $LoadedBridgeRuntime.loadedJarName) {
    $loadedJarMatchesDeployedJar = ([string]$BridgeJar.activeName -eq [string]$LoadedBridgeRuntime.loadedJarName)
  }

  $likelyLoadedCurrentJar = $false
  if ($preferred) {
    if ($null -ne $runningClientStartedAfterJarWrite) {
      $likelyLoadedCurrentJar = [bool]$runningClientStartedAfterJarWrite
    } else {
      $likelyLoadedCurrentJar = $loadedJarMatchesDeployedJar
    }
  }

  $issue = $null
  if ($ForgeClients.count -gt 1 -and -not $preferred) {
    $issue = 'multiple_clients_without_confident_keeper'
  } elseif ($preferred -and $jarWriteTime -and $runningClientStartedAfterJarWrite -eq $false) {
    $issue = 'running_client_started_before_bridge_jar_write'
  } elseif ($LoadedBridgeRuntime.found -and $BridgeJar.activeName -and -not $loadedJarMatchesDeployedJar) {
    $issue = 'loaded_bridge_jar_name_differs_from_deployed_mod_jar'
  }

  return [pscustomobject]@{
    preferredClientPid = if ($preferred) { [int]$preferred.processId } else { $null }
    preferredClientCreationTime = if ($preferred) { [string]$preferred.creationTime } else { $null }
    preferredClientReason = $preferredReason
    duplicateClientPids = $duplicatePids
    activeJarLastWriteTime = if ($BridgeJar.activeLastWriteTime) { [string]$BridgeJar.activeLastWriteTime } else { $null }
    loadedJarMatchesDeployedJar = $loadedJarMatchesDeployedJar
    runningClientStartedAfterJarWrite = $runningClientStartedAfterJarWrite
    likelyLoadedCurrentJar = $likelyLoadedCurrentJar
    issue = $issue
  }
}

$jar = Get-BridgeJarInfo -Path $modsPath
$conflictingJar = Get-ConflictingBridgeJarInfo -Path $modsPath
$status = Get-StatusInfo -Path $statusPath
$loadedBridgeRuntime = Get-LoadedBridgeRuntimeInfo -LogPath $latestLogPath
$liveContext = Get-LiveContextInfo -LogPath $latestLogPath -Status $status -StaleAfterSeconds $StaleAfterSeconds
$forgeClientProcesses = Get-ForgeClientProcessInfo -TargetInstanceRoot $InstanceRoot
if ($status.parseOk -and $status.bridgeWriterPid -gt 0) {
  $forgeClientProcesses.activeWriterPid = [int64]$status.bridgeWriterPid
}
$bridgeRuntimeAssessment = Get-BridgeRuntimeAssessment -BridgeJar $jar -ForgeClients $forgeClientProcesses -Status $status -LoadedBridgeRuntime $loadedBridgeRuntime -LiveContext $liveContext

$versionMismatch = $false
if ($jar.activeVersion -and $status.bridgeVersion) {
  try {
    $versionMismatch = ([version]$jar.activeVersion -ne [version]$status.bridgeVersion)
  } catch {
    $versionMismatch = ($jar.activeVersion -ne $status.bridgeVersion)
  }
}

$deathScreenTokens = @(
  [string]$status.currentScreen,
  [string]$status.screenName,
  [string]$status.screenClass,
  [string]$status.screenTitle
)
$deathScreenReason = @(
  $deathScreenTokens | Where-Object {
    $text = [string]$_
    $text -and $text.ToLowerInvariant() -match 'death|respawn'
  }
) | Select-Object -First 1
$deathScreenActive = $status.parseOk -and $liveContext.statusFresh -and -not [string]::IsNullOrWhiteSpace([string]$deathScreenReason)

$readinessIssues = @()
if ($jar.count -eq 0) { $readinessIssues += "expected_bridge_jar_missing" }
if (-not $status.exists) { $readinessIssues += "status_missing" }
if ($status.exists -and -not $status.parseOk) { $readinessIssues += "status_parse_failed" }
if ($status.parseOk -and -not $status.bridgeOk) { $readinessIssues += "bridge_not_ok" }
if ($status.parseOk -and -not $status.baritoneLoaded) { $readinessIssues += "baritone_not_loaded" }
if ($status.parseOk -and -not $liveContext.statusFresh) { $readinessIssues += "status_stale" }
if ($status.parseOk -and -not $status.inWorld) { $readinessIssues += "not_in_world" }
if ($jar.count -gt 1) { $readinessIssues += "multiple_bridge_jars_in_mods" }
if ($forgeClientProcesses.count -gt 1) { $readinessIssues += "multiple_forgeclient_processes" }
if ($bridgeRuntimeAssessment.issue -eq 'multiple_clients_without_confident_keeper') { $readinessIssues += "multiple_clients_without_confident_keeper" }
if ($conflictingJar.count -gt 0) { $readinessIssues += "conflicting_legacy_bridge_mod_present" }
if ($loadedBridgeRuntime.mode -eq 'legacy_http_bridge') { $readinessIssues += "legacy_bridge_mod_loaded" }
if ($jar.count -eq 0 -and $status.parseOk -and -not $liveContext.statusFresh) { $readinessIssues += "stale_status_without_active_bridge_jar" }
if ($liveContext.classification -eq 'in_world_but_stale_bridge_status') { $readinessIssues += "in_world_but_stale_bridge_status" }
if ($versionMismatch) { $readinessIssues += "deployed_vs_live_version_mismatch" }
if ($bridgeRuntimeAssessment.issue -eq 'running_client_started_before_bridge_jar_write') { $readinessIssues += "running_client_started_before_bridge_jar_write" }
if ($bridgeRuntimeAssessment.issue -eq 'loaded_bridge_jar_name_differs_from_deployed_mod_jar') { $readinessIssues += "loaded_bridge_jar_name_differs_from_deployed_mod_jar" }
if ($deathScreenActive) { $readinessIssues += "death_screen_active" }

$actions = @()
if ($jar.count -eq 0) { $actions += "Deploy sb4baritonebridge-*.jar to mods, then restart Minecraft so a live bridge runtime can start writing status.json again." }
if ($forgeClientProcesses.count -gt 1) {
  if ($bridgeRuntimeAssessment.preferredClientPid) {
    $actions += "Multiple FTB StoneBlock 4 clients are open for this instance. Keep PID $($bridgeRuntimeAssessment.preferredClientPid) ($($bridgeRuntimeAssessment.preferredClientReason)) and close duplicate PID(s) $($bridgeRuntimeAssessment.duplicateClientPids -join ', ') before running automation."
  } elseif ($status.parseOk -and $status.bridgeWriterPid -gt 0) {
    $otherPids = @($forgeClientProcesses.pids | Where-Object { $_ -ne $status.bridgeWriterPid })
    $actions += "Close extra FTB StoneBlock 4 clients for this instance. status.json is currently being written by forgeclient PID $($status.bridgeWriterPid) (session=$($status.bridgeWriterSessionId), seq=$($status.bridgeWriterStatusSeq)); duplicate forgeclient PIDs are $($otherPids -join ', '). Multiple live clients can overwrite the same status.json and make readiness/world state untruthful."
  } else {
    $actions += "Close extra FTB StoneBlock 4 clients for this instance (forgeclient PIDs: $($forgeClientProcesses.pids -join ', ')). Multiple live clients can overwrite the same status.json and make readiness/world state untruthful."
  }
}
if ($conflictingJar.count -gt 0) {
  $actions += "Remove legacy/conflicting bridge mod(s) $($conflictingJar.names -join ', ') (mod id hint: $($conflictingJar.modIdHints -join ', ')) so the file-bridge runtime is the active client bridge."
}
if ($loadedBridgeRuntime.mode -eq 'legacy_http_bridge') {
  $actions += "The live client loaded $($loadedBridgeRuntime.loadedJarName), which is the legacy bridge path. Restart Minecraft after deploying sb4baritonebridge so file-bridge telemetry can become truthful."
}
if ($versionMismatch) { $actions += "Restart Minecraft to load the deployed bridge jar version." }
if ($bridgeRuntimeAssessment.issue -eq 'running_client_started_before_bridge_jar_write') {
  $actions += "The running forgeclient PID $($bridgeRuntimeAssessment.preferredClientPid) started at $($bridgeRuntimeAssessment.preferredClientCreationTime), before the deployed bridge jar write time $($bridgeRuntimeAssessment.activeJarLastWriteTime). Restart Minecraft once so the current client actually loads the patched bridge jar."
}
if ($bridgeRuntimeAssessment.issue -eq 'loaded_bridge_jar_name_differs_from_deployed_mod_jar') {
  $actions += "latest.log shows the client loaded $($loadedBridgeRuntime.loadedJarName), but the deployed mods jar is $($jar.activeName). Restart the client after verifying the intended jar file in mods."
}
if ($status.parseOk -and -not $liveContext.statusFresh) {
  $actions += "Do not trust the current status.json as live menu/world evidence until it refreshes within the stale threshold."
}
if ($status.parseOk -and -not $status.inWorld -and $liveContext.statusFresh -and -not $liveContext.worldEvidenceInLog) {
  $clientState = [string]$status.clientStateHint
  if ($clientState -eq 'main_menu') {
    $actions += "The bridge is live but the client is at the main menu. Join the intended StoneBlock world before running automation validation commands."
  } elseif ($clientState -eq 'loading_or_joining' -or $clientState -eq 'loading_or_transition') {
    $actions += "The bridge is live but the client is still loading/joining. Wait for the world to finish loading before running automation validation commands."
  } elseif ($clientState -eq 'paused_menu' -or $clientState -eq 'paused_in_world') {
    $actions += "The bridge is live but a pause/menu screen is open. Return to active gameplay before running automation validation commands."
  } else {
    $actions += "Join a world before running automation validation commands."
  }
}
if ($deathScreenActive) {
  $actions += "The client is on a death/respawn screen ($deathScreenReason). Respawn or otherwise return to active in-world control before running automation validation commands."
}
if ($status.parseOk -and -not $status.baritoneLoaded) { $actions += "Ensure Baritone jar is installed and active in the same client." }
if ($jar.count -gt 1) { $actions += "Keep only one sb4baritonebridge-*.jar in mods." }
if ($liveContext.classification -eq 'in_world_but_stale_bridge_status') {
  $actions += "Do not trust status.json as menu-state evidence; latest.log shows the client entered a world while bridge telemetry stayed stale."
}
if (-not $actions) { $actions += "Bridge deployment/runtime checks look healthy." }

[ordered]@{
  now = (Get-Date -Format o)
  instanceRoot = $InstanceRoot
  modsPath = $modsPath
  statusPath = $statusPath
  latestLogPath = $latestLogPath
  bridgeJar = $jar
  conflictingBridgeJar = $conflictingJar
  forgeClientProcesses = $forgeClientProcesses
  bridgeRuntimeAssessment = $bridgeRuntimeAssessment
  loadedBridgeRuntime = $loadedBridgeRuntime
  status = $status
  liveContext = $liveContext
  versionMismatch = $versionMismatch
  deathScreenActive = $deathScreenActive
  deathScreenReason = if ($deathScreenActive) { [string]$deathScreenReason } else { "" }
  readinessIssues = $readinessIssues
  recommendedActions = $actions
} | ConvertTo-Json -Depth 6
