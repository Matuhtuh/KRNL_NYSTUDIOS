# StoneBlock 4 Autonomous Agent Workspace

This repository contains a Python autonomous-agent brain and a NeoForge 1.21.1 client bridge mod for FTB StoneBlock 4.

## 1) Repository audit (current state)

### Already implemented
- Deterministic Python brain architecture (planner/executor/recovery/research/memory/state/bridge/builder modules).
- Typed Python↔Java localhost HTTP transport contracts and bridge client, including heartbeat bridge status reporting (`running`/`unavailable`/`failed_to_bind`).
- Real NeoForge client state capture for player, inventory/hotbar, open screen, nearby entities, and nearby block sampling.
- Deterministic action loop with safety checks, no-progress detection, failure tracking, and research fallback hook.
- Local mock bridge mode for repeatable integration tests.

### Still partial / placeholder
- Mine/place and inventory-click execution remain typed placeholders (honest, explicit partials).
- No full pathfinding graph or full combat automation yet.
- Modded GUI semantics are captured at screen class/title/slot-count level only.

### Biggest blockers to first in-game playability
1. Reliable tick-synchronized movement/action scheduling under varying client FPS/tick timing.
2. Robust block interaction/mine/place with raycast, reach, and server-confirmed state change.
3. Deeper modded-screen semantics for StoneBlock 4 machines and questing GUIs.
4. Deterministic navigation abstraction beyond short movement pulses.

## 2) Open-source research and design selection

Required inspirations reviewed: Voyager, Baritone, AltoClef, Mineflayer, Mindcraft, plus additional historical embodied-agent references.

Design note: `docs/open_source_design_note.md`

### Adopted
- Voyager-style plan → execute → verify → retry/escalate.
- AltoClef/Baritone-style small deterministic task/action decomposition.
- Mineflayer-style state/action API boundary and explicit contracts.

### Rejected/deferred
- Direct code port from Baritone/AltoClef (runtime/loader mismatch).
- Mineflayer runtime integration (Node bot runtime mismatch with NeoForge client bridge).
- Mindcraft-style runtime code-writing autonomy (safety/reliability mismatch).

## 3) Why architecture remains Python brain + NeoForge body

- Python brain handles planning/task logic/memory/recovery orchestration and future LLM planner integration.
- NeoForge body provides truthful client-state snapshots and deterministic low-level controls.
- LLM remains restricted to high-level planning/research/build design, never per-tick control.

## 4) What is now real for local in-game debugging

Real action surface:
- `select_hotbar_slot`
- `turn_to_yaw_pitch`
- `move_forward_short`
- `interact_use`

Real state capture:
- player pose/health/hunger/dimension/held items/hotbar selection
- full inventory + hotbar snapshot
- open screen metadata
- nearby entities and nearby blocks (bounded sample)
- typed partial-state and warnings when unavailable

## 5) What remains before full playability

- full pathfinding abstraction and robust goal-directed navigation
- robust mine/place interaction with confirmation loops
- full combat routines
- deeper StoneBlock 4 modded GUI/action understanding
- web-backed research (hook exists, web search not implemented)

## 6) Supervised operator console (local)

Use `operator_console.py` to supervise the Python brain during local debugging.

Supported commands:
- `goal <description>` / `set_goal <description>`
- `pause`
- `resume`
- `cancel`
- `status`
- `step` (console-only helper for one loop tick)

Status output includes current goal, current plan id, active action signature, bridge status, recent failures, and recent action logs.

## Testing

Run:
- `pytest -q`

The test suite covers schema validation, bridge serialization/error handling, bridge status heartbeat parsing, skill/task registry behavior, plan progression, repeated-failure escalation, no-progress detection, operator console command handling, and end-to-end mock action flow.
