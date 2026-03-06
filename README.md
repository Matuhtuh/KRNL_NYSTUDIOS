# StoneBlock 4 Autonomous Agent Workspace

This workspace now includes a **real local end-to-end control loop scaffold** between:

1. Python autonomous-agent brain (deterministic executor + hierarchical planning hooks)
2. NeoForge 1.21.1 client bridge mod (local HTTP bridge/body)

## Integration architecture (current)

- Python uses `HttpGameBridge` to call bridge endpoints over localhost HTTP JSON.
- Java bridge exposes `/heartbeat`, `/state`, `/inventory`, `/screen`, and `/action`.
- Python `BridgeAgentLoop` executes one deterministic action per step:
  1) read state
  2) safety/recovery checks
  3) create/load tiny plan
  4) execute one action
  5) verify result + update memory
  6) retry/fail tracking + research hook after repeated failure

## Why localhost HTTP first

HTTP on `127.0.0.1` is the simplest reliable transport for early debugging:
- inspectable with curl and logs
- language-agnostic across Python and Java
- low setup friction and easy failure diagnosis
- easy later migration to websocket streaming if needed

## Current shared contract

Python and Java now align on typed payloads for:
- `GameStateSnapshot`
- `InventorySnapshot`
- `NearbyBlockObservation`
- `NearbyEntityObservation`
- `OpenScreenState`
- `ActionRequest`
- `ActionResult`
- `ErrorResponse`
- `HeartbeatResponse`

## Action surface implemented in loop

Minimal deterministic action types are wired through schema and handler validation:
- `noop`
- `move_look` (placeholder)
- `interact_use` (placeholder)
- `inventory_click` (placeholder)
- `mine_block` (placeholder)
- `place_block` (placeholder)

These are honest placeholders for now: typed, routable, and testable, but not full gameplay automation.

## What is real vs placeholder

Real now:
- local Python↔Java transport contract and bridge calls
- deterministic action dispatch path with typed responses
- memory updates, failure persistence, stuck counting
- repeated-failure research trigger hook
- mock local mode for repeatable tests without Minecraft runtime

Placeholder still:
- full in-game movement/pathfinding
- full combat execution
- full mining/build automation wiring in NeoForge runtime
- external web research

## Inspiration adopted vs rejected

Adopted ideas:
- **Voyager-inspired looping**: plan -> execute -> self-verify -> retry/fallback hook
- **Baritone/AltoClef-inspired decomposition**: small deterministic actions and task steps
- **Mineflayer-style abstraction**: explicit action/state bridge contract and orchestration loop

Rejected or deferred (and why):
- direct reuse of those codebases (mismatch in runtime, APIs, and mod-loader constraints)
- LLM per-tick control (too nondeterministic for reliable StoneBlock 4 execution)
- premature full autonomy claims before deterministic low-level control is actually implemented

## Testing locally

- Python unit/integration tests:
  - `pytest -q`

## Next steps

1. Replace Java placeholders with real `Minecraft.getInstance()` state capture.
2. Add strict JSON serializers/deserializers shared by both sides.
3. Wire deterministic client input for movement/interact/mine/place actions.
4. Add action timeout enforcement against game ticks.
5. Expand planner/executor skills while preserving deterministic low-level control.
