from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.pack_parser import (
    _extract_array_after_key,
    _extract_object_after_key,
    _find_matching_block,
    _split_top_level_objects,
    build_pack_model,
)


_HAMMER_SOURCE = ("kubejs", "server_scripts", "recipes", "mods", "ftb", "hammer.js")
_CROOK_SOURCE = ("kubejs", "server_scripts", "recipes", "mods", "ftb", "crook.js")
_WOODEN_BASIN_SOURCE = ("kubejs", "server_scripts", "recipes", "mods", "ftb", "wooden_basin.js")
_WORLD_ENGINE_SOURCE = (
    "kubejs",
    "server_scripts",
    "recipes",
    "mods",
    "ftb",
    "custom_machinery",
    "world_engine.js",
)
_WORLD_ENGINE_STAGE_CHECKER_SOURCE = (
    "kubejs",
    "server_scripts",
    "recipes",
    "mods",
    "ftb",
    "custom_machinery",
    "world_engine_stage_checker.js",
)
_WORLD_ENGINE_STRUCTURES_SOURCE = (
    "kubejs",
    "server_scripts",
    "recipes",
    "mods",
    "ftb",
    "custom_machinery",
    "_machinery_structures.js",
)
_WORLD_ENGINE_QUEST_SOURCE = (
    "kubejs",
    "server_scripts",
    "event_handlers",
    "player",
    "quest",
    "worldengine_quests.js",
)
_QUEST_LANG_SOURCE = ("config", "ftbquests", "quests", "lang", "en_us.snbt")
_QUEST_CHAPTERS_SOURCE = ("config", "ftbquests", "quests", "chapters")

_CANONICAL_TAG_INPUTS = {
    "#c:cobblestones": "minecraft:cobblestone",
    "#c:gravels": "minecraft:gravel",
    "#minecraft:dirt": "minecraft:dirt",
    "#c:sandstone/uncolored_blocks": "minecraft:sandstone",
    "#c:sandstone/red_blocks": "minecraft:red_sandstone",
    "#minecraft:leaves": "minecraft:leaves",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_stage_hints_path() -> Path:
    return _repo_root() / "data" / "stoneblock4" / "stage_hints.json"


def default_chat_schema_path() -> Path:
    return _repo_root() / "data" / "stoneblock4" / "chat_response_schema.json"


def default_knowledge_output_path() -> Path:
    return _repo_root() / "runtime" / "dashboard" / "stoneblock_knowledge.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object at {path}")
    return raw


def _extract_js_string_field(text: str, key: str) -> str | None:
    m = re.search(rf"\b{re.escape(key)}\s*:\s*(?P<q>['\"])(?P<value>.*?)(?P=q)", text, flags=re.DOTALL)
    if not m:
        return None
    value = str(m.group("value")).strip()
    return value or None


