from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import AppConfig


def _read_json_with_snapshot(
    path: Path | None,
    last_good: dict[str, Any],
    label: str,
) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return last_good, None
    if not path.exists():
        if last_good:
            return last_good, f"{label}=stale(missing,using-last-good)"
        return {}, f"{label}=unavailable(missing)"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        if last_good:
            return last_good, f"{label}=stale({exc.__class__.__name__},using-last-good)"
        return {}, f"{label}=unavailable({exc.__class__.__name__})"
    if isinstance(payload, dict):
        return payload, None
    payload_type = type(payload).__name__
    if last_good:
        return last_good, f"{label}=stale(type={payload_type},using-last-good)"
    return {}, f"{label}=unavailable(type={payload_type})"


def _fmt_age(ts: str) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_s = max(0, int((now - dt).total_seconds()))
        return f"{age_s}s ago"
    except Exception:
        return "-"


def _short(text: str, limit: int = 120) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _read_jsonl_tail(path: Path | None, limit: int = 10) -> list[dict[str, Any]]:
    if path is None or not path.exists() or limit <= 0:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            chunk_size = 8192
            buf = b""
            while size > 0 and buf.count(b"\n") <= limit:
                read_size = min(chunk_size, size)
                size -= read_size
                handle.seek(size)
                buf = handle.read(read_size) + buf
        lines = buf.splitlines()[-limit:]
        out: list[dict[str, Any]] = []
        for raw in lines:
            text = raw.decode("utf-8-sig", errors="replace").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if isinstance(payload, dict):
                out.append(payload)
        return out
    except Exception:
        return []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _task_desc(task: dict[str, Any]) -> str:
    if not isinstance(task, dict):
        return "-"
    task_type = str(task.get("type", "")).strip() or "-"
    if task_type == "build_tunnel":
        w = _as_int(task.get("width", 2), 2)
        h = _as_int(task.get("height", 2), 2)
        length = _as_int(task.get("length", 16), 16)
        return f"{task_type} {w}x{h} len={length}"
    if task_type in {"craft_item", "craft_with_dependencies", "quest_item_acquire"}:
        item = str(task.get("item", "")).strip()
        count = _as_int(task.get("count", 1), 1)
        if item:
            return f"{task_type} {item} x{count}"
    if task_type == "baritone_command":
        command = str(task.get("command", "")).strip()
        if command:
            return f"{task_type} `{_short(command, 72)}`"
    if task_type == "run_module":
        name = str(task.get("name", "")).strip()
        if name:
            return f"{task_type} {name}"
    return task_type


def _bridge_position(bridge: dict[str, Any]) -> str:
    x = bridge.get("posX")
    y = bridge.get("posY")
    z = bridge.get("posZ")
    if any(v is None for v in (x, y, z)):
        return "-"
    try:
        return f"({_as_float(x):.1f}, {_as_float(y):.1f}, {_as_float(z):.1f})"
    except Exception:
        return "-"


def _inventory_slots(bridge: dict[str, Any]) -> tuple[int, int]:
    inv = bridge.get("inventory")
    if not isinstance(inv, list):
        return (0, 0)
    total = len(inv)
    free = 0
    for slot in inv:
        if not isinstance(slot, dict):
            continue
        item_id = str(slot.get("itemId", "")).strip()
        count = _as_int(slot.get("count", 0), 0)
        if not item_id and count <= 0:
            free += 1
    return free, total


def _learning_line(event: dict[str, Any]) -> str:
    name = str(event.get("event", "event"))
    age = _fmt_age(str(event.get("timestamp", "")))
    if name == "task_error":
        task_type = str(event.get("taskType", "-"))
        attempt = event.get("attempt")
        err = _short(str(event.get("error", "")), 96)
        return f"{age} task_error {task_type} attempt={attempt}: {err}"
    if name == "quest_strategy_failure":
        item = str(event.get("itemId", "-"))
        strategy = str(event.get("strategy", "-"))
        err = _short(str(event.get("error", "")), 92)
        return f"{age} strategy_failure {item} via {strategy}: {err}"
    if name == "quest_strategy_success":
        item = str(event.get("itemId", "-"))
        strategy = str(event.get("strategy", "-"))
        count = _as_int(event.get("requiredCount", 1), 1)
        return f"{age} strategy_success {item} via {strategy} x{count}"
    if name == "web_research_hints":
        item = str(event.get("itemId", "-"))
        cached = bool(event.get("cached", False))
        err = _short(str(event.get("error", "")), 78)
        extra = f" error={err}" if err else ""
        return f"{age} web_research {item} cached={cached}{extra}"
    return f"{age} {name}: {_short(json.dumps(event, ensure_ascii=True), 110)}"


