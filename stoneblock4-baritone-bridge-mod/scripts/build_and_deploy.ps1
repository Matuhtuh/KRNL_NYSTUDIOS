param(
  [string]$InstanceModsPath = 'C:\Users\suret\curseforge\minecraft\Instances\FTB StoneBlock 4\mods',
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo

function Resolve-JavaHome {
  if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\javac.exe'))) {
    return $env:JAVA_HOME
  }

  $jdkRoot = Join-Path $HOME '.gradle\jdks'
  if (Test-Path $jdkRoot) {
    $candidates = Get-ChildItem -Path $jdkRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
      Test-Path (Join-Path $_.FullName 'bin\javac.exe')
    } | Sort-Object LastWriteTime -Descending
    if (@($candidates).Count -gt 0) {
      return $candidates[0].FullName
    }
  }

  throw 'No JDK with javac.exe found. Install JDK 21 or set JAVA_HOME.'
}

$javaHome = Resolve-JavaHome
$env:JAVA_HOME = $javaHome
$env:Path = "$javaHome\bin;$env:Path"

if (-not $SkipBuild) {
  & .\gradlew.bat clean build
}

$jar = Get-ChildItem -Path (Join-Path $repo 'build\libs') -Filter 'sb4baritonebridge-*.jar' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $jar) {
  throw 'Build output jar not found in build\libs.'
}

if (-not (Test-Path $InstanceModsPath)) {
  throw "Mods path not found: $InstanceModsPath"
}

$backupDir = Join-Path (Split-Path $InstanceModsPath -Parent) 'mod_backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$legacyBridgePatterns = @(
  'stoneblock4-client-bridge-*.jar',
  'stoneblock4bridge-*.jar'
)

$retiredJars = @()
$oldBridgeJars = Get-ChildItem -Path $InstanceModsPath -Filter 'sb4baritonebridge-*.jar' -ErrorAction SilentlyContinue
foreach ($oldJar in $oldBridgeJars) {
  if ($oldJar.Name -ieq $jar.Name) {
    continue
  }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $retiredPath = Join-Path $backupDir ("{0}.retired-{1}{2}" -f $oldJar.BaseName, $stamp, $oldJar.Extension)
  Move-Item -Path $oldJar.FullName -Destination $retiredPath -Force
  $retiredJars += $retiredPath
}

$retiredLegacyBridgeJars = @()
foreach ($pattern in $legacyBridgePatterns) {
  $legacyJars = Get-ChildItem -Path $InstanceModsPath -Filter $pattern -ErrorAction SilentlyContinue
  foreach ($legacyJar in $legacyJars) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $retiredPath = Join-Path $backupDir ("{0}.legacy-conflict-{1}{2}" -f $legacyJar.BaseName, $stamp, $legacyJar.Extension)
    Move-Item -Path $legacyJar.FullName -Destination $retiredPath -Force
    $retiredLegacyBridgeJars += $retiredPath
  }
}

$target = Join-Path $InstanceModsPath $jar.Name
$backup = $null
if (Test-Path $target) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = Join-Path $backupDir ("{0}.backup-{1}.jar" -f [System.IO.Path]::GetFileNameWithoutExtension($jar.Name), $stamp)
  Copy-Item -Path $target -Destination $backup -Force
}

Copy-Item -Path $jar.FullName -Destination $target -Force

$deployedVersion = $null
$match = [regex]::Match($jar.Name, 'sb4baritonebridge-([0-9]+\.[0-9]+\.[0-9]+)')
if ($match.Success) {
  $deployedVersion = $match.Groups[1].Value
}

$bridgeStatusPath = Join-Path (Split-Path $InstanceModsPath -Parent) 'config\sb4_baritone_bridge\status.json'
$liveBridgeVersion = $null
$bridgeStatusTimestamp = $null
$restartRequired = $false
$versionMismatch = $null

if (Test-Path $bridgeStatusPath) {
  try {
    $status = Get-Content -Path $bridgeStatusPath -Raw | ConvertFrom-Json
    $liveBridgeVersion = [string]$status.bridgeVersion
    $bridgeStatusTimestamp = [string]$status.timestampIso
  } catch {
    $liveBridgeVersion = $null
  }
}

if ($deployedVersion -and $liveBridgeVersion) {
  try {
    $restartRequired = ([version]$liveBridgeVersion -ne [version]$deployedVersion)
  } catch {
    $restartRequired = ($liveBridgeVersion -ne $deployedVersion)
  }
  if ($restartRequired) {
    $versionMismatch = "deployed=$deployedVersion live=$liveBridgeVersion (restart Minecraft to load new bridge jar)"
  }
}

if (@($retiredLegacyBridgeJars).Count -gt 0) {
  $restartRequired = $true
  if (-not $versionMismatch) {
    $versionMismatch = "legacy bridge jar retired during deploy (restart Minecraft to load sb4baritonebridge)"
  }
}

[ordered]@{
  repo = $repo
  javaHome = $javaHome
  builtJar = $jar.FullName
  deployedTo = $target
  backupJar = $backup
  retiredJars = $retiredJars
  retiredLegacyBridgeJars = $retiredLegacyBridgeJars
  deployedSize = (Get-Item $target).Length
  deployedVersion = $deployedVersion
  liveBridgeVersion = $liveBridgeVersion
  bridgeStatusPath = $bridgeStatusPath
  bridgeStatusTimestamp = $bridgeStatusTimestamp
  restartRequired = $restartRequired
  versionMismatch = $versionMismatch
} | ConvertTo-Json -Depth 4