def _extract_js_int_field(text: str, key: str) -> int | None:
    m = re.search(rf"\b{re.escape(key)}\s*:\s*([-]?[0-9]+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_js_bool_field(text: str, key: str) -> bool | None:
    m = re.search(rf"\b{re.escape(key)}\s*:\s*(true|false)", text, flags=re.IGNORECASE)
    if not m:
        return None
    return str(m.group(1)).strip().lower() == "true"


def _extract_js_string_list(text: str, key: str) -> list[str]:
    arr = _extract_array_after_key(text, f"{key}:")
    if not arr:
        return []
    return [match[1] for match in re.findall(r"(['\"])(.*?)\1", arr, flags=re.DOTALL)]


def _split_top_level_arrays(array_text: str) -> list[str]:
    if not array_text.startswith("[") or not array_text.endswith("]"):
        return []
    body = array_text[1:-1]
    arrays: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    arr_start = -1
    quote_char = ""
    for i, ch in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_char:
                in_string = False
            continue

        if ch in {'"', "'"}:
            in_string = True
            quote_char = ch
            continue

        if ch == "[":
            if depth == 0:
                arr_start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and arr_start >= 0:
                arrays.append(body[arr_start : i + 1])
                arr_start = -1
    return arrays


def _canonical_input_token(item_id: str) -> str:
    canonical = _CANONICAL_TAG_INPUTS.get(item_id.strip(), item_id.strip().lstrip("#"))
    return _slug(canonical)


def _slug(value: str) -> str:
    token = str(value).strip().lower()
    token = token.replace("#", "")
    token = re.sub(r"[^a-z0-9]+", "_", token)
    token = token.strip("_")
    return token or "unknown"


def _item_label(item_id: str) -> str:
    raw = str(item_id).strip()
    if not raw:
        return ""
    token = raw.split(":")[-1]
    token = re.sub(r"\[[^\]]+\]", "", token)
    token = token.replace("_", " ").replace("/", " ").strip()
    return token.title() if token else raw


def _route_evidence(path: Path, label: str | None = None) -> list[dict[str, str]]:
    return [{"path": str(path), "label": label or path.name}]


def _parse_io_entries(obj_text: str, key: str) -> list[dict[str, Any]]:
    arr = _extract_array_after_key(obj_text, f"{key}:")
    if not arr:
        return []
    out: list[dict[str, Any]] = []
    for entry in _split_top_level_objects(arr):
        item_id = _extract_js_string_field(entry, "item") or _extract_js_string_field(entry, "tag")
        fluid_id = _extract_js_string_field(entry, "fluid") or _extract_js_string_field(entry, "id")
        payload: dict[str, Any] = {}
        if item_id:
            payload["itemId"] = item_id
        if fluid_id and not item_id:
            payload["fluidId"] = fluid_id
        count = _extract_js_int_field(entry, "count")
        if count is not None:
            payload["count"] = count
        amount = _extract_js_int_field(entry, "amount")
        if amount is not None:
            payload["amount"] = amount
        slot = _extract_js_string_field(entry, "slot")
        if slot:
            payload["slot"] = slot
        tank = _extract_js_string_field(entry, "tank")
        if tank:
            payload["tank"] = tank
        nbt = _extract_js_string_field(entry, "nbt")
        if nbt:
            payload["nbt"] = nbt
        if payload:
            out.append(payload)
    return out


def _parse_hammer_routes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    routes: list[dict[str, Any]] = []
    for input_id, output_id, amount_raw in re.findall(
        r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*([0-9]+)\s*\]',
        text,
    ):
        amount = int(amount_raw)
        route_id = f"hammer__{_canonical_input_token(input_id)}__{_slug(output_id)}"
        notes: list[str] = []
        if input_id.startswith("#"):
            notes.append(f"input uses tag {input_id}")
        routes.append(
            {
                "routeId": route_id,
                "mechanism": "hammer",
                "routeType": "item_acquisition",
                "actionClass": "local_in_place",
                "displayName": f"{_item_label(_CANONICAL_TAG_INPUTS.get(input_id, input_id))} to {_item_label(output_id)}",
                "inputs": [{"itemId": input_id, "count": 1}],
                "outputs": [{"itemId": output_id, "count": amount}],
                "requirements": {"upgradeKeys": [], "stageFlags": [], "structures": []},
                "notes": notes,
                "evidence": _route_evidence(path),
            }
        )
    return routes


