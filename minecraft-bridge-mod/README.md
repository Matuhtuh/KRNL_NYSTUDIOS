# StoneBlock 4 Client Bridge Mod (NeoForge, Minecraft 1.21.1)

This module is a **client-side NeoForge mod** that acts as the local game-body bridge for an external AI brain.

## Purpose

- Expose deterministic game-state snapshots from the Minecraft client.
- Accept bounded action requests from a local external process.
- Keep low-level input and interaction deterministic and auditable.
- Avoid unsafe, hidden, or fake "fully autonomous" logic inside the mod.

## Local communication choice

This project uses a **localhost HTTP server** (`127.0.0.1:8765`) as the first transport.

Why HTTP first:
1. Simple to debug with curl/Postman/logs.
2. Language-agnostic for external AI process implementations.
3. Reliable on localhost without extra broker/runtime dependencies.
4. Easy migration path to WebSocket later if streaming events are needed.

Current endpoints are skeletons:
- `GET /health`
- `GET /state`
- `POST /action`

They intentionally provide safe placeholder behavior rather than pretending gameplay integration is complete.

## Package structure

- `api/` protocol constants
- `bridge/` local bridge transport interfaces + localhost HTTP server
- `state/` state provider interface + placeholder provider
- `observe/` world observation interfaces/placeholders
- `screen/` current GUI/screen inspection interfaces/placeholders
- `control/` low-level player control interfaces/placeholders
- `action/` bounded action executor interfaces/placeholders
- `dto/` transport DTOs for state snapshots and action messages

## DTOs

- `PlayerStateDto`
- `InventorySnapshotDto`, `InventoryItemDto`
- `NearbyObservationDto`, `BlockObservationDto`, `EntityObservationDto`
- `OpenScreenStateDto`
- `ActionRequestDto`, `ActionResultDto`
- `BridgeStateSnapshotDto`

## What is intentionally not implemented yet

- Full pathfinding
- Full combat automation
- Full block placement/mining automation
- Full GUI automation/click plans
- Unsafe or hidden autonomous behaviors

## Integration path with external AI brain

1. External process polls `/state` for latest player/world/screen snapshot.
2. External deterministic executor/planner selects next bounded action.
3. External process sends `POST /action` with structured action payload.
4. Mod validates/dispatches to client control modules and returns `ActionResultDto`.
5. External process tracks failures/retries/recovery logic.

## Next implementation steps

1. Wire DTO serialization (JSON) and strict request parsing.
2. Implement state capture from `Minecraft.getInstance().player` and world APIs.
3. Implement deterministic movement/input wiring in `control/`.
4. Implement safe block interaction wrappers (raycast + reach + cooldown checks).
5. Add rate limiting + command guardrails on `/action`.
6. Add deterministic tick synchronization for action timeouts.
