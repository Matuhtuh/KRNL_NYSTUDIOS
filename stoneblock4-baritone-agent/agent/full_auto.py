from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.knowledge import Goal

SUPPORTED_QUEST_TASK_TYPES = {"item", "location"}
STONEBLOCK_EARLY_PROGRESS_STATES: tuple[str, ...] = (
    "bootstrap_crafting",
    "bootstrap_hammer",
    "hammer_to_resources",
    "pre_sieving",
    "early_smelting",
    "machine_bootstrap",
    "automation_midgame",
)
STONEBLOCK_EARLY_PROGRESS_TRANSITIONS: dict[str, str | None] = {
    "bootstrap_crafting": "bootstrap_hammer",
    "bootstrap_hammer": "hammer_to_resources",
    "hammer_to_resources": "pre_sieving",
    "pre_sieving": "early_smelting",
    "early_smelting": "machine_bootstrap",
    "machine_bootstrap": "automation_midgame",
    "automation_midgame": None,
}
STONEBLOCK_EARLY_PROGRESS_INDEX = {
    state: idx for idx, state in enumerate(STONEBLOCK_EARLY_PROGRESS_STATES)
}


def _quest_flag(quest_id: str) -> str:
    return f"quest_done_{quest_id.lower()}"


def _goal_id(quest_id: str) -> str:
    return f"quest_{quest_id.lower()}"


def _include_chapter(chapter: dict[str, Any], include_files: set[str]) -> bool:
    if not include_files:
        file_name = str(chapter.get("file", "")).strip().lower()
        chapter_title = str(chapter.get("chapter_title", "")).strip().lower()
        if "bounty" in file_name or "bounty" in chapter_title:
            return False
        return True
    file_name = str(chapter.get("file", "")).strip().lower()
    chapter_title = str(chapter.get("chapter_title", "")).strip().lower()
    if file_name in include_files:
        return True
    return any(token in chapter_title for token in include_files)


def _looks_like_direct_resource_item(item_id: str) -> bool:
    token = str(item_id).strip().lower()
    if not token:
        return False
    if ":" in token:
        namespace, path = token.split(":", 1)
    else:
        namespace, path = "minecraft", token

    if namespace != "minecraft":
        return path.endswith("_ore") or path.startswith("ore_") or "_ore_" in path

    if any(
        marker in path
        for marker in (
            "ingot",
            "nugget",
            "dust",
            "alloy",
            "plate",
            "gear",
            "rod",
            "wire",
            "connector",
            "circuit",
            "machine",
            "frame",
            "component",
            "seed",
            "redstone",
            "charcoal",
            "coal",
            "lapis",
            "diamond",
            "emerald",
            "quartz",
        )
    ):
        return False
    return any(
        marker in path
        for marker in (
            "ore",
            "stone",
            "cobble",
            "deepslate",
            "gravel",
            "sand",
            "dirt",
            "clay",
            "log",
            "wood",
            "planks",
            "basalt",
            "netherrack",
            "obsidian",
            "coal",
            "slate",
            "andesite",
            "granite",
            "diorite",
        )
    )


def _looks_like_craft_or_resource_item(item_id: str) -> bool:
    token = str(item_id).strip().lower()
    if not token:
        return False
    if _looks_like_direct_resource_item(token):
        return True
    craft_markers = (
        "ingot",
        "nugget",
        "dust",
        "alloy",
        "plate",
        "gear",
        "rod",
        "wire",
        "connector",
        "circuit",
        "machine",
        "frame",
        "component",
        "furnace",
        "crafting_table",
        "hammer",
        "pickaxe",
        "drill",
    )
    return any(marker in token for marker in craft_markers)


def _task_to_runtime_tasks(task: dict[str, Any]) -> list[dict[str, Any]]:
    task_type = str(task.get("type", "")).strip().lower()
    if task_type == "item":
        item_id = str(task.get("item_id", "")).strip()
        if not item_id:
            return []
        optional = bool(task.get("optional_task", False))
        return [
            {
                "type": "quest_item_acquire",
                "item": item_id,
                "count": int(task.get("count", 1)),
                "optional": optional,
            }
        ]

    if task_type == "location":
        pos = task.get("position")
        if isinstance(pos, list) and len(pos) >= 3:
            x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
            return [
                {"type": "baritone_command", "command": f"goto {x} {y} {z}"},
                {"type": "wait_baritone_idle", "timeout_seconds": 120.0},
            ]
        return []

    if task_type == "gamestage":
        stage = str(task.get("stage", "")).strip()
        return [{"type": "note", "text": f"Unsupported quest task type 'gamestage' ({stage or '<unknown>'})"}]

    return [{"type": "note", "text": f"Unsupported quest task type '{task_type}'"}]


