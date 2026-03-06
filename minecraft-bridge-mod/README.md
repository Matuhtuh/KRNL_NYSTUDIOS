# StoneBlock 4 Client Bridge Mod (NeoForge, Minecraft 1.21.1)

Client-side bridge/body mod for an external Python AI brain.

## Endpoints

- `GET /heartbeat` → `HeartbeatResponse`
- `GET /state` → `GameStateSnapshot`
- `GET /inventory` → `InventorySnapshot`
- `GET /screen` → `OpenScreenState`
- `POST /action` → `ActionResult` or `ErrorResponse`

## Real state capture now implemented

`ClientStateProvider` now reads real client data when available:
- player position, yaw/pitch
- health + hunger
- dimension identifier
- main/offhand held items
- selected hotbar slot
- hotbar and full inventory snapshot from player inventory container
- current open screen class/title/slot count
- nearby entity sample with hostile heuristic
- nearby block sample in a small radius window

If player/level is unavailable, typed partial snapshots are returned with `partial/warnings` metadata.

## Action surface (still intentionally partial)

Accepted action types:
- `noop`
- `move_look`
- `interact_use`
- `inventory_click`
- `mine_block`
- `place_block`

Handlers validate and return typed results, but full movement/combat/pathfinding/control wiring is not complete yet.

## Limitations

- no full pathfinding graph
- no full combat automation
- no deep per-mod GUI semantic parsing yet
- nearby observations are bounded samples, not a complete world model

## Inspiration and compatibility notes

Conceptually inspired by Voyager (verify/retry loops), Baritone/AltoClef (deterministic task decomposition), and Mineflayer (clear state/action API boundaries). Direct code reuse was intentionally avoided due NeoForge + StoneBlock 4 compatibility constraints.
