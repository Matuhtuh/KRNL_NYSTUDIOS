from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HEX_ID_RE = re.compile(r'"([0-9A-F]{16})"')
TITLE_RE = re.compile(r'quest\.([0-9A-F]{16})\.title:\s*"(.*)"')
CHAPTER_TITLE_RE = re.compile(r'chapter\.([0-9A-F]{16})\.title:\s*"(.*)"')


@dataclass
class ParsedQuest:
    quest_id: str
    dependencies: list[str]
    optional: bool
    task_types: list[str]
    reward_types: list[str]
    tasks: list[dict[str, Any]]
    rewards: list[dict[str, Any]]


def _find_matching_block(text: str, start_index: int, open_ch: str, close_ch: str) -> tuple[int, int]:
    depth = 0
    in_string = False
    escaped = False
    for i in range(start_index, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return start_index, i
    raise ValueError("unmatched block")


def _find_key_index(text: str, key: str) -> int:
    token = key.rstrip(":")
    if not token:
        return -1
    in_string = False
    escaped = False
    n = len(text)
    tlen = len(token)
    i = 0
    while i < n:
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if i + tlen + 1 <= n and text[i : i + tlen] == token and text[i + tlen : i + tlen + 1] == ":":
            prev = text[i - 1] if i > 0 else " "
            if not (prev.isalnum() or prev == "_"):
                return i
        i += 1
    return -1


def _extract_array_after_key(text: str, key: str) -> str | None:
    key_index = _find_key_index(text, key)
    if key_index < 0:
        return None
    colon_index = text.find(":", key_index)
    if colon_index < 0:
        return None
    arr_start = text.find("[", colon_index)
    if arr_start < 0:
        return None
    s, e = _find_matching_block(text, arr_start, "[", "]")
    return text[s : e + 1]


def _extract_object_after_key(text: str, key: str) -> str | None:
    key_index = _find_key_index(text, key)
    if key_index < 0:
        return None
    colon_index = text.find(":", key_index)
    if colon_index < 0:
        return None
    obj_start = text.find("{", colon_index)
    if obj_start < 0:
        return None
    s, e = _find_matching_block(text, obj_start, "{", "}")
    return text[s : e + 1]


def _split_top_level_objects(array_text: str) -> list[str]:
    if not array_text.startswith("[") or not array_text.endswith("]"):
        return []
    body = array_text[1:-1]
    objs: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    obj_start = -1
    for i, ch in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                objs.append(body[obj_start : i + 1])
                obj_start = -1
    return objs


def _parse_item_spec(block_text: str) -> tuple[str, int]:
    item_id = ""
    count = 1
    id_matches = re.findall(r'\bid:\s*"([^"]+)"', block_text)
    if id_matches:
        item_id = id_matches[-1]
    count_match = re.search(r"\bcount:\s*([0-9]+)", block_text)
    if count_match:
        count = int(count_match.group(1))
    return item_id, count


def _extract_string_field(text: str, key: str) -> str | None:
    m = re.search(rf"\b{re.escape(key)}:\s*\"([^\"]+)\"", text)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def _extract_int_field(text: str, key: str) -> int | None:
    m = re.search(rf"\b{re.escape(key)}:\s*([-]?[0-9]+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_task_object(task_text: str) -> dict[str, Any]:
    task_type_match = re.search(r'\btype:\s*"([^"]+)"', task_text)
    task_type = task_type_match.group(1) if task_type_match else ""
    task_id_match = re.search(r'\bid:\s*"([0-9A-F]{16})"', task_text)
    optional_task = bool(re.search(r"\boptional_task:\s*true", task_text))

    out: dict[str, Any] = {
        "id": task_id_match.group(1) if task_id_match else "",
        "type": task_type,
        "optional_task": optional_task,
    }

    item_block = _extract_object_after_key(task_text, "item:")
    if item_block:
        item_id, count = _parse_item_spec(item_block)
        if item_id:
            out["item_id"] = item_id
            out["count"] = count

    position_block = _extract_array_after_key(task_text, "position:")
    if position_block:
        coords = [int(v) for v in re.findall(r"[-]?[0-9]+", position_block)]
        if len(coords) >= 3:
            out["position"] = coords[:3]

    size_block = _extract_array_after_key(task_text, "size:")
    if size_block:
        dims = [int(v) for v in re.findall(r"[-]?[0-9]+", size_block)]
        if len(dims) >= 3:
            out["size"] = dims[:3]

    stage_match = re.search(r'\bstage:\s*"([^"]+)"', task_text)
    if stage_match:
        out["stage"] = stage_match.group(1)

    dimension_match = re.search(r'\bdimension:\s*"([^"]+)"', task_text)
    if dimension_match:
        out["dimension"] = dimension_match.group(1)

    if task_type == "kill":
        entity = (
            _extract_string_field(task_text, "entity")
            or _extract_string_field(task_text, "entity_id")
            or _extract_string_field(task_text, "target")
            or _extract_string_field(task_text, "mob")
        )
        if entity:
            out["entity"] = entity
        count = (
            _extract_int_field(task_text, "count")
            or _extract_int_field(task_text, "amount")
            or _extract_int_field(task_text, "value")
        )
        if count is not None:
            out["count"] = count

    elif task_type == "structure":
        structure = (
            _extract_string_field(task_text, "structure")
            or _extract_string_field(task_text, "structure_id")
            or _extract_string_field(task_text, "template")
            or _extract_string_field(task_text, "name")
        )
        if structure:
            out["structure"] = structure

    elif task_type == "observation":
        target = (
            _extract_string_field(task_text, "to_observe")
            or _extract_string_field(task_text, "observe")
            or _extract_string_field(task_text, "observation")
            or _extract_string_field(task_text, "target")
        )
        if target:
            out["target"] = target
        amount = _extract_int_field(task_text, "value") or _extract_int_field(task_text, "count")
        if amount is not None:
            out["count"] = amount

    elif task_type == "stat":
        stat_name = _extract_string_field(task_text, "stat")
        if stat_name:
            out["stat"] = stat_name
        stat_key = _extract_string_field(task_text, "key")
        if stat_key:
            out["key"] = stat_key
        value = _extract_int_field(task_text, "value") or _extract_int_field(task_text, "count")
        if value is not None:
            out["value"] = value

    elif task_type == "biome":
        biome = _extract_string_field(task_text, "biome")
        if biome:
            out["biome"] = biome

    elif task_type == "fluid":
        fluid_block = _extract_object_after_key(task_text, "fluid:")
        if fluid_block:
            fluid_id = (
                _extract_string_field(fluid_block, "id")
                or _extract_string_field(fluid_block, "fluid")
                or _extract_string_field(fluid_block, "name")
            )
            if fluid_id:
                out["fluid_id"] = fluid_id
            amount = (
                _extract_int_field(fluid_block, "amount")
                or _extract_int_field(fluid_block, "count")
                or _extract_int_field(fluid_block, "value")
            )
            if amount is not None:
                out["amount"] = amount
        else:
            fluid_id = _extract_string_field(task_text, "fluid")
            if fluid_id:
                out["fluid_id"] = fluid_id
            amount = (
                _extract_int_field(task_text, "amount")
                or _extract_int_field(task_text, "count")
                or _extract_int_field(task_text, "value")
            )
            if amount is not None:
                out["amount"] = amount

    elif task_type == "advancement":
        advancement = _extract_string_field(task_text, "advancement")
        if advancement:
            out["advancement"] = advancement
        criterion = _extract_string_field(task_text, "criterion")
        if criterion:
            out["criterion"] = criterion

    elif task_type == "checkmark":
        auto_claim = re.search(r"\bauto_claim:\s*true", task_text) is not None
        out["auto_claim"] = auto_claim
        title = _extract_string_field(task_text, "title")
        if title:
            out["title"] = title

    return out


def _parse_reward_object(reward_text: str) -> dict[str, Any]:
    reward_type_match = re.search(r'\btype:\s*"([^"]+)"', reward_text)
    reward_type = reward_type_match.group(1) if reward_type_match else ""
    reward_id_match = re.search(r'\bid:\s*"([0-9A-F]{16})"', reward_text)

    out: dict[str, Any] = {
        "id": reward_id_match.group(1) if reward_id_match else "",
        "type": reward_type,
    }

    amount_match = re.search(r"\bamount:\s*([0-9]+)", reward_text)
    if amount_match:
        out["amount"] = int(amount_match.group(1))

    item_block = _extract_object_after_key(reward_text, "item:")
    if item_block:
        item_id, count = _parse_item_spec(item_block)
        if item_id:
            out["item_id"] = item_id
            out["count"] = count

    command_match = re.search(r'\bcommand:\s*"([^"]+)"', reward_text)
    if command_match:
        out["command"] = command_match.group(1)

    stage_match = re.search(r'\bstage:\s*"([^"]+)"', reward_text)
    if stage_match:
        out["stage"] = stage_match.group(1)

    return out


def _parse_quest_object(obj_text: str) -> ParsedQuest | None:
    id_match = re.search(r'\bid:\s*"([0-9A-F]{16})"', obj_text)
    if not id_match:
        return None
    quest_id = id_match.group(1)

    dep_ids: list[str] = []
    dep_match = re.search(r"\bdependencies:\s*\[(.*?)\]", obj_text, flags=re.DOTALL)
    if dep_match:
        dep_ids = HEX_ID_RE.findall(dep_match.group(1))

    task_types: list[str] = []
    task_details: list[dict[str, Any]] = []
    tasks_text = _extract_array_after_key(obj_text, "tasks:")
    for t in _split_top_level_objects(tasks_text or "[]"):
        parsed_task = _parse_task_object(t)
        if parsed_task.get("type"):
            task_types.append(str(parsed_task["type"]))
        task_details.append(parsed_task)

    reward_types: list[str] = []
    reward_details: list[dict[str, Any]] = []
    rewards_match = re.search(r"\brewards:\s*\[(.*?)\]", obj_text, flags=re.DOTALL)
    if rewards_match:
        rewards_text = _extract_array_after_key(obj_text, "rewards:")
        for r in _split_top_level_objects(rewards_text or "[]"):
            parsed_reward = _parse_reward_object(r)
            if parsed_reward.get("type"):
                reward_types.append(str(parsed_reward["type"]))
            reward_details.append(parsed_reward)

    return ParsedQuest(
        quest_id=quest_id,
        dependencies=dep_ids,
        optional=bool(re.search(r"\boptional:\s*true", obj_text)),
        task_types=task_types,
        reward_types=reward_types,
        tasks=task_details,
        rewards=reward_details,
    )


def _parse_lang_titles(lang_file: Path) -> tuple[dict[str, str], dict[str, str]]:
    quest_titles: dict[str, str] = {}
    chapter_titles: dict[str, str] = {}
    if not lang_file.exists():
        return quest_titles, chapter_titles

    text = lang_file.read_text(encoding="utf-8", errors="replace")
    for m in TITLE_RE.finditer(text):
        quest_titles[m.group(1)] = m.group(2)
    for m in CHAPTER_TITLE_RE.finditer(text):
        chapter_titles[m.group(1)] = m.group(2)
    return quest_titles, chapter_titles


def _parse_worldengine_map(world_engine_js: Path) -> dict[str, str]:
    if not world_engine_js.exists():
        return {}
    text = world_engine_js.read_text(encoding="utf-8", errors="replace")
    idx = text.find("const WORLDENGINE_AUTOBUILD_QUESTS")
    if idx < 0:
        return {}
    brace_start = text.find("{", idx)
    if brace_start < 0:
        return {}
    _, brace_end = _find_matching_block(text, brace_start, "{", "}")
    blob = text[brace_start + 1 : brace_end]

    mapping: dict[str, str] = {}
    for line in blob.splitlines():
        m = re.search(r"([a-z0-9_]+)\s*:\s*\"([0-9A-F]{16})\"", line, flags=re.IGNORECASE)
        if m:
            mapping[m.group(1)] = m.group(2)
    return mapping


def build_pack_model(instance_path: str | Path, out_file: str | Path) -> dict[str, Any]:
    root = Path(instance_path).expanduser().resolve()
    chapters_dir = root / "config" / "ftbquests" / "quests" / "chapters"
    lang_file = root / "config" / "ftbquests" / "quests" / "lang" / "en_us.snbt"
    world_engine_js = (
        root
        / "kubejs"
        / "server_scripts"
        / "recipes"
        / "mods"
        / "ftb"
        / "custom_machinery"
        / "world_engine.js"
    )

    quest_titles, chapter_titles = _parse_lang_titles(lang_file)
    worldengine_map = _parse_worldengine_map(world_engine_js)

    chapters: list[dict[str, Any]] = []
    for chapter_file in sorted(chapters_dir.glob("*.snbt")):
        text = chapter_file.read_text(encoding="utf-8", errors="replace")
        chapter_id_match = re.search(r'\bid:\s*"([0-9A-F]{16})"', text)
        chapter_group_match = re.search(r'\bgroup:\s*"([0-9A-F]{16})"', text)

        chapter_id = chapter_id_match.group(1) if chapter_id_match else ""
        quests_arr = _extract_array_after_key(text, "quests:")
        quest_objs = _split_top_level_objects(quests_arr or "[]")
        parsed_quests: list[dict[str, Any]] = []
        for obj in quest_objs:
            parsed = _parse_quest_object(obj)
            if not parsed:
                continue
            parsed_quests.append(
                {
                    "quest_id": parsed.quest_id,
                    "title": quest_titles.get(parsed.quest_id, ""),
                    "dependencies": parsed.dependencies,
                    "optional": parsed.optional,
                    "task_types": parsed.task_types,
                    "reward_types": parsed.reward_types,
                    "tasks": parsed.tasks,
                    "rewards": parsed.rewards,
                }
            )

        chapters.append(
            {
                "file": chapter_file.name,
                "chapter_id": chapter_id,
                "chapter_title": chapter_titles.get(chapter_id, ""),
                "group_id": chapter_group_match.group(1) if chapter_group_match else "",
                "quest_count": len(parsed_quests),
                "quests": parsed_quests,
            }
        )

    quest_index: dict[str, dict[str, Any]] = {}
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id", ""))
        chapter_file = str(chapter.get("file", ""))
        chapter_title = str(chapter.get("chapter_title", ""))
        for quest in chapter.get("quests", []):
            quest_id = str(quest.get("quest_id", "")).strip()
            if not quest_id:
                continue
            quest_index[quest_id] = {
                "quest_id": quest_id,
                "title": quest.get("title", ""),
                "chapter_id": chapter_id,
                "chapter_file": chapter_file,
                "chapter_title": chapter_title,
                "dependencies": list(quest.get("dependencies", [])),
                "optional": bool(quest.get("optional", False)),
                "task_types": list(quest.get("task_types", [])),
                "reward_types": list(quest.get("reward_types", [])),
                "tasks": list(quest.get("tasks", [])),
                "rewards": list(quest.get("rewards", [])),
            }

    out = {
        "instance_path": str(root),
        "chapters_count": len(chapters),
        "quests_count": sum(ch["quest_count"] for ch in chapters),
        "chapters": chapters,
        "quest_index": quest_index,
        "worldengine_autobuild_quests": worldengine_map,
    }

    out_path = Path(out_file).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