def _parse_crook_routes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = text.find("const crookRecipes")
    if marker < 0:
        return []
    arr_start = text.find("[", marker)
    if arr_start < 0:
        return []
    s, e = _find_matching_block(text, arr_start, "[", "]")
    arr = text[s : e + 1]
    routes: list[dict[str, Any]] = []
    for obj in _split_top_level_objects(arr):
        input_id = _extract_js_string_field(obj, "input")
        if not input_id:
            continue
        tagged = bool(_extract_js_bool_field(obj, "tagged"))
        max_uses = _extract_js_int_field(obj, "max") or 1
        outputs_arr = _extract_array_after_key(obj, "outputs:")
        output_arrays = _split_top_level_arrays(outputs_arr or "[]")
        for output_arr in output_arrays:
            match = re.search(
                r"\[\s*(?P<q>['\"])(?P<id>.*?)(?P=q)\s*,\s*(?P<count>[0-9]+)\s*,\s*(?P<chance>[0-9.]+)\s*\]",
                output_arr,
                flags=re.DOTALL,
            )
            if not match:
                continue
            output_id = str(match.group("id")).strip()
            count = int(match.group("count"))
            chance = float(match.group("chance"))
            route_id = f"crook__{_canonical_input_token(input_id)}__{_slug(output_id)}"
            notes = [f"crook max uses={max_uses}", f"output chance={chance:.2f}"]
            if tagged:
                notes.append(f"input uses tag {input_id}")
            routes.append(
                {
                    "routeId": route_id,
                    "mechanism": "crook",
                    "routeType": "item_acquisition",
                    "actionClass": "local_in_place",
                    "displayName": f"{_item_label(_CANONICAL_TAG_INPUTS.get(input_id, input_id))} crook to {_item_label(output_id)}",
                    "inputs": [{"itemId": input_id, "count": 1}],
                    "outputs": [{"itemId": output_id, "count": count, "chance": chance}],
                    "requirements": {"upgradeKeys": [], "stageFlags": [], "structures": []},
                    "notes": notes,
                    "evidence": _route_evidence(path),
                }
            )
    return routes


def _parse_wooden_basin_routes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    fluid_id = _extract_js_string_field(text, "id") or "minecraft:water"
    amount = _extract_js_int_field(text, "amount") or 1000
    input_id = _extract_js_string_field(text, "input") or "#minecraft:leaves"
    route_id = f"wooden_basin__{_canonical_input_token(input_id)}__{_slug(fluid_id)}"
    return [
        {
            "routeId": route_id,
            "mechanism": "wooden_basin",
            "routeType": "fluid_generation",
            "actionClass": "local_stationary",
            "displayName": f"{_item_label(_CANONICAL_TAG_INPUTS.get(input_id, input_id))} to {_item_label(fluid_id)}",
            "inputs": [{"itemId": input_id, "count": 1}],
            "outputs": [{"fluidId": fluid_id, "amount": amount}],
            "requirements": {"upgradeKeys": [], "stageFlags": [], "structures": []},
            "notes": [f"requires {amount} mB output fluid", "block_consume_chance and recipe chance are defined in wooden_basin.js"],
            "evidence": _route_evidence(path),
        }
    ]


