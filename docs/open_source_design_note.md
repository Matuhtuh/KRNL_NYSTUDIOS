# Open-Source Design Note for StoneBlock 4 Agent (NeoForge + Python Brain)

This project researched and compared the following open-source systems before implementation updates:

- Voyager (LLM-driven embodied loop)
- Baritone (deterministic pathing/control architecture)
- AltoClef (task decomposition over Baritone)
- Mineflayer (state/action API abstraction patterns)
- Mindcraft (LLM + Mineflayer orchestration and safety caveats)
- Additional historical references: CraftAssist-like embodied agent framing

## Adopted ideas

### Navigation / movement control
- Adopted Baritone/AltoClef-style **small deterministic movement primitives** rather than monolithic autonomy.
- Implemented a first actionable surface around:
  - `select_hotbar_slot`
  - `turn_to_yaw_pitch`
  - `move_forward_short`
  - `interact_use`

### Task / goal decomposition
- Adopted Voyager/AltoClef concept of **ordered subtasks + explicit action progression**.
- Improved plan cursor progression and explicit plan completion in the Python loop.

### State/action abstractions
- Adopted Mineflayer-style clear contracts: snapshot request, action request, action result.
- Expanded typed transport with preconditions/postconditions and structured partial-state warnings.

### Retry / verification / stuck handling
- Adopted Voyager-like verify-then-escalate loop:
  - post-action verification
  - failure classification
  - repeated-failure counters
  - no-progress detector
  - research hook trigger (without web search implementation yet)

## Rejected or deferred ideas

- **Direct code reuse from Baritone/AltoClef**: incompatible with current NeoForge 1.21.1 bridge layering and this repo’s Python<->HTTP architecture.
- **Direct Mineflayer runtime integration**: wrong runtime (Node bot stack) vs client-mod bridge architecture.
- **Mindcraft-style code-writing agents in runtime loop**: rejected for safety/reliability; this project keeps deterministic low-level execution and uses LLM only for planning/research/build design.
- **Full pathfinding/combat now**: deferred to preserve correctness and observability for StoneBlock 4 modded-state debugging first.

## Why this fits StoneBlock 4 better than generic vanilla assumptions

- StoneBlock 4 requires modded GUI and progression awareness; therefore state truthfulness and deterministic action auditability are prioritized over broad autonomous claims.
- The NeoForge bridge captures real client state and reports typed partials when data is unavailable, avoiding fake state assumptions.
- The Python brain can now reason over real snapshots while preserving deterministic control boundaries.
