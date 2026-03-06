# StoneBlock 4 Autonomous Agent Workspace

This repository contains a Python autonomous-agent brain and a NeoForge 1.21.1 client bridge mod for FTB StoneBlock 4.

## Audit of existing foundation (before this change)

Already present before this update:
- layered Python architecture (`planner`, `executor`, `recovery`, `research`, `memory`, `state`, `bridge`, `builder`)
- typed Python↔Java contracts and local HTTP bridge integration
- deterministic action loop scaffolding with retries/failure history/research hook
- NeoForge client-side bridge with typed endpoints and placeholder action handling
- local mock bridge test mode

This change extends that scaffold with real client state capture and stricter runtime safety/progress checks.

## Transport choice

The project continues to use localhost HTTP JSON (`127.0.0.1:8765`) because it is the simplest reliable debug transport for mixed Python/Java development and Codex CLI live troubleshooting.

## What real state is now captured (NeoForge side)

`ClientStateProvider` now pulls live client values (when player/level are available):
- player position (`x/y/z`)
- yaw/pitch
- health
- hunger
- dimension id
- held main/offhand items
- selected hotbar slot
- hotbar snapshot (first 9 slots)
- full inventory snapshot via player inventory container size
- open screen type/title/slot count
- nearby entities within radius (+hostile heuristic)
- nearby blocks in configurable small sample volume

All of the above are exposed through `/state`, with `/inventory` and `/screen` still available separately.

## What is still partial or placeholder

- action execution remains intentionally partial (`noop`, `move_look`, `interact_use`, `inventory_click`, `mine_block`, `place_block` are validated but not fully wired to gameplay input yet)
- nearby block/entity observation is bounded sampling (not a full world-model or pathfinding graph)
- modded GUI semantics are captured as screen class/title/slot count but not yet deeply parsed per-mod screen logic
- no full pathfinding, full combat, or web research automation yet

## Python integration improvements

- bridge client now validates and normalizes richer snapshots
- memory persists recent snapshots for progress/stuck analysis
- deterministic safety checks now block unsafe actions for:
  - low health
  - low hunger
  - missing main-hand tool for tool-requiring actions
  - open screen blocking gameplay actions
- no-progress detector compares consecutive snapshots across:
  - position delta
  - inventory delta
  - open-screen state delta
- executor now reports precondition/postcondition outcomes and checks likely state change after action dispatch

## Open-source inspiration (adopted vs rejected)

Adopted conceptually:
- **Voyager**: iterative plan → execute → verify → retry flow and skill/research hooks
- **Baritone / AltoClef**: deterministic small-task decomposition and strict low-level control boundaries
- **Mineflayer ecosystem**: practical state/action API abstractions and orchestration patterns

Rejected for direct reuse:
- direct code copy from those projects due runtime mismatch (NeoForge client internals, modpack constraints, and this repository’s strict typed contract structure)
- LLM per-tick control, which remains disallowed for deterministic StoneBlock 4 execution reliability

## Testing

Run:
- `pytest -q`

The Python tests cover snapshot validation, bridge serialization behavior, malformed payload handling, safety checks, no-progress detection, and end-to-end loop execution in mock mode.
