"""Deterministic executor for ordered small-plan execution against bridge transport."""

from __future__ import annotations

from agent.safety import evaluate_safety
from bridge.contracts import ActionResult
from bridge.interfaces import GameBridge
from planner.models import Action
from state.models import GameState


class DeterministicExecutor:
    """Executor that validates preconditions, dispatches action, then verifies likely state change."""

    def __init__(self, bridge: GameBridge) -> None:
        self.bridge = bridge

    def execute_one(self, action: Action, state: GameState) -> ActionResult:
        pre_snapshot = self.bridge.read_state_snapshot() if hasattr(self.bridge, "read_state_snapshot") else None
        preconditions = evaluate_safety(pre_snapshot, action) if pre_snapshot else []
        if preconditions:
            return ActionResult(
                request_id=f"rejected_{action.action_type}",
                accepted=False,
                completed=False,
                success=False,
                error_code="unsafe_state",
                message="Action blocked by safety preconditions",
                preconditions=preconditions,
                postconditions=[],
            )

        result = self.bridge.perform_action(action)
        postconditions: list[str] = []
        if pre_snapshot and hasattr(self.bridge, "read_state_snapshot"):
            post_snapshot = self.bridge.read_state_snapshot()
            if self._state_changed(pre_snapshot, post_snapshot):
                postconditions.append("state_changed")
            else:
                postconditions.append("no_observable_state_change")
            result.postconditions.extend(postconditions)
        return result

    @staticmethod
    def _state_changed(before, after) -> bool:
        moved = abs(after.player.x - before.player.x) + abs(after.player.y - before.player.y) + abs(after.player.z - before.player.z) > 0.01
        inv_changed = before.inventory.items != after.inventory.items
        screen_changed = before.open_screen != after.open_screen
        return moved or inv_changed or screen_changed
