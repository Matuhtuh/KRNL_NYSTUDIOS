"""Integration tests for Python agent loop against local bridge transport."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from agent.progress import NoProgressDetector
from agent.safety import evaluate_safety
from agent_loop import BridgeAgentLoop
from bridge.contracts import ActionResult, GameStateSnapshot
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

    def read_state_snapshot(self) -> GameStateSnapshot:
        return GameStateSnapshot.model_validate(
            {
                "player": {
                    "tick": self.calls,
                    "dimension": "minecraft:overworld",
                    "x": 0,
                    "y": 64,
                    "z": 0,
                    "yaw": 0,
                    "pitch": 0,
                    "health": 20,
                    "hunger": 20,
                    "on_ground": True,
                    "in_fluid": False,
                    "held_main_hand_item": "minecraft:stone_pickaxe",
                    "held_off_hand_item": "minecraft:air",
                    "selected_hotbar_slot": 0,
                },
                "inventory": {"items": [], "hotbar": [], "partial": False, "note": None},
                "nearby_blocks": [],
                "nearby_entities": [],
                "open_screen": {"screen_open": False, "screen_class": "none", "title": "No screen", "slot_count": 0},
                "observation_radius": 4,
                "partial": False,
                "warnings": [],
            }
        )

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
            preconditions=[],
            postconditions=[],
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
        assert len(loop.memory.recent_snapshots) == 1
    finally:
        server.stop()


def test_safety_check_triggering() -> None:
    snapshot = GameStateSnapshot.model_validate(
        {
            "player": {
                "tick": 1,
                "dimension": "minecraft:overworld",
                "x": 0,
                "y": 64,
                "z": 0,
                "yaw": 0,
                "pitch": 0,
                "health": 4,
                "hunger": 3,
                "on_ground": True,
                "in_fluid": False,
                "held_main_hand_item": "minecraft:air",
                "held_off_hand_item": "minecraft:air",
                "selected_hotbar_slot": 0,
            },
            "inventory": {"items": [], "hotbar": [], "partial": True, "note": "partial"},
            "nearby_blocks": [],
            "nearby_entities": [],
            "open_screen": {"screen_open": True, "screen_class": "ChestScreen", "title": "Chest", "slot_count": 27},
            "observation_radius": 4,
            "partial": True,
            "warnings": [],
        }
    )
    issues = evaluate_safety(snapshot, Action(action_type="mine_block", parameters={}, timeout_ticks=10))
    assert "low_health" in issues
    assert "low_hunger" in issues
    assert "missing_main_hand_tool" in issues
    assert "open_screen_blocks_action" in issues


def test_no_progress_detector() -> None:
    detector = NoProgressDetector(min_move_delta=0.1)
    snapshots = []
    for tick in [1, 2, 3]:
        snapshots.append(
            GameStateSnapshot.model_validate(
                {
                    "player": {
                        "tick": tick,
                        "dimension": "minecraft:overworld",
                        "x": 0,
                        "y": 64,
                        "z": 0,
                        "yaw": 0,
                        "pitch": 0,
                        "health": 20,
                        "hunger": 20,
                        "on_ground": True,
                        "in_fluid": False,
                        "held_main_hand_item": "minecraft:stone_pickaxe",
                        "held_off_hand_item": "minecraft:air",
                        "selected_hotbar_slot": 0,
                    },
                    "inventory": {"items": [], "hotbar": [], "partial": False, "note": None},
                    "nearby_blocks": [],
                    "nearby_entities": [],
                    "open_screen": {"screen_open": False, "screen_class": "none", "title": "No screen", "slot_count": 0},
                    "observation_radius": 4,
                    "partial": False,
                    "warnings": [],
                }
            )
        )
    assert detector.no_progress(snapshots) is True


def test_executor_rejects_unsafe_action_state() -> None:
    class UnsafeBridge(AlwaysFailBridge):
        def read_state_snapshot(self) -> GameStateSnapshot:
            snap = super().read_state_snapshot().model_copy(deep=True)
            snap.player.health = 2
            snap.player.hunger = 2
            return snap

        def perform_action(self, action: Action) -> ActionResult:
            raise AssertionError("perform_action should not be called for unsafe state")

    bridge = UnsafeBridge()
    executor = DeterministicExecutor(bridge)
    result = executor.execute_one(Action(action_type="mine_block", parameters={}, timeout_ticks=10), bridge.read_state())
    assert result.accepted is False
    assert result.error_code == "unsafe_state"


def test_bridge_rejects_unsupported_action_payload() -> None:
    server = MockBridgeServer(port=8883)
    server.start()
    try:
        payload = json.dumps({"request_id": "bad1", "action_type": "fly", "parameters": {}, "timeout_ticks": 10}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8883/action",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 400
    finally:
        server.stop()