def _strategy_stats(strategy_memory: dict[str, Any]) -> list[dict[str, Any]]:
    items = strategy_memory.get("items", {})
    if not isinstance(items, dict):
        return []
    out: list[dict[str, Any]] = []
    for item_id, payload in items.items():
        if not isinstance(payload, dict):
            continue
        strategies = payload.get("strategies", {})
        if not isinstance(strategies, dict):
            continue
        attempts = 0
        successes = 0
        for stats in strategies.values():
            if not isinstance(stats, dict):
                continue
            attempts += _as_int(stats.get("attempts", 0), 0)
            successes += _as_int(stats.get("successes", 0), 0)
        if attempts <= 0:
            continue
        out.append(
            {
                "item": str(item_id),
                "attempts": attempts,
                "successes": successes,
                "failures": max(0, attempts - successes),
                "success_rate": successes / attempts if attempts else 0.0,
            }
        )
    out.sort(key=lambda row: (row["failures"], row["attempts"]), reverse=True)
    return out


def _progress_suggestions(
    live: dict[str, Any],
    state: dict[str, Any],
    bridge: dict[str, Any],
    learning_events: list[dict[str, Any]],
    strategy_memory: dict[str, Any],
) -> list[str]:
    suggestions: list[str] = []

    def add(text: str) -> None:
        value = text.strip()
        if value and value not in suggestions and len(suggestions) < 5:
            suggestions.append(value)

    goal_id = (_text(live.get("goalId", "")) or _text(state.get("current_goal_id", ""))).strip()
    task = dict(live.get("task", {})) if isinstance(live.get("task"), dict) else {}
    task_desc = _task_desc(task)
    if goal_id:
        add(f"Finish active goal `{goal_id}` with the current task flow (`{task_desc}`).")
    elif task and task_desc != "-":
        add(f"Complete the active task chain (`{task_desc}`) before switching modules.")

    task_errors: Counter[str] = Counter()
    research_failures = 0
    for evt in learning_events:
        name = str(evt.get("event", ""))
        if name == "task_error":
            task_errors[str(evt.get("taskType", "unknown"))] += 1
        if name == "web_research_hints" and str(evt.get("error", "")).strip():
            research_failures += 1
    if task_errors:
        hot_task, hot_count = task_errors.most_common(1)[0]
        if hot_count >= 2:
            add(
                f"Unstick `{hot_task}` (recent errors: {hot_count}) by shortening the action scope "
                "or inserting a recovery step."
            )
    if research_failures > 0:
        add(
            "Progress using in-pack recipes and gather paths first; web research hints are currently unavailable."
        )

    strategy_rows = _strategy_stats(strategy_memory)
    worst = next((row for row in strategy_rows if row["attempts"] >= 2 and row["successes"] == 0), None)
    best = next((row for row in strategy_rows if row["attempts"] >= 2 and row["success_rate"] >= 0.8), None)
    if worst is not None:
        add(
            f"Defer direct retries for `{worst['item']}` (0/{worst['attempts']} success) "
            "and acquire prerequisites first."
        )
    if best is not None:
        add(
            f"Lean on proven routes like `{best['item']}` ({best['successes']}/{best['attempts']} success) "
            "to stabilize progression."
        )

    in_world = bool(bridge.get("inWorld", False))
    if not in_world:
        add("Re-enter world/bridge-ready state so commands can execute and progression can resume.")
    health = _as_float(bridge.get("health", 20), 20.0)
    max_health = _as_float(bridge.get("maxHealth", 20), 20.0)
    food = _as_int(bridge.get("foodLevel", 20), 20)
    hostile_dist = _as_float(bridge.get("nearestHostileDistance", -1), -1.0)
    in_fire = bool(bridge.get("onFire", False))
    in_lava = bool(bridge.get("inLava", False))
    if max_health > 0 and (health / max_health) < 0.7 or food < 12 or in_fire or in_lava:
        add("Stabilize survival first (heal/eat/retreat) before committing to longer mining/build steps.")
    if hostile_dist >= 0 and hostile_dist < 12:
        add(f"Clear or kite nearby hostiles (nearest {hostile_dist:.1f}m) to reduce task interruptions.")

    free_slots, total_slots = _inventory_slots(bridge)
    if total_slots > 0 and free_slots <= 2:
        add(f"Free inventory space ({free_slots}/{total_slots} open) to prevent gather/build stalls.")

    if bool(state.get("safe_paused", False)):
        add("Resolve `safe_paused` by addressing the last error, then resume autonomous progression.")

    done_goals = len(list(state.get("completed_goals", []))) if isinstance(state.get("completed_goals", []), list) else 0
    if done_goals > 0:
        add(f"Use completed milestone momentum ({done_goals} goals done) to push the next unlocked objective.")

    fallback = [
        "Keep the automation loop running until `task_complete` events outnumber `task_error` events.",
        "Prioritize repeatable resource loops that reduce dependency on brittle one-shot tasks.",
        "After each successful craft/acquire cycle, immediately chain into the next dependency in that branch.",
        "Favor shorter command segments while errors are frequent, then scale segment size back up after stability.",
        "Maintain safety and inventory headroom so progression tasks can execute without emergency pauses.",
    ]
    for text in fallback:
        add(text)
        if len(suggestions) >= 5:
            break
    return suggestions[:5]


