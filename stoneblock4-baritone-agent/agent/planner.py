from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent.knowledge import Goal

_MISSING = object()
_DEFAULT_RUN_MODE = "strict_progression"
_VALID_RUN_MODES = {"strict_progression", "idle_grind", "recovery_only"}
_RUN_MODE_ALIASES = {
    "strict": "strict_progression",
    "progression": "strict_progression",
    "idle": "idle_grind",
    "grind": "idle_grind",
    "recovery": "recovery_only",
}
_IDLE_MODE_HINTS = ("idle", "grind", "housekeeping", "maintenance")
_RECOVERY_MODE_HINTS = ("recovery", "recover", "unstuck", "panic", "triage")
_NON_ACTION_TASK_TYPES = {"note", "set_flag", "sleep_seconds", "quest_claim_all"}
_OPTIONAL_RETRY_BUDGET_CHECK_HELPERS = (
    "is_optional_item_retry_allowed",
    "can_retry_optional_item",
    "should_retry_optional_item",
    "optional_retry_allowed",
    "within_optional_retry_budget",
    "is_within_optional_retry_budget",
    "check_optional_retry_budget",
    "check_optional_item_retry_budget",
)
_OPTIONAL_RETRY_BUDGET_HOOK_HELPERS = (
    "record_optional_retry_budget_event",
    "record_optional_retry_event",
    "note_optional_retry_budget_event",
    "note_optional_retry_event",
)


