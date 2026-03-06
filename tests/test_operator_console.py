"""Tests for lightweight operator console command handling."""

from __future__ import annotations

from agent_loop import BridgeAgentLoop
from bridge.http_client import HttpGameBridge
from bridge.mock_server import MockBridgeServer
from executor.deterministic import DeterministicExecutor
from ops.console import OperatorConsole
from planner.simple_planner import TinyDeterministicPlanner
from recovery.simple import SimpleRecoveryManager
from research.noop import NoopResearchProvider


def _make_console(port: int = 8890):
    server = MockBridgeServer(port=port)
    server.start()
    bridge = HttpGameBridge(f"http://127.0.0.1:{port}")
    loop = BridgeAgentLoop(
        bridge=bridge,
        planner=TinyDeterministicPlanner(),
        executor=DeterministicExecutor(bridge),
        recovery_manager=SimpleRecoveryManager(),
        research=NoopResearchProvider(),
    )
    return server, OperatorConsole(loop), loop


def test_operator_goal_pause_resume_cancel_status() -> None:
    server, console, loop = _make_console(8890)
    try:
        r1 = console.handle("goal gather starter cobble")
        assert r1["ok"] is True
        assert loop.current_goal is not None

        r2 = console.handle("pause")
        assert r2["ok"] is True
        assert loop.paused is True

        r3 = console.handle("resume")
        assert r3["ok"] is True
        assert loop.paused is False

        loop.step()
        assert loop.current_plan is not None

        r4 = console.handle("cancel")
        assert r4["ok"] is True
        assert loop.current_plan is None

        r5 = console.handle("status")
        assert r5["ok"] is True
        assert "status" in r5
        assert "bridge_status" in r5["status"]
    finally:
        server.stop()


def test_operator_rejects_unknown_and_short_goal() -> None:
    server, console, _ = _make_console(8891)
    try:
        short = console.handle("goal hi")
        assert short["ok"] is False

        unknown = console.handle("dance")
        assert unknown["ok"] is False
    finally:
        server.stop()