def _quest_unsupported_task_buckets(quest: dict[str, Any]) -> tuple[list[str], list[str]]:
    unsupported_required: list[str] = []
    unsupported_optional: list[str] = []
    for task in quest.get("tasks", []):
        task_type = str(task.get("type", "")).strip().lower()
        if task_type not in SUPPORTED_QUEST_TASK_TYPES:
            bucket = unsupported_optional if bool(task.get("optional_task", False)) else unsupported_required
            if task_type not in bucket:
                bucket.append(task_type)
    return unsupported_required, unsupported_optional


def _quest_has_actionable_required_supported_task(quest: dict[str, Any]) -> bool:
    for task in quest.get("tasks", []):
        if bool(task.get("optional_task", False)):
            continue
        task_type = str(task.get("type", "")).strip().lower()
        if task_type == "item":
            item_id = str(task.get("item_id", "")).strip()
            if item_id:
                return True
    return False


def _quest_required_items_progression_friendly(quest: dict[str, Any]) -> bool:
    for task in quest.get("tasks", []):
        if bool(task.get("optional_task", False)):
            continue
        if str(task.get("type", "")).strip().lower() != "item":
            continue
        item_id = str(task.get("item_id", "")).strip()
        if not _looks_like_craft_or_resource_item(item_id):
            return False
    return True


