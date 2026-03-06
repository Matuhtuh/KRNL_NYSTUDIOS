"""Minimal production-oriented deterministic skill registry."""

from __future__ import annotations

from collections.abc import Callable

from bridge.contracts import ActionResult
from planner.models import Action
from state.models import GameState

SkillFn = Callable[[GameState, Action | None, ActionResult | None], bool]


class SkillRegistry:
    """Named deterministic behaviors used by the loop before/after action execution."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillFn] = {
            "inspect_state": lambda state, action, result: state.health > 0,
            "ensure_safe": lambda state, action, result: not state.in_danger,
            "execute_action": lambda state, action, result: action is not None,
            "verify_action_result": lambda state, action, result: bool(result and result.success),
        }

    def run(self, name: str, state: GameState, action: Action | None = None, result: ActionResult | None = None) -> bool:
        if name not in self._skills:
            raise KeyError(f"Unknown skill: {name}")
        return self._skills[name](state, action, result)
