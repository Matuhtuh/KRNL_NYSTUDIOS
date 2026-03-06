"""Minimal orchestration loop for layered StoneBlock 4 autonomous-agent architecture."""

from __future__ import annotations

from memory.models import AgentMemory, FailureRecord
from planner.models import Goal, Plan


class AgentLoop:
    """Coordinates state read, recovery checks, planning, execution, and memory updates per cycle."""

    def __init__(self, bridge, planner, executor, recovery_manager):
        self.bridge = bridge
        self.planner = planner
        self.executor = executor
        self.recovery_manager = recovery_manager
        self.memory = AgentMemory()
        self.current_goal: Goal | None = None
        self.current_plan: Plan | None = None

    def step(self) -> None:
        """Run one control cycle with deterministic execution and structured memory recording."""

        state = self.bridge.read_state()

        if self.recovery_manager.requires_recovery(state):
            recovered = self.recovery_manager.recover(state)
            if not recovered:
                self.memory.failure_history.append(
                    FailureRecord(
                        tick=state.tick,
                        context="recovery",
                        error_type="RecoveryFailed",
                        detail="Recovery manager could not stabilize the agent.",
                        action_type="recovery",
                    )
                )
            return

        if self.current_plan is None and self.current_goal is not None:
            self.current_plan = self.planner.create_plan(self.current_goal, state)
            self.memory.current_plan_id = self.current_plan.plan_id

        if self.current_plan is None:
            return

        next_action = self.current_plan.subtasks[0].actions[0]
        success = self.executor.execute_one(next_action, state)
        if not success:
            self.memory.failure_history.append(
                FailureRecord(
                    tick=state.tick,
                    context="execution",
                    error_type="ActionFailed",
                    detail=f"Action {next_action.action_type} failed in executor.",
                    action_type=next_action.action_type,
                )
            )
