## StoneBlock 4 Knowledge Layer

The local knowledge layer is built from the installed `FTB StoneBlock 4` instance, not from generic Minecraft assumptions.

Authoritative local sources:
- `config/ftbquests/quests/chapters/*.snbt`
- `config/ftbquests/quests/lang/en_us.snbt`
- `kubejs/server_scripts/recipes/mods/ftb/hammer.js`
- `kubejs/server_scripts/recipes/mods/ftb/crook.js`
- `kubejs/server_scripts/recipes/mods/ftb/wooden_basin.js`
- `kubejs/server_scripts/recipes/mods/ftb/custom_machinery/world_engine.js`
- `kubejs/server_scripts/recipes/mods/ftb/custom_machinery/world_engine_stage_checker.js`
- `kubejs/server_scripts/recipes/mods/ftb/custom_machinery/_machinery_structures.js`
- `kubejs/server_scripts/event_handlers/player/quest/worldengine_quests.js`

Repo-side overlays:
- `data/stoneblock4/stage_hints.json`
- `data/stoneblock4/chat_response_schema.json`

Generated runtime cache:
- `runtime/dashboard/stoneblock_knowledge.json`

What the cache contains:
- item acquisition and conversion routes
- route action classes such as `local_in_place` and `machine`
- source evidence paths for every route and gate
- World Engine autobuild quest links
- stage flags and upgrade gates
- structure requirements
- fluid and slot notes where the local scripts expose them
- stage-hint mappings used by the dashboard and chat

The web dashboard and chat API should treat this cache as a read-only explanation/index layer. It is not a planner rewrite.
