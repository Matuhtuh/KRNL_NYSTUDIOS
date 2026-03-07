from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

PlannerRunMode = Literal["strict_progression", "idle_grind", "recovery_only"]
_VALID_PLANNER_RUN_MODES: tuple[PlannerRunMode, ...] = (
    "strict_progression",
    "idle_grind",
    "recovery_only",
)


@dataclass
class WindowConfig:
    title_contains: str


@dataclass
class ControlConfig:
    chat_key: str
    emergency_stop_key: str
    toggle_pause_key: str
    command_prefix: str
    input_backend: str
    ahk_exe: str
    type_delay_seconds: float
    pre_action_delay_seconds: float
    key_hold_seconds: float
    inter_key_delay_seconds: float
    torch_click_hold_seconds: float
    focus_settle_seconds: float
    focus_timeout_seconds: float
    stop_poll_seconds: float
    toggle_poll_seconds: float
    auto_refocus_enabled: bool
    auto_refocus_attempts: int
    auto_refocus_backoff_seconds: float


@dataclass
class Region:
    left: int
    top: int
    width: int
    height: int


@dataclass
class CaptureConfig:
    monitor_index: int
    full_screen: bool
    region: Region
    chat_region: Region
    hud_region: Region


@dataclass
class OcrConfig:
    tesseract_cmd: str
    lang: str
    psm: int
    min_confidence: float
    use_adaptive_threshold: bool


@dataclass
class PlannerConfig:
    run_mode: PlannerRunMode
    knowledge_file: str
    tick_seconds: float
    state_file: str
    task_max_retries: int
    max_goal_retries: int
    retry_backoff_seconds: float
    retry_backoff_multiplier: float
    retry_jitter_seconds: float
    max_idle_cycles: int
    full_auto_enabled: bool
    pack_model_file: str
    instance_path: str
    full_auto_max_quests: int
    full_auto_include_chapters: list[str]
    full_auto_include_optional_quests: bool
    idle_autonomous_enabled: bool
    idle_autonomous_interval_cycles: int
    idle_autonomous_module: str
    idle_autonomous_modules: list[str]
    auto_debug_loop_enabled: bool
    auto_debug_loop_delay_seconds: float
    auto_debug_loop_max_rearms: int
    debug_learning_log_file: str
    web_research_enabled: bool
    web_research_ttl_minutes: int
    web_research_timeout_seconds: float
    web_research_max_snippets: int
    web_research_cache_file: str
    strategy_memory_file: str
    strategy_exploration_rate: float
    quest_transient_defer_seconds: float


@dataclass
class BaritoneConfig:
    transport: str
    bridge_dir: str
    bridge_ack_timeout_seconds: float
    bridge_status_stale_after_seconds: float
    bridge_ready_wait_seconds: float
    bridge_unresponsive_after_seconds: float
    allow_legacy_proc_commands: bool
    require_pathing_transition_for_long_commands: bool
    long_command_startup_seconds: float
    long_running_prefixes: list[str]
    pre_mining_commands: list[str]
    ultimine_auto_enabled: bool
    ultimine_for_prefixes: list[str]
    ultimine_enable_command: str
    ultimine_disable_command: str
    idle_success_phrases: list[str]
    failure_phrases: list[str]
    min_phrase_confidence: float
    required_consecutive_matches: int
    poll_seconds: float
    idle_timeout_seconds: float
    auto_torch_enabled: bool
    torch_hotbar_slot: int
    torch_initial_delay_seconds: float
    torch_interval_seconds: float
    torch_for_prefixes: list[str]
    auto_torch_item_ids: list[str]
    auto_torch_craft_if_missing: bool
    auto_torch_craft_command: str
    auto_torch_min_count: int
    auto_eat_enabled: bool
    auto_eat_food_below: int
    food_hotbar_slot: int
    eat_hold_seconds: float
    eat_attempt_cooldown_seconds: float
    eat_fallback_interval_seconds: float
    food_item_keywords: list[str]
    inventory_cleanup_enabled: bool
    inventory_cleanup_cooldown_seconds: float
    inventory_full_free_slots_threshold: int
    inventory_cleanup_target_free_slots: int
    inventory_max_stacks_per_cleanup: int
    inventory_drop_junk_item_ids: list[str]
    inventory_drop_junk_keywords: list[str]
    inventory_keep_item_keywords: list[str]
    min_pickaxe_count: int
    pickaxe_hotbar_slot: int
    min_main_hand_durability: int
    pickaxe_item_keywords: list[str]
    tool_recovery_commands: list[str]
    stuck_recovery_enabled: bool
    stuck_no_progress_seconds: float
    stuck_recovery_max_attempts: int
    stuck_recovery_pause_seconds: float
    enable_safety_monitoring: bool
    min_health: float
    min_food_level: int
    min_air_supply: int
    pause_on_fire: bool
    pause_in_lava: bool
    avoid_hostiles: bool
    nearby_hostile_min_distance: float
    fight_hostiles: bool
    fight_hostile_max_distance: float
    fight_min_health: float
    fight_min_food_level: int
    fight_attempt_cooldown_seconds: float
    fight_pause_seconds: float
    fight_command_templates: list[str]
    avoid_players_while_mining: bool
    nearby_player_min_distance: float
    safety_recover_timeout_seconds: float
    safety_poll_seconds: float
    pre_task_safety_wait_seconds: float


