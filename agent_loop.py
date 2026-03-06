"""First executable end-to-end deterministic loop between Python brain and bridge."""

from __future__ import annotations

from agent.progress import NoProgressDetector
from agent.safety import evaluate_safety
from memory.models import AgentMemory, FailureRecord
from planner.interfaces import Planner
from planner.models import Goal, Plan
from recovery.interfaces import RecoveryManager
from research.interfaces import ResearchProvider
from research.models import ResearchQuery
from skills.registry import SkillRegistry


class BridgeAgentLoop:
    """Runs one action per step with deterministic progression, verification, and escalation."""

    def __init__(self, bridge, planner: Planner, executor, recovery_manager: RecoveryManager, research: ResearchProvider):
        self.bridge = bridge
        self.planner = planner
        self.executor = executor
        self.recovery_manager = recovery_manager
        self.research = research
        self.skills = SkillRegistry()
        self.progress_detector = NoProgressDetector()
        self.memory = AgentMemory()
        self.current_goal: Goal | None = None
        self.current_plan: Plan | None = None
        self._subtask_index = 0
        self._action_index = 0

    def set_goal(self, goal: Goal) -> None:
        self.current_goal = goal
        self.memory.active_goal_id = goal.goal_id
        self.current_plan = None

    def step(self) -> None:
        snapshot = self.bridge.read_state_snapshot()
        self.memory.push_snapshot(snapshot)
        state = self.bridge.read_state()

        if not self.skills.run("inspect_state", snapshot):
            self._record_failure(state.tick, "state", "InvalidState", "State inspection skill failed.", "noop")
            return

        if self.progress_detector.no_progress(self.memory.recent_snapshots):
            self.memory.stuck_counter += 1

        if self.recovery_manager.requires_recovery(state):
            recovered = self.recovery_manager.recover(state)
            if not recovered:
                self._record_failure(state.tick, "recovery", "RecoveryFailed", "Recovery manager failed.", "noop")
            return

        if self.current_goal and self.current_plan is None:
            self.current_plan = self.planner.create_plan(self.current_goal, state)
            self.memory.current_plan_id = self.current_plan.plan_id
            self._subtask_index = 0
            self._action_index = 0

        action = self._next_action()
        if action is None:
            self._complete_plan_if_done()
            return

        skill_name = self._action_skill_name(action.action_type)
        if skill_name and not self.skills.run(skill_name, snapshot, action):
            self._record_failure(state.tick, "skill", "SkillPreconditionFailed", f"{skill_name} precheck failed", action.action_type)
            return

        safety_issues = evaluate_safety(snapshot, action)
        if safety_issues:
            self._record_failure(
                state.tick,
                "safety",
                "UnsafeAction",
                f"Blocked by safety checks: {', '.join(safety_issues)}",
                action.action_type,
            )
            return

        result = self.executor.execute_one(action, state)
        signature = f"{action.action_type}:{action.parameters}"
        self.memory.action_log.append(f"{state.tick}:{signature}:{result.success}:{result.postconditions}")

        verified = self.skills.run("verify_state_change", snapshot, action, result)
        if not verified:
            failure_type = self._classify_failure(result)
            self._track_failure(state.tick, action.action_type, f"{failure_type}:{result.message}", signature)
            self._maybe_trigger_research(state.tick)
            return

        self.memory.last_action_signature = None
        self.memory.repeated_failure_count = 0
        self._advance_action_cursor()
        self._complete_plan_if_done()

    def _next_action(self):
        if self.current_plan is None:
            return None
        if self._subtask_index >= len(self.current_plan.subtasks):
            return None
        subtask = self.current_plan.subtasks[self._subtask_index]
        if self._action_index >= len(subtask.actions):
            return None
        return subtask.actions[self._action_index]

    def _advance_action_cursor(self) -> None:
        if self.current_plan is None:
            return
        subtask = self.current_plan.subtasks[self._subtask_index]
        self._action_index += 1
        if self._action_index >= len(subtask.actions):
            self._subtask_index += 1
            self._action_index = 0

    def _complete_plan_if_done(self) -> None:
        if self.current_plan is None or self.current_goal is None:
            return
        if self._subtask_index < len(self.current_plan.subtasks):
            return
        self.memory.completed_goals.append(self.current_goal.goal_id)
        self.current_plan = None
        self.memory.current_plan_id = None
        self.current_goal = None
        self.memory.active_goal_id = None

    @staticmethod
    def _action_skill_name(action_type: str) -> str | None:
        mapping = {
            "select_hotbar_slot": "select_hotbar_slot",
            "turn_to_yaw_pitch": "turn_to_yaw_pitch",
            "move_forward_short": "move_forward_short",
            "interact_use": "interact_use",
        }
        return mapping.get(action_type)

    @staticmethod
    def _classify_failure(result) -> str:
        if result.error_code == "unsafe_state":
            return "UnsafeState"
        if "no_observable_state_change" in result.postconditions:
            return "NoStateChange"
        if not result.accepted:
            return "RejectedByBridge"
        return "ActionFailed"

    def _track_failure(self, tick: int, action_type: str, detail: str, signature: str) -> None:
        if self.memory.last_action_signature == signature:
            self.memory.repeated_failure_count += 1
            self.memory.stuck_counter += 1
        else:
            self.memory.last_action_signature = signature
            self.memory.repeated_failure_count = 1
            self.memory.stuck_counter += 1
        self._record_failure(tick, "execution", "ActionFailed", detail, action_type)

    def _record_failure(self, tick: int, context: str, error_type: str, detail: str, action_type: str) -> None:
        self.memory.failure_history.append(
            FailureRecord(
                tick=tick,
                context=context,
                error_type=error_type,
                detail=detail,
                action_type=action_type,
            )
        )

    def _maybe_trigger_research(self, tick: int) -> None:
        if self.memory.repeated_failure_count < 3 or self.memory.research_triggered:
            return
        self.memory.research_triggered = True
        query = ResearchQuery(
            query_id=f"rq_{tick}",
            objective="Resolve repeated deterministic action failure",
            local_context={"stuck_counter": self.memory.stuck_counter, "plan_id": self.memory.current_plan_id or "none"},
        )
        self.research.research(query)
