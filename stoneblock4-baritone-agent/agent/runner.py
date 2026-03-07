from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.baritone import BaritoneBridge, BaritoneCommandValidationError
from agent.config import AppConfig
from agent.modules import module_tasks
from agent.perception import ScreenPerception
from agent.planner import AgentState, GoalPlanner
from agent.recipes import RecipeIndex
from agent.stoneblock4_knowledge import StoneBlockKnowledgeBase
from agent.strategy_memory import StrategyMemory
from agent.web_research import WebResearcher

log = logging.getLogger(__name__)


_ITEM_ID_RE = re.compile(r"\b([a-z0-9_.-]+:[a-z0-9_./-]+)\b", re.IGNORECASE)


class UnrecoverableQuestAcquireError(RuntimeError):
    def __init__(self, message: str, *, item_id: str = "") -> None:
        super().__init__(message)
        self.item_id = str(item_id).strip().lower()


class BridgeUnavailableError(RuntimeError):
    pass


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
            time.sleep(min(0.5, (0.02 * (2**attempt)) + (random.random() * 0.01)))
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


@dataclass
class AgentRuntime:
    config: AppConfig
    planner: GoalPlanner
    baritone: BaritoneBridge
    perception: ScreenPerception
    _recipe_index: RecipeIndex | None = field(default=None, init=False)
    _strategy_memory: StrategyMemory | None = field(default=None, init=False)
    _web_researcher: WebResearcher | None = field(default=None, init=False)
    _web_research_disabled: bool = field(default=False, init=False)
    _idle_module_failures: dict[str, int] = field(default_factory=dict, init=False)
    _last_idle_module: str = field(default="", init=False)
    _base_anchor_pos: tuple[float, float, float] | None = field(default=None, init=False)
    _base_protection_radius_blocks: float = field(default=14.0, init=False)
    _warned_bridge_craft_unsupported: bool = field(default=False, init=False)
    _optional_item_cooldowns: dict[str, float] = field(default_factory=dict, init=False)
    _optional_item_failures: dict[str, int] = field(default_factory=dict, init=False)
    _store_to_chest_bridge_supported: bool | None = field(default=None, init=False)
    _warned_store_to_chest_bridge_version: bool = field(default=False, init=False)
    _window_focus_wait_last_logged_at: float = field(default=0.0, init=False)
    _stoneblock_knowledge: StoneBlockKnowledgeBase | None = field(default=None, init=False)
    _operator_loop_state: dict[str, Any] = field(default_factory=dict, init=False)
    _idle_inventory_pressure_failures: int = field(default=0, init=False)

    def _live_status_path(self) -> Path:
        state_path = Path(self.config.planner.state_file).resolve()
        return state_path.with_name("live_status.json")

    def _task_summary(self, task: dict[str, Any] | None) -> dict[str, Any]:
        if not task:
            return {}
        task_type = str(task.get("type", "")).strip()
        summary: dict[str, Any] = {"type": task_type}
        if task_type == "baritone_command":
            summary["command"] = str(task.get("command", "")).strip()
        elif task_type == "build_tunnel":
            summary["width"] = int(task.get("width", 2))
            summary["height"] = int(task.get("height", 2))
            summary["length"] = int(task.get("length", 16))
        elif task_type in {"craft_item", "craft_with_dependencies", "quest_item_acquire"}:
            summary["item"] = str(task.get("item", "")).strip()
            summary["count"] = int(task.get("count", 1))
        elif task_type == "place_storage_chest":
            summary["count"] = int(task.get("count", 1))
        elif task_type == "store_inventory_in_chest":
            summary["radius"] = int(task.get("radius", 6))
            summary["max_stacks"] = int(task.get("max_stacks", 24))
            summary["include_hotbar"] = bool(task.get("include_hotbar", False))
        elif task_type == "run_module":
            summary["name"] = str(task.get("name", "")).strip()
        return summary

    def _write_live_status(
        self,
        state: AgentState,
        phase: str,
        *,
        goal_id: str | None = None,
        task: dict[str, Any] | None = None,
        attempt: int | None = None,
        message: str = "",
        error: str = "",
        safety_reasons: list[str] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        bridge_status: dict[str, Any] | None = None
        if self.config.baritone.transport.strip().lower() == "bridge_file":
            bridge_status = self.baritone.read_bridge_status()
        focused: bool | None = None
        try:
            focused = self.baritone.controller.is_window_focused()
        except Exception:
            focused = None
        payload = {
            "timestamp": now,
            "phase": phase,
            "goalId": goal_id if goal_id is not None else state.current_goal_id,
            "task": self._task_summary(task),
            "attempt": attempt,
            "message": message,
            "error": error,
            "safetyReasons": list(safety_reasons or []),
            "state": state.to_json(),
            "controller": {
                "stopRequested": bool(self.baritone.controller.stop_requested),
                "automationEnabled": bool(self.baritone.controller.automation_enabled),
                "windowFocused": focused,
            },
            "baritone": self.baritone.active_command_state(),
            "operatorLoop": self._operator_loop_snapshot(state),
            "bridge": {
                "available": bridge_status is not None,
                "status": bridge_status if isinstance(bridge_status, dict) else {},
            },
        }
        out = self._live_status_path()
        _atomic_write_json(out, payload)

    def _append_debug_learning(self, event: dict[str, Any]) -> None:
        try:
            path = Path(self.config.planner.debug_learning_log_file).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **event,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except Exception as exc:
            log.debug("Failed to append debug learning event: %s", exc)

    def _clear_optional_item_retry_state(self, item_id: str, state: AgentState | None = None) -> None:
        self._optional_item_failures.pop(item_id, None)
        self._optional_item_cooldowns.pop(item_id, None)
        if state is not None:
            state.optional_item_failures.pop(item_id, None)
            state.optional_item_cooldowns.pop(item_id, None)

    def _log_quest_item_acquire_diagnostic(
        self,
        *,
        item_id: str,
        required_count: int,
        observed_count: int,
        optional: bool,
        scheduled_reason: str,
        current_substage: str,
        slice_success_boolean: bool,
        stage_hint: str = "",
        action_name: str = "",
        action_category: str = "quest_item_acquire",
        local_housekeeping_boolean: bool = False,
        state: AgentState | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "quest_item_acquire_diagnostic",
            "actionName": action_name or f"quest_item_acquire {item_id} x{required_count}",
            "actionCategory": action_category,
            "scheduledReason": scheduled_reason,
            "currentStage": stage_hint or "unknown",
            "currentSubstage": current_substage,
            "sliceSuccessBoolean": bool(slice_success_boolean),
            "observedCount": int(observed_count),
            "requiredCount": int(required_count),
            "optional": bool(optional),
            "taskItem": item_id,
            "taskType": "quest_item_acquire",
            "localHousekeepingBoolean": bool(local_housekeeping_boolean),
        }
        if state is not None and state.current_goal_id:
            payload["goalId"] = state.current_goal_id
        log.info("quest_item_acquire_diagnostic %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))

    def _log_base_protection_decision(
        self,
        *,
        action_name: str,
        action_class: str,
        route_id: str,
        relocation_required: bool,
        relocation_reason: str,
        protected_rule: str,
        local_housekeeping_boolean: bool,
        stage_hint: str = "",
    ) -> None:
        payload = {
            "event": "base_protection_decision",
            "actionName": str(action_name).strip(),
            "actionClass": str(action_class).strip() or "unknown",
            "routeId": str(route_id).strip(),
            "relocationRequired": bool(relocation_required),
            "relocationReason": str(relocation_reason).strip() or "unknown",
            "protectedRule": str(protected_rule).strip() or "unknown",
            "localHousekeepingBoolean": bool(local_housekeeping_boolean),
            "stageHint": str(stage_hint).strip() or "unknown",
        }
        log.info("base_protection_decision %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))

    @staticmethod
    def _task_display_text(task: dict[str, Any] | None) -> str:
        if not isinstance(task, dict):
            return ""
        task_type = str(task.get("type", "")).strip()
        if not task_type:
            return ""
        if task_type in {"quest_item_acquire", "craft_item", "craft_with_dependencies"}:
            item = str(task.get("item", "")).strip()
            count = int(task.get("count", 1) or 1)
            return f"{task_type} {item} x{count}".strip()
        if task_type == "baritone_command":
            return str(task.get("command", "")).strip()
        if task_type == "run_module":
            return f"run_module {str(task.get('name', '')).strip()}".strip()
        return task_type

    def _operator_loop_snapshot(self, state: AgentState) -> dict[str, Any]:
        now = time.time()
        deferred_tasks: list[dict[str, Any]] = []
        for goal_id, until in sorted(dict(getattr(state, "goal_defer_until", {})).items()):
            try:
                until_f = float(until)
            except Exception:
                continue
            if until_f <= now:
                continue
            deferred_tasks.append(
                {
                    "kind": "goal",
                    "id": str(goal_id),
                    "remainingSeconds": round(max(0.0, until_f - now), 2),
                }
            )
        for item_id, until in sorted(dict(getattr(state, "optional_item_cooldowns", {})).items()):
            try:
                until_f = float(until)
            except Exception:
                continue
            if until_f <= now:
                continue
            deferred_tasks.append(
                {
                    "kind": "optional_item",
                    "id": str(item_id),
                    "remainingSeconds": round(max(0.0, until_f - now), 2),
                }
            )
        payload = {
            "currentTask": "",
            "previousCompletedTask": "",
            "nextSelectedTask": "",
            "nextTaskReason": "",
            "decision": "",
            "currentGoalId": str(state.current_goal_id or "").strip(),
            "lastBlocker": str(state.last_error or "").strip(),
            "deferredTasks": deferred_tasks[:16],
        }
        for key, value in dict(self._operator_loop_state).items():
            payload[key] = value
        if not payload.get("lastBlocker"):
            payload["lastBlocker"] = str(state.last_error or "").strip()
        payload["currentGoalId"] = str(state.current_goal_id or payload.get("currentGoalId") or "").strip()
        payload["deferredTasks"] = deferred_tasks[:16]
        return payload

    def _set_operator_loop_state(self, state: AgentState, **fields: Any) -> None:
        current = dict(self._operator_loop_state)
        for raw_key, raw_value in fields.items():
            key = str(raw_key).strip()
            if not key:
                continue
            value = raw_value
            if key == "task":
                current["currentTask"] = self._task_display_text(value if isinstance(value, dict) else None)
                continue
            if key == "previous_task":
                current["previousCompletedTask"] = self._task_display_text(value if isinstance(value, dict) else None)
                continue
            if key == "next_task":
                current["nextSelectedTask"] = self._task_display_text(value if isinstance(value, dict) else None)
                continue
            if key == "last_blocker":
                current["lastBlocker"] = str(value or "").strip()
                continue
            if value is None:
                current.pop(key, None)
                continue
            current[key] = value
        current["currentGoalId"] = str(state.current_goal_id or current.get("currentGoalId") or "").strip()
        if not str(current.get("lastBlocker", "")).strip():
            current["lastBlocker"] = str(state.last_error or "").strip()
        self._operator_loop_state = current

    def _log_operator_loop_decision(
        self,
        state: AgentState,
        *,
        decision: str,
        current_task: dict[str, Any] | None = None,
        previous_task: dict[str, Any] | None = None,
        next_task: dict[str, Any] | None = None,
        next_task_reason: str = "",
        last_blocker: str = "",
    ) -> None:
        self._set_operator_loop_state(
            state,
            decision=str(decision).strip(),
            task=current_task,
            previous_task=previous_task,
            next_task=next_task,
            nextTaskReason=str(next_task_reason).strip(),
            last_blocker=str(last_blocker).strip(),
        )
        payload = self._operator_loop_snapshot(state)
        payload["event"] = "operator_loop_decision"
        log.info("operator_loop_decision %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))

    def _backoff_delay(self, attempt: int) -> float:
        p = self.config.planner
        base = p.retry_backoff_seconds * (p.retry_backoff_multiplier ** max(0, attempt - 1))
        jitter = random.uniform(0.0, max(0.0, p.retry_jitter_seconds))
        return max(0.0, base + jitter)

    def _sleep_interruptible(self, seconds: float, poll_seconds: float = 0.2) -> None:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self.baritone.controller.stop_requested:
                raise RuntimeError("Stop requested")
            self.baritone.sync_manual_pause_with_controller()
            self.baritone.controller.wait_if_paused()
            self.baritone.sync_manual_pause_with_controller()
            remaining = max(0.0, deadline - time.time())
            time.sleep(min(max(0.05, poll_seconds), remaining))

    def _wait_for_window_focus(
        self,
        reason: str,
        *,
        poll_seconds: float = 0.25,
        timeout_seconds: float = 8.0,
    ) -> None:
        if self.config.control.input_backend.strip().lower() == "noop":
            return
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while True:
            if self.baritone.controller.stop_requested:
                raise RuntimeError("Stop requested")
            self.baritone.sync_manual_pause_with_controller()
            self.baritone.controller.wait_if_paused()
            self.baritone.sync_manual_pause_with_controller()
            try:
                focused = self.baritone.controller.is_window_focused()
            except Exception:
                focused = False
            if focused:
                return
            if time.time() >= deadline:
                raise RuntimeError(f"Minecraft window focus not available for {reason}")
            if getattr(self.config.control, "auto_refocus_enabled", False):
                try:
                    self.baritone.controller.ensure_window_focus()
                except Exception as exc:
                    raise RuntimeError(f"Minecraft window focus not available for {reason}: {exc}") from exc
                try:
                    if self.baritone.controller.is_window_focused():
                        return
                except Exception as exc:
                    raise RuntimeError(
                        f"Minecraft window focus not available for {reason}: unable to verify foreground window ({exc})"
                    ) from exc
                raise RuntimeError(
                    f"Minecraft window focus not available for {reason}: target window did not become foreground"
                )
            now = time.time()
            if now - self._window_focus_wait_last_logged_at >= 3.0:
                log.warning("Waiting for Minecraft window focus before %s", reason)
                self._window_focus_wait_last_logged_at = now
            time.sleep(max(0.05, poll_seconds))

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        token = str(value).strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _parse_optional_bool(raw: str | None) -> bool | None:
        if raw is None:
            return None
        token = str(raw).strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return None

    def _runtime_watchdog_settings(self) -> tuple[bool, float, float]:
        planner_cfg = self.config.planner
        default_enabled = True
        default_max_runtime_seconds = 4.0 * 60.0 * 60.0
        default_checkpoint_seconds = max(30.0, float(self.config.planner.tick_seconds))

        enabled = self._coerce_bool(getattr(planner_cfg, "runtime_watchdog_enabled", default_enabled), default_enabled)
        max_runtime_seconds = self._coerce_float(
            getattr(planner_cfg, "max_continuous_runtime_seconds", default_max_runtime_seconds),
            default_max_runtime_seconds,
        )
        checkpoint_seconds = self._coerce_float(
            getattr(planner_cfg, "runtime_idle_checkpoint_seconds", default_checkpoint_seconds),
            default_checkpoint_seconds,
        )

        env_enabled_raw = os.getenv("SB4_RUNTIME_WATCHDOG_ENABLED")
        env_enabled = self._parse_optional_bool(env_enabled_raw)
        if env_enabled_raw is not None:
            if env_enabled is None:
                log.warning(
                    "Ignoring invalid SB4_RUNTIME_WATCHDOG_ENABLED value '%s' (expected true/false).",
                    env_enabled_raw,
                )
            else:
                enabled = env_enabled

        env_max_runtime_raw = os.getenv("SB4_MAX_CONTINUOUS_RUNTIME_SECONDS")
        if env_max_runtime_raw is not None and str(env_max_runtime_raw).strip():
            try:
                max_runtime_seconds = float(env_max_runtime_raw)
            except Exception:
                log.warning(
                    "Ignoring invalid SB4_MAX_CONTINUOUS_RUNTIME_SECONDS value '%s' (expected float seconds).",
                    env_max_runtime_raw,
                )

        env_checkpoint_raw = os.getenv("SB4_RUNTIME_IDLE_CHECKPOINT_SECONDS")
        if env_checkpoint_raw is not None and str(env_checkpoint_raw).strip():
            try:
                checkpoint_seconds = float(env_checkpoint_raw)
            except Exception:
                log.warning(
                    "Ignoring invalid SB4_RUNTIME_IDLE_CHECKPOINT_SECONDS value '%s' (expected float seconds).",
                    env_checkpoint_raw,
                )

        max_runtime_seconds = max(0.0, max_runtime_seconds)
        checkpoint_seconds = max(5.0, checkpoint_seconds)
        if max_runtime_seconds <= 0.0:
            enabled = False
        return enabled, max_runtime_seconds, checkpoint_seconds

    def _dead_respawn_hard_stop_enabled(self) -> bool:
        baritone_cfg = self.config.baritone
        enabled = True
        for attr in (
            "hard_stop_on_dead_or_respawn",
            "hard_stop_on_dead_respawn_screen",
            "hard_stop_on_death_screen",
        ):
            if hasattr(baritone_cfg, attr):
                enabled = self._coerce_bool(getattr(baritone_cfg, attr), True)
                break

        env_raw = os.getenv("SB4_HARD_STOP_ON_DEAD_RESPAWN")
        env_value = self._parse_optional_bool(env_raw)
        if env_raw is not None:
            if env_value is None:
                log.warning("Ignoring invalid SB4_HARD_STOP_ON_DEAD_RESPAWN value '%s' (expected true/false).", env_raw)
            else:
                enabled = env_value
        return enabled

    @staticmethod
    def _bridge_dead_respawn_reason_from_status(status: dict[str, Any] | None) -> str:
        if not isinstance(status, dict):
            return ""

        bool_keys = (
            "deadOrDying",
            "deathScreen",
            "isDeathScreen",
            "respawnScreen",
            "isRespawnScreen",
            "inDeathScreen",
            "inRespawnScreen",
        )
        for key in bool_keys:
            if AgentRuntime._coerce_bool(status.get(key, False), False):
                return f"{key}=true"

        in_world = AgentRuntime._coerce_bool(status.get("inWorld", True), True)
        raw_health = status.get("health")
        if in_world and raw_health is not None:
            try:
                if float(raw_health) <= 0.0:
                    return f"health={float(raw_health):.1f}"
            except Exception:
                pass

        text_keys = (
            "screen",
            "currentScreen",
            "screenName",
            "screenClass",
            "uiScreen",
            "activeScreen",
        )
        for key in text_keys:
            raw = status.get(key)
            text = str(raw).strip().lower()
            if not text:
                continue
            if "death" in text or "respawn" in text:
                return f"{key}={str(raw).strip()}"

        for parent in ("screen", "ui", "screenInfo"):
            nested = status.get(parent)
            if not isinstance(nested, dict):
                continue
            for key in ("name", "class", "type", "id", "title"):
                raw = nested.get(key)
                text = str(raw).strip().lower()
                if not text:
                    continue
                if "death" in text or "respawn" in text:
                    return f"{parent}.{key}={str(raw).strip()}"
        return ""

    def _bridge_dead_respawn_stop_reason(self) -> str:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return ""
        status = self.baritone.read_bridge_status()
        if not isinstance(status, dict):
            return ""
        if not self.baritone._status_is_fresh(status) and not self.baritone._status_has_recent_ack(status):
            return ""
        reason = self._bridge_dead_respawn_reason_from_status(status)
        if not reason:
            return ""
        player_name = str(status.get("playerName", "")).strip()
        age_seconds = self.baritone._status_age_seconds(status)
        return (
            f"{reason}; status_age_seconds={age_seconds:.2f}"
            f"{f'; player={player_name}' if player_name else ''}"
        )

    def _enter_safe_pause(
        self,
        state: AgentState,
        state_path: Path,
        *,
        goal_id: str | None,
        error: str,
        message: str,
        phase: str = "safe_paused",
    ) -> None:
        state.safe_paused = True
        state.current_goal_id = None
        state.last_error = error
        _save_state(state_path, state)
        self._write_live_status(
            state,
            phase,
            goal_id=goal_id,
            error=error,
            message=message,
        )

    def _maybe_hard_stop_for_dead_respawn(
        self,
        state: AgentState,
        state_path: Path,
        *,
        goal_id: str | None = None,
        enabled: bool = True,
    ) -> bool:
        if not enabled:
            return False
        reason = self._bridge_dead_respawn_stop_reason()
        if not reason:
            return False
        try:
            self.baritone.pause_pathing()
        except Exception as exc:
            log.warning("Failed to send pause for dead/respawn hard stop: %s", exc)
        error = f"hard_stop_dead_respawn: {reason}"
        self._enter_safe_pause(
            state,
            state_path,
            goal_id=goal_id,
            error=error,
            message="bridge indicates dead/respawn screen; automation paused",
            phase="hard_stop_dead_respawn",
        )
        log.error("Hard stop triggered by bridge dead/respawn signal: %s", reason)
        return True

    @staticmethod
    def _task_command_body(task: dict[str, Any]) -> str:
        if str(task.get("type", "")).strip() != "baritone_command":
            return ""
        return str(task.get("command", "")).strip()

    def _task_considers_player_distance(self, task: dict[str, Any]) -> bool:
        if str(task.get("type", "")).strip() == "build_tunnel":
            return True
        cmd = self._task_command_body(task)
        if not cmd:
            return False
        return self.baritone.is_long_running_command(cmd)

    @staticmethod
    def _task_requires_bridge(task: dict[str, Any]) -> bool:
        task_type = str(task.get("type", "")).strip()
        if not task_type:
            return False
        return task_type not in {"note", "set_flag", "sleep_seconds"}

    def _get_recipe_index(self) -> RecipeIndex | None:
        if self._recipe_index is not None:
            return self._recipe_index
        instance_path = self.config.planner.instance_path.strip()
        if not instance_path:
            return None
        try:
            self._recipe_index = RecipeIndex.from_instance(instance_path)
            log.info("Loaded recipe index: %d recipes", len(self._recipe_index.recipes))
            return self._recipe_index
        except Exception as exc:
            log.warning("Failed to build recipe index: %s", exc)
            return None

    def _get_strategy_memory(self) -> StrategyMemory:
        if self._strategy_memory is None:
            self._strategy_memory = StrategyMemory(self.config.planner.strategy_memory_file)
        return self._strategy_memory

    def _get_web_researcher(self) -> WebResearcher | None:
        if not self.config.planner.web_research_enabled:
            return None
        if self._web_research_disabled:
            return None
        if self._web_researcher is not None:
            return self._web_researcher
        try:
            self._web_researcher = WebResearcher(
                cache_file=self.config.planner.web_research_cache_file,
                ttl_minutes=self.config.planner.web_research_ttl_minutes,
                timeout_seconds=self.config.planner.web_research_timeout_seconds,
                max_snippets=self.config.planner.web_research_max_snippets,
            )
            return self._web_researcher
        except Exception as exc:
            self._web_research_disabled = True
            log.warning("Disabling web research due to initialization failure: %s", exc)
            return None

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in items:
            token = str(raw).strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    def _preferred_strategies_for_item(self, item_id: str) -> tuple[list[str], dict[str, Any]]:
        hints: dict[str, Any] = {"keywords": [], "sources": [], "snippets": [], "error": ""}
        researcher = self._get_web_researcher()
        if researcher is None:
            return [], hints

        try:
            hints = researcher.hints_for_item(item_id)
        except Exception as exc:
            msg = str(exc)
            hints = {"keywords": [], "sources": [], "snippets": [], "error": msg}
            self._append_debug_learning(
                {
                    "event": "web_research_error",
                    "itemId": item_id,
                    "error": msg,
                }
            )
            return [], hints

        keywords = [str(v).strip().lower() for v in hints.get("keywords", []) if str(v).strip()]
        preferred: list[str] = []
        if any(k in keywords for k in {"craft", "smelt", "machine", "hammer", "sieve"}):
            preferred.extend(["craft_tree", "direct_craft"])
        if any(k in keywords for k in {"mine", "loot", "farm"}):
            preferred.append("gather")
        preferred = self._dedupe_preserve_order(preferred)

        self._append_debug_learning(
            {
                "event": "web_research_hints",
                "itemId": item_id,
                "keywords": keywords,
                "sources": list(hints.get("sources", []))[:3],
                "cached": bool(hints.get("cached", False)),
                "error": str(hints.get("error", "")),
            }
        )
        return preferred, hints

    @staticmethod
    def _bridge_status_inventory_count(status: dict[str, Any], item_id: str) -> int:
        target = str(item_id).strip().lower()
        if not target:
            return -1
        inventory = status.get("inventory")
        if not isinstance(inventory, list):
            return -1
        total = 0
        for entry in inventory:
            if not isinstance(entry, dict):
                continue
            current = str(entry.get("itemId", "")).strip().lower()
            if current != target:
                continue
            total += max(0, int(entry.get("count", 0)))
        return total

    def _bridge_item_count(self, item_id: str, status: dict[str, Any] | None = None) -> int:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return -1
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return -1
        return self._bridge_status_inventory_count(live, item_id)

    def _knowledge_base(self) -> StoneBlockKnowledgeBase | None:
        if self._stoneblock_knowledge is not None:
            return self._stoneblock_knowledge
        try:
            self._stoneblock_knowledge = StoneBlockKnowledgeBase(self.config)
        except Exception as exc:
            log.warning("Failed to load StoneBlock knowledge cache: %s", exc)
            self._stoneblock_knowledge = None
        return self._stoneblock_knowledge

    def _stoneblock_route_context(
        self,
        *,
        target_item: str,
        source_item: str,
        action_name: str,
        before_source_count: int,
        before_target_count: int,
        target_total: int,
        stage_hint_before: str,
    ) -> dict[str, Any]:
        knowledge = self._knowledge_base()
        route_payload: dict[str, Any] = {}
        if knowledge is not None:
            try:
                route_info = knowledge.get_item_route(target_item)
                for route in route_info.get("routes", []):
                    if isinstance(route, dict):
                        route_payload = dict(route)
                        break
            except Exception as exc:
                log.warning("Failed to resolve StoneBlock route for %s: %s", target_item, exc)
        raw_action_class = str(route_payload.get("actionClass", "")).strip()
        action_class = self.baritone._normalize_action_class(raw_action_class) or "local_in_place"
        return {
            "actionName": action_name,
            "actionClass": action_class,
            "successCriteriaUsed": self.baritone._success_criteria_for_action_class(action_class),
            "routeId": str(route_payload.get("routeId", "")).strip(),
            "routeMechanism": str(route_payload.get("mechanism", "")).strip(),
            "sourceItem": source_item,
            "targetItem": target_item,
            "beforeSourceCount": int(before_source_count),
            "beforeTargetCount": int(before_target_count),
            "targetTotal": int(target_total),
            "stageHintBefore": str(stage_hint_before).strip(),
            "knowledgeEvidence": list(route_payload.get("evidence", []))
            if isinstance(route_payload.get("evidence"), list)
            else [],
        }

    def _bridge_keyword_count(self, keywords: list[str], status: dict[str, Any] | None = None) -> int:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return -1
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return -1
        inventory = live.get("inventory")
        if not isinstance(inventory, list):
            return -1
        probes = [str(v).strip().lower() for v in keywords if str(v).strip()]
        if not probes:
            return -1
        total = 0
        for entry in inventory:
            if not isinstance(entry, dict):
                continue
            current = str(entry.get("itemId", "")).strip().lower()
            if not current:
                continue
            if any(probe in current for probe in probes):
                total += max(0, int(entry.get("count", 0)))
        return total

    @staticmethod
    def _bridge_inventory_slots_for_item(status: dict[str, Any] | None, item_id: str) -> list[int]:
        if not isinstance(status, dict):
            return []
        target = str(item_id).strip().lower()
        if not target:
            return []
        inventory = status.get("inventory")
        if not isinstance(inventory, list):
            return []
        slots: list[int] = []
        for entry in inventory:
            if not isinstance(entry, dict):
                continue
            current = str(entry.get("itemId", "")).strip().lower()
            if current != target:
                continue
            count = int(entry.get("count", 0))
            slot = int(entry.get("slot", 0))
            if count <= 0 or slot < 1 or slot > 36:
                continue
            slots.append(slot)
        return slots

    def _ensure_item_hotbar_slot(
        self,
        item_id: str,
        *,
        preferred_hotbar_slot: int = 1,
        status: dict[str, Any] | None = None,
    ) -> int | None:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return None
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return None
        target = str(item_id).strip().lower()
        if not target:
            return None

        slots = self._bridge_inventory_slots_for_item(live, target)
        for slot in slots:
            if 1 <= slot <= 9:
                return slot

        non_hotbar_source = next((slot for slot in slots if slot >= 10), None)
        if non_hotbar_source is None:
            return None

        target_slot = max(1, min(9, int(preferred_hotbar_slot)))
        swapped = self.baritone._bridge_swap_to_hotbar(non_hotbar_source, target_slot)
        if not swapped:
            return None
        refreshed = self.baritone.read_bridge_status()
        if refreshed and self.baritone._status_is_fresh(refreshed):
            refreshed_slots = self._bridge_inventory_slots_for_item(refreshed, target)
            for slot in refreshed_slots:
                if 1 <= slot <= 9:
                    return slot
        return target_slot

    def _ensure_any_item_hotbar_slot(
        self,
        item_ids: list[str] | tuple[str, ...] | set[str],
        *,
        preferred_hotbar_slot: int = 1,
        status: dict[str, Any] | None = None,
    ) -> tuple[str, int] | None:
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return None

        for raw_item_id in item_ids:
            item_id = str(raw_item_id).strip().lower()
            if not item_id:
                continue
            slot = self._ensure_item_hotbar_slot(item_id, preferred_hotbar_slot=preferred_hotbar_slot, status=live)
            if slot is not None:
                return item_id, slot
        return None

    @staticmethod
    def _stoneblock_hammer_chain_source_item(item_id: str) -> str:
        token = str(item_id).strip().lower()
        mapping = {
            "minecraft:gravel": "minecraft:cobblestone",
            "minecraft:dirt": "minecraft:gravel",
            "minecraft:sand": "minecraft:dirt",
            "ftbstuff:dust": "minecraft:sand",
        }
        return mapping.get(token, "")

    def _acquire_stoneblock_hammer_chain_requirement(self, item_id: str, count: int) -> bool:
        target_item = str(item_id).strip().lower()
        source_item = self._stoneblock_hammer_chain_source_item(target_item)
        if not source_item:
            return False
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return False

        status = self.baritone.read_bridge_status()
        if not status or not self.baritone._status_is_fresh(status):
            return False

        hammer_slot = self._ensure_any_item_hotbar_slot(
            [
                "ftbstuff:diamond_hammer",
                "ftbstuff:iron_hammer",
                "ftbstuff:stone_hammer",
            ],
            preferred_hotbar_slot=5,
            status=status,
        )
        if hammer_slot is None:
            return False

        source_count = self._bridge_item_count(source_item, status=status)
        if source_count == 0:
            return False

        slot = hammer_slot[1]

        before_target = self._bridge_item_count(target_item, status=status)
        target_total = before_target + max(1, int(count)) if before_target >= 0 else -1
        attempts = max(1, min(4, int((max(1, int(count)) + 31) / 32)))
        stage_hint_before = str(status.get("stoneblockStageHint", "")).strip()
        action_name = f"mine {source_item}"
        action_context = self._stoneblock_route_context(
            target_item=target_item,
            source_item=source_item,
            action_name=action_name,
            before_source_count=source_count,
            before_target_count=before_target,
            target_total=target_total,
            stage_hint_before=stage_hint_before,
        )
        action_class = str(action_context.get("actionClass", "")).strip() or "local_in_place"
        route_id = str(action_context.get("routeId", "")).strip()
        inside_base_zone = self._inside_base_protection_zone(status=status)

        if inside_base_zone and action_class == "local_in_place":
            self._log_base_protection_decision(
                action_name=action_name,
                action_class=action_class,
                route_id=route_id,
                relocation_required=False,
                relocation_reason="local_in_place_allowed_in_base_zone",
                protected_rule="base_protection_outside_required_for_destructive_breaking_only",
                local_housekeeping_boolean=True,
                stage_hint=stage_hint_before,
            )
            if self._attempt_in_place_stoneblock_hammer_conversion(
                source_item=source_item,
                target_item=target_item,
                target_total=target_total,
                hammer_slot=slot,
                attempts=attempts,
                status=status,
                before_target=before_target,
            ):
                return True
            self._log_base_protection_decision(
                action_name=action_name,
                action_class=action_class,
                route_id=route_id,
                relocation_required=False,
                relocation_reason="local_in_place_attempt_failed_no_relocation_fallback",
                protected_rule="base_protection_outside_required_for_destructive_breaking_only",
                local_housekeeping_boolean=True,
                stage_hint=stage_hint_before,
            )
            return False

        if inside_base_zone:
            self._log_base_protection_decision(
                action_name=action_name,
                action_class=action_class,
                route_id=route_id,
                relocation_required=True,
                relocation_reason="protected_base_zone_requires_outside_execution",
                protected_rule="base_protection_outside_required_for_destructive_breaking",
                local_housekeeping_boolean=action_class == "local_in_place",
                stage_hint=stage_hint_before,
            )

        try:
            self._wait_for_window_focus(f"StoneBlock hammer slot switch for {target_item}")
            self.baritone.controller.press(str(slot))
        except Exception as exc:
            log.warning("Failed to select StoneBlock hammer slot %d: %s", slot, exc)
            return False

        self._assert_outside_base_protection(f"stoneblock hammering for {target_item}", status=status)

        for _ in range(attempts):
            self.baritone.command(f"mine {source_item}")
            ok = self.baritone.wait_for_idle(timeout_seconds=90.0, poll_seconds=self.config.baritone.poll_seconds)
            refreshed = self.baritone.read_bridge_status()
            after_target = self._bridge_item_count(target_item, status=refreshed)
            if target_total >= 0 and after_target >= target_total:
                return True
            if before_target >= 0 and after_target > before_target:
                return True
            if before_target < 0 and ok:
                return True
        return False

    def _attempt_in_place_stoneblock_hammer_conversion(
        self,
        *,
        source_item: str,
        target_item: str,
        target_total: int,
        hammer_slot: int,
        attempts: int,
        status: dict[str, Any],
        before_target: int,
    ) -> bool:
        try:
            status = self._ensure_local_conversion_inventory_headroom(
                source_item=source_item,
                target_item=target_item,
                route_id=f"hammer__{source_item.replace(':', '_')}__{target_item.replace(':', '_')}",
                stage_hint=str(status.get("stoneblockStageHint", "")).strip(),
                status=status,
            )
        except Exception as exc:
            log.warning("In-place StoneBlock inventory headroom check failed for %s: %s", target_item, exc)
            return False
        source_slot = self._ensure_item_hotbar_slot(source_item, preferred_hotbar_slot=4, status=status)
        if source_slot is None:
            return False

        log.info(
            "Using in-place StoneBlock hammer conversion for %s from inventory %s inside base zone",
            target_item,
            source_item,
        )
        confirm_poll_seconds = max(0.05, min(0.25, float(self.config.baritone.poll_seconds)))
        confirm_timeout_seconds = max(0.75, confirm_poll_seconds * 6.0)
        for _ in range(max(1, attempts)):
            live = self.baritone.read_bridge_status()
            if not live or not self.baritone._status_is_fresh(live):
                return False
            before_source = self._bridge_item_count(source_item, status=live)
            before_target_live = self._bridge_item_count(target_item, status=live)
            try:
                self._wait_for_window_focus(f"in-place StoneBlock source placement for {target_item}")
                self.baritone.controller.place_torch(
                    source_slot,
                    hold_seconds=max(0.05, self.config.control.torch_click_hold_seconds),
                )
                time.sleep(0.25)
                self.baritone.controller.press(str(hammer_slot))
                log.info("Selected StoneBlock hammer slot %d for %s", hammer_slot, target_item)
            except Exception as exc:
                log.warning("In-place StoneBlock hammer setup failed for %s: %s", target_item, exc)
                return False

            after_source = before_source
            confirm_deadline = time.time() + confirm_timeout_seconds
            while time.time() < confirm_deadline:
                candidate = self.baritone.read_bridge_status()
                if candidate and self.baritone._status_is_fresh(candidate):
                    after_source = self._bridge_item_count(source_item, status=candidate)
                    if before_source < 0 or after_source < before_source:
                        break
                time.sleep(confirm_poll_seconds)
            if before_source >= 0 and after_source >= before_source:
                continue
            log.info(
                "Placed in-place StoneBlock source block for %s using %s (%d -> %d)",
                target_item,
                source_item,
                before_source,
                after_source,
            )
            stage_hint_before = str(live.get("stoneblockStageHint", "")).strip()
            action_context = self._stoneblock_route_context(
                target_item=target_item,
                source_item=source_item,
                action_name=f"local_hammer {source_item} -> {target_item}",
                before_source_count=before_source,
                before_target_count=before_target_live,
                target_total=target_total,
                stage_hint_before=stage_hint_before,
            )

            action_payload: dict[str, Any] = {
                "event": "stoneblock_housekeeping_action",
                "actionName": f"local_hammer {source_item} -> {target_item}",
                "actionCategory": "stoneblock_housekeeping",
                "actionClass": action_context.get("actionClass", "local_in_place"),
                "successCriteriaUsed": action_context.get("successCriteriaUsed", "inventory_or_stage_delta"),
                "scheduledReason": "stoneblock_in_place_hammer_conversion",
                "currentStage": stage_hint_before or "unknown",
                "currentSubstage": "in_place_hammer_conversion",
                "sliceSuccessBoolean": bool(target_total >= 0 and before_target_live >= target_total),
                "observedCount": int(before_target_live),
                "requiredCount": int(target_total),
                "sourceItem": source_item,
                "targetItem": target_item,
                "localHousekeepingBoolean": True,
                "routeId": action_context.get("routeId", ""),
            }
            log.info("stoneblock_housekeeping_action %s", json.dumps(action_payload, ensure_ascii=True, sort_keys=True, default=str))
            self.baritone.set_planner_task_context(action_context)
            try:
                self._wait_for_window_focus(f"in-place StoneBlock hammer strike for {target_item}")
                self.baritone.controller.left_click(
                    hold_seconds=max(0.05, self.config.control.torch_click_hold_seconds)
                )
                log.info("Performed in-place StoneBlock hammer hit for %s", target_item)
                evaluation: dict[str, Any] | None = None
                confirm_deadline = time.time() + max(1.25, confirm_poll_seconds * 24.0)
                while time.time() < confirm_deadline:
                    refreshed = self.baritone.read_bridge_status()
                    if refreshed and self.baritone._status_is_fresh(refreshed):
                        evaluation = self.baritone.evaluate_planner_task_context(
                            refreshed,
                            movement_observed=False,
                            pathing_observed=False,
                        )
                        if bool(evaluation.get("finalSuccess", False)):
                            after_target = self._bridge_item_count(target_item, status=refreshed)
                            log.info(
                                "In-place StoneBlock hammer conversion succeeded for %s (%d -> %d)",
                                target_item,
                                before_target_live,
                                after_target,
                            )
                            return True
                    time.sleep(confirm_poll_seconds)
            finally:
                self.baritone.clear_planner_task_context()
        return False

    def _ensure_local_conversion_inventory_headroom(
        self,
        *,
        source_item: str,
        target_item: str,
        route_id: str,
        stage_hint: str,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return status

        free_slots_before = self.baritone._status_free_inventory_slots(live)
        target_count_before = self._bridge_item_count(target_item, status=live)
        if target_count_before > 0 or free_slots_before > 0:
            return live

        payload = {
            "event": "stoneblock_inventory_headroom",
            "actionName": f"local_hammer {source_item} -> {target_item}",
            "actionClass": "local_in_place",
            "routeId": route_id,
            "stageHint": stage_hint or "unknown",
            "freeSlotsBefore": int(free_slots_before),
            "targetCountBefore": int(target_count_before),
            "storageAction": "store_inventory_in_chest",
        }
        log.info("stoneblock_inventory_headroom %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))

        store_error = ""
        try:
            self._store_inventory_in_chest(radius=8, max_stacks=24, include_hotbar=False, optional=False)
        except Exception as exc:
            store_error = str(exc)
            log.warning("Inventory storage attempt failed before local StoneBlock conversion for %s: %s", target_item, exc)

        refreshed = self.baritone.read_bridge_status()
        if refreshed and self.baritone._status_is_fresh(refreshed):
            free_slots_after = self.baritone._status_free_inventory_slots(refreshed)
            target_count_after = self._bridge_item_count(target_item, status=refreshed)
            result_payload = {
                **payload,
                "freeSlotsAfter": int(free_slots_after),
                "targetCountAfter": int(target_count_after),
                "storageSuccess": bool(target_count_after > 0 or free_slots_after > 0),
                "storageError": store_error,
            }
            log.info("stoneblock_inventory_headroom %s", json.dumps(result_payload, ensure_ascii=True, sort_keys=True, default=str))
            if target_count_after > 0 or free_slots_after > 0:
                return refreshed

        if self.config.baritone.inventory_cleanup_enabled and refreshed and self.baritone._status_is_fresh(refreshed):
            try:
                improved = bool(self.baritone.try_inventory_cleanup(refreshed, context="store_local_conversion_headroom"))
                if improved:
                    final_status = self.baritone.read_bridge_status()
                    if final_status and self.baritone._status_is_fresh(final_status):
                        return final_status
            except Exception as exc:
                if store_error:
                    store_error = f"{store_error}; cleanup: {exc}"
                else:
                    store_error = f"cleanup: {exc}"

        raise RuntimeError(
            f"inventory full before local StoneBlock conversion for {target_item}; "
            f"free_slots={free_slots_before} target_count={target_count_before} "
            f"storage_error={store_error or '<none>'}"
        )

    def _place_storage_chest(self, count: int, *, optional: bool = True) -> None:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            if optional:
                return
            raise RuntimeError("place_storage_chest requires bridge_file transport")

        required = max(1, int(count))
        target_item = "minecraft:chest"
        before_total = self._bridge_item_count(target_item)
        if before_total >= 0 and before_total < required:
            missing = required - before_total
            try:
                try:
                    self._craft_with_dependencies(target_item, missing, max_depth=6)
                except Exception:
                    self._craft_item(target_item, missing, timeout=90.0)
            except Exception as exc:
                if optional:
                    log.warning("Optional storage chest craft skipped: %s", exc)
                    return
                raise
        elif before_total < 0:
            try:
                self._craft_item(target_item, required, timeout=90.0)
            except Exception as exc:
                if not optional:
                    raise
                log.warning("Optional storage chest craft skipped (inventory unknown): %s", exc)
                return

        placed = 0
        for _ in range(required):
            status = self.baritone.read_bridge_status()
            if not status or not self.baritone._status_is_fresh(status):
                if optional:
                    return
                raise RuntimeError("bridge status unavailable while placing chest")
            slot = self._ensure_item_hotbar_slot(target_item, preferred_hotbar_slot=9, status=status)
            if slot is None:
                if optional:
                    return
                raise RuntimeError("no chest item available in inventory to place")

            before = self._bridge_item_count(target_item, status=status)
            self._wait_for_window_focus("storage chest placement")
            self.baritone.controller.place_torch(slot, hold_seconds=max(0.05, self.config.control.torch_click_hold_seconds))
            time.sleep(0.2)
            after = self._bridge_item_count(target_item)
            if before >= 0 and after >= 0 and after < before:
                placed += 1

        if placed <= 0 and not optional:
            raise RuntimeError("failed to place storage chest")

    def _store_inventory_in_chest(
        self,
        *,
        radius: int,
        max_stacks: int,
        include_hotbar: bool,
        optional: bool,
    ) -> None:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            if optional:
                return
            raise RuntimeError("store_inventory_in_chest requires bridge_file transport")

        live = self.baritone.read_bridge_status()
        free_before = -1
        if live and self.baritone._status_is_fresh(live):
            free_before = self.baritone._status_free_inventory_slots(live)
            version = str(live.get("bridgeVersion", "")).strip()
            version_triplet = self.baritone._parse_version_triplet(version)
            required = (0, 1, 6)
            if version_triplet < required:
                self._store_to_chest_bridge_supported = False
                if not self._warned_store_to_chest_bridge_version:
                    log.warning(
                        "Bridge version '%s' does not support bridge.store_to_nearby_chest (requires >=0.1.6). "
                        "Restart Minecraft after deploying the new bridge jar.",
                        version or "<unknown>",
                    )
                    self._warned_store_to_chest_bridge_version = True
                if optional:
                    return
                raise RuntimeError(
                    "bridge.store_to_nearby_chest unsupported by active bridge runtime "
                    f"(bridgeVersion={version or '<unknown>'}, required>=0.1.6)"
                )
            if version_triplet >= required and self._store_to_chest_bridge_supported is False:
                self._store_to_chest_bridge_supported = None
                self._warned_store_to_chest_bridge_version = False

        if self._store_to_chest_bridge_supported is False:
            if optional:
                return
            raise RuntimeError("bridge.store_to_nearby_chest is marked unsupported by active bridge runtime")

        r = max(2, min(12, int(radius)))
        cap = max(1, min(36, int(max_stacks)))
        hotbar_flag = 1 if include_hotbar else 0
        cmd = f"bridge.store_to_nearby_chest {r} {cap} {hotbar_flag}"

        if self._base_anchor_pos is not None and not self._inside_base_protection_zone(status=live):
            ax = int(round(self._base_anchor_pos[0]))
            ay = int(round(self._base_anchor_pos[1]))
            az = int(round(self._base_anchor_pos[2]))
            self.baritone.command(f"goto {ax} {ay} {az}")
            self.baritone.wait_for_idle(timeout_seconds=45.0, poll_seconds=self.config.baritone.poll_seconds)

        def _run_store_command() -> None:
            self.baritone.command(cmd)
            ok = self.baritone.wait_for_idle(timeout_seconds=20.0, poll_seconds=self.config.baritone.poll_seconds)
            if not ok:
                status = self.baritone.read_bridge_status() or {}
                result = str(status.get("lastCommandResult", "")).strip().lower()
                err = str(status.get("lastCommandError", "")).strip()
                if result in {"rejected", "error"}:
                    raise RuntimeError(err or result)
                raise RuntimeError("bridge.store_to_nearby_chest did not reach idle")

        try:
            _run_store_command()
            self._store_to_chest_bridge_supported = True
        except Exception as exc:
            msg = str(exc).strip().lower()
            unknown_cmd = "unknown bridge command" in msg and "bridge.store_to_nearby_chest" in msg
            no_container = "no nearby chest container found" in msg
            if unknown_cmd:
                self._store_to_chest_bridge_supported = False
                log.warning(
                    "Bridge command bridge.store_to_nearby_chest is unsupported by active bridge version; using cleanup fallback."
                )
                if optional:
                    return
                raise RuntimeError(
                    "bridge.store_to_nearby_chest is unsupported by active bridge runtime (unknown command)"
                ) from exc
            if no_container:
                if optional:
                    return
                self._place_storage_chest(1, optional=False)
                try:
                    _run_store_command()
                    self._store_to_chest_bridge_supported = True
                except Exception as retry_exc:
                    if optional:
                        log.warning("Chest placement/store retry failed after no-container response: %s", retry_exc)
                        return
                    raise RuntimeError(f"failed storing inventory after chest placement: {retry_exc}") from retry_exc
            else:
                if optional:
                    return
                raise

        refreshed = self.baritone.read_bridge_status()
        if refreshed and self.baritone._status_is_fresh(refreshed):
            free_after = self.baritone._status_free_inventory_slots(refreshed)

    @staticmethod
    def _item_simple_name(item_id: str) -> str:
        token = str(item_id).strip().lower()
        if ":" in token:
            return token.split(":", 1)[1]
        return token

    @staticmethod
    def _quest_item_block_flag(item_id: str) -> str:
        token = str(item_id).strip().lower()
        if not token:
            return ""
        return f"quest_item_blocked::{token}"

    @staticmethod
    def _should_persist_block_flag(item_id: str) -> bool:
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

    @staticmethod
    def _quest_required_item_from_task(task: dict[str, Any]) -> str:
        if str(task.get("type", "")).strip() != "quest_item_acquire":
            return ""
        if bool(task.get("optional", False)):
            return ""
        return str(task.get("item", "")).strip().lower()

    @staticmethod
    def _extract_item_id_from_error(error_text: str) -> str:
        text = str(error_text).strip().lower()
        if not text:
            return ""
        targeted_patterns = (
            r"quest acquisition failed for\s+([a-z0-9_.-]+:[a-z0-9_./-]+)",
            r"craft failed for\s+([a-z0-9_.-]+:[a-z0-9_./-]+)",
            r"no fitting crafting recipe(?: for)?\s+([a-z0-9_.-]+:[a-z0-9_./-]+)",
        )
        for pattern in targeted_patterns:
            match = re.search(pattern, text)
            if match:
                return str(match.group(1)).strip().lower()
        match = _ITEM_ID_RE.search(text)
        if not match:
            return ""
        return str(match.group(1)).strip().lower()

    @staticmethod
    def _is_unrecoverable_quest_acquire_error(exc: Exception) -> bool:
        if isinstance(exc, UnrecoverableQuestAcquireError):
            return True
        text = str(exc).strip().lower()
        if not text:
            return False
        return text.startswith("unrecoverable quest acquisition error for ") or text.startswith(
            "unrecoverable acquisition for "
        )

    @staticmethod
    def _is_bridge_unavailable_error(exc: Exception) -> bool:
        text = str(exc).strip().lower()
        if not text:
            return False
        markers = (
            "bridge status stale",
            "bridge status not available",
            "bridge status not ok",
            "bridge status missing",
            "bridge unresponsive",
            "timed out waiting for bridge command acknowledgment",
            "player is not in-world",
            "baritone not loaded",
        )
        return any(marker in text for marker in markers)

    def _quest_acquire_error_item_id(self, exc: Exception, task: dict[str, Any]) -> str:
        if isinstance(exc, UnrecoverableQuestAcquireError):
            explicit = str(exc.item_id).strip().lower()
            if explicit:
                return explicit
        parsed = self._extract_item_id_from_error(str(exc))
        if parsed:
            return parsed
        return str(task.get("item", "")).strip().lower()

    def _mark_quest_item_block(self, state: AgentState, item_id: str, *, source: str = "") -> str:
        token = str(item_id).strip().lower()
        if not token:
            return ""
        if not self._should_persist_block_flag(token):
            return ""
        flag = self._quest_item_block_flag(token)
        if not flag:
            return ""
        state.completed_flags.add(flag)
        if source:
            self._append_debug_learning(
                {
                    "event": "quest_item_blocked",
                    "itemId": token,
                    "flag": flag,
                    "source": source,
                    "goalId": state.current_goal_id,
                }
            )
        return flag

    @staticmethod
    def _mark_goal_complete(state: AgentState, goal: Any, *, include_completion_flags: bool = True) -> None:
        goal_id = str(getattr(goal, "id", "")).strip()
        if goal_id:
            state.completed_goals.add(goal_id)
            state.goal_retries.pop(goal_id, None)
            state.goal_defer_until.pop(goal_id, None)
        if not include_completion_flags:
            return
        for raw in list(getattr(goal, "completion_flags", [])):
            flag = str(raw).strip()
            if flag:
                state.completed_flags.add(flag)

    def _goal_required_quest_items(self, goal: Any) -> list[str]:
        if not str(getattr(goal, "id", "")).strip().lower().startswith("quest_"):
            return []
        tasks = list(getattr(goal, "tasks", []))
        required: list[str] = []
        seen: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            item_id = self._quest_required_item_from_task(task)
            if not item_id:
                continue
            if item_id in seen:
                continue
            seen.add(item_id)
            required.append(item_id)
        return required

    def _goal_blocked_required_quest_items(self, goal: Any, state: AgentState) -> list[str]:
        # Do not hard-block required quest items from historical markers; defer/retry handles transient failures.
        return []

    def _goal_blocked_required_quest_item(self, goal: Any, state: AgentState) -> str:
        blocked_items = self._goal_blocked_required_quest_items(goal, state)
        if not blocked_items:
            return ""
        return blocked_items[0]

    def _craft_command_templates(self, task_templates: list[str] | None = None) -> list[str]:
        templates = [str(v).strip() for v in (task_templates or []) if str(v).strip()]
        if templates:
            return templates
        fallback_templates = [
            "craft {item}",
            "craft {item_simple}",
        ]
        if self.config.baritone.transport.strip().lower() == "bridge_file":
            status = self.baritone.read_bridge_status() or {}
            version = str(status.get("bridgeVersion", "")).strip()
            supports_bridge_craft = self.baritone._bridge_supports_craft_item(status)
            if not supports_bridge_craft:
                if not self._warned_bridge_craft_unsupported:
                    if version:
                        log.warning(
                            "bridge.craft_item requires bridge >=0.1.4 (detected %s). "
                            "Falling back to craft-command templates.",
                            version,
                        )
                    else:
                        log.warning(
                            "bridgeVersion is unknown; cannot verify bridge.craft_item support. "
                            "Falling back to craft-command templates."
                        )
                    self._warned_bridge_craft_unsupported = True
                return list(fallback_templates)
            self._warned_bridge_craft_unsupported = False
            return [
                "bridge.craft_item {item} {count}",
                "bridge.craft_item {item}",
                *fallback_templates,
            ]
        defaults: list[str] = []
        if (
            self.config.baritone.transport.strip().lower() != "bridge_file"
            and self.config.baritone.allow_legacy_proc_commands
        ):
            defaults.append("proc {item} {count}")
        defaults.extend(fallback_templates)
        return defaults

    def _render_command_template(self, template: str, item_id: str, count: int) -> str:
        item = str(item_id).strip().lower()
        simple = self._item_simple_name(item)
        rendered = (
            str(template)
            .replace("{item}", item)
            .replace("{item_id}", item)
            .replace("{item_simple}", simple)
            .replace("{count}", str(max(1, int(count))))
            .strip()
        )
        return rendered

    @staticmethod
    def _is_craft_output_pending_error(exc: Exception | str) -> bool:
        return "craft output pending" in str(exc).strip().lower()

    @staticmethod
    def _craft_target_count_reached(*, before_count: int, after_count: int, target_total: int) -> bool:
        if target_total >= 0:
            return after_count >= target_total
        if before_count >= 0:
            return after_count > before_count
        return False

    @staticmethod
    def _craft_pending_blocking_screen_label(status: dict[str, Any] | None) -> str:
        if not isinstance(status, dict) or not bool(status.get("inWorld", False)):
            return ""
        labels = [
            str(status.get("screenTitle", "")).strip(),
            str(status.get("currentScreen", "")).strip(),
            str(status.get("screenName", "")).strip(),
            str(status.get("screenClass", "")).strip(),
        ]
        combined = " ".join(label.lower() for label in labels if label)
        if not combined:
            return ""
        if "inventoryscreen" in combined or "crafting" in combined:
            for label in labels:
                if label:
                    return label
        return ""

    def _maybe_close_craft_pending_screen(
        self,
        *,
        status: dict[str, Any] | None,
        poll_seconds: float,
    ) -> str:
        screen = self._craft_pending_blocking_screen_label(status)
        if not screen:
            return ""
        controller = getattr(self.baritone, "controller", None)
        press = getattr(controller, "press", None)
        if not callable(press):
            detail = f"blocking screen detected without controller close path: {screen}"
            log.warning(detail)
            return detail
        try:
            press("esc")
        except Exception as exc:
            detail = f"failed to close blocking screen {screen}: {exc}"
            log.warning(detail)
            return detail
        time.sleep(max(0.05, poll_seconds))
        refreshed = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
        if self._craft_pending_blocking_screen_label(refreshed):
            log.warning("blocking screen remained open after close attempt: %s", screen)
            return ""
        detail = f"closed blocking in-world screen before craft retry: {screen}"
        log.warning(detail)
        return detail

    def _log_craft_pending_diagnostic(
        self,
        *,
        phase: str,
        command: str,
        target_item: str,
        before_count: int,
        after_count: int,
        target_total: int,
        attempt: int,
        status: dict[str, Any] | None,
        error_text: str = "",
        final_success: bool = False,
    ) -> None:
        live = status if isinstance(status, dict) else {}
        payload = {
            "event": "craft_pending_diagnostic",
            "phase": str(phase).strip(),
            "command": str(command).strip(),
            "targetItem": str(target_item).strip(),
            "attempt": int(attempt),
            "beforeCount": int(before_count),
            "afterCount": int(after_count),
            "targetTotal": int(target_total),
            "finalSuccess": bool(final_success),
            "screen": str(live.get("screenTitle") or live.get("currentScreen") or "").strip(),
            "lastCommandResult": str(live.get("lastCommandResult", "")).strip(),
            "lastCommandError": str(live.get("lastCommandError", "")).strip(),
            "error": str(error_text).strip(),
        }
        rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        if final_success:
            log.info("craft_pending_diagnostic %s", rendered)
        else:
            log.warning("craft_pending_diagnostic %s", rendered)

    def _wait_for_craft_pending_resolution(
        self,
        *,
        command: str,
        target_item: str,
        before_count: int,
        target_total: int,
        timeout_seconds: float,
    ) -> tuple[bool, str]:
        poll_seconds = max(0.1, min(0.5, float(self.config.baritone.poll_seconds)))
        deadline = time.time() + max(poll_seconds, float(timeout_seconds))
        attempt = 0
        screen_close_attempts = 0
        max_screen_close_attempts = 3
        while True:
            status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
            after_count = self._bridge_item_count(target_item, status=status)
            if self._craft_target_count_reached(
                before_count=before_count,
                after_count=after_count,
                target_total=target_total,
            ):
                self._log_craft_pending_diagnostic(
                    phase="materialized",
                    command=command,
                    target_item=target_item,
                    before_count=before_count,
                    after_count=after_count,
                    target_total=target_total,
                    attempt=attempt,
                    status=status,
                    final_success=True,
                )
                return True, ""
            if time.time() >= deadline:
                self._log_craft_pending_diagnostic(
                    phase="timeout",
                    command=command,
                    target_item=target_item,
                    before_count=before_count,
                    after_count=after_count,
                    target_total=target_total,
                    attempt=attempt,
                    status=status,
                    error_text="craft output pending timed out",
                )
                return False, "craft output pending timed out"
            screen_note = ""
            if screen_close_attempts < max_screen_close_attempts:
                screen_note = self._maybe_close_craft_pending_screen(status=status, poll_seconds=poll_seconds)
                if screen_note:
                    screen_close_attempts += 1
                    refreshed = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
                    if isinstance(refreshed, dict):
                        status = refreshed
                        after_count = self._bridge_item_count(target_item, status=status)
                        if self._craft_target_count_reached(
                            before_count=before_count,
                            after_count=after_count,
                            target_total=target_total,
                        ):
                            self._log_craft_pending_diagnostic(
                                phase="materialized_after_screen_close",
                                command=command,
                                target_item=target_item,
                                before_count=before_count,
                                after_count=after_count,
                                target_total=target_total,
                                attempt=attempt,
                                status=status,
                                error_text=screen_note,
                                final_success=True,
                            )
                            return True, ""
                        if self._craft_pending_blocking_screen_label(status):
                            attempt += 1
                            self._log_craft_pending_diagnostic(
                                phase="screen_still_blocking",
                                command=command,
                                target_item=target_item,
                                before_count=before_count,
                                after_count=after_count,
                                target_total=target_total,
                                attempt=attempt,
                                status=status,
                                error_text=f"craft output pending; {screen_note}",
                            )
                            continue
            attempt += 1
            self._log_craft_pending_diagnostic(
                phase="retry",
                command=command,
                target_item=target_item,
                before_count=before_count,
                after_count=after_count,
                target_total=target_total,
                attempt=attempt,
                status=status,
                error_text=f"craft output pending; {screen_note}" if screen_note else "craft output pending",
            )
            time.sleep(poll_seconds)
            try:
                self.baritone.command(command)
            except Exception as exc:
                if self._is_craft_output_pending_error(exc):
                    continue
                status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
                after_count = self._bridge_item_count(target_item, status=status)
                self._log_craft_pending_diagnostic(
                    phase="hard_reject",
                    command=command,
                    target_item=target_item,
                    before_count=before_count,
                    after_count=after_count,
                    target_total=target_total,
                    attempt=attempt,
                    status=status,
                    error_text=str(exc),
                )
                return False, str(exc)

            ok = self.baritone.wait_for_idle(
                timeout_seconds=max(3.0, float(timeout_seconds)),
                poll_seconds=self.config.baritone.poll_seconds,
            )
            status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
            after_count = self._bridge_item_count(target_item, status=status)
            if self._craft_target_count_reached(
                before_count=before_count,
                after_count=after_count,
                target_total=target_total,
            ):
                self._log_craft_pending_diagnostic(
                    phase="retried_success",
                    command=command,
                    target_item=target_item,
                    before_count=before_count,
                    after_count=after_count,
                    target_total=target_total,
                    attempt=attempt,
                    status=status,
                    final_success=True,
                )
                return True, ""
            if not ok:
                self._log_craft_pending_diagnostic(
                    phase="idle_wait_timeout",
                    command=command,
                    target_item=target_item,
                    before_count=before_count,
                    after_count=after_count,
                    target_total=target_total,
                    attempt=attempt,
                    status=status,
                    error_text="did not reach idle while waiting for pending craft output",
                )
                return False, "did not reach idle while waiting for pending craft output"

    def _craft_item(self, item_id: str, count: int, *, templates: list[str] | None = None, timeout: float = 75.0) -> None:
        target_item = str(item_id).strip().lower()
        target_count = max(1, int(count))
        if target_item in {"minecraft:oak_planks", "minecraft:planks"}:
            log_count = self._bridge_keyword_count(["_log", ":log", "stem"])
            if log_count <= 0:
                raise RuntimeError(f"craft failed for {target_item} x{target_count}: no log-like items available")
        if target_item in {"minecraft:crafting_table", "minecraft:chest"}:
            plank_need = 4 if target_item == "minecraft:crafting_table" else 8
            plank_count = self._bridge_keyword_count(["planks"])
            if plank_count >= 0 and plank_count < plank_need:
                log_count = self._bridge_keyword_count(["_log", ":log", "stem"])
                if log_count > 0:
                    for plank_item in ("minecraft:oak_planks", "minecraft:planks"):
                        try:
                            self._craft_item(plank_item, plank_need, timeout=45.0)
                            break
                        except Exception:
                            continue
        candidates: list[str] = []
        for template in self._craft_command_templates(task_templates=templates):
            rendered = self._render_command_template(template, target_item, target_count)
            if rendered and rendered not in candidates:
                candidates.append(rendered)
        if not candidates:
            raise RuntimeError(f"no craft command templates available for {target_item}")

        before_count = self._bridge_item_count(target_item)
        target_total = before_count + target_count if before_count >= 0 else -1
        errors: list[str] = []

        for cmd in candidates:
            try:
                self.baritone.command(cmd)
            except Exception as exc:
                if self._is_craft_output_pending_error(exc):
                    resolved, pending_error = self._wait_for_craft_pending_resolution(
                        command=cmd,
                        target_item=target_item,
                        before_count=before_count,
                        target_total=target_total,
                        timeout_seconds=min(max(3.0, float(timeout)), 8.0),
                    )
                    if resolved:
                        return
                    errors.append(f"{cmd}: {pending_error or 'craft output pending timed out'}")
                    continue
                errors.append(f"{cmd}: {exc}")
                continue

            ok = self.baritone.wait_for_idle(
                timeout_seconds=max(3.0, float(timeout)),
                poll_seconds=self.config.baritone.poll_seconds,
            )
            if not ok:
                errors.append(f"{cmd}: did not reach idle")
                continue

            after_count = self._bridge_item_count(target_item)
            if target_total >= 0:
                if after_count >= target_total:
                    return
                errors.append(f"{cmd}: item count unchanged ({before_count}->{after_count})")
                continue
            if before_count >= 0:
                if after_count > before_count:
                    return
                errors.append(f"{cmd}: item count unchanged ({before_count}->{after_count})")
                continue
            return

        details = "; ".join(errors[-4:]) if errors else "no craft commands attempted"
        raise RuntimeError(f"craft failed for {target_item} x{target_count}: {details}")

    @staticmethod
    def _status_pos(status: dict[str, Any] | None) -> tuple[float, float, float] | None:
        if not isinstance(status, dict):
            return None
        keys = ("posX", "posY", "posZ")
        if not all(k in status for k in keys):
            return None
        try:
            return (
                float(status.get("posX", 0.0)),
                float(status.get("posY", 0.0)),
                float(status.get("posZ", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _dist_sq(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        dz = a[2] - b[2]
        return dx * dx + dy * dy + dz * dz

    @staticmethod
    def _normalize_xz(dx: float, dz: float) -> tuple[float, float] | None:
        norm = (dx * dx + dz * dz) ** 0.5
        if norm < 1e-6:
            return None
        return dx / norm, dz / norm

    def _ensure_base_anchor(self, status: dict[str, Any] | None = None) -> None:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return
        if self._base_anchor_pos is not None:
            return
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return
        pos = self._status_pos(live)
        if pos is None:
            return
        self._base_anchor_pos = pos
        log.info(
            "Base protection anchor set at (%.1f, %.1f, %.1f) radius=%.1f",
            pos[0],
            pos[1],
            pos[2],
            self._base_protection_radius_blocks,
        )

    def _inside_base_protection_zone(self, status: dict[str, Any] | None = None) -> bool:
        self._ensure_base_anchor(status=status)
        if self._base_anchor_pos is None:
            return False
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        if not live or not self.baritone._status_is_fresh(live):
            return False
        pos = self._status_pos(live)
        if pos is None:
            return False
        radius_sq = float(self._base_protection_radius_blocks) ** 2
        return self._dist_sq(pos, self._base_anchor_pos) <= radius_sq

    def _base_protection_relocation_targets(
        self,
        *,
        anchor: tuple[float, float, float],
        pos: tuple[float, float, float],
    ) -> list[tuple[int, int, int]]:
        outward = self._normalize_xz(pos[0] - anchor[0], pos[2] - anchor[2])
        directions: list[tuple[float, float]] = [
            (1.0, 0.0),
            (-1.0, 0.0),
            (0.0, 1.0),
            (0.0, -1.0),
            (1.0, 1.0),
            (1.0, -1.0),
            (-1.0, 1.0),
            (-1.0, -1.0),
        ]

        def _direction_score(direction: tuple[float, float]) -> float:
            normalized = self._normalize_xz(direction[0], direction[1])
            if normalized is None or outward is None:
                return 0.0
            return (normalized[0] * outward[0]) + (normalized[1] * outward[1])

        directions.sort(key=_direction_score, reverse=True)
        target_distances = [
            max(self._base_protection_radius_blocks + 6.0, 12.0),
            max(self._base_protection_radius_blocks + 12.0, 18.0),
        ]
        ty = int(round(pos[1]))
        candidates: list[tuple[int, int, int]] = []
        seen: set[tuple[int, int, int]] = set()
        for target_dist in target_distances:
            for dx, dz in directions:
                normalized = self._normalize_xz(dx, dz)
                if normalized is None:
                    continue
                tx = int(round(anchor[0] + (normalized[0] * target_dist)))
                tz = int(round(anchor[2] + (normalized[1] * target_dist)))
                candidate = (tx, ty, tz)
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)
        return candidates

    def _relocate_outside_base_protection(
        self,
        *,
        context: str,
        anchor: tuple[float, float, float],
        pos: tuple[float, float, float],
    ) -> bool:
        poll_seconds = max(0.1, float(self.config.baritone.safety_poll_seconds))
        candidate_timeout_seconds = max(8.0, min(20.0, float(self.config.baritone.safety_recover_timeout_seconds)))

        for tx, ty, tz in self._base_protection_relocation_targets(anchor=anchor, pos=pos):
            command_body = f"goto {tx} {ty} {tz}"
            log.warning(
                "Relocating outside base protection before '%s' (target=%d %d %d, radius=%.1f)",
                context,
                tx,
                ty,
                tz,
                self._base_protection_radius_blocks,
            )
            self.baritone.command(command_body)
            candidate_deadline = time.time() + candidate_timeout_seconds
            progress_deadline = time.time() + max(3.0, poll_seconds * 8.0)
            last_pos = pos

            while time.time() < candidate_deadline:
                if self.baritone.controller.stop_requested:
                    raise RuntimeError("Stop requested")
                self.baritone.sync_manual_pause_with_controller()
                self.baritone.controller.wait_if_paused()
                self.baritone.sync_manual_pause_with_controller()

                refreshed = self.baritone.read_bridge_status()
                if not refreshed or not self.baritone._status_is_fresh(refreshed):
                    self._sleep_interruptible(poll_seconds)
                    continue
                if not self._inside_base_protection_zone(status=refreshed):
                    return True

                last_command = str(refreshed.get("lastCommand", "")).strip().lower()
                last_result = str(refreshed.get("lastCommandResult", "")).strip().lower()
                last_error = str(refreshed.get("lastCommandError", "")).strip()
                if last_command == command_body.lower() and last_result in {"rejected", "error"}:
                    log.warning(
                        "Base-protection relocation command failed '%s': %s",
                        command_body,
                        last_error or last_result,
                    )
                    break

                if bool(refreshed.get("isPathing", False)):
                    progress_deadline = time.time() + max(3.0, poll_seconds * 8.0)

                refreshed_pos = self._status_pos(refreshed)
                if refreshed_pos is not None and self._dist_sq(refreshed_pos, last_pos) >= 1.0:
                    last_pos = refreshed_pos
                    progress_deadline = time.time() + max(3.0, poll_seconds * 8.0)

                if time.time() >= progress_deadline:
                    break

                self._sleep_interruptible(poll_seconds)

            refreshed = self.baritone.read_bridge_status()
            if refreshed and self.baritone._status_is_fresh(refreshed) and not self._inside_base_protection_zone(status=refreshed):
                return True
        return False

    def _assert_outside_base_protection(self, context: str, status: dict[str, Any] | None = None) -> None:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return
        if not self._inside_base_protection_zone(status=status):
            return
        self._ensure_base_anchor(status=status)
        live = status if isinstance(status, dict) else self.baritone.read_bridge_status()
        anchor = self._base_anchor_pos
        pos = self._status_pos(live)
        if anchor is None or pos is None:
            raise RuntimeError(
                f"{context} blocked in protected base zone "
                f"(radius={self._base_protection_radius_blocks:.1f}); move outside base area to continue breaking blocks"
            )

        if self._relocate_outside_base_protection(
            context=context,
            anchor=anchor,
            pos=pos,
        ):
            return
        raise RuntimeError(
            f"{context} blocked in protected base zone "
            f"(radius={self._base_protection_radius_blocks:.1f}); auto-relocation failed"
        )

    @contextmanager
    def _with_cleanup_protection(self, item_ids: set[str] | list[str]) -> Any:
        previous = self.baritone.push_inventory_cleanup_protection(set(item_ids))
        try:
            yield
        finally:
            self.baritone.restore_inventory_cleanup_protection(previous)

    def _run_build_tunnel(
        self,
        *,
        width: int,
        height: int,
        length: int,
        segment_length: int,
        verify_item_ids: list[str] | None = None,
        min_item_gain: int = 0,
        require_progress: bool = False,
        base_protection_context: str = "build_tunnel",
    ) -> None:
        self._assert_outside_base_protection(base_protection_context)
        w = max(1, int(width))
        h = max(2, int(height))
        total = max(1, int(length))
        seg = max(4, int(segment_length))
        verify_ids = [str(v).strip().lower() for v in (verify_item_ids or []) if str(v).strip()]
        remaining = total
        moved_segments = 0
        gained_items = 0
        telemetry_seen = False

        with self._with_cleanup_protection(set(verify_ids)):
            while remaining > 0:
                this_seg = min(seg, remaining)
                before = self.baritone.read_bridge_status()
                before_pos = self._status_pos(before)
                before_counts = {
                    item_id: self._bridge_item_count(item_id, status=before)
                    for item_id in verify_ids
                }

                # Baritone tunnel syntax is: tunnel <height> <width> <depth>
                self.baritone.command(f"tunnel {h} {w} {this_seg}")
                idle_ok = self.baritone.wait_for_idle(
                    timeout_seconds=max(30.0, self.config.baritone.idle_timeout_seconds),
                    poll_seconds=self.config.baritone.poll_seconds,
                )

                after = self.baritone.read_bridge_status()
                after_pos = self._status_pos(after)
                moved = False
                if before_pos is not None and after_pos is not None:
                    telemetry_seen = True
                    moved = self._dist_sq(before_pos, after_pos) >= 0.64
                if moved:
                    moved_segments += 1

                seg_gain = 0
                for item_id in verify_ids:
                    old = before_counts.get(item_id, -1)
                    new = self._bridge_item_count(item_id, status=after)
                    if old >= 0 or new >= 0:
                        telemetry_seen = True
                    if old >= 0 and new > old:
                        seg_gain += new - old
                gained_items += seg_gain

                if require_progress and telemetry_seen and not moved and seg_gain <= 0:
                    raise RuntimeError(
                        f"build_tunnel segment made no observable progress (segment={this_seg}, gain={seg_gain})"
                    )

                if not idle_ok and not moved and seg_gain <= 0:
                    raise RuntimeError(f"build_tunnel stalled at segment length {this_seg}")

                remaining -= this_seg

        if telemetry_seen and min_item_gain > 0 and gained_items < min_item_gain:
            raise RuntimeError(
                f"build_tunnel completed but item gain too low: gained={gained_items}, required={min_item_gain}"
            )
        if require_progress and telemetry_seen and verify_ids and moved_segments <= 0 and gained_items <= 0:
            raise RuntimeError("build_tunnel completed with no observed movement or resource gain")

    def _acquire_base_requirement(self, item_id: str, count: int) -> None:
        token = str(item_id).strip().lower()
        qty = max(1, int(count))
        with self._with_cleanup_protection({token}):
            gatherable_tunnel_items = {
                "minecraft:stone",
                "minecraft:cobblestone",
                "minecraft:deepslate",
                "minecraft:cobbled_deepslate",
                "minecraft:gravel",
                "minecraft:sand",
                "minecraft:dirt",
                "minecraft:clay_ball",
            }
            if token in gatherable_tunnel_items:
                length = self._tunnel_length_for_count(qty)
                self._run_build_tunnel(
                    width=2,
                    height=2,
                    length=length,
                    segment_length=min(24, length),
                    verify_item_ids=[token, "minecraft:cobblestone", "minecraft:cobbled_deepslate"],
                    min_item_gain=0,
                    base_protection_context=f"resource gathering for {token}",
                )
                return

            if not self._looks_like_mineable_block(token):
                # For non-mineable requirements, try direct craft before failing.
                self._craft_item(token, qty, timeout=75.0)
                return

            before = self._bridge_item_count(token)
            target_total = before + qty if before >= 0 else -1
            attempts = max(1, min(4, int((qty + 31) / 32)))
            self._assert_outside_base_protection(f"mining for {token}")
            for _ in range(attempts):
                self.baritone.command(f"mine {token}")
                ok = self.baritone.wait_for_idle(timeout_seconds=90.0, poll_seconds=self.config.baritone.poll_seconds)
                after = self._bridge_item_count(token)
                if target_total >= 0 and after >= target_total:
                    return
                if target_total < 0 and ok:
                    return
            raise RuntimeError(f"failed to gather base requirement by mining: {token} x{qty}")

    def _craft_with_dependencies(
        self,
        item: str,
        count: int,
        max_depth: int = 8,
        *,
        templates: list[str] | None = None,
    ) -> None:
        target_item = str(item).strip().lower()
        target_count = max(1, int(count))
        if self._stoneblock_hammer_chain_source_item(target_item):
            if self._acquire_stoneblock_hammer_chain_requirement(target_item, target_count):
                return
        index = self._get_recipe_index()
        if index is None:
            self._craft_item(target_item, target_count, templates=templates)
            return

        plan = index.plan(target_item, target_count, max_depth=max_depth)
        if not plan.steps:
            self._craft_item(target_item, target_count, templates=templates)
            return

        # Try to gather missing base requirements before crafting tree execution.
        for base_item, base_count in plan.base_requirements.items():
            required = max(1, int(base_count))
            current = self._bridge_item_count(base_item)
            if current >= required:
                continue
            missing = required - current if current >= 0 else required
            if self._stoneblock_hammer_chain_source_item(base_item):
                if self._acquire_stoneblock_hammer_chain_requirement(base_item, missing):
                    continue
            self._acquire_base_requirement(base_item, missing)

        for step_item, step_count in plan.steps:
            timeout = 90.0 if step_item == target_item else 60.0
            self._craft_item(step_item, step_count, templates=templates, timeout=timeout)

    @staticmethod
    def _tunnel_length_for_count(count: int) -> int:
        return max(8, min(256, int((max(1, count) + 31) / 32) * 8))

    @staticmethod
    def _looks_like_mineable_block(item_id: str) -> bool:
        token = str(item_id).strip().lower()
        if not token:
            return False
        if ":" in token:
            namespace, path = token.split(":", 1)
        else:
            namespace, path = "minecraft", token

        if namespace != "minecraft":
            # For non-vanilla ids, only trust clear ore-like blocks.
            return path.endswith("_ore") or path.startswith("ore_") or "_ore_" in path

        non_block_markers = (
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
            "seed",
            "essence",
            "chunk",
            "shard",
            "machine",
            "frame",
            "component",
            "redstone",
            "charcoal",
            "coal",
            "lapis",
            "diamond",
            "emerald",
            "quartz",
        )
        if any(marker in path for marker in non_block_markers):
            return False
        block_markers = (
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
        return any(marker in path for marker in block_markers)

    @staticmethod
    def _quest_strategy_candidates() -> list[str]:
        return ["craft_tree", "direct_craft", "gather"]

    def _run_quest_strategy(self, strategy: str, item_id: str, needed_count: int) -> None:
        mode = str(strategy).strip().lower()
        needed = max(1, int(needed_count))
        if mode == "gather" and self._stoneblock_hammer_chain_source_item(item_id):
            status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
            source_item = self._stoneblock_hammer_chain_source_item(item_id)
            before_source = self._bridge_item_count(source_item, status=status)
            before_target = self._bridge_item_count(item_id, status=status)
            target_total = before_target + needed if before_target >= 0 else -1
            stage_hint_before = str(status.get("stoneblockStageHint", "")).strip() if isinstance(status, dict) else ""
            action_context = self._stoneblock_route_context(
                target_item=item_id,
                source_item=source_item,
                action_name=f"mine {source_item}",
                before_source_count=before_source,
                before_target_count=before_target,
                target_total=target_total,
                stage_hint_before=stage_hint_before,
            )
            if self._acquire_stoneblock_hammer_chain_requirement(item_id, needed):
                return
            if str(action_context.get("actionClass", "")).strip() == "local_in_place":
                raise RuntimeError(
                    f"known local StoneBlock hammer route unavailable for {item_id}; refusing generic gather fallback"
                )
        if mode == "craft_tree":
            depth = (
                6
                if item_id in {"minecraft:gravel", "minecraft:sand", "minecraft:dirt", "minecraft:clay_ball"}
                else 10
            )
            self._craft_with_dependencies(item_id, needed, max_depth=depth)
            return
        if mode == "direct_craft":
            self._craft_item(item_id, needed, timeout=90.0)
            return
        if mode == "gather":
            self._acquire_base_requirement(item_id, needed)
            return
        raise RuntimeError(f"unsupported quest strategy: {mode}")

    def _quest_item_reached(self, item_id: str, required_count: int, *, optional: bool) -> tuple[bool, int, str]:
        observed = self._bridge_item_count(item_id)
        if observed >= required_count:
            return True, observed, "count_met"

        if observed < 0:
            if self.config.baritone.transport.strip().lower() != "bridge_file":
                return True, observed, "unverified_chat_transport"
            if optional:
                return True, observed, "unverified_optional_no_telemetry"
            return False, observed, "inventory telemetry unavailable"

        return False, observed, f"count_low:{observed}/{required_count}"

    def _quest_item_acquire(self, item: str, count: int, optional: bool, state: AgentState | None = None) -> None:
        item_id = item.strip().lower()
        qty = max(1, int(count))
        block_flag = self._quest_item_block_flag(item_id)
        if state is not None and block_flag and block_flag in state.completed_flags:
            if optional:
                return
            raise UnrecoverableQuestAcquireError(
                f"quest item is blocked by prior unrecoverable failure: {item_id}",
                item_id=item_id,
            )

        now = time.time()
        if optional:
            cooldown_until = float(self._optional_item_cooldowns.get(item_id, 0.0))
            if cooldown_until > now:
                remaining = max(0.0, cooldown_until - now)
                fail_streak = int(self._optional_item_failures.get(item_id, 0))
                self._append_debug_learning(
                    {
                        "event": "optional_item_cooldown_skip",
                        "itemId": item_id,
                        "remainingSeconds": round(remaining, 2),
                        "failureStreak": fail_streak,
                    }
                )
                return

        precheck_status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
        observed_before = self._bridge_item_count(item_id, status=precheck_status)
        stage_hint_before = ""
        if isinstance(precheck_status, dict):
            stage_hint_before = str(precheck_status.get("stoneblockStageHint", "")).strip()
        if observed_before >= qty:
            self._log_quest_item_acquire_diagnostic(
                item_id=item_id,
                required_count=qty,
                observed_count=observed_before,
                optional=optional,
                scheduled_reason="bridge_count_met_before_stoneblock_chain",
                current_substage="already_satisfied",
                slice_success_boolean=True,
                stage_hint=stage_hint_before,
                state=state,
            )
            if optional:
                self._clear_optional_item_retry_state(item_id, state)
            return

        if self._stoneblock_hammer_chain_source_item(item_id):
            scheduled_reason = (
                f"bridge_count_low_before_stoneblock_chain:{observed_before}/{qty}"
                if observed_before >= 0
                else "bridge_count_unavailable_before_stoneblock_chain"
            )
            self._log_quest_item_acquire_diagnostic(
                item_id=item_id,
                required_count=qty,
                observed_count=observed_before,
                optional=optional,
                scheduled_reason=scheduled_reason,
                current_substage="stoneblock_hammer_chain",
                slice_success_boolean=False,
                stage_hint=stage_hint_before,
                state=state,
                local_housekeeping_boolean=True,
            )
            if self._acquire_stoneblock_hammer_chain_requirement(item_id, qty):
                success_status = self.baritone.read_bridge_status() if self.config.baritone.transport.strip().lower() == "bridge_file" else None
                observed_after = self._bridge_item_count(item_id, status=success_status)
                stage_hint_after = ""
                if isinstance(success_status, dict):
                    stage_hint_after = str(success_status.get("stoneblockStageHint", "")).strip()
                self._log_quest_item_acquire_diagnostic(
                    item_id=item_id,
                    required_count=qty,
                    observed_count=observed_after,
                    optional=optional,
                    scheduled_reason="bridge_count_met_after_stoneblock_chain",
                    current_substage="stoneblock_hammer_chain_complete",
                    slice_success_boolean=observed_after >= qty,
                    stage_hint=stage_hint_after,
                    state=state,
                    local_housekeeping_boolean=True,
                )
                if optional:
                    self._clear_optional_item_retry_state(item_id, state)
                return

        strategy_memory = self._get_strategy_memory()
        preferred, hints = self._preferred_strategies_for_item(item_id)
        if self._stoneblock_hammer_chain_source_item(item_id):
            preferred = self._dedupe_preserve_order(["gather", *preferred])
        ranked = strategy_memory.rank_strategies(
            item_id=item_id,
            candidates=self._quest_strategy_candidates(),
            preferred=preferred,
            exploration_rate=self.config.planner.strategy_exploration_rate,
        )
        if not ranked:
            ranked = self._quest_strategy_candidates()

        errors: list[str] = []
        for strategy in ranked:
            existing = self._bridge_item_count(item_id)
            if existing >= qty:
                return
            missing = qty - existing if existing >= 0 else qty
            start = time.perf_counter()
            try:
                self._run_quest_strategy(strategy, item_id, missing)
                success, observed, reason = self._quest_item_reached(item_id, qty, optional=optional)
                if not success:
                    raise RuntimeError(f"verification failed: {reason}")
                duration = max(0.0, time.perf_counter() - start)
                strategy_memory.record_attempt(
                    item_id=item_id,
                    strategy=strategy,
                    success=True,
                    duration_seconds=duration,
                )
                self._append_debug_learning(
                    {
                        "event": "quest_strategy_success",
                        "itemId": item_id,
                        "strategy": strategy,
                        "requiredCount": qty,
                        "observedCount": observed,
                        "reason": reason,
                        "durationSeconds": round(duration, 3),
                        "preferredStrategies": preferred,
                        "hintKeywords": list(hints.get("keywords", [])),
                    }
                )
                if reason == "unverified_optional_no_telemetry":
                    log.warning(
                        "Quest acquisition for %s x%d is unverified (no inventory telemetry). Continuing.",
                        item_id,
                        qty,
                    )
                if optional:
                    self._clear_optional_item_retry_state(item_id, state)
                return
            except Exception as exc:
                duration = max(0.0, time.perf_counter() - start)
                strategy_memory.record_attempt(
                    item_id=item_id,
                    strategy=strategy,
                    success=False,
                    duration_seconds=duration,
                    error=str(exc),
                )
                self._append_debug_learning(
                    {
                        "event": "quest_strategy_failure",
                        "itemId": item_id,
                        "strategy": strategy,
                        "requiredCount": qty,
                        "durationSeconds": round(duration, 3),
                        "error": str(exc),
                        "preferredStrategies": preferred,
                        "hintKeywords": list(hints.get("keywords", [])),
                    }
                )
                errors.append(f"{strategy}: {exc}")

        detail = "; ".join(errors[-4:]) if errors else "no strategies attempted"
        if optional:
            detail_l = detail.lower()
            cooldown_seconds = 45.0
            if "missing required ingredients" in detail_l:
                cooldown_seconds = 120.0
            if "blocked in protected base zone" in detail_l:
                cooldown_seconds = 180.0
            if "no fitting crafting recipe" in detail_l:
                cooldown_seconds = 300.0
            fail_streak = int(self._optional_item_failures.get(item_id, 0)) + 1
            self._optional_item_failures[item_id] = fail_streak
            cooldown_scale = min(8.0, 1.0 + (max(0, fail_streak - 1) * 0.75))
            cooldown_seconds *= cooldown_scale
            if "no fitting crafting recipe" in detail_l and fail_streak >= 2:
                cooldown_seconds = max(cooldown_seconds, 900.0)
            if fail_streak >= 5:
                cooldown_seconds = max(cooldown_seconds, 1800.0)
            if fail_streak >= 10:
                cooldown_seconds = max(cooldown_seconds, 3600.0)
            cooldown_seconds = max(45.0, min(14400.0, float(cooldown_seconds)))
            self._optional_item_cooldowns[item_id] = time.time() + cooldown_seconds
            if state is not None:
                state.optional_item_failures[item_id] = fail_streak
                state.optional_item_cooldowns[item_id] = self._optional_item_cooldowns[item_id]
            self._append_debug_learning(
                {
                    "event": "optional_item_cooldown_set",
                    "itemId": item_id,
                    "failureStreak": fail_streak,
                    "cooldownSeconds": round(cooldown_seconds, 2),
                    "detail": detail,
                }
            )
            log.warning(
                "Optional quest item acquisition failed for %s x%d (streak=%d cooldown=%.0fs: %s)",
                item_id,
                qty,
                fail_streak,
                cooldown_seconds,
                detail,
            )
            return
        lower_errors = [str(msg).strip().lower() for msg in errors if str(msg).strip()]
        if lower_errors and all(
            ("missing required ingredients" in msg) or ("no fitting crafting recipe" in msg)
            for msg in lower_errors
        ):
            raise UnrecoverableQuestAcquireError(
                f"unrecoverable acquisition for {item_id} x{qty}: {detail}",
                item_id=item_id,
            )
        raise RuntimeError(f"quest acquisition failed for {item_id} x{qty}: {detail}")

    def _run_module(self, name: str, params: dict[str, Any], state: AgentState) -> None:
        tasks = module_tasks(name, params)
        for subtask in tasks:
            self._run_task_with_retry(subtask, state)

    def _quest_claim_all(self, commands: list[str] | None = None) -> None:
        if self.config.baritone.transport.strip().lower() == "bridge_file":
            # Bridge transport avoids focus-dependent chat typing; quest claiming is best effort
            # and should not spam logs when no focused game window is found.
            return
        claim_commands = commands or [
            "/ftbquests claim all",
            "/ftbquests claim",
            "/ftbquests claim_all",
        ]
        for cmd in claim_commands:
            text = str(cmd).strip()
            if not text:
                continue
            try:
                self.baritone.controller.send_chat_command(text)
                self._sleep_interruptible(0.25, poll_seconds=0.05)
            except Exception as exc:
                log.debug("Quest claim command failed '%s': %s", text, exc)

    def _idle_module_candidates(self) -> list[str]:
        configured = getattr(self.config.planner, "idle_autonomous_modules", None)
        modules = [str(v).strip().lower() for v in (configured or []) if str(v).strip()]
        if not modules:
            fallback = str(self.config.planner.idle_autonomous_module).strip().lower() or "idle_resource_grind"
            modules = [fallback]
        seen: set[str] = set()
        out: list[str] = []
        for module in modules:
            if module in seen:
                continue
            seen.add(module)
            out.append(module)
        return out

    def _rank_idle_modules(self, candidates: list[str], idle_cycles: int) -> list[str]:
        if not candidates:
            return []
        preferred: list[str] = ["idle_recovery_housekeeping", "stoneblock_hammer_brush_loop", "idle_resource_grind"]
        # Rotate preferred action by cycle to reduce repetitive behavior while idle.
        if idle_cycles % 9 == 0:
            preferred.append("basic_machine_bootstrap")
        if idle_cycles % 6 == 0:
            preferred.append("basic_farm_bootstrap")
        if idle_cycles % 4 == 0:
            preferred.append("base_storage_housekeeping")
        if self._last_idle_module:
            preferred = [m for m in preferred if m != self._last_idle_module]

        memory = self._get_strategy_memory()
        ranked = memory.rank_strategies(
            item_id="__idle__",
            candidates=candidates,
            preferred=preferred,
            exploration_rate=max(0.05, float(self.config.planner.strategy_exploration_rate) * 0.5),
        )
        if not ranked:
            ranked = list(candidates)

        rank_index = {name: idx for idx, name in enumerate(ranked)}
        ordered = sorted(
            ranked,
            key=lambda name: (
                int(self._idle_module_failures.get(name, 0)),
                rank_index.get(name, 999),
            ),
        )
        if self._last_idle_module and int(self._idle_module_failures.get(self._last_idle_module, 0)) > 0:
            ordered = [m for m in ordered if m != self._last_idle_module] + [
                m for m in ordered if m == self._last_idle_module
            ]
        return ordered

    def _run_idle_inventory_pressure(self, state: AgentState) -> bool:
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return False
        if not (self.config.baritone.inventory_cleanup_enabled or self._store_to_chest_bridge_supported is not False):
            return False

        status = self.baritone.read_bridge_status()
        if not status or not self.baritone._status_is_fresh(status):
            return False

        threshold = max(0, int(self.config.baritone.inventory_full_free_slots_threshold))
        def _pressure_active(free_slots: int) -> bool:
            if threshold <= 0:
                return free_slots <= 0
            return free_slots < threshold

        free_slots_before = self.baritone._status_free_inventory_slots(status)
        if not _pressure_active(free_slots_before):
            self._idle_inventory_pressure_failures = 0
            return False

        self._write_live_status(
            state,
            "idle_inventory_pressure",
            message=f"inventory pressure detected (free={free_slots_before} threshold={threshold})",
        )

        error = ""
        cleanup_triggered = False
        store_triggered = False
        free_slots_after = free_slots_before
        try:
            self._store_inventory_in_chest(radius=8, max_stacks=24, include_hotbar=False, optional=False)
            store_triggered = True
            refreshed = self.baritone.read_bridge_status()
            if refreshed and self.baritone._status_is_fresh(refreshed):
                free_slots_after = self.baritone._status_free_inventory_slots(refreshed)
        except Exception as exc:
            error = str(exc)
            log.warning("Idle inventory pressure cleanup failed: %s", exc)

        if _pressure_active(free_slots_after):
            cleanup_status = self.baritone.read_bridge_status()
            if cleanup_status and self.baritone._status_is_fresh(cleanup_status):
                try:
                    cleanup_triggered = bool(
                        self.baritone.try_inventory_cleanup(cleanup_status, context="idle_inventory_pressure")
                    )
                    if cleanup_triggered:
                        refreshed = self.baritone.read_bridge_status()
                        if refreshed and self.baritone._status_is_fresh(refreshed):
                            free_slots_after = self.baritone._status_free_inventory_slots(refreshed)
                except Exception as exc:
                    cleanup_error = str(exc)
                    if error:
                        error = f"{error}; cleanup: {cleanup_error}"
                    else:
                        error = cleanup_error
                    log.warning("Idle inventory junk-drop cleanup failed: %s", exc)

        improved = free_slots_after > free_slots_before
        cleared = not _pressure_active(free_slots_after)
        if improved or cleared:
            self._idle_inventory_pressure_failures = 0
        else:
            self._idle_inventory_pressure_failures = max(0, int(self._idle_inventory_pressure_failures)) + 1
        self._write_live_status(
            state,
            "idle_inventory_pressure_result",
            error=error,
            message=(
                f"store_triggered={store_triggered} cleanup_triggered={cleanup_triggered} improved={improved} "
                f"cleared={cleared} free={free_slots_after} threshold={threshold}"
            ),
        )
        if not improved and not cleared and self._idle_inventory_pressure_failures >= 3:
            blocker = (
                "idle inventory pressure unresolved after "
                f"{self._idle_inventory_pressure_failures} attempts: "
                f"{error or f'free={free_slots_after} threshold={threshold}'}"
            )
            state.safe_paused = True
            state.current_goal_id = None
            state.last_error = blocker
            self._set_operator_loop_state(
                state,
                decision="safe_pause",
                task=None,
                next_task=None,
                nextTaskReason="idle inventory pressure repeatedly failed",
                last_blocker=blocker,
            )
            self._write_live_status(
                state,
                "safe_paused",
                error=blocker,
                message="idle inventory pressure repeatedly failed",
            )
            self._log_operator_loop_decision(
                state,
                decision="safe_pause",
                current_task=None,
                next_task=None,
                next_task_reason="idle inventory pressure repeatedly failed",
                last_blocker=blocker,
            )
            log.error(
                "Idle inventory pressure exceeded retry budget (%d). Entering safe pause.",
                self._idle_inventory_pressure_failures,
            )
        return improved or cleared

    def _run_idle_maintenance_cycle(self, state: AgentState, idle_cycles: int) -> bool:
        if not self.config.planner.idle_autonomous_enabled:
            return False
        interval = max(1, int(self.config.planner.idle_autonomous_interval_cycles))
        if idle_cycles % interval != 0:
            return False

        candidates = self._idle_module_candidates()
        ranked_modules = self._rank_idle_modules(candidates, idle_cycles)[:3]
        if not ranked_modules:
            return False

        if self.config.baritone.transport.strip().lower() == "bridge_file":
            status = self.baritone.read_bridge_status()
            if status and self.baritone._status_is_fresh(status):
                try:
                    self.baritone.try_inventory_cleanup(status, context="idle_autonomy")
                except Exception as exc:
                    log.warning("Idle inventory cleanup failed: %s", exc)

        memory = self._get_strategy_memory()
        attempts = max(1, min(3, len(ranked_modules)))
        for idx, module_name in enumerate(ranked_modules[:attempts], start=1):
            self._write_live_status(
                state,
                "idle_autonomy_selected",
                message=f"module={module_name} candidate={idx}/{attempts}",
            )
            started = time.perf_counter()
            try:
                self._run_module(name=module_name, params={"cycles": 1}, state=state)
                elapsed = max(0.0, time.perf_counter() - started)
                self._idle_module_failures[module_name] = 0
                self._last_idle_module = module_name
                memory.record_attempt(
                    item_id="__idle__",
                    strategy=module_name,
                    success=True,
                    duration_seconds=elapsed,
                )
                self._append_debug_learning(
                    {
                        "event": "idle_module_success",
                        "module": module_name,
                        "attemptIndex": idx,
                        "durationSeconds": round(elapsed, 3),
                    }
                )
                self._write_live_status(
                    state,
                    "idle_autonomy_complete",
                    message=f"module complete={module_name}",
                )
                return True
            except Exception as exc:
                elapsed = max(0.0, time.perf_counter() - started)
                current_failures = int(self._idle_module_failures.get(module_name, 0)) + 1
                self._idle_module_failures[module_name] = current_failures
                memory.record_attempt(
                    item_id="__idle__",
                    strategy=module_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                )
                self._append_debug_learning(
                    {
                        "event": "idle_module_failure",
                        "module": module_name,
                        "attemptIndex": idx,
                        "failureCount": current_failures,
                        "durationSeconds": round(elapsed, 3),
                        "error": str(exc),
                    }
                )
                log.warning("Idle autonomy module '%s' failed: %s", module_name, exc)
                self._write_live_status(
                    state,
                    "idle_autonomy_failed",
                    error=str(exc),
                    message=f"module failed={module_name} candidate={idx}/{attempts}",
                )

        self._write_live_status(
            state,
            "idle_autonomy_fallback",
            message=f"all idle candidates failed ({', '.join(ranked_modules[:attempts])})",
        )
        return False

    def _pre_task_gate(self, task: dict[str, Any], state: AgentState) -> None:
        self.baritone.sync_manual_pause_with_controller()
        self.baritone.controller.wait_if_paused()
        self.baritone.sync_manual_pause_with_controller()
        task_type = str(task.get("type", "")).strip().lower()
        optional_quest_acquire = task_type == "quest_item_acquire" and bool(task.get("optional", False))
        inventory_relief_task = task_type in {"store_inventory_in_chest", "place_storage_chest"} or optional_quest_acquire
        requires_bridge = self._task_requires_bridge(task)
        if self.config.baritone.transport.strip().lower() == "bridge_file" and requires_bridge:
            self.baritone.wait_for_bridge_ready(timeout_seconds=self.config.baritone.bridge_ready_wait_seconds)
        if not requires_bridge:
            return

        if not self.config.baritone.enable_safety_monitoring:
            return
        if self.config.baritone.transport.strip().lower() != "bridge_file":
            return

        requires_player_clear = self._task_considers_player_distance(task)
        deadline = time.time() + max(0.0, self.config.baritone.pre_task_safety_wait_seconds)
        hard_deadline = deadline + max(10.0, self.config.baritone.safety_recover_timeout_seconds)
        warned_at = 0.0
        sent_pause = False
        recovery_attempts = 0
        inventory_stagnant_checks = 0
        last_full_inventory_free_slots: int | None = None
        max_inventory_stagnant_checks = max(
            4,
            int(
                max(1.0, float(self.config.baritone.inventory_cleanup_cooldown_seconds))
                / max(0.2, float(self.config.baritone.safety_poll_seconds))
            )
            + 2,
        )

        try:
            while True:
                if self.baritone.controller.stop_requested:
                    raise RuntimeError("Stop requested")

                status = self.baritone.read_bridge_status()
                if not status:
                    if time.time() >= deadline:
                        raise RuntimeError("Bridge status missing while waiting for safety precheck")
                    self._sleep_interruptible(self.config.baritone.safety_poll_seconds)
                    continue
                if not self.baritone._status_is_fresh(status) and not self.baritone._status_has_recent_ack(status):
                    if time.time() >= deadline:
                        raise RuntimeError("Bridge status stale while waiting for safety precheck")
                    self._sleep_interruptible(self.config.baritone.safety_poll_seconds)
                    continue

                if self.config.baritone.inventory_cleanup_enabled:
                    cleanup_triggered = False
                    try:
                        cleanup_triggered = bool(self.baritone.try_inventory_cleanup(status, context="pre_task_gate"))
                        refreshed = self.baritone.read_bridge_status()
                        if refreshed and self.baritone._status_is_fresh(refreshed):
                            status = refreshed
                    except Exception as exc:
                        log.warning("Pre-task inventory cleanup attempt failed: %s", exc)
                    free_slots = self.baritone._status_free_inventory_slots(status)
                    threshold = max(0, int(self.config.baritone.inventory_full_free_slots_threshold))
                    if free_slots <= threshold:
                        if inventory_relief_task:
                            self._write_live_status(
                                state,
                                "pre_task_inventory_pressure_bypass",
                                task=task,
                                message=(
                                    f"inventory pressure bypass for task={task_type} "
                                    f"({free_slots} free <= {threshold})"
                                ),
                            )
                            return
                        if self.config.baritone.auto_eat_enabled:
                            try:
                                self.baritone.try_auto_eat(status)
                            except Exception as exc:
                                log.warning("Pre-task auto-eat under inventory pressure failed: %s", exc)
                        if self.config.baritone.fight_hostiles:
                            try:
                                self.baritone.try_hostile_combat(status, context="pre_task_inventory_full")
                            except Exception as exc:
                                log.warning("Pre-task combat under inventory pressure failed: %s", exc)
                        if cleanup_triggered:
                            if last_full_inventory_free_slots is None:
                                inventory_stagnant_checks = 1
                            elif free_slots == last_full_inventory_free_slots:
                                inventory_stagnant_checks += 1
                            else:
                                inventory_stagnant_checks = 1
                            last_full_inventory_free_slots = free_slots
                        if inventory_stagnant_checks >= max_inventory_stagnant_checks:
                            raise RuntimeError(
                                "inventory full "
                                f"({free_slots} free <= {threshold}) with no cleanup progress after "
                                f"{inventory_stagnant_checks} cleanup attempts"
                            )
                        if time.time() >= deadline:
                            raise RuntimeError(
                                f"inventory full ({free_slots} free <= {threshold}) and cleanup could not free space"
                            )
                        self._write_live_status(
                            state,
                            "pre_task_inventory_full",
                            task=task,
                            message=(
                                f"inventory full ({free_slots} free <= {threshold}); waiting "
                                f"(stagnant_checks={inventory_stagnant_checks}/{max_inventory_stagnant_checks}, "
                                f"cleanup_triggered={cleanup_triggered})"
                            ),
                        )
                        self._sleep_interruptible(self.config.baritone.safety_poll_seconds)
                        continue
                    inventory_stagnant_checks = 0
                    last_full_inventory_free_slots = None

                if self.config.baritone.auto_eat_enabled:
                    food_level = int(status.get("foodLevel", 20))
                    if food_level < self.config.baritone.auto_eat_food_below:
                        try:
                            self.baritone.try_auto_eat(status)
                            refreshed = self.baritone.read_bridge_status()
                            if refreshed:
                                status = refreshed
                        except Exception as exc:
                            log.warning("Pre-task auto-eat attempt failed: %s", exc)

                if self.config.baritone.fight_hostiles:
                    try:
                        engaged = self.baritone.try_hostile_combat(status, context="pre_task_gate")
                        if engaged:
                            self._sleep_interruptible(max(0.15, self.config.baritone.safety_poll_seconds))
                            refreshed = self.baritone.read_bridge_status()
                            if refreshed:
                                status = refreshed
                    except Exception as exc:
                        log.warning("Pre-task combat attempt failed: %s", exc)

                reasons = self.baritone.safety_reasons(status, consider_players=requires_player_clear)
                if not reasons:
                    return

                now = time.time()
                if now - warned_at >= 3.0:
                    log.warning("Safety hold before task '%s': %s", task.get("type", ""), "; ".join(reasons))
                    self._write_live_status(
                        state,
                        "pre_task_safety_hold",
                        task=task,
                        message="waiting for safety conditions to clear",
                        safety_reasons=reasons,
                    )
                    warned_at = now

                if requires_player_clear and not sent_pause:
                    try:
                        self.baritone.pause_pathing()
                    except Exception as exc:
                        log.warning("Failed to send pause during safety hold: %s", exc)
                    sent_pause = True

                if now >= deadline:
                    if recovery_attempts < 2 and now < hard_deadline:
                        recovered = self.baritone.run_recovery_playbook(
                            context="pre_task_safety_timeout",
                            status=status,
                            reissue_active_command=False,
                        )
                        recovery_attempts += 1
                        if recovered:
                            deadline = min(
                                hard_deadline,
                                time.time() + max(5.0, self.config.baritone.safety_recover_timeout_seconds),
                            )
                            warned_at = 0.0
                            sent_pause = False
                            continue
                    raise RuntimeError(f"Safety conditions did not clear: {'; '.join(reasons)}")
                self._sleep_interruptible(self.config.baritone.safety_poll_seconds)
        finally:
            if sent_pause:
                try:
                    self.baritone.resume_pathing()
                except Exception:
                    pass

    def _run_task_once(self, task: dict[str, Any], state: AgentState) -> None:
        self._pre_task_gate(task, state)
        task_type = str(task.get("type", "")).strip()
        if not task_type:
            raise RuntimeError("Task missing type")

        if task_type == "baritone_command":
            cmd = str(task["command"])
            self.baritone.command(cmd)
            return

        if task_type == "build_tunnel":
            width = int(task.get("width", 2))
            height = int(task.get("height", 2))
            length = int(task.get("length", 16))
            segment_length = int(task.get("segment_length", 24))
            raw_verify = task.get("verify_item_ids", [])
            verify_ids = [str(v).strip() for v in raw_verify] if isinstance(raw_verify, list) else []
            min_item_gain = int(task.get("min_item_gain", 0))
            require_progress = bool(task.get("require_progress", True))
            self._run_build_tunnel(
                width=width,
                height=height,
                length=length,
                segment_length=segment_length,
                verify_item_ids=verify_ids,
                min_item_gain=min_item_gain,
                require_progress=require_progress,
            )
            return

        if task_type == "wait_baritone_idle":
            timeout = task.get("timeout_seconds")
            poll = task.get("poll_seconds")
            timeout_f = float(timeout) if timeout is not None else None
            poll_f = float(poll) if poll is not None else None
            ok = self.baritone.wait_for_idle(timeout_seconds=timeout_f, poll_seconds=poll_f)
            if not ok:
                raise RuntimeError("Baritone did not reach idle successfully")
            return

        if task_type == "wait_chat_phrase":
            if self.config.baritone.transport.strip().lower() == "bridge_file":
                timeout = float(task.get("timeout_seconds", 30.0))
                deadline = time.time() + timeout
                expected_id = str(self.baritone.active_command_state().get("activeCommandId") or "").strip()
                while time.time() < deadline:
                    if self.baritone.controller.stop_requested:
                        raise RuntimeError("Stop requested")
                    self.baritone.sync_manual_pause_with_controller()
                    self.baritone.controller.wait_if_paused()
                    self.baritone.sync_manual_pause_with_controller()
                    status = self.baritone.read_bridge_status() or {}
                    if not status or not self.baritone._status_is_fresh(status):
                        time.sleep(0.2)
                        continue
                    if expected_id and str(status.get("lastCommandId", "")).strip() != expected_id:
                        time.sleep(0.2)
                        continue
                    result = str(status.get("lastCommandResult", "")).lower()
                    if result in {"accepted", "ok"}:
                        return
                    if result in {"rejected", "error"}:
                        err = str(status.get("lastCommandError", "")).strip()
                        raise RuntimeError(f"bridge command failed: {err or result}")
                    time.sleep(0.2)
                raise RuntimeError("Timed out waiting for bridge command status")

            phrases = [str(v) for v in task.get("contains_any", [])]
            timeout = float(task.get("timeout_seconds", 30.0))
            deadline = time.time() + timeout
            while time.time() < deadline:
                obs = self.perception.observe()
                combined = " ".join([obs.chat_text, obs.full_text_hint]).strip()
                if self.perception.contains_any(combined, phrases):
                    if max(obs.chat_confidence, obs.full_confidence) >= self.config.ocr.min_confidence:
                        return
                if self.baritone.controller.stop_requested:
                    raise RuntimeError("Stop requested")
                self.baritone.sync_manual_pause_with_controller()
                self.baritone.controller.wait_if_paused()
                self.baritone.sync_manual_pause_with_controller()
                time.sleep(0.5)
            raise RuntimeError("Timed out waiting for chat phrase")

        if task_type == "craft_item":
            item = str(task.get("item", "")).strip()
            if not item:
                raise RuntimeError("craft_item task missing 'item'")
            count = int(task.get("count", 1))
            if count < 1:
                raise RuntimeError("craft_item count must be >= 1")
            raw_templates = task.get("commands")
            templates = [str(v) for v in raw_templates] if isinstance(raw_templates, list) else None
            timeout = float(task.get("timeout_seconds", self.config.baritone.idle_timeout_seconds))
            self._craft_item(item, count, templates=templates, timeout=timeout)
            return

        if task_type == "ensure_baritone_settings":
            commands = task.get("commands", [])
            if not isinstance(commands, list) or not commands:
                raise RuntimeError("ensure_baritone_settings requires non-empty 'commands' list")
            for raw in commands:
                cmd = str(raw).strip()
                if not cmd:
                    continue
                self.baritone.command(cmd)
            return

        if task_type == "sleep_seconds":
            seconds = float(task.get("seconds", 0.0))
            if seconds < 0:
                raise RuntimeError("sleep_seconds cannot be negative")
            self._sleep_interruptible(seconds)
            return

        if task_type == "craft_with_dependencies":
            item = str(task.get("item", "")).strip()
            if not item:
                raise RuntimeError("craft_with_dependencies requires 'item'")
            count = int(task.get("count", 1))
            max_depth = int(task.get("max_depth", 8))
            raw_templates = task.get("commands")
            templates = [str(v) for v in raw_templates] if isinstance(raw_templates, list) else None
            self._craft_with_dependencies(
                item=item,
                count=max(1, count),
                max_depth=max(1, max_depth),
                templates=templates,
            )
            return

        if task_type == "quest_item_acquire":
            item = str(task.get("item", "")).strip()
            if not item:
                raise RuntimeError("quest_item_acquire requires 'item'")
            count = int(task.get("count", 1))
            optional = bool(task.get("optional", False))
            self._quest_item_acquire(item=item, count=max(1, count), optional=optional, state=state)
            return

        if task_type == "place_storage_chest":
            count = int(task.get("count", 1))
            optional = bool(task.get("optional", False))
            self._place_storage_chest(count=max(1, count), optional=optional)
            return

        if task_type == "store_inventory_in_chest":
            radius = int(task.get("radius", 6))
            max_stacks = int(task.get("max_stacks", 24))
            include_hotbar = bool(task.get("include_hotbar", False))
            optional = bool(task.get("optional", False))
            self._store_inventory_in_chest(
                radius=radius,
                max_stacks=max_stacks,
                include_hotbar=include_hotbar,
                optional=optional,
            )
            return

        if task_type == "quest_claim_all":
            custom = task.get("commands")
            commands = [str(v) for v in custom] if isinstance(custom, list) else None
            self._quest_claim_all(commands=commands)
            return

        if task_type == "run_module":
            name = str(task.get("name", "")).strip()
            if not name:
                raise RuntimeError("run_module requires 'name'")
            params = dict(task.get("params", {})) if isinstance(task.get("params"), dict) else {}
            self._run_module(name=name, params=params, state=state)
            return

        if task_type == "set_flag":
            flag = str(task["flag"])
            state.completed_flags.add(flag)
            return

        if task_type == "note":
            txt = str(task.get("text", ""))
            log.info("NOTE: %s", txt)
            return

        raise RuntimeError(f"Unknown task type: {task_type}")

    def _run_task_with_retry(self, task: dict[str, Any], state: AgentState) -> None:
        task_type = str(task.get("type", "<unknown>"))
        task_optional = bool(task.get("optional", False))
        if task_optional and task_type in {"quest_item_acquire", "place_storage_chest", "store_inventory_in_chest"}:
            max_attempts = 1
        elif "max_retries" in task:
            max_attempts = max(1, int(task["max_retries"]))
        elif task_type in {
            "wait_baritone_idle",
            "wait_chat_phrase",
            "sleep_seconds",
            "ensure_baritone_settings",
            "quest_claim_all",
        }:
            # Retrying waits without reissuing the action often just burns timeout.
            max_attempts = 1
        else:
            max_attempts = max(1, self.config.planner.task_max_retries)

        for attempt in range(1, max_attempts + 1):
            try:
                self._set_operator_loop_state(
                    state,
                    decision="continue_task",
                    task=task,
                    next_task=task,
                    nextTaskReason=f"task attempt {attempt}/{max_attempts}",
                    last_blocker="",
                )
                self._write_live_status(
                    state,
                    "task_running",
                    task=task,
                    attempt=attempt,
                    message=f"running task type={task_type}",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="continue_task",
                    current_task=task,
                    next_task=task,
                    next_task_reason=f"task attempt {attempt}/{max_attempts}",
                )
                self._run_task_once(task, state)
                self._set_operator_loop_state(
                    state,
                    decision="continue_after_task_complete",
                    task=None,
                    previous_task=task,
                    nextTaskReason="task completed successfully; runtime will continue",
                    last_blocker="",
                )
                self._write_live_status(
                    state,
                    "task_complete",
                    task=task,
                    attempt=attempt,
                    message=f"task complete type={task_type}",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="continue_after_task_complete",
                    previous_task=task,
                    current_task=None,
                    next_task_reason="task completed successfully; runtime will continue",
                    last_blocker="",
                )
                return
            except Exception as exc:
                state.last_error = f"task={task_type} attempt={attempt} error={exc}"
                self._append_debug_learning(
                    {
                        "event": "task_error",
                        "taskType": task_type,
                        "attempt": attempt,
                        "error": str(exc),
                        "goalId": state.current_goal_id,
                    }
                )
                self._set_operator_loop_state(
                    state,
                    decision="retry_task" if attempt < max_attempts else "task_failed",
                    task=task,
                    next_task=task if attempt < max_attempts else None,
                    nextTaskReason=(
                        f"retrying task after bounded backoff ({attempt}/{max_attempts})"
                        if attempt < max_attempts
                        else "task exhausted retry budget"
                    ),
                    last_blocker=str(exc),
                )
                self._write_live_status(
                    state,
                    "task_error",
                    task=task,
                    attempt=attempt,
                    error=str(exc),
                    message=f"task failed type={task_type}",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="retry_task" if attempt < max_attempts else "task_failed",
                    current_task=task,
                    next_task=task if attempt < max_attempts else None,
                    next_task_reason=(
                        f"retrying task after bounded backoff ({attempt}/{max_attempts})"
                        if attempt < max_attempts
                        else "task exhausted retry budget"
                    ),
                    last_blocker=str(exc),
                )
                if self._is_bridge_unavailable_error(exc):
                    raise BridgeUnavailableError(str(exc)) from exc
                if isinstance(exc, BaritoneCommandValidationError):
                    raise
                if task_type == "quest_item_acquire" and self._is_unrecoverable_quest_acquire_error(exc):
                    blocked_item = self._quest_acquire_error_item_id(exc, task)
                    self._mark_quest_item_block(
                        state,
                        blocked_item,
                        source=f"task_error:{task_type}",
                    )
                    if isinstance(exc, UnrecoverableQuestAcquireError):
                        if blocked_item and blocked_item != exc.item_id:
                            raise UnrecoverableQuestAcquireError(str(exc), item_id=blocked_item) from exc
                        raise
                    raise UnrecoverableQuestAcquireError(
                        f"unrecoverable quest acquisition error for {blocked_item or '<unknown>'}: {exc}",
                        item_id=blocked_item,
                    ) from exc
                if attempt >= max_attempts:
                    raise
                delay = self._backoff_delay(attempt)
                log.warning("Task '%s' failed attempt %d/%d: %s. Retrying in %.2fs", task_type, attempt, max_attempts, exc, delay)
                time.sleep(delay)

    def run(self, exit_when_idle: bool = False, max_cycles: int = 0) -> None:
        state_path = Path(self.config.planner.state_file).resolve()
        state = _load_state(state_path)
        normalize_state = getattr(self.planner, "normalize_state", None)
        if callable(normalize_state):
            try:
                changed, summary = normalize_state(state)
            except Exception as exc:
                log.warning("State normalization failed: %s", exc)
            else:
                if changed:
                    log.info("Normalized planner state: %s", summary)
                    _save_state(state_path, state)

        now = time.time()
        loaded_cooldowns: dict[str, float] = {}
        for key, value in dict(getattr(state, "optional_item_cooldowns", {})).items():
            token = str(key).strip().lower()
            if not token:
                continue
            try:
                until = float(value)
            except Exception:
                continue
            if until <= now:
                continue
            loaded_cooldowns[token] = until
        loaded_failures: dict[str, int] = {}
        for key, value in dict(getattr(state, "optional_item_failures", {})).items():
            token = str(key).strip().lower()
            if not token:
                continue
            try:
                count = int(value)
            except Exception:
                continue
            if count <= 0:
                continue
            loaded_failures[token] = count
        self._optional_item_cooldowns = loaded_cooldowns
        self._optional_item_failures = loaded_failures
        state.optional_item_cooldowns = dict(loaded_cooldowns)
        state.optional_item_failures = dict(loaded_failures)
        log.info("Loaded state: %s", state.to_json())
        self._set_operator_loop_state(
            state,
            decision="startup",
            currentTask="",
            previousCompletedTask="",
            nextSelectedTask="",
            nextTaskReason="runtime started",
            lastBlocker=str(state.last_error or "").strip(),
        )
        self._write_live_status(state, "startup", message="runtime started")
        self._ensure_base_anchor()

        if state.safe_paused:
            log.warning("State is safe-paused. Clear state.safe_paused to resume.")
            self._set_operator_loop_state(
                state,
                decision="safe_pause",
                nextTaskReason="state.safe_paused was already set on startup",
                last_blocker=str(state.last_error or "safe paused"),
            )
            self._write_live_status(state, "safe_paused", error=str(state.last_error or "safe paused"))
            self._log_operator_loop_decision(
                state,
                decision="safe_pause",
                next_task_reason="state.safe_paused was already set on startup",
                last_blocker=str(state.last_error or "safe paused"),
            )
            _save_state(state_path, state)
            return

        dead_respawn_hard_stop_enabled = self._dead_respawn_hard_stop_enabled()
        watchdog_enabled, watchdog_max_runtime_seconds, watchdog_checkpoint_seconds = self._runtime_watchdog_settings()
        runtime_started_at = time.monotonic()
        next_runtime_checkpoint_at = runtime_started_at + watchdog_checkpoint_seconds

        if dead_respawn_hard_stop_enabled and self.config.baritone.transport.strip().lower() == "bridge_file":
            log.info("Bridge dead/respawn hard stop is enabled.")
        if watchdog_enabled:
            log.info(
                "Runtime watchdog enabled (max_continuous_runtime_seconds=%.1f, idle_checkpoint_seconds=%.1f).",
                watchdog_max_runtime_seconds,
                watchdog_checkpoint_seconds,
            )

        def _maybe_runtime_watchdog_checkpoint(goal_id: str | None = None) -> bool:
            nonlocal next_runtime_checkpoint_at
            if not watchdog_enabled:
                return False
            now_monotonic = time.monotonic()
            if now_monotonic < next_runtime_checkpoint_at:
                return False

            elapsed = max(0.0, now_monotonic - runtime_started_at)
            next_runtime_checkpoint_at = now_monotonic + watchdog_checkpoint_seconds
            self._write_live_status(
                state,
                "runtime_watchdog_checkpoint",
                goal_id=goal_id,
                message=(
                    f"safe idle checkpoint elapsed={elapsed:.1f}s/"
                    f"{watchdog_max_runtime_seconds:.1f}s"
                ),
            )
            _save_state(state_path, state)

            if elapsed < watchdog_max_runtime_seconds:
                return False

            reason = (
                "runtime_watchdog_max_continuous_runtime "
                f"elapsed_seconds={elapsed:.1f} "
                f"max_seconds={watchdog_max_runtime_seconds:.1f}"
            )
            try:
                self.baritone.pause_pathing()
            except Exception as exc:
                log.warning("Failed to send pause for runtime watchdog stop: %s", exc)
            self._enter_safe_pause(
                state,
                state_path,
                goal_id=goal_id,
                error=reason,
                message="max continuous runtime reached at safe idle checkpoint",
                phase="runtime_watchdog_stop",
            )
            log.warning(
                "Runtime watchdog stop at safe idle checkpoint after %.1fs (limit %.1fs).",
                elapsed,
                watchdog_max_runtime_seconds,
            )
            return True

        cycles = 0
        idle_cycles = 0
        debug_rearms: dict[str, int] = {}

        while True:
            try:
                self.baritone.sync_manual_pause_with_controller()
                self.baritone.controller.wait_if_paused()
                self.baritone.sync_manual_pause_with_controller()
            except RuntimeError:
                log.warning("Stop requested, exiting run loop")
                self._write_live_status(state, "stopped", message="stop requested while paused")
                _save_state(state_path, state)
                return
            cycles += 1
            if max_cycles > 0 and cycles > max_cycles:
                log.info("Reached max_cycles=%d, exiting", max_cycles)
                self._write_live_status(state, "stopped", message=f"max_cycles reached ({max_cycles})")
                _save_state(state_path, state)
                return

            if self.baritone.controller.stop_requested:
                log.warning("Stop requested, exiting run loop")
                self._write_live_status(state, "stopped", message="stop requested")
                _save_state(state_path, state)
                return

            if self._maybe_hard_stop_for_dead_respawn(
                state,
                state_path,
                goal_id=state.current_goal_id,
                enabled=dead_respawn_hard_stop_enabled,
            ):
                return
            if _maybe_runtime_watchdog_checkpoint(goal_id=state.current_goal_id):
                return

            goal = self.planner.next_goal(
                state,
                planner_config=self.config.planner,
                strategy_memory=self._get_strategy_memory(),
                run_mode=self.config.planner.run_mode,
            )
            if goal is None:
                idle_cycles += 1
                log.info("No remaining ready goals. idle_cycles=%d", idle_cycles)
                self._set_operator_loop_state(
                    state,
                    decision="idle_wait",
                    task=None,
                    next_task=None,
                    nextTaskReason=f"planner returned no ready goals (idle_cycles={idle_cycles})",
                    last_blocker=str(state.last_error or ""),
                )
                self._write_live_status(
                    state,
                    "idle",
                    message=f"no ready goals (idle_cycles={idle_cycles})",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="idle_wait",
                    current_task=None,
                    next_task=None,
                    next_task_reason=f"planner returned no ready goals (idle_cycles={idle_cycles})",
                    last_blocker=str(state.last_error or ""),
                )
                _save_state(state_path, state)
                if exit_when_idle:
                    return
                max_idle = self.config.planner.max_idle_cycles
                if max_idle > 0 and idle_cycles >= max_idle:
                    return
                if self._run_idle_inventory_pressure(state):
                    _save_state(state_path, state)
                    time.sleep(self.config.planner.tick_seconds)
                    continue
                if state.safe_paused:
                    _save_state(state_path, state)
                    return
                ran_idle_cycle = self._run_idle_maintenance_cycle(state, idle_cycles)
                if ran_idle_cycle:
                    _save_state(state_path, state)
                    idle_cycles = 0
                    continue
                time.sleep(self.config.planner.tick_seconds)
                continue

            idle_cycles = 0
            blocked_items = self._goal_blocked_required_quest_items(goal, state)
            if blocked_items:
                blocked_item = blocked_items[0]
                blocked_summary = ", ".join(blocked_items[:3])
                if len(blocked_items) > 3:
                    blocked_summary = f"{blocked_summary}, +{len(blocked_items) - 3} more"
                block_flag = self._mark_quest_item_block(
                    state,
                    blocked_item,
                    source="pre_goal_skip_block_marker",
                )
                self._mark_goal_complete(state, goal, include_completion_flags=False)
                state.current_goal_id = None
                state.last_error = (
                    f"goal={goal.id} skipped blocked_required_item={blocked_item} "
                    f"flag={block_flag}"
                )
                self._append_debug_learning(
                    {
                        "event": "goal_skipped_blocked_required_item",
                        "goalId": goal.id,
                        "itemId": blocked_item,
                        "flag": block_flag,
                    }
                )
                _save_state(state_path, state)
                self._write_live_status(
                    state,
                    "goal_skipped_blocked_item",
                    goal_id=goal.id,
                    error=state.last_error or "",
                    message=f"required quest item blocked: {blocked_summary}",
                )
                log.warning("Skipping goal '%s': required quest item is blocked (%s)", goal.id, blocked_summary)
                continue
            log.info("Executing goal: %s - %s", goal.id, goal.description)
            state.current_goal_id = goal.id
            next_task = goal.tasks[0] if getattr(goal, "tasks", None) else None
            self._set_operator_loop_state(
                state,
                decision="continue_goal",
                task=None,
                next_task=next_task if isinstance(next_task, dict) else None,
                nextTaskReason=f"planner selected ready goal {goal.id}",
                last_blocker="",
            )
            self._write_live_status(
                state,
                "goal_running",
                goal_id=goal.id,
                message=goal.description,
            )
            self._log_operator_loop_decision(
                state,
                decision="continue_goal",
                current_task=None,
                next_task=next_task if isinstance(next_task, dict) else None,
                next_task_reason=f"planner selected ready goal {goal.id}",
                last_blocker="",
            )
            _save_state(state_path, state)

            try:
                for task in goal.tasks:
                    if self._maybe_hard_stop_for_dead_respawn(
                        state,
                        state_path,
                        goal_id=goal.id,
                        enabled=dead_respawn_hard_stop_enabled,
                    ):
                        return
                    self._run_task_with_retry(task, state)
                    if self._maybe_hard_stop_for_dead_respawn(
                        state,
                        state_path,
                        goal_id=goal.id,
                        enabled=dead_respawn_hard_stop_enabled,
                    ):
                        return
                    _save_state(state_path, state)

                self._mark_goal_complete(state, goal, include_completion_flags=False)
                state.current_goal_id = None
                state.last_error = None
                _save_state(state_path, state)
                self._set_operator_loop_state(
                    state,
                    decision="continue_after_goal_complete",
                    task=None,
                    next_task=None,
                    nextTaskReason=f"goal {goal.id} completed; selecting next ready goal",
                    last_blocker="",
                )
                self._write_live_status(
                    state,
                    "goal_complete",
                    goal_id=goal.id,
                    message="goal complete",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="continue_after_goal_complete",
                    current_task=None,
                    next_task=None,
                    next_task_reason=f"goal {goal.id} completed; selecting next ready goal",
                    last_blocker="",
                )
                log.info("Goal complete: %s", goal.id)

            except BridgeUnavailableError as exc:
                if self._maybe_hard_stop_for_dead_respawn(
                    state,
                    state_path,
                    goal_id=goal.id,
                    enabled=dead_respawn_hard_stop_enabled,
                ):
                    return
                state.current_goal_id = None
                state.last_error = f"goal={goal.id} bridge_unavailable error={exc}"
                _save_state(state_path, state)
                self._write_live_status(
                    state,
                    "bridge_waiting",
                    goal_id=goal.id,
                    error=str(exc),
                    message="bridge unavailable; waiting for reconnect",
                )
                wait_timeout = max(30.0, float(self.config.baritone.bridge_ready_wait_seconds))
                try:
                    status = self.baritone.wait_for_bridge_ready(timeout_seconds=wait_timeout)
                    player = str(status.get("playerName", "")).strip()
                    state.last_error = None
                    _save_state(state_path, state)
                    self._write_live_status(
                        state,
                        "bridge_recovered",
                        goal_id=goal.id,
                        message=f"bridge reconnected{f' (player={player})' if player else ''}",
                    )
                except Exception as wait_exc:
                    state.last_error = f"goal={goal.id} bridge_wait_timeout error={wait_exc}"
                    _save_state(state_path, state)
                    self._write_live_status(
                        state,
                        "bridge_wait_timeout",
                        goal_id=goal.id,
                        error=str(wait_exc),
                        message=f"bridge did not recover in {wait_timeout:.1f}s",
                    )
                    log.warning(
                        "Bridge unavailable during goal '%s' and did not recover in %.1fs: %s",
                        goal.id,
                        wait_timeout,
                        wait_exc,
                    )
                    time.sleep(max(1.0, float(self.config.planner.tick_seconds)))
                continue

            except UnrecoverableQuestAcquireError as exc:
                blocked_item = str(exc.item_id).strip().lower()
                if not blocked_item:
                    blocked_item = self._extract_item_id_from_error(str(exc))
                transient_item = bool(blocked_item) and not self._should_persist_block_flag(blocked_item)
                if goal.id.startswith("quest_") and transient_item:
                    defer_seconds = max(15.0, float(self.config.planner.quest_transient_defer_seconds))
                    defer_until = time.time() + defer_seconds
                    state.goal_defer_until[goal.id] = defer_until
                    state.goal_retries[goal.id] = 0
                    state.current_goal_id = None
                    state.last_error = (
                        f"goal={goal.id} deferred transient_quest_item={blocked_item} "
                        f"defer_seconds={defer_seconds:.1f} error={exc}"
                    )
                    self._append_debug_learning(
                        {
                            "event": "goal_deferred_transient_quest_item",
                            "goalId": goal.id,
                            "itemId": blocked_item,
                            "deferSeconds": defer_seconds,
                            "error": str(exc),
                        }
                    )
                    _save_state(state_path, state)
                    self._set_operator_loop_state(
                        state,
                        decision="defer_goal",
                        task=None,
                        next_task=None,
                        nextTaskReason=f"transient quest item deferred for {defer_seconds:.0f}s",
                        last_blocker=str(exc),
                    )
                    self._write_live_status(
                        state,
                        "goal_deferred_transient_item",
                        goal_id=goal.id,
                        error=str(exc),
                        message=(
                            f"transient quest item {blocked_item} unavailable; "
                            f"deferring goal for {defer_seconds:.0f}s"
                        ),
                    )
                    self._log_operator_loop_decision(
                        state,
                        decision="defer_goal",
                        current_task=None,
                        next_task=None,
                        next_task_reason=f"transient quest item deferred for {defer_seconds:.0f}s",
                        last_blocker=str(exc),
                    )
                    log.warning(
                        "Deferring goal '%s' for %.1fs due to transient quest acquisition failure for '%s': %s",
                        goal.id,
                        defer_seconds,
                        blocked_item,
                        exc,
                    )
                    continue
                block_flag = self._mark_quest_item_block(
                    state,
                    blocked_item,
                    source="unrecoverable_quest_acquire",
                )
                self._mark_goal_complete(state, goal, include_completion_flags=True)
                state.current_goal_id = None
                state.last_error = (
                    f"goal={goal.id} skipped unrecoverable_quest_item={blocked_item or '<unknown>'} "
                    f"flag={block_flag} error={exc}"
                )
                self._append_debug_learning(
                    {
                        "event": "goal_skipped_unrecoverable_quest_acquire",
                        "goalId": goal.id,
                        "itemId": blocked_item,
                        "flag": block_flag,
                        "error": str(exc),
                    }
                )
                _save_state(state_path, state)
                self._write_live_status(
                    state,
                    "goal_skipped_unrecoverable_quest_acquire",
                    goal_id=goal.id,
                    error=str(exc),
                    message=f"unrecoverable quest acquisition for {blocked_item or '<unknown>'}; skipping goal",
                )
                log.error(
                    "Skipping goal '%s' due to unrecoverable quest acquisition failure for '%s': %s",
                    goal.id,
                    blocked_item or "<unknown>",
                    exc,
                )
                continue

            except Exception as exc:
                if self._maybe_hard_stop_for_dead_respawn(
                    state,
                    state_path,
                    goal_id=goal.id,
                    enabled=dead_respawn_hard_stop_enabled,
                ):
                    return
                if isinstance(exc, BaritoneCommandValidationError):
                    state.safe_paused = True
                    state.current_goal_id = None
                    state.last_error = f"goal={goal.id} invalid_command error={exc}"
                    _save_state(state_path, state)
                    self._set_operator_loop_state(
                        state,
                        decision="safe_pause",
                        task=None,
                        next_task=None,
                        nextTaskReason="invalid command shape detected",
                        last_blocker=str(exc),
                    )
                    self._write_live_status(
                        state,
                        "safe_paused",
                        goal_id=goal.id,
                        error=str(exc),
                        message="invalid command shape detected; automation paused",
                    )
                    self._log_operator_loop_decision(
                        state,
                        decision="safe_pause",
                        current_task=None,
                        next_task=None,
                        next_task_reason="invalid command shape detected",
                        last_blocker=str(exc),
                    )
                    log.error("Goal '%s' produced invalid command shape. Entering safe pause: %s", goal.id, exc)
                    return
                retries = state.goal_retries.get(goal.id, 0) + 1
                state.goal_retries[goal.id] = retries
                state.last_error = f"goal={goal.id} retry={retries} error={exc}"
                state.current_goal_id = None
                self._append_debug_learning(
                    {
                        "event": "goal_error",
                        "goalId": goal.id,
                        "retry": retries,
                        "maxGoalRetries": int(self.config.planner.max_goal_retries),
                        "error": str(exc),
                    }
                )

                max_goal_retries = max(1, self.config.planner.max_goal_retries)
                if retries >= max_goal_retries:
                    if goal.id.startswith("quest_"):
                        self._mark_goal_complete(state, goal, include_completion_flags=False)
                        state.current_goal_id = None
                        state.last_error = (
                            f"goal={goal.id} skipped after retry budget "
                            f"({max_goal_retries}) last_error={exc}"
                        )
                        self._append_debug_learning(
                            {
                                "event": "quest_goal_skipped_after_retries",
                                "goalId": goal.id,
                                "maxGoalRetries": int(max_goal_retries),
                                "error": str(exc),
                            }
                        )
                        _save_state(state_path, state)
                        self._write_live_status(
                            state,
                            "goal_skipped",
                            goal_id=goal.id,
                            error=str(exc),
                            message=f"quest goal skipped after retry budget ({max_goal_retries})",
                        )
                        log.error(
                            "Quest goal '%s' exceeded retry budget (%d). Skipping and continuing.",
                            goal.id,
                            max_goal_retries,
                        )
                        continue
                    if self.config.planner.auto_debug_loop_enabled:
                        rearms = debug_rearms.get(goal.id, 0) + 1
                        debug_rearms[goal.id] = rearms
                        max_rearms = max(0, int(self.config.planner.auto_debug_loop_max_rearms))
                        if max_rearms <= 0 or rearms <= max_rearms:
                            state.safe_paused = False
                            state.goal_retries[goal.id] = 0
                            delay = max(0.5, float(self.config.planner.auto_debug_loop_delay_seconds))
                            _save_state(state_path, state)
                            self._append_debug_learning(
                                {
                                    "event": "goal_auto_rearm",
                                    "goalId": goal.id,
                                    "rearmCount": rearms,
                                    "delaySeconds": delay,
                                    "error": str(exc),
                                }
                            )
                            self._write_live_status(
                                state,
                                "auto_debug_rearm",
                                goal_id=goal.id,
                                error=str(exc),
                                message=f"goal exceeded retries; auto-rearm {rearms} in {delay:.2f}s",
                            )
                            log.error(
                                "Goal '%s' exceeded retry budget (%d). Auto-debug rearm %d in %.2fs.",
                                goal.id,
                                max_goal_retries,
                                rearms,
                                delay,
                            )
                            time.sleep(delay)
                            continue
                    state.safe_paused = True
                    _save_state(state_path, state)
                    self._set_operator_loop_state(
                        state,
                        decision="safe_pause",
                        task=None,
                        next_task=None,
                        nextTaskReason=f"goal exceeded retry budget ({max_goal_retries})",
                        last_blocker=str(exc),
                    )
                    self._write_live_status(
                        state,
                        "safe_paused",
                        goal_id=goal.id,
                        error=str(exc),
                        message=f"goal exceeded retry budget ({max_goal_retries})",
                    )
                    self._log_operator_loop_decision(
                        state,
                        decision="safe_pause",
                        current_task=None,
                        next_task=None,
                        next_task_reason=f"goal exceeded retry budget ({max_goal_retries})",
                        last_blocker=str(exc),
                    )
                    log.error("Goal '%s' exceeded retry budget (%d). Entering safe pause.", goal.id, max_goal_retries)
                    return

                delay = self._backoff_delay(retries)
                _save_state(state_path, state)
                self._set_operator_loop_state(
                    state,
                    decision="retry_goal",
                    task=None,
                    next_task=None,
                    nextTaskReason=f"goal retry {retries}/{max_goal_retries} in {delay:.2f}s",
                    last_blocker=str(exc),
                )
                self._write_live_status(
                    state,
                    "goal_retrying",
                    goal_id=goal.id,
                    error=str(exc),
                    message=f"goal retry {retries}/{max_goal_retries} in {delay:.2f}s",
                )
                self._log_operator_loop_decision(
                    state,
                    decision="retry_goal",
                    current_task=None,
                    next_task=None,
                    next_task_reason=f"goal retry {retries}/{max_goal_retries} in {delay:.2f}s",
                    last_blocker=str(exc),
                )
                log.warning("Goal '%s' failed retry %d/%d: %s. Retrying in %.2fs", goal.id, retries, max_goal_retries, exc, delay)
                time.sleep(delay)
