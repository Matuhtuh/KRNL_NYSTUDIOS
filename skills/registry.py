"""Deterministic skill/task registry inspired by Voyager and AltoClef task wrappers."""

from __future__ import annotations

from collections.abc import Callable

from bridge.contracts import ActionResult, GameStateSnapshot
from planner.models import Action
from state.models import GameState

SkillFn = Callable[[GameStateSnapshot, Action | None, ActionResult | None], bool]


class SkillRegistry:
    """Small reusable deterministic skill set for pre/post action checks."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillFn] = {
            "inspect_state": lambda snapshot, action, result: snapshot.player.health > 0,
            "ensure_safe": lambda snapshot, action, result: snapshot.player.health > 6 and snapshot.player.hunger > 4,
            "select_hotbar_slot": self._validate_hotbar_slot,
            "turn_to_yaw_pitch": self._validate_yaw_pitch,
            "move_forward_short": lambda snapshot, action, result: bool(action and action.timeout_ticks <= 40),
            "interact_use": lambda snapshot, action, result: not snapshot.open_screen.screen_open,
            "verify_state_change": lambda snapshot, action, result: bool(result and (result.success or "state_changed" in result.postconditions)),
        }

    def run(
        self,
        name: str,
        snapshot: GameStateSnapshot,
        action: Action | None = None,
        result: ActionResult | None = None,
    ) -> bool:
        if name not in self._skills:
            raise KeyError(f"Unknown skill: {name}")
        return self._skills[name](snapshot, action, result)

    def available_skills(self) -> list[str]:
        """Expose deterministic skill names for planner/task wiring."""

        return sorted(self._skills.keys())

    @staticmethod
    def _validate_hotbar_slot(snapshot: GameStateSnapshot, action: Action | None, result: ActionResult | None) -> bool:
        if action is None:
            return False
        slot = action.parameters.get("slot")
        return isinstance(slot, int) and 0 <= slot <= 8

    @staticmethod
    def _validate_yaw_pitch(snapshot: GameStateSnapshot, action: Action | None, result: ActionResult | None) -> bool:
        if action is None:
            return False
        yaw = action.parameters.get("yaw")
        pitch = action.parameters.get("pitch")
        return isinstance(yaw, (int, float)) and isinstance(pitch, (int, float)) and -90 <= float(pitch) <= 90
