"""Task progression and skill registry behavior tests inspired by hierarchical agent loops."""

from __future__ import annotations

from agent_loop import BridgeAgentLoop
from bridge.http_client import HttpGameBridge
from bridge.mock_server import MockBridgeServer
from executor.deterministic import DeterministicExecutor
from planner.models import Goal
from planner.simple_planner import TinyDeterministicPlanner
from recovery.simple import SimpleRecoveryManager
from research.noop import NoopResearchProvider
from skills.registry import SkillRegistry


def test_skill_registry_expected_skills_present() -> None:
    registry = SkillRegistry()
    skills = registry.available_skills()
    assert "inspect_state" in skills
    assert "select_hotbar_slot" in skills
    assert "turn_to_yaw_pitch" in skills
    assert "move_forward_short" in skills
    assert "interact_use" in skills
    assert "verify_state_change" in skills


def test_plan_progression_completes_goal_after_successful_actions() -> None:
    server = MockBridgeServer(port=8881)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8881")
        loop = BridgeAgentLoop(
            bridge=bridge,
            planner=TinyDeterministicPlanner(),
            executor=DeterministicExecutor(bridge),
            recovery_manager=SimpleRecoveryManager(),
            research=NoopResearchProvider(),
        )
        loop.set_goal(Goal(goal_id="g_progress", description="progress test", priority=5))

        for _ in range(6):
            loop.step()

        assert "g_progress" in loop.memory.completed_goals
        assert loop.memory.current_plan_id is None
        assert server.action_history[:4] == [
            "select_hotbar_slot",
            "turn_to_yaw_pitch",
            "move_forward_short",
            "interact_use",
        ]
    finally:
        server.stop()


def test_end_to_end_minimal_action_flow_changes_state() -> None:
    server = MockBridgeServer(port=8882)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8882")
        before = bridge.read_state_snapshot()
        loop = BridgeAgentLoop(
            bridge=bridge,
            planner=TinyDeterministicPlanner(),
            executor=DeterministicExecutor(bridge),
            recovery_manager=SimpleRecoveryManager(),
            research=NoopResearchProvider(),
        )
        loop.set_goal(Goal(goal_id="g_e2e", description="e2e action flow", priority=5))
        loop.step()
        loop.step()
        after = bridge.read_state_snapshot()
        assert after.player.selected_hotbar_slot == 0
        assert after.player.yaw != before.player.yaw or after.player.pitch != before.player.pitch
    finally:
        server.stop()


def test_paused_loop_does_not_dispatch_actions() -> None:
    server = MockBridgeServer(port=8885)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8885")
        loop = BridgeAgentLoop(
            bridge=bridge,
            planner=TinyDeterministicPlanner(),
            executor=DeterministicExecutor(bridge),
            recovery_manager=SimpleRecoveryManager(),
            research=NoopResearchProvider(),
        )
        loop.set_goal(Goal(goal_id="g_pause", description="pause test", priority=5))
        loop.pause()
        loop.step()
        assert server.action_history == []
        loop.resume()
        loop.step()
        assert len(server.action_history) == 1
    finally:
        server.stop()
