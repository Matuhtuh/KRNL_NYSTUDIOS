# StoneBlock AI Bot - 100% Completion Task List

This is the end-to-end completion plan across:
- `C:\Users\suret\stoneblock4-baritone-agent` (Python AI runtime)
- `C:\Users\suret\stoneblock4-baritone-bridge-mod` (NeoForge bridge jar)

Status legend:
- `[x]` done
- `[ ]` pending
- `[~]` in progress / needs verification in live game

## 1. Bridge Jar Stability (Mod)
- `[x]` Add bridge command `bridge.store_to_nearby_chest` with args `[radius] [max_stacks] [include_hotbar]`.
- `[x]` Process inbox in deterministic arrival order (mtime + name), not random filename order.
- `[x]` Use atomic JSON writes (temp file + replace) for `status.json` and command responses.
- `[x]` Add pending response retry queue so transient outbox write failures do not drop command acks.
- `[x]` Add duplicate command-id replay protection to avoid double-execution side effects.
- `[x]` Keep bridge response schema backward compatible (`status`, `accepted`, `error`, `bridgeVersion`).
- `[x]` Add stronger session telemetry: `playerUuid`, `dimensionId`, `gameMode`, `worldTime`, `dayTime`.
- `[x]` Add modpack telemetry: `modpackName`, `stoneblockStageHint`, `stoneblockKeyItemCounts`, `inventoryFreeSlots`.
- `[x]` Bump bridge version to `0.1.8`.
- `[x]` Build succeeds on current tree (`gradlew build`).
- `[ ]` Live verify `bridge.store_to_nearby_chest` against actual chest/barrel/shulker in StoneBlock world. (live-only)
- `[x]` Add command id dedupe cache to avoid double-execution on ack-write edge cases.
- `[x]` Harden command-id handling against sanitize/case collisions (raw-id dedupe + hashed canonical response filenames).
- `[x]` Harden `bridge.swap_to_hotbar` (close container first and verify post-click inventory change).
- `[x]` Add Baritone reflection-handle retry for runtime class/proxy mismatch (`IllegalArgumentException` path).
- `[x]` Add rate-limited warning metrics (command failures by reason) in status.
- `[x]` Add explicit `statusSchemaVersion` field for future compatibility.

## 2. Python Agent Runtime Hardening
- `[x]` Make optional storage-chest placement truly optional (no hard fail on missing chest ingredients).
- `[x]` Add adaptive cooldown scaling for repeatedly failing optional item acquisition.
- `[x]` Reduce retries for optional maintenance tasks to avoid command spam.
- `[x]` Dashboard reads and displays new bridge player/modpack fields.
- `[x]` Add bridge-version capability checks for `store_to_nearby_chest` before calling it.
- `[x]` Persist optional-failure cooldown state to disk so restarts do not reset learned backoff.
- `[x]` Align craft-template capability gating with bridge status checks and add safe fallback templates when bridge craft support is unknown.
- `[x]` Make optional cooldown/failure state loading tolerant to malformed map entries.
- `[~]` Add per-item max optional retry budget with timed decay. (planner-side budget/decay logic landed; runner-side timestamp wiring still partial)
- `[x]` Add run-mode switch for strict progression vs idle grind vs recovery-only.
- `[x]` Add Ultimine integration for mining/tunnel/build commands with bridge capability fallback.
- `[~]` Add command id correlation in logs (planner task -> bridge command id mapping). (correlation events exist; full planner-task context plumbing still partial)

## 3. StoneBlock 4 Modpack-Specific Intelligence
- `[~]` Add explicit progression state machine for StoneBlock 4 early game. (state graph + telemetry inference landed; needs live tuning)
- `[~]` `bootstrap_crafting`
- `[~]` `bootstrap_hammer`
- `[~]` `hammer_to_resources`
- `[~]` `pre_sieving`
- `[~]` `early_smelting`
- `[~]` `machine_bootstrap`
- `[~]` `automation_midgame`
- `[~]` Encode canonical acquisition routes for gravel/sand/dirt/dust that work on this exact server pack. (knowledge model added; live recipe confirmation still needed)
- `[~]` Add inventory target bands for key resources (min/max stock goals). (bands encoded in knowledge model; runtime enforcement is partial)
- `[~]` Add task templates that prioritize recipe-verified paths over speculative crafting. (fallback/gating logic improved; recipe-verification coverage incomplete)
- `[~]` Add world-engine-specific machine placement/build macros for your current base layout. (bootstrap modules exist; base-layout specialization still pending)
- `[~]` Add modpack-specific dangerous-action denylist (commands/items to never drop/store automatically). (denylist stub exists; enforcement still pending)

