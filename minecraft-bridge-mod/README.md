# StoneBlock 4 Client Bridge Mod (NeoForge, Minecraft 1.21.1)

Client-side bridge/body mod for an external Python AI brain.

## Endpoints

- `GET /heartbeat` → `HeartbeatResponse`
- `GET /state` → `GameStateSnapshot`
- `GET /inventory` → `InventorySnapshot`
- `GET /screen` → `OpenScreenState`
- `POST /action` → `ActionResult` or `ErrorResponse`

Bridge startup is now resilient: if port binding fails, the mod logs the failure, reports `failed_to_bind` status in heartbeat semantics, and continues client runtime without crashing.

## Real state capture now implemented

`ClientStateProvider` reads real client data when available:
- player position, yaw/pitch
- health + hunger
- dimension identifier
- main/offhand held items
- selected hotbar slot
- hotbar and full inventory snapshot from player inventory container
- current open screen class/title/slot count
- nearby entity sample with hostile heuristic
- nearby block sample in a bounded radius window

When player/level are unavailable, typed partial snapshots are returned with `partial` + `warnings` metadata.

## First real action surface

Implemented deterministic wrappers:
- `select_hotbar_slot` (real slot selection)
- `turn_to_yaw_pitch` (real local orientation set)
- `move_forward_short` (short key pulse wrapper)
- `interact_use` (use key pulse wrapper)

Typed partial placeholders (honest, not faked full automation):
- `inventory_click`
- `mine_block`
- `place_block`

## Observability

- bridge start/stop and action dispatch are logged
- action results include typed `preconditions` and `postconditions`
- malformed/unsupported requests return typed error responses

## Limits (intentional)

- no full pathfinding graph
- no full combat automation
- no deep per-mod GUI semantic parsing yet
- nearby observations are bounded samples, not complete world-state indexing

## Inspiration and compatibility notes

Conceptually inspired by Voyager (verify/retry loops), Baritone/AltoClef (deterministic task decomposition), Mineflayer/Mindcraft (clean action/state boundaries and orchestration caution). Direct code reuse was intentionally avoided due NeoForge + StoneBlock 4 architecture constraints.
