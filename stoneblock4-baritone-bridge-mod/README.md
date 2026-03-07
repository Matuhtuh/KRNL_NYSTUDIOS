# StoneBlock Baritone Bridge Mod

Client-side NeoForge mod for Minecraft `1.21.1` that exposes Baritone through a local file bridge.

## What It Does

- Reads command files from:
  - `config/sb4_baritone_bridge/inbox/*.json`
- Executes commands via Baritone API (reflection, no compile-time Baritone dependency).
- Supports bridge-native helper command:
  - `bridge.drop_item <slot_1_36> [count_1_64|all]`
  - `bridge.swap_to_hotbar <source_slot_1_36> <target_hotbar_slot_1_9>`
  - `bridge.store_to_nearby_chest [radius_2_12] [max_stacks_1_36] [include_hotbar_0_or_1]`
  - `bridge.fight_hostile [max_distance_1_24] [swings_1_6]`
- Inbox command processing is now FIFO-like by file timestamp (not random filename order).
- JSON writes use atomic temp-file replacement to reduce status/ack read corruption.
- Writes command responses to:
  - `config/sb4_baritone_bridge/outbox/<id>.json`
- Writes live status to:
  - `config/sb4_baritone_bridge/status.json`

## Runtime Requirements

- NeoForge `21.1.219`
- Minecraft `1.21.1`
- Baritone mod jar loaded in the same client instance (recommended line for 1.21.1: `v1.11.x` artifacts)

## Command File Format

Create a JSON file in `inbox`:

```json
{
  "id": "f3c2f27f0f2547f7a2de8e8f06f7e4f1",
  "command": "mine minecraft:cobblestone 128",
  "createdAtMs": 1762250000000,
  "trackActive": true
}
```

`trackActive` defaults to `true`. Set it to `false` for auxiliary commands (pause/resume/tooling/inventory swaps) so they do not overwrite the active command tracking fields in `status.json`.

## Response File Format

Bridge writes `outbox/<id>.json`:

```json
{
  "id": "f3c2f27f0f2547f7a2de8e8f06f7e4f1",
  "command": "mine minecraft:cobblestone 128",
  "status": "accepted",
  "accepted": true,
  "error": "",
  "createdAtMs": 1762250000000,
  "processedAtMs": 1762250000100,
  "trackActive": true,
  "bridgeVersion": "0.1.8"
}
```

## Status File

`status.json` includes:
- `baritoneLoaded`
- `isPathing`
- `currentGoal`
- `lastCommandId`
- `lastCommandResult`
- `lastCommandError`
- `queueDepth`
- `pendingResponseCount` (queued response retries if an outbox write fails)
- player vitals (`health`, `foodLevel`, `airSupply`, fire/lava/water flags)
- player binding/session details (`playerUuid`, `dimensionId`, `gameMode`, `worldTime`, `dayTime`)
- StoneBlock-specific summary (`modpackName`, `stoneblockStageHint`, `stoneblockKeyItemCounts`)
- hotbar telemetry and full inventory telemetry (`hotbar`, `inventory`, offhand fields)

## Build

```powershell
cd C:\Users\suret\stoneblock4-baritone-bridge-mod
.\gradlew.bat build
```

Output jar:
- `build\libs\sb4baritonebridge-<version>.jar`

One-step build + deploy (auto-detects JDK from `%USERPROFILE%\.gradle\jdks` if `JAVA_HOME` is unset):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_and_deploy.ps1
```

The deploy script stores previous bridge jars in `Instances\FTB StoneBlock 4\mod_backups` so stale backups are not loaded from `mods`.
It also reports `deployedVersion`, `liveBridgeVersion`, and `restartRequired` to detect when Minecraft must be restarted to load the new jar.

## Install

1. Copy built jar to your StoneBlock instance `mods` folder.
2. Ensure Baritone mod jar is also present and compatible with `1.21.1`.
3. Start client and join world.
4. Confirm `config/sb4_baritone_bridge/status.json` appears.
