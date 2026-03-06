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
                    subtask_id="orient",
                    title="Select tool slot and orient player",
                    success_criteria=["Hotbar selected and view turned"],
                    actions=[
                        Action(action_type="select_hotbar_slot", parameters={"slot": 0}, timeout_ticks=20),
                        Action(action_type="turn_to_yaw_pitch", parameters={"yaw": 10.0, "pitch": 0.0}, timeout_ticks=20),
                    ],
                ),
                Subtask(
                    subtask_id="probe",
                    title="Move and interact probe",
                    success_criteria=["Bridge accepted short move and use"],
                    actions=[
                        Action(action_type="move_forward_short", parameters={"ticks": 6}, timeout_ticks=20),
                        Action(action_type="interact_use", parameters={}, timeout_ticks=20),
                    ],
                ),
            ],
        )