def _load_stoneblock_progress_telemetry(
    model_path: Path,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(telemetry, dict):
        return dict(telemetry)

    runtime_live_status = model_path.parent / "live_status.json"
    if not runtime_live_status.exists():
        return {}
    try:
        raw = json.loads(runtime_live_status.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    if "stoneblockStageHint" in raw or "stoneblockKeyItemCounts" in raw:
        return raw

    bridge_payload = raw.get("bridge")
    if isinstance(bridge_payload, dict):
        status = bridge_payload.get("status")
        if isinstance(status, dict):
            return status
        return bridge_payload
    return raw


def _stoneblock_stage_hint_state(stage_hint: str) -> str | None:
    hint = str(stage_hint).strip().lower()
    if not hint:
        return None
    normalized = hint.replace("-", "_").replace(" ", "_")
    if normalized in STONEBLOCK_EARLY_PROGRESS_INDEX:
        return normalized

    hinted_states: tuple[tuple[str, str], ...] = (
        ("automation_midgame", "automation_midgame"),
        ("midgame", "automation_midgame"),
        ("world_engine__tier_2", "automation_midgame"),
        ("tier_2", "automation_midgame"),
        ("machine_bootstrap", "machine_bootstrap"),
        ("machine", "machine_bootstrap"),
        ("mekanism", "machine_bootstrap"),
        ("oritech", "machine_bootstrap"),
        ("processing", "machine_bootstrap"),
        ("technology", "machine_bootstrap"),
        ("early_smelting", "early_smelting"),
        ("smelting", "early_smelting"),
        ("furnace", "early_smelting"),
        ("pre_sieving", "pre_sieving"),
        ("sieving", "pre_sieving"),
        ("sieve", "pre_sieving"),
        ("mesh", "pre_sieving"),
        ("hammer_to_resources", "hammer_to_resources"),
        ("resources", "hammer_to_resources"),
        ("gravel", "hammer_to_resources"),
        ("sand", "hammer_to_resources"),
        ("dirt", "hammer_to_resources"),
        ("bootstrap_hammer", "bootstrap_hammer"),
        ("bootstrap_crafting", "bootstrap_crafting"),
        ("getting_started", "bootstrap_crafting"),
        ("crafting", "bootstrap_crafting"),
    )
    for token, state in hinted_states:
        if token in hint or token in normalized:
            return state
    return None


def _stoneblock_item_count_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for item_id, value in raw.items():
        token = str(item_id).strip().lower()
        if not token:
            continue
        count = _coerce_non_negative_int(value)
        out[token] = max(out.get(token, 0), count)
        if ":" in token:
            _, path = token.split(":", 1)
            if path:
                out[path] = max(out.get(path, 0), count)
    return out


def _stoneblock_progress_state_from_telemetry(telemetry: dict[str, Any]) -> tuple[str, bool]:
    if not telemetry:
        return "bootstrap_crafting", False

    stage_hint_state = _stoneblock_stage_hint_state(str(telemetry.get("stoneblockStageHint", "")))
    key_counts = _stoneblock_item_count_map(telemetry.get("stoneblockKeyItemCounts", {}))
    telemetry_known = bool(stage_hint_state) or bool(key_counts)
    if not telemetry_known:
        return "bootstrap_crafting", False

    def _count_any(*item_ids: str) -> int:
        return sum(key_counts.get(str(item_id).strip().lower(), 0) for item_id in item_ids)

    has_crafting_table = _count_any("minecraft:crafting_table", "crafting_table") > 0
    has_hammer = _count_any(
        "ftbstuff:stone_hammer",
        "stone_hammer",
        "ftbstuff:iron_hammer",
        "iron_hammer",
    ) > 0
    gravel_count = _count_any("minecraft:gravel", "gravel")
    sand_count = _count_any("minecraft:sand", "sand")
    dirt_count = _count_any("minecraft:dirt", "dirt")
    has_pre_sieving_resources = (
        (gravel_count > 0 and sand_count > 0 and dirt_count > 0)
        or (gravel_count + sand_count + dirt_count) >= 24
    )
    has_sieving_items = any(
        count > 0 and any(marker in item_id for marker in ("sieve", "mesh", "dust"))
        for item_id, count in key_counts.items()
    )
    has_early_smelting_items = _count_any(
        "minecraft:furnace",
        "furnace",
        "minecraft:iron_ingot",
        "iron_ingot",
        "minecraft:coal",
        "coal",
        "minecraft:charcoal",
        "charcoal",
    ) > 0
    has_machine_items = any(
        count > 0
        and any(
            marker in item_id
            for marker in ("machine", "infuser", "crusher", "generator", "frame", "factory", "conveyor")
        )
        for item_id, count in key_counts.items()
    )
    has_midgame_items = any(
        count > 0
        and any(
            marker in item_id
            for marker in (
                "automation",
                "ultimate",
                "creative",
                "reactor",
                "quantum",
                "singularity",
                "nether_star",
            )
        )
        for item_id, count in key_counts.items()
    )

    observed_index = STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_crafting"]
    if stage_hint_state:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX[stage_hint_state])
    if has_crafting_table:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_hammer"])
    if has_hammer:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["hammer_to_resources"])
    if has_pre_sieving_resources:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["pre_sieving"])
    if has_sieving_items or has_early_smelting_items:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["early_smelting"])
    if has_machine_items:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["machine_bootstrap"])
    if has_midgame_items:
        observed_index = max(observed_index, STONEBLOCK_EARLY_PROGRESS_INDEX["automation_midgame"])

    state = "bootstrap_crafting"
    while True:
        next_state = STONEBLOCK_EARLY_PROGRESS_TRANSITIONS.get(state)
        if not next_state:
            break
        if STONEBLOCK_EARLY_PROGRESS_INDEX[next_state] > observed_index:
            break
        state = next_state
    return state, True


