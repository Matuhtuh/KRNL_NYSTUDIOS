from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.baritone import BaritoneBridge


def make_bridge() -> BaritoneBridge:
    bridge = BaritoneBridge.__new__(BaritoneBridge)
    bridge.config = SimpleNamespace(
        transport="bridge_file",
        bridge_ack_timeout_seconds=0.5,
        bridge_status_stale_after_seconds=4.0,
        bridge_unresponsive_after_seconds=20.0,
    )
    bridge.controller = SimpleNamespace()
    bridge.perception = SimpleNamespace()
    bridge._active_command_id = "active-123"
    bridge._active_command_body = "mine minecraft:gravel"
    bridge._active_command_started_at = time.time() - 1.0
    bridge._next_torch_at = time.time() + 30.0
    bridge._stuck_recovery_attempts = 2
    bridge._ultimine_active_for_command = False
    return bridge


class FailClosedStopTest(unittest.TestCase):
    def test_fail_closed_stop_sends_pause_then_stop_and_verifies_bridge_state(self) -> None:
        bridge = make_bridge()
        commands: list[str] = []
        now_ms = int(time.time() * 1000.0)

        bridge._send_bridge_aux_command_direct = lambda body, ack_timeout_seconds=None: commands.append(body) or True  # type: ignore[method-assign]
        bridge.read_bridge_status = lambda: {  # type: ignore[method-assign]
            "timestampMs": now_ms,
            "lastCommandAtMs": now_ms,
            "lastCommand": "stop",
            "lastCommandResult": "accepted",
            "isPathing": False,
            "currentGoal": "",
            "ultimineActive": False,
        }

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            result = bridge.fail_closed_stop(reason="unit_test", source="tests", verify_timeout_seconds=0.2)

        self.assertEqual(["pause", "stop"], commands)
        self.assertTrue(result["pauseSent"])
        self.assertTrue(result["stopSent"])
        self.assertTrue(result["shutdownVerified"])
        self.assertTrue(result["bridgeStatusAvailable"])
        self.assertFalse(result["postStopIsPathing"])
        self.assertEqual("", result["postStopCurrentGoal"])
        self.assertEqual("", bridge._active_command_body)
        self.assertIsNone(bridge._active_command_id)

    def test_fail_closed_stop_is_best_effort_when_bridge_status_is_unavailable(self) -> None:
        bridge = make_bridge()
        commands: list[str] = []

        def send_command(body: str, ack_timeout_seconds=None) -> bool:
            commands.append(body)
            if body == "pause":
                raise RuntimeError("pause ack timeout")
            return True

        bridge._send_bridge_aux_command_direct = send_command  # type: ignore[method-assign]
        bridge.read_bridge_status = lambda: None  # type: ignore[method-assign]

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            result = bridge.fail_closed_stop(reason="unit_test_unverified", source="tests", verify_timeout_seconds=0.0)

        self.assertEqual(["pause", "stop"], commands)
        self.assertIn("pause ack timeout", result["pauseError"])
        self.assertTrue(result["stopSent"])
        self.assertFalse(result["shutdownVerified"])
        self.assertFalse(result["bridgeStatusAvailable"])
        self.assertEqual("mine minecraft:gravel", bridge._active_command_body)


if __name__ == "__main__":
    unittest.main()
