"""Integration tests for Python agent loop against local bridge transport."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from agent_loop import BridgeAgentLoop
from bridge.contracts import ActionResult
from bridge.http_client import HttpGameBridge
from bridge.interfaces import GameBridge
from bridge.mock_server import MockBridgeServer
from executor.deterministic import DeterministicExecutor
from planner.models import Action, Goal
from planner.simple_planner import TinyDeterministicPlanner
from recovery.simple import SimpleRecoveryManager
from research.noop import NoopResearchProvider
from state.models import GameState


class AlwaysFailBridge(GameBridge):
    def __init__(self) -> None:
        self.calls = 0

    def read_state(self) -> GameState:
        return GameState(
            tick=self.calls,
            dimension="minecraft:overworld",
            x=0,
            y=64,
            z=0,
            yaw=0,
            pitch=0,
            health=20,
            hunger=20,
            inventory=[],
            nearby_entities=[],
            observed_blocks=[],
            in_danger=False,
        )

    def perform_action(self, action: Action) -> ActionResult:
        self.calls += 1
        return ActionResult(
            request_id=f"f{self.calls}",
            accepted=True,
            completed=True,
            success=False,
            error_code="timeout",
            message="deterministic fail",
        )


def test_python_can_call_bridge() -> None:
    server = MockBridgeServer(port=8877)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8877")
        hb = bridge.heartbeat()
        state = bridge.read_state_snapshot()
        assert hb.status == "ok"
        assert state.player.dimension == "minecraft:overworld"
    finally:
        server.stop()


def test_malformed_action_payload_rejected() -> None:
    server = MockBridgeServer(port=8878)
    server.start()
    try:
        bad_payload = json.dumps({"request_id": "x"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8878/action",
            data=bad_payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 400
    finally:
        server.stop()


def test_minimal_plan_executes_end_to_end() -> None:
    server = MockBridgeServer(port=8879)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8879")
        loop = BridgeAgentLoop(
            bridge=bridge,
            planner=TinyDeterministicPlanner(),
            executor=DeterministicExecutor(bridge),
            recovery_manager=SimpleRecoveryManager(),
            research=NoopResearchProvider(),
        )
        loop.set_goal(Goal(goal_id="g1", description="run minimal loop", priority=5))
        loop.step()
        assert loop.memory.action_log
        assert server.last_action is not None
    finally:
        server.stop()


def test_repeated_failure_increments_stuck_and_triggers_research() -> None:
    bridge = AlwaysFailBridge()
    research = NoopResearchProvider()
    loop = BridgeAgentLoop(
        bridge=bridge,
        planner=TinyDeterministicPlanner(),
        executor=DeterministicExecutor(bridge),
        recovery_manager=SimpleRecoveryManager(),
        research=research,
    )
    loop.set_goal(Goal(goal_id="g1", description="repeat fail", priority=5))

    for _ in range(4):
        loop.step()

    assert loop.memory.stuck_counter >= 3
    assert loop.memory.research_triggered is True
    assert len(research.queries) == 1


def test_memory_updates_after_action_result() -> None:
    server = MockBridgeServer(port=8880)
    server.start()
    try:
        bridge = HttpGameBridge("http://127.0.0.1:8880")
        loop = BridgeAgentLoop(
            bridge=bridge,
            planner=TinyDeterministicPlanner(),
            executor=DeterministicExecutor(bridge),
            recovery_manager=SimpleRecoveryManager(),
            research=NoopResearchProvider(),
        )
        loop.set_goal(Goal(goal_id="g1", description="memory update", priority=5))
        loop.step()
        assert len(loop.memory.action_log) == 1
        assert loop.memory.current_plan_id is not None
    finally:
        server.stop()