def _item_required_progress_state(item_id: str) -> str:
    token = str(item_id).strip().lower()
    if not token:
        return "bootstrap_crafting"
    if ":" in token:
        _, path = token.split(":", 1)
    else:
        path = token

    late_markers = (
        "mark_of_mastery",
        "creative",
        "ultimate",
        "supreme",
        "chaos",
        "singularity",
        "nether_star",
        "dragon_egg",
        "infinity",
        "reactor",
        "quantum",
    )
    machine_markers = (
        "machine",
        "infuser",
        "crusher",
        "enrichment",
        "generator",
        "conveyor",
        "factory",
        "frame",
        "cable",
        "pipe",
        "energy",
    )
    early_smelting_markers = (
        "furnace",
        "ingot",
        "charcoal",
        "coal",
        "smelt",
        "alloy",
        "blast",
    )
    pre_sieving_markers = (
        "sieve",
        "mesh",
        "dust",
        "pebble",
    )
    hammer_to_resources_markers = (
        "gravel",
        "sand",
        "dirt",
        "clay",
        "cobble",
        "stone",
        "deepslate",
        "netherrack",
        "andesite",
        "granite",
        "diorite",
        "flint",
    )
    bootstrap_crafting_markers = (
        "crafting_table",
        "planks",
        "stick",
        "log",
        "wood",
        "torch",
    )

    state_index = STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_crafting"]
    is_hammer_tool = "hammer" in path
    if is_hammer_tool:
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_hammer"])
    if any(marker in path for marker in pre_sieving_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["pre_sieving"])
    if any(marker in path for marker in early_smelting_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["early_smelting"])
    if any(marker in path for marker in machine_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["machine_bootstrap"])
    if any(marker in path for marker in late_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["automation_midgame"])
    if not is_hammer_tool and any(marker in path for marker in hammer_to_resources_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["hammer_to_resources"])
    if any(marker in path for marker in bootstrap_crafting_markers):
        state_index = max(state_index, STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_crafting"])
    return STONEBLOCK_EARLY_PROGRESS_STATES[state_index]


def _quest_required_progress_state(quest: dict[str, Any]) -> str:
    required_index = STONEBLOCK_EARLY_PROGRESS_INDEX["bootstrap_crafting"]
    for task in quest.get("tasks", []):
        if bool(task.get("optional_task", False)):
            continue
        if str(task.get("type", "")).strip().lower() != "item":
            continue
        item_id = str(task.get("item_id", "")).strip()
        item_state = _item_required_progress_state(item_id)
        required_index = max(required_index, STONEBLOCK_EARLY_PROGRESS_INDEX[item_state])
    return STONEBLOCK_EARLY_PROGRESS_STATES[required_index]


def _quest_allowed_in_progress_state(quest: dict[str, Any], progress_state: str) -> bool:
    current_index = STONEBLOCK_EARLY_PROGRESS_INDEX.get(progress_state, 0)
    required_state = _quest_required_progress_state(quest)
    required_index = STONEBLOCK_EARLY_PROGRESS_INDEX.get(required_state, 0)
    return required_index <= current_index


def _coerce_non_negative_int(value: Any) -> int:
    try:
        num = int(value)
    except Exception:
        return 0
    return max(0, num)


def _persist_strategy_block(item_id: str) -> bool:
    token = str(item_id).strip().lower()
    if not token:
        return False
    if ":" in token:
        namespace, path = token.split(":", 1)
    else:
        namespace, path = "minecraft", token
    if namespace != "minecraft":
        transient_mod_items = {
            "ftbstuff:stone_hammer",
            "ftbstuff:iron_hammer",
        }
        return token not in transient_mod_items
    transient_paths = {
        "crafting_table",
        "furnace",
        "chest",
        "stick",
        "planks",
        "oak_planks",
        "spruce_planks",
        "birch_planks",
        "jungle_planks",
        "acacia_planks",
        "dark_oak_planks",
        "mangrove_planks",
        "bamboo_planks",
        "cobblestone",
        "stone",
        "deepslate",
        "gravel",
        "sand",
        "dirt",
        "torch",
        "redstone",
        "iron_ingot",
        "coal",
        "charcoal",
    }
    return path not in transient_paths


def _blocked_item_ids_from_strategy_memory(strategy_memory_file: str | Path | None) -> set[str]:
    if strategy_memory_file is None:
        return set()
    raw_path = str(strategy_memory_file).strip()
    if not raw_path:
        return set()
    memory_path = Path(raw_path).expanduser().resolve()
    if not memory_path.exists():
        return set()
    try:
        raw = json.loads(memory_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    if not isinstance(raw, dict):
        return set()
    items = raw.get("items")
    if not isinstance(items, dict):
        return set()

    blocked: set[str] = set()
    for item_id, item_payload in items.items():
        token = str(item_id).strip().lower()
        if not token or not isinstance(item_payload, dict):
            continue
        attempts = 0
        successes = 0
        strategies = item_payload.get("strategies")
        if isinstance(strategies, dict):
            for strategy_payload in strategies.values():
                if not isinstance(strategy_payload, dict):
                    continue
                attempts += _coerce_non_negative_int(strategy_payload.get("attempts", 0))
                successes += _coerce_non_negative_int(strategy_payload.get("successes", 0))
        else:
            attempts = _coerce_non_negative_int(item_payload.get("attempts", 0))
            successes = _coerce_non_negative_int(item_payload.get("successes", 0))
        if attempts >= 2 and successes == 0 and _persist_strategy_block(token):
            blocked.add(token)
    return blocked


def _quest_has_blocked_required_item(quest: dict[str, Any], blocked_item_ids: set[str]) -> bool:
    if not blocked_item_ids:
        return False
    for task in quest.get("tasks", []):
        if bool(task.get("optional_task", False)):
            continue
        if str(task.get("type", "")).strip().lower() != "item":
            continue
        item_id = str(task.get("item_id", "")).strip().lower()
        if item_id and item_id in blocked_item_ids:
            return True
    return False


def _item_progression_score(item_id: str) -> int:
    token = str(item_id).strip().lower()
    if not token:
        return 0
    if ":" in token:
        namespace, path = token.split(":", 1)
    else:
        namespace, path = "minecraft", token

    score = 0
    if namespace in {"minecraft", "ftbmaterials", "ftbstuff"}:
        score += 18
    elif namespace in {"mekanism", "oritech", "thermal"}:
        score += 2
    else:
        score -= 8

    early_markers = (
        "cobble",
        "stone",
        "deepslate",
        "gravel",
        "sand",
        "dirt",
        "clay",
        "log",
        "planks",
        "torch",
        "furnace",
        "crafting_table",
        "hammer",
        "sieve",
        "mesh",
    )
    if any(marker in path for marker in early_markers):
        score += 10

    late_markers = (
        "mark_of_mastery",
        "creative",
        "ultimate",
        "supreme",
        "chaos",
        "singularity",
        "nether_star",
        "dragon_egg",
        "infinity",
    )
    if any(marker in path for marker in late_markers):
        score -= 40
    return score


def _quest_progression_score(quest: dict[str, Any]) -> int:
    score = 0
    for task in quest.get("tasks", []):
        if str(task.get("type", "")).strip().lower() != "item":
            continue
        item_id = str(task.get("item_id", "")).strip()
        score += _item_progression_score(item_id)
    return score


def _build_goal_for_quest(quest: dict[str, Any], include_ids: set[str], depth: int) -> Goal:
    quest_id = str(quest.get("quest_id", "")).strip()
    title = str(quest.get("title", "")).strip() or quest_id
    chapter_title = str(quest.get("chapter_title", "")).strip()
    desc = f"Quest automation: {title}" if not chapter_title else f"Quest automation: {chapter_title} / {title}"

    prerequisites = ["calibration_ok"]
    for dep in quest.get("dependencies", []):
        dep_id = str(dep).strip()
        if dep_id in include_ids:
            prerequisites.append(_quest_flag(dep_id))

    tasks: list[dict[str, Any]] = [
        {"type": "note", "text": f"Starting quest {title} ({quest_id})"},
    ]
    for task in quest.get("tasks", []):
        task_type = str(task.get("type", "")).strip().lower()
        if task_type not in SUPPORTED_QUEST_TASK_TYPES and bool(task.get("optional_task", False)):
            # Optional unsupported tasks are ignored rather than blocking automation.
            continue
        tasks.extend(_task_to_runtime_tasks(task))

    tasks.append({"type": "quest_claim_all"})
    tasks.append({"type": "set_flag", "flag": _quest_flag(quest_id)})

    chapter_index = max(0, int(quest.get("chapter_index", 0)))
    chapter_penalty = chapter_index * 20
    progression_bonus = _quest_progression_score(quest)
    # Earlier chapter + lower dependency depth + easier item mix are prioritized first.
    priority = max(10, 2500 - chapter_penalty - (depth * 3) + progression_bonus)
    return Goal(
        id=_goal_id(quest_id),
        priority=priority,
        description=desc,
        prerequisites=prerequisites,
        completion_flags=[_quest_flag(quest_id)],
        tasks=tasks,
    )


def build_full_auto_goals(
    pack_model_path: str | Path,
    include_chapter_files: list[str] | None = None,
    max_quests: int = 40,
    include_optional: bool = False,
    strategy_memory_file: str | Path | None = None,
    telemetry: dict[str, Any] | None = None,
) -> list[Goal]:
    model_path = Path(pack_model_path).expanduser().resolve()
    if not model_path.exists():
        return []
    raw = json.loads(model_path.read_text(encoding="utf-8-sig"))
    chapters = list(raw.get("chapters", []))
    include_files = {str(v).strip().lower() for v in (include_chapter_files or []) if str(v).strip()}
    blocked_item_ids = _blocked_item_ids_from_strategy_memory(strategy_memory_file)
    progress_telemetry = _load_stoneblock_progress_telemetry(model_path, telemetry=telemetry)
    progress_state, progress_state_known = _stoneblock_progress_state_from_telemetry(progress_telemetry)

    def _collect_selected(blocked: set[str], enforce_progress_state: bool) -> list[dict[str, Any]]:
        selected_local: list[dict[str, Any]] = []
        for chapter_index, chapter in enumerate(chapters):
            if not _include_chapter(chapter, include_files):
                continue
            chapter_title = str(chapter.get("chapter_title", ""))
            chapter_file = str(chapter.get("file", ""))
            for quest in chapter.get("quests", []):
                if not include_optional and bool(quest.get("optional", False)):
                    continue
                unsupported_required, _ = _quest_unsupported_task_buckets(quest)
                if unsupported_required:
                    continue
                if not _quest_has_actionable_required_supported_task(quest):
                    continue
                if not _quest_required_items_progression_friendly(quest):
                    continue
                if _quest_has_blocked_required_item(quest, blocked):
                    continue
                if enforce_progress_state and not _quest_allowed_in_progress_state(quest, progress_state):
                    continue
                q = dict(quest)
                q["chapter_title"] = chapter_title
                q["chapter_file"] = chapter_file
                q["chapter_index"] = chapter_index
                selected_local.append(q)
                if max_quests > 0 and len(selected_local) >= max_quests:
                    break
            if max_quests > 0 and len(selected_local) >= max_quests:
                break
        return selected_local

    selected: list[dict[str, Any]] = _collect_selected(
        blocked_item_ids,
        enforce_progress_state=progress_state_known,
    )
    if not selected and progress_state_known:
        # Compatibility fallback: if stage gating is too strict, preserve existing broad quest flow.
        selected = _collect_selected(blocked_item_ids, enforce_progress_state=False)
    if not selected and blocked_item_ids:
        # Fallback: do not let stale strategy memory fully suppress progression planning.
        selected = _collect_selected(set(), enforce_progress_state=progress_state_known)
        if not selected and progress_state_known:
            selected = _collect_selected(set(), enforce_progress_state=False)

    if not selected:
        return []

    selected_ids = {str(q.get("quest_id", "")).strip() for q in selected}
    selected = [
        q
        for q in selected
        if all(str(dep).strip() in selected_ids for dep in q.get("dependencies", []))
    ]
    if not selected:
        return []

    include_ids = {str(q.get("quest_id", "")).strip() for q in selected}
    selected_map = {str(q.get("quest_id", "")).strip(): q for q in selected}
    depth_cache: dict[str, int] = {}

    def depth_of(quest_id: str, visiting: set[str] | None = None) -> int:
        if quest_id in depth_cache:
            return depth_cache[quest_id]
        visiting = visiting or set()
        if quest_id in visiting:
            return 1
        visiting.add(quest_id)
        quest = selected_map.get(quest_id)
        if not quest:
            return 1
        dep_depths: list[int] = []
        for dep in quest.get("dependencies", []):
            dep_id = str(dep).strip()
            if dep_id in include_ids:
                dep_depths.append(depth_of(dep_id, visiting))
        d = 1 + (max(dep_depths) if dep_depths else 0)
        depth_cache[quest_id] = d
        visiting.remove(quest_id)
        return d

    goals: list[Goal] = []
    for quest in selected:
        quest_id = str(quest.get("quest_id", "")).strip()
        if not quest_id:
            continue
        goals.append(_build_goal_for_quest(quest, include_ids=include_ids, depth=depth_of(quest_id)))

    goals.sort(key=lambda g: g.priority, reverse=True)
    return goals