## 4. Safety and Human-Control Guarantees
- `[x]` Add hard stop condition when bridge says dead/respawn screen.
- `[~]` Add "human present nearby" policy mode (slow/avoid/stop) with configurable radius. (player-proximity safety exists; explicit policy modes still pending)
- `[~]` Add protected-zone map support (never tunnel/mine/build inside protected coordinates). (base-radius protection exists; map-based zones still pending)
- `[x]` Add max continuous runtime watchdog with periodic safe idle checkpoints.
- `[~]` Add panic recovery macro: stop, eat, retreat, relight, resume. (partial safety recovery exists; explicit full macro still pending)
- `[ ]` Add explicit anti-grief allowlist for place/break actions.

## 5. Deployment and Ops
- `[x]` Build jar `sb4baritonebridge-0.1.8.jar`.
- `[~]` Deploy `0.1.8` jar into StoneBlock instance `mods`. (live-only)
- `[~]` Remove/retire old bridge jars from `mods` (only one active jar). (live-only)
- `[~]` Confirm bridge status appears under `config/sb4_baritone_bridge/status.json`. (live-only)
- `[x]` Confirm Python config points to same bridge dir and transport is `bridge_file`.
- `[x]` Deploy script reports live/deployed bridge version mismatch and restart requirement.
- `[x]` Add one-command runtime audit script for bridge jar/version/readiness drift (`runtime/audit_bridge_runtime.ps1`).
- `[~]` Freeze exact known-good versions (Minecraft/NeoForge/Baritone/bridge/agent deps) in one release note. (`runtime/KNOWN_GOOD_RELEASE.md` created; final values pending live lock)

## 6. End-to-End Validation Matrix (Must Pass)
- `[~]` Bridge readiness: `inWorld=true`, `baritoneLoaded=true`, status freshness stable (< stale threshold). (live-only)
- `[~]` Command ack: 100 command burst test, no stuck pending/timeout, no malformed response JSON. (live-only)
- `[ ]` Pathing: `mine`, `goto`, `tunnel` all acknowledged and path-state transitions detected.
- `[ ]` Inventory ops: swap, drop, store-to-chest all succeed and telemetry reflects changes.
- `[ ]` Crafting ops: success case and "missing ingredients" case return correct rejection reasons.
- `[ ]` Combat ops: `bridge.fight_hostile` engages and exits without desync.
- `[ ]` Idle loop: no repeated high-frequency optional retry spam after repeated failures.
- `[ ]` Recovery loop: bridge disconnect/reconnect recovers without process restart.
- `[ ]` Safety gates: low health/food/lava/player proximity pause behavior works as configured.
- `[ ]` 2+ hour soak test with no crash, no safe-pause deadlock, and stable status writes.

## 7. Project Completion Gates
- `[ ]` All "End-to-End Validation Matrix" items pass on your live StoneBlock instance.
- `[ ]` No known P0/P1 issues left open.
- `[ ]` Runtime can recover from transient failures without manual file edits.
- `[ ]` Task list here is fully checked.
- `[ ]` Tag release: bridge `v0.1.8+` and matching agent config snapshot.

## 8. Suggested Execution Order
- `[~]` Deploy bridge `0.1.8`. (live-only)
- `[~]` Run bridge-only validation commands. (live-only)
- `[~]` Run `simulate` sanity check. (rerun recommended after recent checklist-related code updates)
- `[ ]` Run `arm-run` supervised for 15 minutes.
- `[ ]` Run 2-hour supervised soak test.
- `[ ]` Close remaining checklist items and cut release snapshot.

