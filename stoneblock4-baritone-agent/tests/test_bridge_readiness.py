from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from agent.baritone import BaritoneBridge


def make_bridge() -> BaritoneBridge:
    bridge = BaritoneBridge.__new__(BaritoneBridge)
    bridge.config = SimpleNamespace(
        bridge_status_stale_after_seconds=4.0,
        bridge_unresponsive_after_seconds=20.0,
    )
    return bridge


class BridgeReadinessIssuesTest(unittest.TestCase):
    def test_not_in_world_reports_client_state_details(self) -> None:
        bridge = make_bridge()
        status = {
            "timestampMs": int((time.time() - 10.0) * 1000.0),
            "bridgeOk": True,
            "inWorld": False,
            "baritoneLoaded": True,
            "clientStateHint": "main_menu",
            "currentScreen": "TitleScreen",
            "windowActive": False,
        }

        issues = bridge._bridge_readiness_issues(status)

        self.assertTrue(any(issue.startswith("bridge status stale") for issue in issues))
        self.assertIn("player is not in-world (main_menu; screen=TitleScreen; window_inactive)", issues)

    def test_in_world_ready_status_has_no_readiness_issues(self) -> None:
        bridge = make_bridge()
        status = {
            "timestampMs": int(time.time() * 1000.0),
            "bridgeOk": True,
            "inWorld": True,
            "baritoneLoaded": True,
            "clientStateHint": "in_world",
            "currentScreen": "",
            "windowActive": True,
        }

        self.assertEqual([], bridge._bridge_readiness_issues(status))


class BridgeCommandReissueTest(unittest.TestCase):
    def test_reissued_active_command_is_recognized(self) -> None:
        bridge = make_bridge()
        bridge._active_command_id = "reissued-456"
        bridge._active_command_body = "mine minecraft:gravel"
        status = {
            "timestampMs": int(time.time() * 1000.0),
            "lastCommandAtMs": int(time.time() * 1000.0),
            "lastCommandId": "reissued-456",
            "lastCommand": "mine minecraft:gravel",
            "lastCommandResult": "accepted",
        }

        self.assertTrue(
            bridge._bridge_status_reissued_active_command(
                status,
                command_id="original-123",
                command_body="mine minecraft:gravel",
            )
        )

    def test_aux_command_reissue_is_not_treated_as_active_reissue(self) -> None:
        bridge = make_bridge()
        bridge._active_command_id = "reissued-456"
        bridge._active_command_body = "bridge.drop_item 28 all"
        status = {
            "timestampMs": int(time.time() * 1000.0),
            "lastCommandAtMs": int(time.time() * 1000.0),
            "lastCommandId": "reissued-456",
            "lastCommand": "bridge.drop_item 28 all",
            "lastCommandResult": "accepted",
        }

        self.assertFalse(
            bridge._bridge_status_reissued_active_command(
                status,
                command_id="original-123",
                command_body="bridge.drop_item 28 all",
            )
        )


if __name__ == "__main__":
    unittest.main()
