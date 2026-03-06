"""Deterministic fallback planner for first playable local loop tests."""

from __future__ import annotations

from state.models import GameState

from .interfaces import Planner
from .models import Action, Goal, Plan, Subtask


class TinyDeterministicPlanner(Planner):
    """Builds a tiny ordered plan without LLM calls for local end-to-end bootstrapping."""

    def create_plan(self, goal: Goal, state: GameState) -> Plan:
        return Plan(
            plan_id=f"plan_{goal.goal_id}_{state.tick}",
            goal=goal,
            rationale="Deterministic bootstrap plan for bridge-loop integration.",
            subtasks=[
                Subtask(
                    subtask_id="inspect",
                    title="Inspect current state",
                    success_criteria=["Snapshot obtained"],
                    actions=[Action(action_type="noop", parameters={"reason": "inspect_state"})],
                ),
                Subtask(
                    subtask_id="move",
                    title="Issue minimal movement/look command",
                    success_criteria=["Bridge accepted movement placeholder"],
                    actions=[Action(action_type="move_look", parameters={"yaw_delta": 10.0})],
                ),
            ],
        )
