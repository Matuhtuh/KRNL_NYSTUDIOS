from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from agent.planner import AgentState
from agent.runner import AgentRuntime


def make_runtime() -> AgentRuntime:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = SimpleNamespace(
        baritone=SimpleNamespace(transport="bridge_file"),
        planner=SimpleNamespace(debug_learning_log_file="C:/tmp/debug_learning_log.jsonl"),
    )
    runtime.baritone = SimpleNamespace(
        read_bridge_status=lambda: {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [
                {"slot": 27, "itemId": "minecraft:gravel", "count": 26},
            ],
        },
        _status_is_fresh=lambda status: True,
    )
    runtime._optional_item_cooldowns = {"minecraft:gravel": 60.0}
    runtime._optional_item_failures = {"minecraft:gravel": 2}
    runtime._quest_item_block_flag = lambda item_id: ""
    runtime._stoneblock_hammer_chain_source_item = lambda item_id: "minecraft:cobblestone" if item_id == "minecraft:gravel" else ""
    runtime._acquire_stoneblock_hammer_chain_requirement = mock.Mock(side_effect=AssertionError("stoneblock chain should not run"))
    runtime._get_strategy_memory = mock.Mock(side_effect=AssertionError("strategy ranking should not run"))
    runtime._append_debug_learning = lambda event: None
    return runtime


class QuestItemAcquireAdvanceTest(unittest.TestCase):
    def test_existing_bridge_count_short_circuits_before_stoneblock_chain(self) -> None:
        runtime = make_runtime()
        state = AgentState(
            current_goal_id="idle_housekeeping",
            optional_item_cooldowns={"minecraft:gravel": 60.0},
            optional_item_failures={"minecraft:gravel": 2},
        )

        with self.assertLogs("agent.runner", level="INFO") as logs:
            runtime._quest_item_acquire("minecraft:gravel", 12, optional=True, state=state)

        runtime._acquire_stoneblock_hammer_chain_requirement.assert_not_called()
        runtime._get_strategy_memory.assert_not_called()
        self.assertNotIn("minecraft:gravel", runtime._optional_item_cooldowns)
        self.assertNotIn("minecraft:gravel", runtime._optional_item_failures)
        self.assertNotIn("minecraft:gravel", state.optional_item_cooldowns)
        self.assertNotIn("minecraft:gravel", state.optional_item_failures)

        combined = "\n".join(logs.output)
        self.assertIn('"scheduledReason": "bridge_count_met_before_stoneblock_chain"', combined)
        self.assertIn('"currentSubstage": "already_satisfied"', combined)
        self.assertIn('"sliceSuccessBoolean": true', combined)


if __name__ == "__main__":
    unittest.main()