def _parse_world_engine_stage_flags(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = text.find("const WE_STAGES")
    if marker < 0:
        return {}
    brace_start = text.find("{", marker)
    if brace_start < 0:
        return {}
    s, e = _find_matching_block(text, brace_start, "{", "}")
    block = text[s : e + 1]
    mapping: dict[str, str] = {}
    for key, stage in re.findall(r"([a-z0-9_]+)\s*:\s*\"([^\"]+)\"", block, flags=re.IGNORECASE):
        mapping[str(key)] = str(stage)
    return mapping


def _parse_world_engine_structures(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    structures: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"^[ ]{2}([a-z0-9_]+)\s*:\s*\{", text, flags=re.MULTILINE | re.IGNORECASE):
        key = str(match.group(1))
        brace_start = text.find("{", match.start())
        if brace_start < 0:
            continue
        s, e = _find_matching_block(text, brace_start, "{", "}")
        block = text[s : e + 1]
        keys_obj = _extract_object_after_key(block, "keys:")
        key_map: dict[str, str] = {}
        if keys_obj:
            for groups in re.findall(r"([a-z])\s*:\s*(?:\"([^\"]+)\"|'([^']+)')", keys_obj, flags=re.IGNORECASE):
                letter = str(groups[0])
                block_id = str(groups[1] or groups[2])
                key_map[letter] = block_id
        pattern_block = _extract_array_after_key(block, "pattern:")
        row_strings = [match[1] for match in re.findall(r"(['\"])(.*?)\1", pattern_block or "", flags=re.DOTALL)]
        floor_blocks = _split_top_level_arrays(pattern_block or "[]")
        floor_count = len(floor_blocks) if floor_blocks else (1 if row_strings else 0)
        row_count = 0
        col_count = 0
        if floor_blocks:
            for floor in floor_blocks:
                floor_rows = [match[1] for match in re.findall(r"(['\"])(.*?)\1", floor, flags=re.DOTALL)]
                row_count = max(row_count, len(floor_rows))
                for row in floor_rows:
                    col_count = max(col_count, len(row))
        else:
            row_count = len(row_strings)
            for row in row_strings:
                col_count = max(col_count, len(row))
        structures[key] = {
            "upgradeKey": key,
            "keys": key_map,
            "dimensions": {"floors": floor_count, "rows": row_count, "cols": col_count},
            "patternPreview": row_strings[: min(12, len(row_strings))],
            "evidence": _route_evidence(path),
        }
    return structures


def _extract_add_world_engine_array(text: str) -> str | None:
    marker = text.find("addWorldEngineRecipes(event, [")
    if marker < 0:
        return None
    arr_start = text.find("[", marker)
    if arr_start < 0:
        return None
    s, e = _find_matching_block(text, arr_start, "[", "]")
    return text[s : e + 1]


def _parse_world_engine_routes(
    path: Path,
    *,
    autobuild_quests: dict[str, str],
    stage_flags: dict[str, str],
    structures: dict[str, dict[str, Any]],
    quest_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    arr = _extract_add_world_engine_array(text)
    if not arr:
        return []
    routes: list[dict[str, Any]] = []
    for obj in _split_top_level_objects(arr):
        recipe_id = _extract_js_string_field(obj, "id")
        if not recipe_id:
            continue
        machine_id = _extract_js_string_field(obj, "machineId") or "ftb:world_engine"
        route_id = f"world_engine__{_slug(recipe_id)}"
        structures_required = _extract_js_string_list(obj, "structures")
        item_inputs = _parse_io_entries(obj, "itemInputs")
        item_outputs = _parse_io_entries(obj, "itemOutputs")
        fluid_inputs = _parse_io_entries(obj, "fluidInputs")
        fluid_outputs = _parse_io_entries(obj, "fluidOutputs")
        duration = _extract_js_int_field(obj, "duration")
        energy_per_tick = _extract_js_int_field(obj, "energyPerTick")
        source_amount = None
        source_block = _extract_object_after_key(obj, "source:")
        if source_block:
            source_amount = _extract_js_int_field(source_block, "amount")
        temp_block = _extract_object_after_key(obj, "tempC:")
        temp_min = _extract_js_int_field(temp_block or "", "min")
        temp_max = _extract_js_int_field(temp_block or "", "max")
        requirements = {
            "upgradeKeys": structures_required,
            "stageFlags": [stage_flags[key] for key in structures_required if key in stage_flags],
            "structures": structures_required,
        }
        notes: list[str] = [f"machineId={machine_id}"]
        if duration is not None:
            notes.append(f"duration={duration} ticks")
        if energy_per_tick is not None:
            notes.append(f"energyPerTick={energy_per_tick}")
        if source_amount is not None:
            notes.append(f"source={source_amount}")
        if temp_min is not None:
            notes.append(f"tempC.min={temp_min}")
        if temp_max is not None:
            notes.append(f"tempC.max={temp_max}")
        for fluid in fluid_inputs:
            if fluid.get("fluidId"):
                tank = str(fluid.get("tank", "")).strip()
                note = f"fluid input {fluid['fluidId']} {int(fluid.get('amount', 0))}mB"
                if tank:
                    note = f"{note} via {tank}"
                notes.append(note)
        for fluid in fluid_outputs:
            if fluid.get("fluidId"):
                tank = str(fluid.get("tank", "")).strip()
                note = f"fluid output {fluid['fluidId']} {int(fluid.get('amount', 0))}mB"
                if tank:
                    note = f"{note} via {tank}"
                notes.append(note)
        evidence = _route_evidence(path)
        for key in structures_required:
            if key in stage_flags:
                evidence.append(
                    {
                        "path": str(path.parent / "world_engine_stage_checker.js"),
                        "label": "world_engine_stage_checker.js",
                    }
                )
            if key in structures:
                evidence.append(
                    {
                        "path": str(path.parent / "_machinery_structures.js"),
                        "label": "_machinery_structures.js",
                    }
                )
            quest_id = autobuild_quests.get(key, "")
            if quest_id and quest_id in quest_index:
                quest = quest_index[quest_id]
                notes.append(f"autobuild quest={quest_id} {quest.get('title', '').strip()}")
        routes.append(
            {
                "routeId": route_id,
                "mechanism": "world_engine",
                "routeType": "machine_recipe",
                "actionClass": "machine",
                "displayName": recipe_id,
                "machineId": machine_id,
                "inputs": item_inputs,
                "outputs": item_outputs,
                "fluidInputs": fluid_inputs,
                "fluidOutputs": fluid_outputs,
                "requirements": requirements,
                "notes": notes,
                "evidence": evidence,
            }
        )
    return routes


def _build_item_index(routes: list[dict[str, Any]], quest_index: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    item_index: dict[str, dict[str, Any]] = {}

    def ensure_item(item_id: str) -> dict[str, Any]:
        token = str(item_id).strip()
        if token not in item_index:
            item_index[token] = {
                "itemId": token,
                "label": _item_label(token),
                "questRefs": [],
                "producedBy": [],
                "consumedBy": [],
            }
        return item_index[token]

    for route in routes:
        route_id = str(route.get("routeId", "")).strip()
        for payload in route.get("inputs", []):
            item_id = str(payload.get("itemId", "")).strip()
            if not item_id or item_id.startswith("#"):
                continue
            ensure_item(item_id)["consumedBy"].append(route_id)
        for payload in route.get("outputs", []):
            item_id = str(payload.get("itemId", "")).strip()
            if not item_id:
                continue
            ensure_item(item_id)["producedBy"].append(route_id)

    for quest_id, quest in quest_index.items():
        refs: list[str] = []
        for task in quest.get("tasks", []):
            item_id = str(task.get("item_id", "")).strip()
            if item_id:
                refs.append(item_id)
        for reward in quest.get("rewards", []):
            item_id = str(reward.get("item_id", "")).strip()
            if item_id:
                refs.append(item_id)
        for item_id in refs:
            item = ensure_item(item_id)
            item["questRefs"].append(
                {
                    "questId": quest_id,
                    "title": str(quest.get("title", "")),
                    "chapterFile": str(quest.get("chapter_file", "")),
                }
            )

    for item in item_index.values():
        item["producedBy"] = sorted(set(item["producedBy"]))
        item["consumedBy"] = sorted(set(item["consumedBy"]))
        seen_quests: set[str] = set()
        deduped_refs: list[dict[str, Any]] = []
        for ref in item["questRefs"]:
            quest_id = str(ref.get("questId", "")).strip()
            if not quest_id or quest_id in seen_quests:
                continue
            seen_quests.add(quest_id)
            deduped_refs.append(ref)
        item["questRefs"] = deduped_refs[:8]
    return item_index


def _load_stage_hints(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    return {str(key): value for key, value in raw.items()}


def build_stoneblock_knowledge(
    instance_path: str | Path,
    *,
    pack_model_path: str | Path,
    out_path: str | Path | None = None,
    stage_hints_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(instance_path).expanduser().resolve()
    out = Path(out_path).expanduser().resolve() if out_path else default_knowledge_output_path()
    stage_hints_file = Path(stage_hints_path).expanduser().resolve() if stage_hints_path else default_stage_hints_path()
    pack_model_file = Path(pack_model_path).expanduser().resolve()
    pack_model = build_pack_model(root, pack_model_file)
    quest_index = dict(pack_model.get("quest_index", {}))
    autobuild_quests = dict(pack_model.get("worldengine_autobuild_quests", {}))

    hammer_path = root.joinpath(*_HAMMER_SOURCE)
    crook_path = root.joinpath(*_CROOK_SOURCE)
    basin_path = root.joinpath(*_WOODEN_BASIN_SOURCE)
    world_engine_path = root.joinpath(*_WORLD_ENGINE_SOURCE)
    world_engine_stage_checker_path = root.joinpath(*_WORLD_ENGINE_STAGE_CHECKER_SOURCE)
    world_engine_structures_path = root.joinpath(*_WORLD_ENGINE_STRUCTURES_SOURCE)
    world_engine_quest_path = root.joinpath(*_WORLD_ENGINE_QUEST_SOURCE)

    stage_flags = _parse_world_engine_stage_flags(world_engine_stage_checker_path)
    structures = _parse_world_engine_structures(world_engine_structures_path)
    routes: list[dict[str, Any]] = []
    routes.extend(_parse_hammer_routes(hammer_path))
    routes.extend(_parse_crook_routes(crook_path))
    routes.extend(_parse_wooden_basin_routes(basin_path))
    routes.extend(
        _parse_world_engine_routes(
            world_engine_path,
            autobuild_quests=autobuild_quests,
            stage_flags=stage_flags,
            structures=structures,
            quest_index=quest_index,
        )
    )
    routes.sort(key=lambda row: str(row.get("routeId", "")))

    items = _build_item_index(routes, quest_index)
    stage_hints = _load_stage_hints(stage_hints_file)
    routes_by_output: dict[str, list[str]] = {}
    for route in routes:
        route_id = str(route.get("routeId", "")).strip()
        for payload in route.get("outputs", []):
            item_id = str(payload.get("itemId", "")).strip()
            if not item_id:
                continue
            routes_by_output.setdefault(item_id, []).append(route_id)

    machine_requirements: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        if str(route.get("mechanism", "")).strip() != "world_engine":
            continue
        requirement_payload = {
            "routeId": route.get("routeId", ""),
            "displayName": route.get("displayName", ""),
            "requirements": route.get("requirements", {}),
            "notes": route.get("notes", []),
            "evidence": route.get("evidence", []),
        }
        for payload in route.get("outputs", []):
            item_id = str(payload.get("itemId", "")).strip()
            if not item_id:
                continue
            machine_requirements.setdefault(item_id, []).append(requirement_payload)

    world_engine_upgrade_gates: dict[str, dict[str, Any]] = {}
    for upgrade_key, quest_id in autobuild_quests.items():
        quest = quest_index.get(str(quest_id), {})
        world_engine_upgrade_gates[str(upgrade_key)] = {
            "upgradeKey": str(upgrade_key),
            "stageFlag": stage_flags.get(str(upgrade_key), ""),
            "autobuildQuestId": str(quest_id),
            "autobuildQuestTitle": str(quest.get("title", "")),
            "autobuildQuestChapter": str(quest.get("chapter_file", "")),
            "structure": structures.get(str(upgrade_key), {}),
            "evidence": [
                {"path": str(pack_model_file), "label": "pack_model.json"},
                {"path": str(world_engine_stage_checker_path), "label": "world_engine_stage_checker.js"},
                {"path": str(world_engine_structures_path), "label": "_machinery_structures.js"},
                {"path": str(world_engine_quest_path), "label": "worldengine_quests.js"},
            ],
        }

    payload = {
        "generatedAt": _iso_now(),
        "instancePath": str(root),
        "packModelPath": str(pack_model_file),
        "stageHintsPath": str(stage_hints_file),
        "sourcesUsed": [
            str(root.joinpath(*_QUEST_CHAPTERS_SOURCE)),
            str(root.joinpath(*_QUEST_LANG_SOURCE)),
            str(hammer_path),
            str(crook_path),
            str(basin_path),
            str(world_engine_path),
            str(world_engine_stage_checker_path),
            str(world_engine_structures_path),
            str(world_engine_quest_path),
            str(stage_hints_file),
        ],
        "items": items,
        "routes": routes,
        "routesByOutput": {key: sorted(value) for key, value in routes_by_output.items()},
        "machineRequirements": machine_requirements,
        "worldEngine": {
            "autobuildQuests": autobuild_quests,
            "stageFlags": stage_flags,
            "structures": structures,
            "upgradeGates": world_engine_upgrade_gates,
        },
        "stageHints": stage_hints,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def ensure_stoneblock_knowledge(cfg: AppConfig, *, out_path: str | Path | None = None) -> Path:
    if not str(cfg.planner.instance_path).strip():
        raise ValueError("planner.instance_path is required to build StoneBlock knowledge")
    output_path = Path(out_path).expanduser().resolve() if out_path else default_knowledge_output_path()
    instance_root = Path(cfg.planner.instance_path).expanduser().resolve()
    inputs = [
        instance_root.joinpath(*_HAMMER_SOURCE),
        instance_root.joinpath(*_CROOK_SOURCE),
        instance_root.joinpath(*_WOODEN_BASIN_SOURCE),
        instance_root.joinpath(*_WORLD_ENGINE_SOURCE),
        instance_root.joinpath(*_WORLD_ENGINE_STAGE_CHECKER_SOURCE),
        instance_root.joinpath(*_WORLD_ENGINE_STRUCTURES_SOURCE),
        instance_root.joinpath(*_WORLD_ENGINE_QUEST_SOURCE),
        Path(cfg.planner.pack_model_file).expanduser().resolve(),
        default_stage_hints_path(),
    ]
    rebuild = not output_path.exists()
    if not rebuild:
        try:
            out_mtime = output_path.stat().st_mtime
            rebuild = any(path.exists() and path.stat().st_mtime > out_mtime for path in inputs)
        except OSError:
            rebuild = True
    if rebuild:
        build_stoneblock_knowledge(
            cfg.planner.instance_path,
            pack_model_path=cfg.planner.pack_model_file,
            out_path=output_path,
            stage_hints_path=default_stage_hints_path(),
        )
    return output_path


class StoneBlockKnowledgeBase:
    def __init__(self, cfg: AppConfig, *, out_path: str | Path | None = None) -> None:
        self.cfg = cfg
        self.path = ensure_stoneblock_knowledge(cfg, out_path=out_path)

    def reload(self) -> dict[str, Any]:
        self.path = ensure_stoneblock_knowledge(self.cfg, out_path=self.path)
        return _read_json(self.path)

    def data(self) -> dict[str, Any]:
        return _read_json(self.path)

    def get_stage_knowledge(self, stage_hint: str, bridge_status: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self.data()
        stage_key = str(stage_hint or "").strip()
        stage_hints = dict(data.get("stageHints", {}))
        stage = dict(stage_hints.get(stage_key, {}))
        if not stage and bridge_status:
            observed = str((bridge_status or {}).get("stoneblockStageHint", "")).strip()
            stage_key = observed or stage_key
            stage = dict(stage_hints.get(stage_key, {}))
        route_ids = [str(route_id) for route_id in stage.get("routeIds", []) if str(route_id).strip()]
        route_index = {str(route.get("routeId", "")): route for route in data.get("routes", [])}
        related_routes = [route_index[route_id] for route_id in route_ids if route_id in route_index]
        return {
            "stageHint": stage_key,
            "label": str(stage.get("label", stage_key or "unknown")),
            "summary": str(stage.get("summary", "")),
            "focusItems": list(stage.get("focusItems", [])),
            "targetBands": dict(stage.get("targetBands", {})),
            "nextStageHint": stage.get("nextStageHint"),
            "routes": related_routes,
            "evidence": list(stage.get("evidence", [])),
        }

    def get_item_route(self, item_id: str) -> dict[str, Any]:
        data = self.data()
        token = str(item_id).strip().lower()
        routes = []
        for route in data.get("routes", []):
            for output in route.get("outputs", []):
                if str(output.get("itemId", "")).strip().lower() == token:
                    routes.append(route)
                    break
        return {
            "itemId": token,
            "item": dict(data.get("items", {}).get(token, {})),
            "routes": routes,
            "machineRequirements": list(data.get("machineRequirements", {}).get(token, [])),
        }

    def get_next_obtainable_targets(self, bridge_status: dict[str, Any] | None = None, *, limit: int = 6) -> dict[str, Any]:
        data = self.data()
        status = bridge_status or {}
        stage_hint = str(status.get("stoneblockStageHint", "")).strip()
        current_stage = self.get_stage_knowledge(stage_hint, bridge_status=status)
        item_counts = dict(status.get("stoneblockKeyItemCounts", {}))
        routes_by_output = dict(data.get("routesByOutput", {}))
        route_index = {str(route.get("routeId", "")): route for route in data.get("routes", [])}
        suggestions: list[dict[str, Any]] = []
        for item_id in current_stage.get("focusItems", []):
            band = dict(current_stage.get("targetBands", {}).get(item_id, {}))
            min_count = int(band.get("min", 0) or 0)
            observed = int(item_counts.get(item_id, 0) or 0)
            if observed >= min_count > 0:
                continue
            route_ids = list(routes_by_output.get(item_id, []))
            supporting_routes = [route_index[route_id] for route_id in route_ids if route_id in route_index][:3]
            suggestions.append(
                {
                    "itemId": item_id,
                    "observedCount": observed,
                    "targetMin": min_count,
                    "routes": supporting_routes,
                }
            )
            if len(suggestions) >= max(1, int(limit)):
                break
        return {
            "stageHint": current_stage.get("stageHint", stage_hint),
            "targets": suggestions,
            "evidence": current_stage.get("evidence", []),
        }

    def search_knowledge(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        data = self.data()
        raw_query = str(query or "").strip().lower()
        tokens = [token for token in re.split(r"[^a-z0-9:_./-]+", raw_query) if token]
        if not tokens:
            return {"query": raw_query, "matches": []}

        matches: list[dict[str, Any]] = []

        for route in data.get("routes", []):
            haystack = " ".join(
                [
                    str(route.get("routeId", "")),
                    str(route.get("displayName", "")),
                    str(route.get("mechanism", "")),
                    " ".join(str(note) for note in route.get("notes", [])),
                    " ".join(str(payload.get("itemId", "")) for payload in route.get("inputs", [])),
                    " ".join(str(payload.get("itemId", "")) for payload in route.get("outputs", [])),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score <= 0:
                continue
            matches.append(
                {
                    "kind": "route",
                    "score": score,
                    "routeId": str(route.get("routeId", "")).strip(),
                    "displayName": str(route.get("displayName", "")).strip(),
                    "mechanism": str(route.get("mechanism", "")).strip(),
                    "actionClass": str(route.get("actionClass", "")).strip(),
                    "requirements": dict(route.get("requirements", {})),
                    "notes": list(route.get("notes", []))[:4],
                    "evidence": list(route.get("evidence", [])),
                }
            )

        for stage_hint, stage in dict(data.get("stageHints", {})).items():
            haystack = " ".join(
                [
                    str(stage_hint),
                    str(stage.get("label", "")),
                    str(stage.get("summary", "")),
                    " ".join(str(item_id) for item_id in stage.get("focusItems", [])),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score <= 0:
                continue
            matches.append(
                {
                    "kind": "stage",
                    "score": score,
                    "stageHint": str(stage_hint),
                    "label": str(stage.get("label", "")).strip(),
                    "summary": str(stage.get("summary", "")).strip(),
                    "focusItems": list(stage.get("focusItems", []))[:6],
                    "evidence": list(stage.get("evidence", [])),
                }
            )

        for item_id, requirements in dict(data.get("machineRequirements", {})).items():
            haystack = " ".join(
                [
                    str(item_id),
                    " ".join(str(req.get("displayName", "")) for req in requirements),
                    " ".join(str(req.get("routeId", "")) for req in requirements),
                    " ".join(str(req.get("upgradeKey", "")) for req in requirements),
                    " ".join(str(note) for req in requirements for note in req.get("notes", [])),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score <= 0:
                continue
            matches.append(
                {
                    "kind": "machine_requirement",
                    "score": score,
                    "itemId": str(item_id).strip(),
                    "requirements": list(requirements)[:4],
                }
            )

        matches.sort(key=lambda row: (-int(row.get("score", 0) or 0), str(row.get("kind", "")), str(row.get("routeId", row.get("itemId", row.get("stageHint", ""))))))
        return {
            "query": raw_query,
            "matches": matches[: max(1, int(limit))],
        }
