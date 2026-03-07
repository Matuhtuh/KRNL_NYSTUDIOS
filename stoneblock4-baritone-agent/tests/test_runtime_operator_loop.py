from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.runner import AgentRuntime


class FakePlanner:
    def __init__(self, goals):
        self.goals = list(goals)

    def next_goal(self, state, **kwargs):
        for goal in self.goals:
            if goal.id not in state.completed_goals:
                return goal
        return None


def make_runtime(root: Path, goals: list[object]) -> AgentRuntime:
    cfg = SimpleNamespace(
        planner=SimpleNamespace(
            state_file=str(root / "runtime" / "state.json"),
            tick_seconds=0.01,
            task_max_retries=1,
            max_goal_retries=1,
            retry_backoff_seconds=0.0,
            retry_backoff_multiplier=1.0,
            retry_jitter_seconds=0.0,
            quest_transient_defer_seconds=5.0,
            auto_debug_loop_enabled=False,
            auto_debug_loop_delay_seconds=0.01,
            auto_debug_loop_max_rearms=0,
            debug_learning_log_file=str(root / "runtime" / "debug_learning_log.jsonl"),
            strategy_exploration_rate=0.0,
            run_mode="strict_progression",
            max_idle_cycles=0,
            idle_autonomous_enabled=False,
            idle_autonomous_interval_cycles=3,
            idle_autonomous_module="idle_resource_grind",
            idle_autonomous_modules=[],
            knowledge_file=str(root / "knowledge.yml"),
            full_auto_enabled=False,
        ),
        baritone=SimpleNamespace(
            transport="chat",
            bridge_ready_wait_seconds=0.1,
            bridge_status_stale_after_seconds=4.0,
            poll_seconds=0.01,
            bridge_unresponsive_after_seconds=20.0,
            inventory_cleanup_enabled=False,
            inventory_full_free_slots_threshold=2,
            enable_safety_monitoring=False,
            safety_recover_timeout_seconds=0.1,
            safety_poll_seconds=0.01,
        ),
        control=SimpleNamespace(
            input_backend="noop",
            torch_click_hold_seconds=0.05,
            auto_refocus_enabled=False,
            toggle_poll_seconds=0.01,
        ),
    )
    baritone = SimpleNamespace(
        controller=SimpleNamespace(
            stop_requested=False,
            automation_enabled=True,
            wait_if_paused=lambda: None,
        ),
        sync_manual_pause_with_controller=lambda: None,
        active_command_state=lambda: {},
        read_bridge_status=lambda: None,
        pause_pathing=lambda: None,
        wait_for_bridge_ready=lambda timeout_seconds=0.1: {},
    )
    runtime = AgentRuntime(cfg, FakePlanner(goals), baritone, SimpleNamespace())
    runtime._ensure_base_anchor = lambda: None  # type: ignore[method-assign]
    runtime._get_strategy_memory = lambda: None  # type: ignore[method-assign]
    runtime._goal_blocked_required_quest_items = lambda goal, state: []  # type: ignore[method-assign]
    return runtime


class RuntimeOperatorLoopTest(unittest.TestCase):
    def test_run_continues_into_next_ready_goal_without_relaunch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            goals = [
                SimpleNamespace(id="goal_one", description="first", tasks=[{"type": "note", "text": "one"}], completion_flags=[], prerequisites=[]),
                SimpleNamespace(id="goal_two", description="second", tasks=[{"type": "note", "text": "two"}], completion_flags=[], prerequisites=[]),
            ]
            runtime = make_runtime(root, goals)

            runtime.run(exit_when_idle=True)

            state = json.loads((root / "runtime" / "state.json").read_text(encoding="utf-8"))
            live = json.loads((root / "runtime" / "live_status.json").read_text(encoding="utf-8"))
            self.assertIn("goal_one", state["completed_goals"])
            self.assertIn("goal_two", state["completed_goals"])
            self.assertEqual("idle_wait", live["operatorLoop"]["decision"])
            self.assertIn("planner returned no ready goals", live["operatorLoop"]["nextTaskReason"])

    def test_repeated_goal_failure_enters_safe_pause_with_operator_loop_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            goals = [
                SimpleNamespace(id="goal_fail", description="fail", tasks=[{"type": "unknown_task"}], completion_flags=[], prerequisites=[]),
            ]
            runtime = make_runtime(root, goals)

            runtime.run(exit_when_idle=True)

            state = json.loads((root / "runtime" / "state.json").read_text(encoding="utf-8"))
            live = json.loads((root / "runtime" / "live_status.json").read_text(encoding="utf-8"))
            self.assertTrue(state["safe_paused"])
            self.assertEqual("safe_pause", live["operatorLoop"]["decision"])
            self.assertIn("Unknown task type", live["operatorLoop"]["lastBlocker"])

    def test_idle_inventory_pressure_safe_pause_exits_run_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime = make_runtime(root, goals=[])

            def fail_idle_pressure(state):
                state.safe_paused = True
                state.last_error = "idle inventory pressure unresolved after 3 attempts"
                runtime._set_operator_loop_state(
                    state,
                    decision="safe_pause",
                    task=None,
                    next_task=None,
                    nextTaskReason="idle inventory pressure repeatedly failed",
                    last_blocker=state.last_error,
                )
                runtime._write_live_status(
                    state,
                    "safe_paused",
                    error=state.last_error,
                    message="idle inventory pressure repeatedly failed",
                )
                return False

            runtime._run_idle_inventory_pressure = fail_idle_pressure  # type: ignore[method-assign]

            runtime.run(exit_when_idle=False, max_cycles=4)

            state = json.loads((root / "runtime" / "state.json").read_text(encoding="utf-8"))
            live = json.loads((root / "runtime" / "live_status.json").read_text(encoding="utf-8"))
            self.assertTrue(state["safe_paused"])
            self.assertEqual("safe_pause", live["operatorLoop"]["decision"])
            self.assertIn("idle inventory pressure unresolved", live["operatorLoop"]["lastBlocker"])


if __name__ == "__main__":
    unittest.main()
