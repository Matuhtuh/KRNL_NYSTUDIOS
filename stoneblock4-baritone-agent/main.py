from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.baritone import BaritoneBridge
from agent.config import load_config
from agent.dashboard import run_dashboard
from agent.dashboard_web import run_dashboard_web
from agent.full_auto import build_full_auto_goals
from agent.input_control import GameController
from agent.knowledge import load_goals
from agent.pack_parser import build_pack_model
from agent.perception import ScreenPerception
from agent.planner import AgentState, GoalPlanner
from agent.runner import AgentRuntime
from agent.simulation import run_simulation
from agent.stoneblock4_knowledge import build_stoneblock_knowledge

log = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(payload, indent=2)
    max_attempts = 6
    last_exc: OSError | None = None
    for attempt in range(max_attempts):
        temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}-{attempt}")
        try:
            temp_path.write_text(payload_text, encoding="utf-8")
            os.replace(temp_path, path)
            return
        except OSError as exc:
            last_exc = exc
            winerr = int(getattr(exc, "winerror", 0) or 0)
            lock_error = isinstance(exc, PermissionError) or winerr in {5, 32, 33}
            if attempt >= max_attempts - 1 or not lock_error:
                raise
            time.sleep(min(0.5, (0.02 * (2**attempt)) + (0.01 * attempt)))
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
    if last_exc is not None:
        raise last_exc


def _load_state_file(path: Path) -> AgentState:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"state file root must be a JSON object: {path}")
    return AgentState.from_json(raw)


def _backup_state_candidates(path: Path) -> list[Path]:
    def _sort_key(candidate: Path) -> tuple[float, str]:
        try:
            return candidate.stat().st_mtime, candidate.name
        except OSError:
            return 0.0, candidate.name

    pattern = f"{path.stem}.backup-*{path.suffix}"
    candidates = [p for p in path.parent.glob(pattern) if p.is_file()]
    candidates.sort(key=_sort_key, reverse=True)
    return candidates


def _load_state(path: Path) -> AgentState:
    if not path.exists():
        return AgentState()
    try:
        return _load_state_file(path)
    except Exception as exc:
        log.warning("Failed to load state file '%s': %s", path, exc)

    for backup in _backup_state_candidates(path):
        try:
            state = _load_state_file(backup)
            log.warning("Recovered state from backup '%s'", backup)
            return state
        except Exception as backup_exc:
            log.warning("Failed to load state backup '%s': %s", backup, backup_exc)

    log.error("Unable to recover planner state from '%s' or any backup. Using fresh state.", path)
    return AgentState()


def _save_state(path: Path, state: AgentState) -> None:
    _atomic_write_json(path, state.to_json())


def _arm_state_for_live_run(
    state_path: Path,
    *,
    keep_last_error: bool,
    keep_goal_retries: bool,
    purge_stale_quest_markers: bool = True,
) -> tuple[AgentState, Path | None]:
    state = _load_state(state_path)
    original = json.dumps(state.to_json(), sort_keys=True)

    state.safe_paused = False
    state.current_goal_id = None
    if not keep_last_error:
        state.last_error = None
    if not keep_goal_retries:
        state.goal_retries = {}
    state.goal_defer_until = {}
    if purge_stale_quest_markers:
        transient_block_suffixes = (
            "ftbstuff:stone_hammer",
            "ftbstuff:iron_hammer",
        )
        state.completed_goals = {
            goal_id
            for goal_id in state.completed_goals
            if not str(goal_id).strip().lower().startswith("quest_")
        }
        state.completed_flags = {
            flag
            for flag in state.completed_flags
            if not str(flag).strip().lower().startswith("quest_done_")
            and not str(flag).strip().lower().startswith("quest_item_blocked::minecraft:")
            and not any(
                str(flag).strip().lower().endswith(suffix)
                for suffix in transient_block_suffixes
            )
        }

    changed = json.dumps(state.to_json(), sort_keys=True) != original
    backup_path: Path | None = None
    if changed and state_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = state_path.with_name(f"{state_path.stem}.backup-{stamp}{state_path.suffix}")
        shutil.copy2(state_path, backup_path)
    if changed or not state_path.exists():
        _save_state(state_path, state)
    return state, backup_path