def run_dashboard(cfg: AppConfig, interval_seconds: float = 1.0, once: bool = False) -> None:
    state_path = Path(cfg.planner.state_file).resolve()
    live_path = state_path.with_name("live_status.json")
    learning_path = Path(cfg.planner.debug_learning_log_file).resolve()
    strategy_path = Path(cfg.planner.strategy_memory_file).resolve()
    bridge_path: Path | None = None
    if cfg.baritone.transport.strip().lower() == "bridge_file":
        bridge_path = Path(cfg.baritone.bridge_dir).expanduser().resolve() / "status.json"

    refresh = max(0.2, float(interval_seconds))
    last_live: dict[str, Any] = {}
    last_state: dict[str, Any] = {}
    last_bridge_direct: dict[str, Any] = {}
    last_strategy_memory: dict[str, Any] = {}
    while True:
        read_errors: list[str] = []

        live, live_error = _read_json_with_snapshot(live_path, last_live, "live")
        if live_error:
            read_errors.append(live_error)
        else:
            last_live = live

        state, state_error = _read_json_with_snapshot(state_path, last_state, "state")
        if state_error:
            read_errors.append(state_error)
        else:
            last_state = state

        bridge_from_live = dict(live.get("bridge", {}).get("status", {})) if isinstance(live.get("bridge"), dict) else {}

        bridge_direct, bridge_error = _read_json_with_snapshot(bridge_path, last_bridge_direct, "bridge")
        if bridge_error:
            read_errors.append(bridge_error)
        elif bridge_path is not None:
            last_bridge_direct = bridge_direct

        bridge = bridge_direct if bridge_direct else bridge_from_live

        strategy_memory, strategy_error = _read_json_with_snapshot(
            strategy_path,
            last_strategy_memory,
            "strategy",
        )
        if strategy_error:
            read_errors.append(strategy_error)
        else:
            last_strategy_memory = strategy_memory

        learning_recent = _read_jsonl_tail(learning_path, limit=120)

        phase = str(live.get("phase", "-"))
        ts = str(live.get("timestamp", ""))
        goal_id = (_text(live.get("goalId", "")) or _text(state.get("current_goal_id", "")))
        task = dict(live.get("task", {})) if isinstance(live.get("task"), dict) else {}
        attempt = live.get("attempt")
        message = _short(str(live.get("message", "")))
        error = _short(str(live.get("error", "")))
        safety = live.get("safetyReasons", [])
        safety_text = "; ".join([str(v) for v in safety]) if isinstance(safety, list) else ""

        state_safe_paused = bool(state.get("safe_paused", False))
        state_last_error = _short(str(state.get("last_error", "")))
        done_goals = len(list(state.get("completed_goals", []))) if isinstance(state.get("completed_goals", []), list) else 0
        done_flags = len(list(state.get("completed_flags", []))) if isinstance(state.get("completed_flags", []), list) else 0

        is_pathing = bool(bridge.get("isPathing", False))
        bridge_goal = _short(str(bridge.get("currentGoal", "")))
        last_cmd = _short(str(bridge.get("lastCommand", "")))
        last_cmd_result = str(bridge.get("lastCommandResult", ""))
        bridge_last_error = _short(str(bridge.get("lastCommandError", "")))

        health = bridge.get("health", "-")
        max_health = bridge.get("maxHealth", "-")
        food = bridge.get("foodLevel", "-")
        air = bridge.get("airSupply", "-")
        fire = bool(bridge.get("onFire", False))
        lava = bool(bridge.get("inLava", False))
        hostile_dist = bridge.get("nearestHostileDistance", -1)
        player_dist = bridge.get("nearestOtherPlayerDistance", -1)
        near_player_name = str(bridge.get("nearestOtherPlayerName", "")).strip()
        bridge_ts = str(bridge.get("timestampIso", ""))
        player_uuid = str(bridge.get("playerUuid", "")).strip()
        dimension_id = str(bridge.get("dimensionId", "")).strip()
        game_mode = str(bridge.get("gameMode", "")).strip()
        modpack_name = str(bridge.get("modpackName", "")).strip()
        stage_hint = str(bridge.get("stoneblockStageHint", "")).strip()

        controller = dict(live.get("controller", {})) if isinstance(live.get("controller"), dict) else {}
        baritone = dict(live.get("baritone", {})) if isinstance(live.get("baritone"), dict) else {}
        active_body = _short(str(baritone.get("activeCommandBody", "")), 120)
        active_id = str(baritone.get("activeCommandId", "")).strip()

        learning_lines = [_learning_line(evt) for evt in learning_recent[-6:]]
        progression = _progress_suggestions(live, state, bridge, learning_recent[-40:], strategy_memory)
        free_slots, total_slots = _inventory_slots(bridge)
        free_slots_from_bridge = _as_int(bridge.get("inventoryFreeSlots", -1), -1)
        if free_slots_from_bridge >= 0:
            free_slots = free_slots_from_bridge
        where = _bridge_position(bridge)
        player_name = str(bridge.get("playerName", "")).strip()
        in_world = bool(bridge.get("inWorld", False))
        key_counts = bridge.get("stoneblockKeyItemCounts", {})
        key_summary = ""
        if isinstance(key_counts, dict):
            focus_keys = [
                "minecraft:cobblestone",
                "minecraft:gravel",
                "minecraft:sand",
                "minecraft:dirt",
                "ftbstuff:stone_hammer",
                "ftbstuff:iron_hammer",
            ]
            parts: list[str] = []
            for key in focus_keys:
                value = _as_int(key_counts.get(key, 0), 0)
                if value > 0:
                    short_key = key.split(":", 1)[1]
                    parts.append(f"{short_key}={value}")
            if parts:
                key_summary = ", ".join(parts)

        print("\x1b[2J\x1b[H", end="")
        print("StoneBlock4 Agent Command Window")
        print(f"Refresh: {refresh:.1f}s  LiveAge: {_fmt_age(ts)}  BridgeAge: {_fmt_age(bridge_ts)}")
        if read_errors:
            print(f"ReadStatus: READ_ERROR  {_short(' | '.join(read_errors), 220)}")
        else:
            print("ReadStatus: OK")

        print("")
        print("Where Bot Is Running")
        print(f"TargetWindow: {cfg.window.title_contains}")
        print(f"InstancePath: {cfg.planner.instance_path or '-'}")
        print(
            f"InWorld: {in_world}  Player: {player_name or '-'}  Pos: {where}  "
            f"Focused: {controller.get('windowFocused')}"
        )
        print(f"Dimension: {dimension_id or '-'}  GameMode: {game_mode or '-'}  UUID: {player_uuid or '-'}")
        print(f"Automation: {bool(controller.get('automationEnabled', False))}  StopRequested: {bool(controller.get('stopRequested', False))}")

        print("")
        print("Current Goal / Task")
        print(f"Phase: {phase}  Goal: {goal_id or '-'}")
        print(f"Task: {_task_desc(task)}{f' attempt={attempt}' if attempt is not None else ''}")
        print(f"PlannerMessage: {message or '-'}")
        if error:
            print(f"PlannerError: {error}")
        if safety_text:
            print(f"Safety: {_short(safety_text, 180)}")

        print("")
        print("Current AI Action")
        print(f"Modpack: {modpack_name or 'FTB StoneBlock 4'}  StageHint: {stage_hint or '-'}")
        print(f"ActiveCmd: {active_body or '-'}")
        print(f"ActiveCmdId: {active_id or '-'}")
        print(f"Pathing: {is_pathing}  QueueDepth: {bridge.get('queueDepth', '-')}  BridgeOK: {bool(bridge.get('bridgeOk', False))}")
        print(f"CurrentGoal: {bridge_goal or '-'}")
        print(f"LastCmd: {last_cmd or '-'}")
        print(f"CmdResult: {last_cmd_result or '-'}")
        if bridge_last_error:
            print(f"CmdError: {bridge_last_error}")
        print(f"Vitals: HP {health}/{max_health}  Food {food}  Air {air}  Fire={fire}  Lava={lava}")
        print(
            f"Distances: hostile={hostile_dist}m  player={player_dist}m"
            f"{f' ({near_player_name})' if near_player_name else ''}"
        )
        if total_slots > 0:
            print(f"Inventory: free_slots={free_slots}/{total_slots}")
        elif free_slots_from_bridge >= 0:
            print(f"Inventory: free_slots={free_slots}/?")
        else:
            print("Inventory: -")
        if key_summary:
            print(f"StoneBlock key items: {key_summary}")

        print("")
        print("Planner State")
        print(f"safe_paused={state_safe_paused}  completed_goals={done_goals}  completed_flags={done_flags}")
        if state_last_error:
            print(f"last_error={state_last_error}")

        print("")
        print("Recent Learning Events")
        if learning_lines:
            for line in learning_lines:
                print(f"- {line}")
        else:
            print("- no learning events yet")

        print("")
        print("Suggested Progression Directions")
        for idx, direction in enumerate(progression, start=1):
            print(f"{idx}. {direction}")

        if once:
            return
        time.sleep(refresh)
