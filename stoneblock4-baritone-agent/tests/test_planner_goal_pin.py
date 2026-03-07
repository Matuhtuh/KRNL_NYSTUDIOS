from __future__ import annotations

import unittest

from agent.knowledge import Goal
from agent.planner import AgentState, GoalPlanner


class PlannerGoalPinTest(unittest.TestCase):
    def test_current_goal_id_is_preferred_when_ready(self) -> None:
        goals = [
            Goal(id="high_priority", priority=100, description="", prerequisites=[], completion_flags=[], tasks=[{"type": "note"}]),
            Goal(id="pinned_goal", priority=10, description="", prerequisites=[], completion_flags=[], tasks=[{"type": "note"}]),
        ]
        planner = GoalPlanner(goals)
        state = AgentState(current_goal_id="pinned_goal")

        goal = planner.next_goal(state)

        self.assertIsNotNone(goal)
        self.assertEqual("pinned_goal", goal.id)


if __name__ == "__main__":
    unittest.main()