def _build_runtime_goals(cfg) -> list:
    knowledge_path = Path(cfg.planner.knowledge_file).resolve()
    goals = load_goals(knowledge_path)

    if cfg.planner.full_auto_enabled:
        pack_model_path = Path(cfg.planner.pack_model_file).resolve()
        instance_path = cfg.planner.instance_path.strip()
        if instance_path:
            build_pack_model(instance_path, pack_model_path)
        full_goals = build_full_auto_goals(
            pack_model_path=pack_model_path,
            include_chapter_files=cfg.planner.full_auto_include_chapters,
            max_quests=cfg.planner.full_auto_max_quests,
            include_optional=cfg.planner.full_auto_include_optional_quests,
            strategy_memory_file=cfg.planner.strategy_memory_file,
        )
        existing_ids = {g.id for g in goals}
        for g in full_goals:
            if g.id not in existing_ids:
                goals.append(g)
        goals.sort(key=lambda x: x.priority, reverse=True)
    return goals


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="StoneBlock 4 Baritone + Screenwatch Agent")
    p.add_argument("--config", default="config.yaml", help="Path to config YAML")
    p.add_argument("--verbose", action="store_true")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("observe", help="Print one observation snapshot")

    send = sub.add_parser("send", help="Send one baritone command and exit")
    send.add_argument("command", help="Command body without # prefix (example: mine minecraft:cobblestone 256)")

    stop_baritone = sub.add_parser(
        "stop-baritone",
        help="Send fail-closed pause/stop commands and verify Baritone shutdown state",
    )
    stop_baritone.add_argument("--reason", default="manual_stop", help="Stop reason for logs")
    stop_baritone.add_argument("--source", default="cli", help="Stop source for logs")
    stop_baritone.add_argument(
        "--verify-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for post-stop bridge verification",
    )

    sub.add_parser("run", help="Run planner loop")
    arm = sub.add_parser(
        "arm-run",
        help="Clear safe-pause/retry deadlocks, wait for in-world bridge readiness, then run planner loop",
    )
    arm.add_argument("--bridge-timeout", type=float, default=180.0, help="Seconds to wait for bridge readiness")
    arm.add_argument("--keep-last-error", action="store_true", help="Do not clear last_error in state file")
    arm.add_argument("--keep-goal-retries", action="store_true", help="Do not reset goal_retries in state file")
    arm.add_argument(
        "--no-purge-quest-markers",
        action="store_true",
        help="Keep existing quest_* and quest_done_* markers in state file",
    )

    sim = sub.add_parser("simulate", help="Run planner in simulation mode (no real key input)")
    sim.add_argument("--scenario", default="", help="Optional YAML scenario with fail_on_commands list")

    extract = sub.add_parser("extract-pack-model", help="Parse local StoneBlock files into planner model JSON")
    extract.add_argument("--instance", required=True, help="Path to CurseForge instance folder")
    extract.add_argument("--out", default="runtime/pack_model.json", help="Output JSON path")

    extract_knowledge = sub.add_parser(
        "extract-stoneblock-knowledge",
        help="Build local StoneBlock knowledge cache from the installed instance",
    )
    extract_knowledge.add_argument("--instance", required=True, help="Path to CurseForge instance folder")
    extract_knowledge.add_argument("--pack-model-out", default="runtime/pack_model.json", help="Pack model output path")
    extract_knowledge.add_argument(
        "--out",
        default="runtime/dashboard/stoneblock_knowledge.json",
        help="Knowledge cache output path",
    )

    dashboard = sub.add_parser("dashboard", help="Live terminal dashboard (reads runtime/live_status.json + bridge status)")
    dashboard.add_argument("--interval", type=float, default=1.0, help="Refresh interval seconds")
    dashboard.add_argument("--once", action="store_true", help="Render once and exit")

    dashboard_web = sub.add_parser("dashboard-web", help="Local web dashboard with preview, status, and AI chat")
    dashboard_web.add_argument("--host", default="127.0.0.1", help="Bind host")
    dashboard_web.add_argument("--port", type=int, default=8765, help="Bind port")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    cfg = load_config(args.config)

    if args.cmd == "simulate":
        result = run_simulation(cfg, scenario_file=args.scenario or None)
        print("simulation_sent_commands:", len(result.sent_commands))
        for i, cmd in enumerate(result.sent_commands, start=1):
            print(f"  {i}. {cmd}")
        print("simulation_completed_flags:", sorted(result.state.completed_flags))
        print("simulation_safe_paused:", result.state.safe_paused)
        print("simulation_last_error:", result.state.last_error)
        return

    if args.cmd == "extract-pack-model":
        model = build_pack_model(args.instance, args.out)
        print("chapters_count:", model["chapters_count"])
        print("quests_count:", model["quests_count"])
        print("worldengine_autobuild_quests:", len(model["worldengine_autobuild_quests"]))
        print("output:", Path(args.out).resolve())
        return

    if args.cmd == "extract-stoneblock-knowledge":
        model = build_stoneblock_knowledge(
            args.instance,
            pack_model_path=args.pack_model_out,
            out_path=args.out,
        )
        print("routes_count:", len(model["routes"]))
        print("items_count:", len(model["items"]))
        print("stage_hints_count:", len(model["stageHints"]))
        print("output:", Path(args.out).resolve())
        return

    if args.cmd == "dashboard":
        run_dashboard(cfg, interval_seconds=args.interval, once=bool(args.once))
        return

    if args.cmd == "dashboard-web":
        run_dashboard_web(cfg, host=str(args.host), port=int(args.port))
        return

    controller = GameController(cfg.window, cfg.control)
    perception = ScreenPerception(cfg.capture, cfg.ocr)
    baritone = BaritoneBridge(controller=controller, perception=perception, config=cfg.baritone)

    if args.cmd == "observe":
        obs = perception.observe()
        print("chat_text:", obs.chat_text)
        print("chat_confidence:", obs.chat_confidence)
        print("hud_text:", obs.hud_text)
        print("hud_confidence:", obs.hud_confidence)
        print("full_text_hint:", obs.full_text_hint)
        print("full_confidence:", obs.full_confidence)
        return

    if args.cmd == "send":
        baritone.command(args.command)
        print("sent")
        return

    if args.cmd == "stop-baritone":
        result = baritone.fail_closed_stop(
            reason=str(args.reason),
            source=str(args.source),
            verify_timeout_seconds=float(args.verify_timeout),
        )
        print(json.dumps(result, indent=2))
        return

    if args.cmd in {"run", "arm-run"}:
        if args.cmd == "arm-run":
            state_path = Path(cfg.planner.state_file).resolve()
            _, backup = _arm_state_for_live_run(
                state_path,
                keep_last_error=bool(args.keep_last_error),
                keep_goal_retries=bool(args.keep_goal_retries),
                purge_stale_quest_markers=not bool(args.no_purge_quest_markers),
            )
            print("armed_state_file:", state_path)
            if backup is not None:
                print("state_backup:", backup)
            if cfg.baritone.transport.strip().lower() == "bridge_file":
                status = baritone.wait_for_bridge_ready(timeout_seconds=float(args.bridge_timeout))
                print("bridge_ready_inworld:", bool(status.get("inWorld", False)))
                print("bridge_player:", str(status.get("playerName", "")))

        goals = _build_runtime_goals(cfg)
        planner = GoalPlanner(goals, planner_config=cfg.planner)
        runtime = AgentRuntime(cfg, planner, baritone, perception)
        shutdown_reason = f"{args.cmd}_return"
        try:
            runtime.run()
        except KeyboardInterrupt:
            shutdown_reason = f"{args.cmd}_keyboard_interrupt"
            log.warning("Keyboard interrupt received during %s; triggering fail-closed Baritone stop.", args.cmd)
        except Exception:
            shutdown_reason = f"{args.cmd}_exception"
            raise
        finally:
            shutdown_result = baritone.fail_closed_stop(
                reason=shutdown_reason,
                source=f"main.{args.cmd}.finally",
            )
            log_level = logging.INFO if bool(shutdown_result.get("shutdownVerified", False)) else logging.WARNING
            log.log(
                log_level,
                "Runtime fail-closed Baritone stop result: %s",
                json.dumps(shutdown_result, ensure_ascii=True, sort_keys=True, default=str),
            )
        return

    raise RuntimeError(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
