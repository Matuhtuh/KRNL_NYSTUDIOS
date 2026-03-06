"""Local CLI operator console entrypoint for supervised autonomy debugging."""

from __future__ import annotations

from agent_loop import BridgeAgentLoop
from bridge.http_client import HttpGameBridge
from executor.deterministic import DeterministicExecutor
from ops.console import OperatorConsole
from planner.simple_planner import TinyDeterministicPlanner
from recovery.simple import SimpleRecoveryManager
from research.noop import NoopResearchProvider


def main() -> None:
    bridge = HttpGameBridge()
    loop = BridgeAgentLoop(
        bridge=bridge,
        planner=TinyDeterministicPlanner(),
        executor=DeterministicExecutor(bridge),
        recovery_manager=SimpleRecoveryManager(),
        research=NoopResearchProvider(),
    )
    console = OperatorConsole(loop)
    print("StoneBlock4 operator console ready. Commands: goal <text>, pause, resume, cancel, status, step, quit")
    while True:
        try:
            raw = input("agent> ").strip()
        except EOFError:
            break
        if raw in {"quit", "exit"}:
            break
        if raw == "step":
            loop.step()
            print({"ok": True, "message": "step executed", "status": loop.status_summary()})
            continue
        print(console.handle(raw))


if __name__ == "__main__":
    main()
