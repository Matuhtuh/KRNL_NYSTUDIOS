"""Executable local runner for the first Python<->bridge control loop."""

from __future__ import annotations

from agent_loop import BridgeAgentLoop
from bridge.http_client import HttpGameBridge
from executor.deterministic import DeterministicExecutor
from planner.models import Goal
from planner.simple_planner import TinyDeterministicPlanner
from recovery.simple import SimpleRecoveryManager
from research.noop import NoopResearchProvider


def run_once() -> None:
    bridge = HttpGameBridge()
    loop = BridgeAgentLoop(
        bridge=bridge,
        planner=TinyDeterministicPlanner(),
        executor=DeterministicExecutor(bridge),
        recovery_manager=SimpleRecoveryManager(),
        research=NoopResearchProvider(),
    )
    loop.set_goal(Goal(goal_id="bootstrap", description="Bootstrap bridge loop", priority=5))
    loop.step()


if __name__ == "__main__":
    run_once()
