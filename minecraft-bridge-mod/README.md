# StoneBlock 4 Client Bridge Mod (NeoForge, Minecraft 1.21.1)

Client-side bridge/body mod for an external AI brain.

## Implemented bridge endpoints

- `GET /heartbeat` → `HeartbeatResponse`
- `GET /state` → `GameStateSnapshot`
- `GET /inventory` → `InventorySnapshot`
- `GET /screen` → `OpenScreenState`
- `POST /action` → `ActionResult` or `ErrorResponse`

All endpoints use localhost HTTP JSON (`127.0.0.1:8765`) for first-stage reliability and debuggability.

## Action types accepted

- `noop`
- `move_look`
- `interact_use`
- `inventory_click`
- `mine_block`
- `place_block`

Currently these are placeholder handlers: accepted and typed, but intentionally not wired to full Minecraft controls yet.

## What is real today

- typed transport contracts and endpoint handlers
- malformed action rejection (`400 bad_request` / `bad_json`)
- deterministic action dispatch surface for external executor integration

## What remains

- real state capture from Minecraft runtime objects
- real input wiring for movement/look/interact/mine/place
- strict shared JSON schema tooling across Python and Java

## Design notes and inspiration

- Uses Voyager-like loop boundaries (plan/execute/verify/fallback) at the system level.
- Uses Baritone/AltoClef-like deterministic small-step action surface.
- Uses Mineflayer-like split between state query and action API.

We intentionally do **not** copy those projects directly because NeoForge client internals, StoneBlock 4 modpack constraints, and this repository’s architecture differ significantly.