@dataclass
class AgentState:
    completed_flags: set[str] = field(default_factory=set)
    completed_goals: set[str] = field(default_factory=set)
    current_goal_id: str | None = None
    goal_retries: dict[str, int] = field(default_factory=dict)
    goal_defer_until: dict[str, float] = field(default_factory=dict)
    optional_item_cooldowns: dict[str, float] = field(default_factory=dict)
    optional_item_failures: dict[str, int] = field(default_factory=dict)
    optional_item_last_failure_at: dict[str, float] = field(default_factory=dict)
    safe_paused: bool = False
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        defer_payload: dict[str, float] = {}
        for key, value in dict(self.goal_defer_until).items():
            token = str(key).strip()
            if not token:
                continue
            try:
                until = float(value)
            except Exception:
                continue
            if until <= 0.0:
                continue
            defer_payload[token] = until
        optional_cooldowns: dict[str, float] = {}
        for key, value in dict(self.optional_item_cooldowns).items():
            token = str(key).strip().lower()
            if not token:
                continue
            try:
                until = float(value)
            except Exception:
                continue
            if until <= 0.0:
                continue
            optional_cooldowns[token] = until
        optional_failures: dict[str, int] = {}
        for key, value in dict(self.optional_item_failures).items():
            token = str(key).strip().lower()
            if not token:
                continue
            try:
                count = int(value)
            except Exception:
                continue
            if count <= 0:
                continue
            optional_failures[token] = count
        optional_last_failure_at: dict[str, float] = {}
        for key, value in dict(self.optional_item_last_failure_at).items():
            token = str(key).strip().lower()
            if not token:
                continue
            try:
                ts = float(value)
            except Exception:
                continue
            if ts <= 0.0:
                continue
            optional_last_failure_at[token] = ts
        return {
            "completed_flags": sorted(self.completed_flags),
            "completed_goals": sorted(self.completed_goals),
            "current_goal_id": self.current_goal_id,
            "goal_retries": self.goal_retries,
            "goal_defer_until": defer_payload,
            "optional_item_cooldowns": optional_cooldowns,
            "optional_item_failures": optional_failures,
            "optional_item_last_failure_at": optional_last_failure_at,
            "safe_paused": self.safe_paused,
            "last_error": self.last_error,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AgentState":
        goal_retries: dict[str, int] = {}
        for k, v in dict(data.get("goal_retries", {})).items():
            key = str(k).strip()
            if not key:
                continue
            try:
                count = int(v)
            except Exception:
                continue
            goal_retries[key] = count

        goal_defer_until: dict[str, float] = {}
        for k, v in dict(data.get("goal_defer_until", {})).items():
            key = str(k).strip()
            if not key:
                continue
            try:
                until = float(v)
            except Exception:
                continue
            goal_defer_until[key] = until

        optional_item_cooldowns: dict[str, float] = {}
        for k, v in dict(data.get("optional_item_cooldowns", {})).items():
            key = str(k).strip().lower()
            if not key:
                continue
            try:
                until = float(v)
            except Exception:
                continue
            optional_item_cooldowns[key] = until

        optional_item_failures: dict[str, int] = {}
        for k, v in dict(data.get("optional_item_failures", {})).items():
            key = str(k).strip().lower()
            if not key:
                continue
            try:
                count = int(v)
            except Exception:
                continue
            optional_item_failures[key] = count
        optional_item_last_failure_at: dict[str, float] = {}
        for k, v in dict(data.get("optional_item_last_failure_at", {})).items():
            key = str(k).strip().lower()
            if not key:
                continue
            try:
                ts = float(v)
            except Exception:
                continue
            optional_item_last_failure_at[key] = ts

        return cls(
            completed_flags=set(data.get("completed_flags", [])),
            completed_goals=set(data.get("completed_goals", [])),
            current_goal_id=data.get("current_goal_id"),
            goal_retries=goal_retries,
            goal_defer_until=goal_defer_until,
            optional_item_cooldowns=optional_item_cooldowns,
            optional_item_failures=optional_item_failures,
            optional_item_last_failure_at=optional_item_last_failure_at,
            safe_paused=bool(data.get("safe_paused", False)),
            last_error=data.get("last_error"),
        )


class GoalPlanner:
    def __init__(
        self,
        goals: list[Goal],
        planner_config: Any | None = None,
        strategy_memory: Any | None = None,
    ) -> None:
        self.goals = goals
        self._planner_config = planner_config
        self._strategy_memory = strategy_memory

    @staticmethod
    def _goal_ready(goal: Goal, state: AgentState) -> bool:
        return all(req in state.completed_flags for req in goal.prerequisites)

    @staticmethod
    def _goal_done(goal: Goal, state: AgentState) -> bool:
        if goal.id in state.completed_goals:
            return True
        if not goal.completion_flags:
            return False
        return all(flag in state.completed_flags for flag in goal.completion_flags)

    @staticmethod
    def _coerce_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        try:
            count = int(value)
        except Exception:
            return None
        if count <= 0:
            return None
        return count

    @staticmethod
    def _normalize_run_mode(value: Any) -> str:
        raw = str(value if value is not None else "").strip().lower()
        token = raw.replace("-", "_").replace(" ", "_")
        if not token:
            return _DEFAULT_RUN_MODE
        resolved = _RUN_MODE_ALIASES.get(token, token)
        if resolved not in _VALID_RUN_MODES:
            return _DEFAULT_RUN_MODE
        return resolved

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}

    @classmethod
    def _contains_any(cls, text: str, hints: tuple[str, ...]) -> bool:
        tokens = cls._tokens(text)
        return any(hint in tokens for hint in hints)

    @staticmethod
    def _normalized_map(raw: Any, *, lowercase_keys: bool = True) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, value in raw.items():
            token = str(key).strip()
            if lowercase_keys:
                token = token.lower()
            if not token:
                continue
            normalized[token] = value
        return normalized

    @staticmethod
    def _resolve_config_value(source: Any, *names: str, default: Any = None) -> Any:
        if not names:
            return default
        candidates: list[Any] = []
        if source is not None:
            candidates.append(source)
            planner_attr = getattr(source, "planner", None)
            if planner_attr is not None:
                candidates.insert(0, planner_attr)
            if isinstance(source, dict):
                planner_dict = source.get("planner")
                if isinstance(planner_dict, dict):
                    candidates.insert(0, planner_dict)
        for candidate in candidates:
            for name in names:
                if isinstance(candidate, dict) and name in candidate:
                    return candidate.get(name)
                if hasattr(candidate, name):
                    return getattr(candidate, name)
        return default

    def _resolve_run_mode(
        self,
        state: AgentState,
        *,
        planner_config: Any | None = None,
        run_mode: str | None = None,
    ) -> str:
        if run_mode is not None:
            return self._normalize_run_mode(run_mode)
        cfg_source = planner_config if planner_config is not None else self._planner_config
        cfg_mode = self._resolve_config_value(cfg_source, "run_mode", default=None)
        if cfg_mode is not None:
            return self._normalize_run_mode(cfg_mode)
        state_mode = getattr(state, "run_mode", None)
        if state_mode is not None:
            return self._normalize_run_mode(state_mode)
        state_mode = getattr(state, "planner_run_mode", None)
        if state_mode is not None:
            return self._normalize_run_mode(state_mode)
        return _DEFAULT_RUN_MODE

    def _goal_mode_hints(self, goal: Goal) -> tuple[bool, bool]:
        blob = str(goal.id).strip().lower()
        idle = self._contains_any(blob, _IDLE_MODE_HINTS)
        recovery = self._contains_any(blob, _RECOVERY_MODE_HINTS)
        for task in goal.tasks:
            if not isinstance(task, dict):
                continue
            task_type = str(task.get("type", "")).strip().lower()
            if task_type == "run_module":
                module_name = str(task.get("name", "")).strip().lower()
                if module_name:
                    idle = idle or self._contains_any(module_name, _IDLE_MODE_HINTS)
                    recovery = recovery or self._contains_any(module_name, _RECOVERY_MODE_HINTS)
                continue
            if task_type in {"store_inventory_in_chest", "place_storage_chest"} and bool(task.get("optional", True)):
                idle = True
            if task_type in {"run_recovery_playbook", "recovery_playbook"}:
                recovery = True
        return idle, recovery

    def _goal_allowed_for_mode(self, goal: Goal, run_mode: str) -> bool:
        mode = self._normalize_run_mode(run_mode)
        idle, recovery = self._goal_mode_hints(goal)
        if mode == "strict_progression":
            return not idle and not recovery
        if mode == "idle_grind":
            return idle or recovery
        if mode == "recovery_only":
            return recovery
        return True

    @staticmethod
    def _goal_optional_items(goal: Goal) -> set[str]:
        items: set[str] = set()
        for task in goal.tasks:
            if not isinstance(task, dict):
                continue
            task_type = str(task.get("type", "")).strip().lower()
            if task_type != "quest_item_acquire":
                continue
            if not bool(task.get("optional", False)):
                continue
            item_id = str(task.get("item", "")).strip().lower()
            if item_id:
                items.add(item_id)
        return items

    @staticmethod
    def _goal_has_required_action(goal: Goal) -> bool:
        for task in goal.tasks:
            if not isinstance(task, dict):
                continue
            task_type = str(task.get("type", "")).strip().lower()
            if not task_type or task_type in _NON_ACTION_TASK_TYPES:
                continue
            if bool(task.get("optional", False)) and task_type in {
                "quest_item_acquire",
                "place_storage_chest",
                "store_inventory_in_chest",
            }:
                continue
            return True
        return False

    def _optional_retry_budget_for_item(
        self,
        item_id: str,
        *,
        planner_config: Any | None,
        state: AgentState,
    ) -> int | None:
        per_item = self._resolve_config_value(
            planner_config,
            "optional_item_retry_budgets",
            "optional_retry_budgets",
            "optional_item_retry_budget_by_item",
            "optional_retry_budget_by_item",
            default=_MISSING,
        )
        if per_item is _MISSING:
            per_item = self._resolve_config_value(
                state,
                "optional_item_retry_budgets",
                "optional_retry_budgets",
                default=_MISSING,
            )
        if isinstance(per_item, dict):
            table = self._normalized_map(per_item, lowercase_keys=True)
            for key in (item_id, "*", "default", "_default"):
                if key not in table:
                    continue
                parsed = self._coerce_positive_int(table.get(key))
                if parsed is not None:
                    return parsed
        raw_default = self._resolve_config_value(
            planner_config,
            "optional_item_retry_budget",
            "optional_retry_budget",
            "optional_item_retry_max",
            "optional_item_max_retries",
            "optional_retry_max",
            "optional_retry_max_attempts",
            "optional_item_retry_budget_max",
            default=_MISSING,
        )
        if raw_default is _MISSING:
            raw_default = self._resolve_config_value(
                state,
                "optional_item_retry_budget",
                "optional_retry_budget",
                default=_MISSING,
            )
        if raw_default is _MISSING:
            return None
        return self._coerce_positive_int(raw_default)

    def _optional_retry_decay_seconds(self, *, planner_config: Any | None, state: AgentState) -> float:
        raw_seconds = self._resolve_config_value(
            planner_config,
            "optional_item_retry_budget_decay_seconds",
            "optional_item_retry_decay_seconds",
            "optional_retry_budget_decay_seconds",
            "optional_retry_decay_seconds",
            default=_MISSING,
        )
        if raw_seconds is _MISSING:
            raw_seconds = self._resolve_config_value(
                state,
                "optional_item_retry_budget_decay_seconds",
                "optional_retry_budget_decay_seconds",
                default=_MISSING,
            )
        if raw_seconds is not _MISSING:
            seconds = max(0.0, self._coerce_float(raw_seconds, default=0.0))
            if seconds > 0.0:
                return seconds
            return 0.0
        raw_minutes = self._resolve_config_value(
            planner_config,
            "optional_item_retry_budget_decay_minutes",
            "optional_item_retry_decay_minutes",
            "optional_retry_budget_decay_minutes",
            "optional_retry_decay_minutes",
            default=_MISSING,
        )
        if raw_minutes is _MISSING:
            raw_minutes = self._resolve_config_value(
                state,
                "optional_item_retry_budget_decay_minutes",
                "optional_retry_budget_decay_minutes",
                default=_MISSING,
            )
        if raw_minutes is _MISSING:
            return 0.0
        minutes = max(0.0, self._coerce_float(raw_minutes, default=0.0))
        return minutes * 60.0

    def _optional_retry_defer_seconds(self, *, planner_config: Any | None) -> float:
        raw = self._resolve_config_value(
            planner_config,
            "optional_item_retry_budget_defer_seconds",
            "optional_retry_budget_defer_seconds",
            "optional_item_retry_defer_seconds",
            "optional_retry_defer_seconds",
            default=_MISSING,
        )
        if raw is _MISSING:
            return 30.0
        return max(0.0, self._coerce_float(raw, default=0.0))

    @staticmethod
    def _call_helper(helper: Any, payload: dict[str, Any]) -> tuple[bool, Any]:
        item_id = payload.get("item_id")
        failure_count = payload.get("failure_count")
        budget = payload.get("budget")
        now = payload.get("now")
        call_attempts = (
            lambda: helper(**payload),
            lambda: helper(item_id=item_id, failure_count=failure_count, budget=budget, now=now),
            lambda: helper(item_id=item_id, failure_count=failure_count, budget=budget),
            lambda: helper(item_id=item_id, failure_count=failure_count),
            lambda: helper(item_id=item_id),
            lambda: helper(item_id, failure_count, budget, now),
            lambda: helper(item_id, failure_count, budget),
            lambda: helper(item_id, failure_count),
            lambda: helper(item_id),
            lambda: helper(),
        )
        for attempt in call_attempts:
            try:
                return True, attempt()
            except TypeError:
                continue
            except Exception:
                return False, None
        return False, None

    def _invoke_first_helper(
        self,
        strategy_memory: Any | None,
        helper_names: tuple[str, ...],
        payload: dict[str, Any],
    ) -> tuple[bool, Any]:
        if strategy_memory is None:
            return False, None
        for name in helper_names:
            helper = getattr(strategy_memory, name, None)
            if not callable(helper):
                continue
            called, result = self._call_helper(helper, payload)
            if called:
                return True, result
        return False, None

    @staticmethod
    def _extract_retry_allowed(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, tuple) and value:
            head = value[0]
            if isinstance(head, bool):
                return head
        if isinstance(value, dict):
            for key in ("allowed", "retry_allowed", "can_retry", "within_budget", "should_retry"):
                if key in value:
                    return bool(value.get(key))
            for key in ("blocked", "over_budget", "deny", "skip"):
                if key in value:
                    return not bool(value.get(key))
        return None

    def _extract_retry_after_seconds(self, value: Any) -> float:
        if isinstance(value, tuple) and len(value) > 1:
            return max(0.0, self._coerce_float(value[1], default=0.0))
        if isinstance(value, dict):
            for key in ("retry_after_seconds", "retry_after", "cooldown_seconds"):
                if key in value:
                    return max(0.0, self._coerce_float(value.get(key), default=0.0))
        return 0.0

    def _emit_optional_retry_hook(
        self,
        strategy_memory: Any | None,
        *,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        if strategy_memory is None:
            return
        hook_payload = dict(payload)
        hook_payload["event"] = event
        self._invoke_first_helper(strategy_memory, _OPTIONAL_RETRY_BUDGET_HOOK_HELPERS, hook_payload)

    def _optional_retry_allowed(
        self,
        *,
        goal_id: str,
        item_id: str,
        state: AgentState,
        now: float,
        planner_config: Any | None,
        strategy_memory: Any | None,
        run_mode: str,
    ) -> tuple[bool, float]:
        item = str(item_id).strip().lower()
        if not item:
            return True, 0.0

        budget = self._optional_retry_budget_for_item(item, planner_config=planner_config, state=state)
        failures_raw = self._normalized_map(getattr(state, "optional_item_failures", {}), lowercase_keys=True)
        cooldowns_raw = self._normalized_map(getattr(state, "optional_item_cooldowns", {}), lowercase_keys=True)
        last_failure_map = self._normalized_map(
            getattr(
                state,
                "optional_item_last_failure_at",
                getattr(state, "optional_item_failure_timestamps", {}),
            ),
            lowercase_keys=True,
        )
        try:
            failure_count = max(0, int(failures_raw.get(item, 0)))
        except Exception:
            failure_count = 0
        cooldown_until = self._coerce_float(cooldowns_raw.get(item, 0.0), default=0.0)
        last_failure_at = self._coerce_float(last_failure_map.get(item, 0.0), default=0.0)
        decay_seconds = self._optional_retry_decay_seconds(planner_config=planner_config, state=state)

        effective_failure_count = failure_count
        if failure_count > 0 and decay_seconds > 0.0 and last_failure_at > 0.0:
            elapsed = max(0.0, now - last_failure_at)
            recovered = int(elapsed // decay_seconds)
            if recovered > 0:
                effective_failure_count = max(0, failure_count - recovered)

        helper_payload = {
            "goal_id": goal_id,
            "item_id": item,
            "run_mode": run_mode,
            "now": now,
            "budget": budget,
            "failure_count": failure_count,
            "effective_failure_count": effective_failure_count,
            "cooldown_until": cooldown_until,
            "last_failure_at": last_failure_at,
            "decay_seconds": decay_seconds,
            "state": state,
        }
        helper_called, helper_result = self._invoke_first_helper(
            strategy_memory,
            _OPTIONAL_RETRY_BUDGET_CHECK_HELPERS,
            helper_payload,
        )
        helper_decision = self._extract_retry_allowed(helper_result)
        if helper_called and helper_decision is not None:
            retry_after = self._extract_retry_after_seconds(helper_result)
            self._emit_optional_retry_hook(
                strategy_memory,
                event="optional_retry_budget_check",
                payload={
                    **helper_payload,
                    "allowed": helper_decision,
                    "retry_after_seconds": retry_after,
                    "decision_source": "strategy_memory_helper",
                },
            )
            return helper_decision, retry_after

        if budget is None:
            allowed = True
        else:
            over_budget = effective_failure_count >= budget
            cooldown_active = cooldown_until > now
            if not over_budget:
                allowed = True
            elif cooldown_active:
                allowed = False
            elif decay_seconds > 0.0 and last_failure_at > 0.0:
                allowed = False
            else:
                # No reliable decay timestamp means we should not hard-block permanently.
                allowed = True

        retry_after = 0.0
        if cooldown_until > now:
            retry_after = max(0.0, cooldown_until - now)
        elif not allowed and decay_seconds > 0.0 and last_failure_at > 0.0 and budget is not None:
            extra = max(0, effective_failure_count - budget) + 1
            target_ts = last_failure_at + (extra * decay_seconds)
            retry_after = max(0.0, target_ts - now)

        self._emit_optional_retry_hook(
            strategy_memory,
            event="optional_retry_budget_check",
            payload={
                **helper_payload,
                "allowed": allowed,
                "retry_after_seconds": retry_after,
                "decision_source": "planner_state_fallback",
            },
        )
        return allowed, retry_after

    def _goal_allowed_by_optional_retry_budget(
        self,
        goal: Goal,
        state: AgentState,
        *,
        now: float,
        planner_config: Any | None,
        strategy_memory: Any | None,
        run_mode: str,
    ) -> bool:
        optional_items = self._goal_optional_items(goal)
        if not optional_items:
            return True
        if self._goal_has_required_action(goal):
            return True

        blocked_items: list[str] = []
        shortest_retry_after = 0.0
        for item_id in sorted(optional_items):
            allowed, retry_after = self._optional_retry_allowed(
                goal_id=goal.id,
                item_id=item_id,
                state=state,
                now=now,
                planner_config=planner_config,
                strategy_memory=strategy_memory,
                run_mode=run_mode,
            )
            if allowed:
                continue
            blocked_items.append(item_id)
            if retry_after > 0.0:
                if shortest_retry_after <= 0.0:
                    shortest_retry_after = retry_after
                else:
                    shortest_retry_after = min(shortest_retry_after, retry_after)

        if not blocked_items:
            return True
        defer_seconds = shortest_retry_after if shortest_retry_after > 0.0 else self._optional_retry_defer_seconds(
            planner_config=planner_config
        )
        if defer_seconds > 0.0:
            target_until = now + max(1.0, defer_seconds)
            current_until = self._coerce_float(state.goal_defer_until.get(goal.id, 0.0), default=0.0)
            if target_until > current_until:
                state.goal_defer_until[goal.id] = target_until
        self._emit_optional_retry_hook(
            strategy_memory,
            event="optional_retry_budget_goal_deferred",
            payload={
                "goal_id": goal.id,
                "run_mode": run_mode,
                "blocked_items": blocked_items,
                "defer_seconds": defer_seconds,
                "now": now,
            },
        )
        return False

    def next_goal(
        self,
        state: AgentState,
        planner_config: Any | None = None,
        strategy_memory: Any | None = None,
        run_mode: str | None = None,
    ) -> Goal | None:
        now = time.time()
        cfg = planner_config if planner_config is not None else self._planner_config
        memory = strategy_memory if strategy_memory is not None else self._strategy_memory
        mode = self._resolve_run_mode(state, planner_config=cfg, run_mode=run_mode)
        goal_by_id = {goal.id: goal for goal in self.goals}
        for goal_id, until in list(state.goal_defer_until.items()):
            try:
                if float(until) <= now:
                    state.goal_defer_until.pop(goal_id, None)
            except Exception:
                state.goal_defer_until.pop(goal_id, None)
        pinned_goal_id = str(state.current_goal_id or "").strip()
        if pinned_goal_id:
            pinned_goal = goal_by_id.get(pinned_goal_id)
            if pinned_goal is None or self._goal_done(pinned_goal, state):
                state.current_goal_id = None
            elif self._goal_ready(pinned_goal, state) and self._goal_allowed_for_mode(pinned_goal, mode):
                defer_until = float(state.goal_defer_until.get(pinned_goal.id, 0.0))
                if defer_until <= now and self._goal_allowed_by_optional_retry_budget(
                    pinned_goal,
                    state,
                    now=now,
                    planner_config=cfg,
                    strategy_memory=memory,
                    run_mode=mode,
                ):
                    return pinned_goal
        for goal in self.goals:
            if self._goal_done(goal, state):
                continue
            defer_until = float(state.goal_defer_until.get(goal.id, 0.0))
            if defer_until > now:
                continue
            if self._goal_ready(goal, state):
                if not self._goal_allowed_for_mode(goal, mode):
                    continue
                if not self._goal_allowed_by_optional_retry_budget(
                    goal,
                    state,
                    now=now,
                    planner_config=cfg,
                    strategy_memory=memory,
                    run_mode=mode,
                ):
                    continue
                return goal
        return None

    def normalize_state(self, state: AgentState) -> tuple[bool, dict[str, int]]:
        changed = False
        summary = {
            "removed_unknown_completed_goals": 0,
            "removed_inconsistent_completed_goals": 0,
            "removed_unknown_goal_retries": 0,
            "removed_unknown_goal_defer_until": 0,
            "cleared_unknown_current_goal": 0,
            "removed_stale_quest_flags": 0,
            "normalized_optional_item_cooldowns": 0,
            "normalized_optional_item_failures": 0,
            "normalized_optional_item_last_failure_at": 0,
        }

        goal_by_id = {goal.id: goal for goal in self.goals}
        known_goal_ids = set(goal_by_id.keys())

        for goal_id in list(state.completed_goals):
            if goal_id in known_goal_ids:
                continue
            state.completed_goals.discard(goal_id)
            summary["removed_unknown_completed_goals"] += 1
            changed = True

        for goal_id in list(state.completed_goals):
            goal = goal_by_id.get(goal_id)
            if goal is None or not goal.completion_flags:
                continue
            if all(flag in state.completed_flags for flag in goal.completion_flags):
                continue
            state.completed_goals.discard(goal_id)
            summary["removed_inconsistent_completed_goals"] += 1
            changed = True

        for goal_id in list(state.goal_retries.keys()):
            if goal_id in known_goal_ids:
                continue
            state.goal_retries.pop(goal_id, None)
            summary["removed_unknown_goal_retries"] += 1
            changed = True

        for goal_id in list(state.goal_defer_until.keys()):
            if goal_id in known_goal_ids:
                continue
            state.goal_defer_until.pop(goal_id, None)
            summary["removed_unknown_goal_defer_until"] += 1
            changed = True

        if state.current_goal_id and state.current_goal_id not in known_goal_ids:
            state.current_goal_id = None
            summary["cleared_unknown_current_goal"] += 1
            changed = True

        known_completion_flags = {
            str(flag).strip()
            for goal in self.goals
            for flag in goal.completion_flags
            if str(flag).strip()
        }
        for flag in list(state.completed_flags):
            token = str(flag).strip()
            if not token.startswith("quest_done_"):
                continue
            if token in known_completion_flags:
                continue
            state.completed_flags.discard(flag)
            summary["removed_stale_quest_flags"] += 1
            changed = True

        normalized_cooldowns: dict[str, float] = {}
        for key, value in dict(getattr(state, "optional_item_cooldowns", {})).items():
            token = str(key).strip().lower()
            if not token:
                summary["normalized_optional_item_cooldowns"] += 1
                changed = True
                continue
            try:
                until = float(value)
            except Exception:
                summary["normalized_optional_item_cooldowns"] += 1
                changed = True
                continue
            if until <= 0.0:
                summary["normalized_optional_item_cooldowns"] += 1
                changed = True
                continue
            existing = normalized_cooldowns.get(token, 0.0)
            normalized_cooldowns[token] = max(existing, until)
        if normalized_cooldowns != dict(getattr(state, "optional_item_cooldowns", {})):
            state.optional_item_cooldowns = normalized_cooldowns
            changed = True

        normalized_failures: dict[str, int] = {}
        for key, value in dict(getattr(state, "optional_item_failures", {})).items():
            token = str(key).strip().lower()
            if not token:
                summary["normalized_optional_item_failures"] += 1
                changed = True
                continue
            try:
                count = int(value)
            except Exception:
                summary["normalized_optional_item_failures"] += 1
                changed = True
                continue
            if count <= 0:
                summary["normalized_optional_item_failures"] += 1
                changed = True
                continue
            existing = normalized_failures.get(token, 0)
            normalized_failures[token] = max(existing, count)
        if normalized_failures != dict(getattr(state, "optional_item_failures", {})):
            state.optional_item_failures = normalized_failures
            changed = True

        normalized_last_failure_at: dict[str, float] = {}
        for key, value in dict(getattr(state, "optional_item_last_failure_at", {})).items():
            token = str(key).strip().lower()
            if not token:
                summary["normalized_optional_item_last_failure_at"] += 1
                changed = True
                continue
            try:
                ts = float(value)
            except Exception:
                summary["normalized_optional_item_last_failure_at"] += 1
                changed = True
                continue
            if ts <= 0.0:
                summary["normalized_optional_item_last_failure_at"] += 1
                changed = True
                continue
            if token not in normalized_failures and token not in normalized_cooldowns:
                summary["normalized_optional_item_last_failure_at"] += 1
                changed = True
                continue
            existing = normalized_last_failure_at.get(token, 0.0)
            normalized_last_failure_at[token] = max(existing, ts)
        if normalized_last_failure_at != dict(getattr(state, "optional_item_last_failure_at", {})):
            state.optional_item_last_failure_at = normalized_last_failure_at
            changed = True

        return changed, summary