@dataclass
class AppConfig:
    window: WindowConfig
    control: ControlConfig
    capture: CaptureConfig
    ocr: OcrConfig
    planner: PlannerConfig
    baritone: BaritoneConfig
    dashboard: "DashboardConfig"


@dataclass
class DashboardConfig:
    chat_provider: str
    chat_model: str
    ollama_model: str
    openai_api_key_env_var: str
    openai_api_key_file: str
    openai_base_url: str
    ollama_base_url: str
    prefer_model_for_natural_language: bool


def _to_region(raw: dict[str, Any]) -> Region:
    return Region(
        left=int(raw["left"]),
        top=int(raw["top"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
    )


def _resolve_config_path(base_dir: Path, value: Any) -> str:
    raw = str(value).strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve())

    primary = (base_dir / path).resolve()
    fallback = (base_dir.parent / path).resolve() if base_dir.parent != base_dir else primary

    if primary.exists():
        return str(primary)
    if fallback.exists():
        return str(fallback)

    # Common case: runtime-scoped config files still reference project-root-relative paths.
    if path.parts and base_dir.name.strip().lower() == str(path.parts[0]).strip().lower():
        return str(fallback)
    return str(primary)


def _parse_planner_run_mode(value: Any) -> PlannerRunMode:
    raw = str(value if value is not None else "").strip().lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    aliases: dict[str, PlannerRunMode] = {
        "strict": "strict_progression",
        "progression": "strict_progression",
        "idle": "idle_grind",
        "grind": "idle_grind",
        "recovery": "recovery_only",
    }
    mode = aliases.get(normalized, normalized) or "strict_progression"
    if mode not in _VALID_PLANNER_RUN_MODES:
        valid = ", ".join(_VALID_PLANNER_RUN_MODES)
        raise ValueError(f"Invalid planner.run_mode '{value}'. Expected one of: {valid}")
    return cast(PlannerRunMode, mode)


def load_config(path: str | Path) -> AppConfig:
    p = Path(path).expanduser().resolve()
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    config_dir = p.parent

    capture_region = _to_region(data["capture"]["region"])
    chat_region = _to_region(data["capture"]["chat_region"])
    hud_region = _to_region(data["capture"]["hud_region"])

    return AppConfig(
        window=WindowConfig(title_contains=str(data["window"]["title_contains"])),
        control=ControlConfig(
            chat_key=str(data["control"]["chat_key"]),
            emergency_stop_key=str(data["control"]["emergency_stop_key"]),
            toggle_pause_key=str(data["control"].get("toggle_pause_key", "PAUSE")),
            command_prefix=str(data["control"]["command_prefix"]),
            input_backend=str(data["control"].get("input_backend", "pydirectinput")),
            ahk_exe=str(data["control"].get("ahk_exe", "AutoHotkey64.exe")),
            type_delay_seconds=float(data["control"]["type_delay_seconds"]),
            pre_action_delay_seconds=float(data["control"]["pre_action_delay_seconds"]),
            key_hold_seconds=float(data["control"].get("key_hold_seconds", 0.05)),
            inter_key_delay_seconds=float(data["control"].get("inter_key_delay_seconds", 0.03)),
            torch_click_hold_seconds=float(data["control"].get("torch_click_hold_seconds", 0.05)),
            focus_settle_seconds=float(data["control"].get("focus_settle_seconds", 0.2)),
            focus_timeout_seconds=float(data["control"].get("focus_timeout_seconds", 1.5)),
            stop_poll_seconds=float(data["control"].get("stop_poll_seconds", 0.05)),
            toggle_poll_seconds=float(data["control"].get("toggle_poll_seconds", 0.08)),
            auto_refocus_enabled=bool(data["control"].get("auto_refocus_enabled", True)),
            auto_refocus_attempts=int(data["control"].get("auto_refocus_attempts", 3)),
            auto_refocus_backoff_seconds=float(data["control"].get("auto_refocus_backoff_seconds", 0.25)),
        ),
        capture=CaptureConfig(
            monitor_index=int(data["capture"]["monitor_index"]),
            full_screen=bool(data["capture"]["full_screen"]),
            region=capture_region,
            chat_region=chat_region,
            hud_region=hud_region,
        ),
        ocr=OcrConfig(
            tesseract_cmd=str(data["ocr"]["tesseract_cmd"]),
            lang=str(data["ocr"]["lang"]),
            psm=int(data["ocr"]["psm"]),
            min_confidence=float(data["ocr"].get("min_confidence", 0.45)),
            use_adaptive_threshold=bool(data["ocr"].get("use_adaptive_threshold", True)),
        ),
        planner=PlannerConfig(
            run_mode=_parse_planner_run_mode(data["planner"].get("run_mode", "strict_progression")),
            knowledge_file=_resolve_config_path(config_dir, data["planner"]["knowledge_file"]),
            tick_seconds=float(data["planner"]["tick_seconds"]),
            state_file=_resolve_config_path(config_dir, data["planner"]["state_file"]),
            task_max_retries=int(data["planner"].get("task_max_retries", 3)),
            max_goal_retries=int(data["planner"].get("max_goal_retries", 3)),
            retry_backoff_seconds=float(data["planner"].get("retry_backoff_seconds", 1.5)),
            retry_backoff_multiplier=float(data["planner"].get("retry_backoff_multiplier", 1.8)),
            retry_jitter_seconds=float(data["planner"].get("retry_jitter_seconds", 0.4)),
            max_idle_cycles=int(data["planner"].get("max_idle_cycles", 0)),
            full_auto_enabled=bool(data["planner"].get("full_auto_enabled", True)),
            pack_model_file=_resolve_config_path(config_dir, data["planner"].get("pack_model_file", "runtime/pack_model.json")),
            instance_path=_resolve_config_path(config_dir, data["planner"].get("instance_path", "")),
            full_auto_max_quests=int(data["planner"].get("full_auto_max_quests", 45)),
            full_auto_include_chapters=[str(v) for v in data["planner"].get("full_auto_include_chapters", [
                "getting_started.snbt",
                "world_engine__tier_1.snbt",
                "world_engine__tier_2.snbt",
                "resource_generation.snbt",
                "processing__automation.snbt",
                "technology.snbt",
                "mekanism.snbt",
                "oritech.snbt",
            ])],
            full_auto_include_optional_quests=bool(data["planner"].get("full_auto_include_optional_quests", False)),
            idle_autonomous_enabled=bool(data["planner"].get("idle_autonomous_enabled", True)),
            idle_autonomous_interval_cycles=int(data["planner"].get("idle_autonomous_interval_cycles", 3)),
            idle_autonomous_module=str(data["planner"].get("idle_autonomous_module", "idle_resource_grind")),
            idle_autonomous_modules=[
                str(v).strip()
                for v in data["planner"].get(
                    "idle_autonomous_modules",
                    [
                        "idle_resource_grind",
                        "stoneblock_hammer_brush_loop",
                        "basic_machine_bootstrap",
                        "basic_farm_bootstrap",
                    ],
                )
                if str(v).strip()
            ],
            auto_debug_loop_enabled=bool(data["planner"].get("auto_debug_loop_enabled", True)),
            auto_debug_loop_delay_seconds=float(data["planner"].get("auto_debug_loop_delay_seconds", 4.0)),
            auto_debug_loop_max_rearms=int(data["planner"].get("auto_debug_loop_max_rearms", 0)),
            debug_learning_log_file=_resolve_config_path(
                config_dir,
                data["planner"].get("debug_learning_log_file", "runtime/debug_learning_log.jsonl"),
            ),
            web_research_enabled=bool(data["planner"].get("web_research_enabled", True)),
            web_research_ttl_minutes=int(data["planner"].get("web_research_ttl_minutes", 180)),
            web_research_timeout_seconds=float(data["planner"].get("web_research_timeout_seconds", 4.0)),
            web_research_max_snippets=int(data["planner"].get("web_research_max_snippets", 6)),
            web_research_cache_file=_resolve_config_path(
                config_dir,
                data["planner"].get("web_research_cache_file", "runtime/web_research_cache.json"),
            ),
            strategy_memory_file=_resolve_config_path(
                config_dir,
                data["planner"].get("strategy_memory_file", "runtime/strategy_memory.json"),
            ),
            strategy_exploration_rate=float(data["planner"].get("strategy_exploration_rate", 0.15)),
            quest_transient_defer_seconds=float(data["planner"].get("quest_transient_defer_seconds", 300.0)),
        ),
        baritone=BaritoneConfig(
            transport=str(data["baritone"].get("transport", "chat")),
            bridge_dir=_resolve_config_path(config_dir, data["baritone"].get("bridge_dir", "")),
            bridge_ack_timeout_seconds=float(data["baritone"].get("bridge_ack_timeout_seconds", 5.0)),
            bridge_status_stale_after_seconds=float(data["baritone"].get("bridge_status_stale_after_seconds", 4.0)),
            bridge_ready_wait_seconds=float(data["baritone"].get("bridge_ready_wait_seconds", 45.0)),
            bridge_unresponsive_after_seconds=float(data["baritone"].get("bridge_unresponsive_after_seconds", 20.0)),
            allow_legacy_proc_commands=bool(data["baritone"].get("allow_legacy_proc_commands", False)),
            require_pathing_transition_for_long_commands=bool(
                data["baritone"].get("require_pathing_transition_for_long_commands", True)
            ),
            long_command_startup_seconds=float(data["baritone"].get("long_command_startup_seconds", 10.0)),
            long_running_prefixes=[str(v).lower() for v in data["baritone"].get(
                "long_running_prefixes", ["mine ", "tunnel ", "goto ", "build ", "follow ", "explore "]
            )],
            pre_mining_commands=[str(v) for v in data["baritone"].get(
                "pre_mining_commands",
                [
                    "set allowBreak true",
                    "set allowPlace true",
                    "set autoTool true",
                    "set itemSaver true",
                    "set itemSaverThreshold 12",
                ],
            )],
            ultimine_auto_enabled=bool(data["baritone"].get("ultimine_auto_enabled", False)),
            ultimine_for_prefixes=[str(v).lower() for v in data["baritone"].get(
                "ultimine_for_prefixes", ["mine ", "tunnel ", "build "]
            )],
            ultimine_enable_command=str(data["baritone"].get("ultimine_enable_command", "bridge.ultimine on")),
            ultimine_disable_command=str(data["baritone"].get("ultimine_disable_command", "bridge.ultimine off")),
            idle_success_phrases=[str(v) for v in data["baritone"]["idle_success_phrases"]],
            failure_phrases=[str(v) for v in data["baritone"]["failure_phrases"]],
            min_phrase_confidence=float(data["baritone"].get("min_phrase_confidence", 0.45)),
            required_consecutive_matches=int(data["baritone"].get("required_consecutive_matches", 2)),
            poll_seconds=float(data["baritone"].get("poll_seconds", 1.0)),
            idle_timeout_seconds=float(data["baritone"].get("idle_timeout_seconds", 240.0)),
            auto_torch_enabled=bool(data["baritone"].get("auto_torch_enabled", True)),
            torch_hotbar_slot=int(data["baritone"].get("torch_hotbar_slot", 9)),
            torch_initial_delay_seconds=float(data["baritone"].get("torch_initial_delay_seconds", 4.0)),
            torch_interval_seconds=float(data["baritone"].get("torch_interval_seconds", 16.0)),
            torch_for_prefixes=[str(v).lower() for v in data["baritone"].get(
                "torch_for_prefixes", ["mine ", "tunnel ", "build "]
            )],
            auto_torch_item_ids=[str(v).lower() for v in data["baritone"].get(
                "auto_torch_item_ids", ["minecraft:torch", "minecraft:soul_torch", "minecraft:redstone_torch"]
            )],
            auto_torch_craft_if_missing=bool(data["baritone"].get("auto_torch_craft_if_missing", False)),
            auto_torch_craft_command=str(data["baritone"].get("auto_torch_craft_command", "")),
            auto_torch_min_count=int(data["baritone"].get("auto_torch_min_count", 8)),
            auto_eat_enabled=bool(data["baritone"].get("auto_eat_enabled", True)),
            auto_eat_food_below=int(data["baritone"].get("auto_eat_food_below", 14)),
            food_hotbar_slot=int(data["baritone"].get("food_hotbar_slot", 8)),
            eat_hold_seconds=float(data["baritone"].get("eat_hold_seconds", 2.5)),
            eat_attempt_cooldown_seconds=float(data["baritone"].get("eat_attempt_cooldown_seconds", 6.0)),
            eat_fallback_interval_seconds=float(data["baritone"].get("eat_fallback_interval_seconds", 45.0)),
            food_item_keywords=[str(v).lower() for v in data["baritone"].get(
                "food_item_keywords", ["bread", "carrot", "apple", "potato", "stew", "soup", "berry", "meat"]
            )],
            inventory_cleanup_enabled=bool(data["baritone"].get("inventory_cleanup_enabled", True)),
            inventory_cleanup_cooldown_seconds=float(data["baritone"].get("inventory_cleanup_cooldown_seconds", 12.0)),
            inventory_full_free_slots_threshold=int(data["baritone"].get("inventory_full_free_slots_threshold", 2)),
            inventory_cleanup_target_free_slots=int(data["baritone"].get("inventory_cleanup_target_free_slots", 8)),
            inventory_max_stacks_per_cleanup=int(data["baritone"].get("inventory_max_stacks_per_cleanup", 6)),
            inventory_drop_junk_item_ids=[str(v).lower() for v in data["baritone"].get(
                "inventory_drop_junk_item_ids",
                [
                    "minecraft:cobblestone",
                    "minecraft:cobbled_deepslate",
                    "minecraft:stone",
                    "minecraft:deepslate",
                    "minecraft:diorite",
                    "minecraft:andesite",
                    "minecraft:granite",
                    "minecraft:tuff",
                    "minecraft:gravel",
                    "minecraft:sand",
                    "minecraft:dirt",
                    "minecraft:netherrack",
                    "minecraft:basalt",
                    "minecraft:blackstone",
                    "minecraft:flint",
                ],
            ) if str(v).strip()],
            inventory_drop_junk_keywords=[str(v).lower() for v in data["baritone"].get(
                "inventory_drop_junk_keywords",
                [
                    "cobblestone",
                    "cobbled_deepslate",
                    "deepslate",
                    "diorite",
                    "andesite",
                    "granite",
                    "tuff",
                    "netherrack",
                ],
            ) if str(v).strip()],
            inventory_keep_item_keywords=[str(v).lower() for v in data["baritone"].get(
                "inventory_keep_item_keywords",
                [
                    "pickaxe",
                    "hammer",
                    "paxel",
                    "drill",
                    "gadget",
                    "torch",
                    "food",
                    "bread",
                    "carrot",
                    "apple",
                    "potato",
                    "stew",
                    "bucket",
                    "crafting_table",
                    "furnace",
                    "chest",
                    "backpack",
                ],
            ) if str(v).strip()],
            min_pickaxe_count=int(data["baritone"].get("min_pickaxe_count", 1)),
            pickaxe_hotbar_slot=int(data["baritone"].get("pickaxe_hotbar_slot", 1)),
            min_main_hand_durability=int(data["baritone"].get("min_main_hand_durability", 24)),
            pickaxe_item_keywords=[str(v).lower() for v in data["baritone"].get(
                "pickaxe_item_keywords", ["pickaxe", "hammer", "paxel", "drill", "mining_gadget"]
            )],
            tool_recovery_commands=[str(v).strip() for v in data["baritone"].get(
                "tool_recovery_commands", []
            ) if str(v).strip()],
            stuck_recovery_enabled=bool(data["baritone"].get("stuck_recovery_enabled", True)),
            stuck_no_progress_seconds=float(data["baritone"].get("stuck_no_progress_seconds", 14.0)),
            stuck_recovery_max_attempts=int(data["baritone"].get("stuck_recovery_max_attempts", 3)),
            stuck_recovery_pause_seconds=float(data["baritone"].get("stuck_recovery_pause_seconds", 1.0)),
            enable_safety_monitoring=bool(data["baritone"].get("enable_safety_monitoring", True)),
            min_health=float(data["baritone"].get("min_health", 10.0)),
            min_food_level=int(data["baritone"].get("min_food_level", 8)),
            min_air_supply=int(data["baritone"].get("min_air_supply", 40)),
            pause_on_fire=bool(data["baritone"].get("pause_on_fire", True)),
            pause_in_lava=bool(data["baritone"].get("pause_in_lava", True)),
            avoid_hostiles=bool(data["baritone"].get("avoid_hostiles", True)),
            nearby_hostile_min_distance=float(data["baritone"].get("nearby_hostile_min_distance", 10.0)),
            fight_hostiles=bool(data["baritone"].get("fight_hostiles", True)),
            fight_hostile_max_distance=float(data["baritone"].get("fight_hostile_max_distance", 7.0)),
            fight_min_health=float(data["baritone"].get("fight_min_health", 14.0)),
            fight_min_food_level=int(data["baritone"].get("fight_min_food_level", 12)),
            fight_attempt_cooldown_seconds=float(data["baritone"].get("fight_attempt_cooldown_seconds", 4.0)),
            fight_pause_seconds=float(data["baritone"].get("fight_pause_seconds", 0.35)),
            fight_command_templates=[str(v) for v in data["baritone"].get(
                "fight_command_templates",
                ["bridge.fight_hostile 6 2", "bridge.fight_hostile 4 1"],
            ) if str(v).strip()],
            avoid_players_while_mining=bool(data["baritone"].get("avoid_players_while_mining", True)),
            nearby_player_min_distance=float(data["baritone"].get("nearby_player_min_distance", 96.0)),
            safety_recover_timeout_seconds=float(data["baritone"].get("safety_recover_timeout_seconds", 45.0)),
            safety_poll_seconds=float(data["baritone"].get("safety_poll_seconds", 1.0)),
            pre_task_safety_wait_seconds=float(data["baritone"].get("pre_task_safety_wait_seconds", 120.0)),
        ),
        dashboard=DashboardConfig(
            chat_provider=str((data.get("dashboard") or {}).get("chat_provider", "ollama")).strip() or "ollama",
            chat_model=str((data.get("dashboard") or {}).get("chat_model", "gpt-4.1-nano")).strip() or "gpt-4.1-nano",
            ollama_model=str((data.get("dashboard") or {}).get("ollama_model", "phi4-mini:latest")).strip() or "phi4-mini:latest",
            openai_api_key_env_var=str((data.get("dashboard") or {}).get("openai_api_key_env_var", "OPENAI_API_KEY")).strip()
            or "OPENAI_API_KEY",
            openai_api_key_file=_resolve_config_path(
                config_dir,
                (data.get("dashboard") or {}).get("openai_api_key_file", "runtime/openai_api_key.txt"),
            ),
            openai_base_url=str((data.get("dashboard") or {}).get("openai_base_url", "")).strip(),
            ollama_base_url=str((data.get("dashboard") or {}).get("ollama_base_url", "http://127.0.0.1:11434")).strip()
            or "http://127.0.0.1:11434",
            prefer_model_for_natural_language=bool(
                (data.get("dashboard") or {}).get("prefer_model_for_natural_language", True)
            ),
        ),
    )
