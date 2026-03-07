from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.runner import AgentRuntime


def make_runtime() -> AgentRuntime:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = SimpleNamespace(
        baritone=SimpleNamespace(
            transport="bridge_file",
            poll_seconds=0.01,
        )
    )
    runtime.baritone = SimpleNamespace(
        command=mock.Mock(),
        wait_for_idle=mock.Mock(return_value=True),
        controller=SimpleNamespace(press=mock.Mock()),
        read_bridge_status=mock.Mock(
            return_value={
                "timestampMs": 1,
                "screenTitle": "Crafting",
                "currentScreen": "CraftingScreen",
                "lastCommandResult": "rejected",
                "lastCommandError": "craft output pending",
            }
        ),
    )
    runtime._bridge_item_count = mock.Mock(return_value=0)
    runtime._bridge_keyword_count = mock.Mock(return_value=8)
    runtime._craft_command_templates = mock.Mock(
        return_value=[
            "bridge.craft_item {item} {count}",
            "bridge.craft_item {item}",
            "craft {item}",
        ]
    )
    return runtime


class RunnerCraftingTest(unittest.TestCase):
    def test_craft_item_pending_retries_same_bridge_command_until_success(self) -> None:
        runtime = make_runtime()
        runtime.baritone.command.side_effect = [
            RuntimeError("bridge rejected command: craft output pending"),
            None,
        ]
        runtime._bridge_item_count.side_effect = [0, 0, 1]

        runtime._craft_item("minecraft:chest", 1, timeout=30.0)

        self.assertEqual(
            [
                mock.call("bridge.craft_item minecraft:chest 1"),
                mock.call("bridge.craft_item minecraft:chest 1"),
            ],
            runtime.baritone.command.call_args_list,
        )
        runtime.baritone.wait_for_idle.assert_called_once()

    def test_craft_pending_resolution_times_out_boundedly(self) -> None:
        runtime = make_runtime()
        runtime.baritone.command.side_effect = RuntimeError("bridge rejected command: craft output pending")
        runtime._bridge_item_count.side_effect = lambda *args, **kwargs: 0

        started = time.time()
        resolved, detail = runtime._wait_for_craft_pending_resolution(
            command="bridge.craft_item minecraft:chest 1",
            target_item="minecraft:chest",
            before_count=0,
            target_total=1,
            timeout_seconds=0.05,
        )
        elapsed = time.time() - started

        self.assertFalse(resolved)
        self.assertIn("timed out", detail)
        self.assertLess(elapsed, 1.0)

    def test_craft_item_hard_reject_still_fails_immediately(self) -> None:
        runtime = make_runtime()
        runtime._craft_command_templates = mock.Mock(return_value=["bridge.craft_item {item} {count}"])
        runtime.baritone.command.side_effect = RuntimeError("bridge rejected command: craft output unavailable")
        runtime._bridge_item_count.side_effect = [0]

        with self.assertRaisesRegex(RuntimeError, "craft output unavailable"):
            runtime._craft_item("minecraft:chest", 1, timeout=30.0)

        self.assertEqual(1, runtime.baritone.command.call_count)
        runtime.baritone.wait_for_idle.assert_not_called()

    def test_craft_pending_closes_blocking_inventory_screen_once_before_retry(self) -> None:
        runtime = make_runtime()
        runtime.baritone.command.side_effect = [
            RuntimeError("bridge rejected command: craft output pending"),
            None,
        ]
        runtime.baritone.read_bridge_status.side_effect = [
            {
                "timestampMs": 1,
                "inWorld": True,
                "screenTitle": "Crafting",
                "currentScreen": "InventoryScreen",
                "lastCommandResult": "rejected",
                "lastCommandError": "craft output pending",
            },
            {
                "timestampMs": 2,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
            {
                "timestampMs": 3,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
            {
                "timestampMs": 4,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
        ]
        runtime._bridge_item_count.side_effect = [0, 0, 0, 1]

        runtime._craft_item("minecraft:chest", 1, timeout=30.0)

        runtime.baritone.controller.press.assert_called_once_with("esc")
        self.assertEqual(
            [
                mock.call("bridge.craft_item minecraft:chest 1"),
                mock.call("bridge.craft_item minecraft:chest 1"),
            ],
            runtime.baritone.command.call_args_list,
        )
        runtime.baritone.wait_for_idle.assert_called_once()

    def test_craft_pending_retries_close_when_screen_stays_open(self) -> None:
        runtime = make_runtime()
        runtime.baritone.command.side_effect = [
            RuntimeError("bridge rejected command: craft output pending"),
            RuntimeError("bridge rejected command: craft output pending"),
            None,
        ]
        runtime.baritone.read_bridge_status.side_effect = [
            {
                "timestampMs": 1,
                "inWorld": True,
                "screenTitle": "Crafting",
                "currentScreen": "CraftingScreen",
                "lastCommandResult": "rejected",
                "lastCommandError": "craft output pending",
            },
            {
                "timestampMs": 2,
                "inWorld": True,
                "screenTitle": "Crafting",
                "currentScreen": "CraftingScreen",
                "lastCommandResult": "rejected",
                "lastCommandError": "craft output pending",
            },
            {
                "timestampMs": 3,
                "inWorld": True,
                "screenTitle": "Crafting",
                "currentScreen": "CraftingScreen",
                "lastCommandResult": "rejected",
                "lastCommandError": "craft output pending",
            },
            {
                "timestampMs": 4,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
            {
                "timestampMs": 5,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
            {
                "timestampMs": 6,
                "inWorld": True,
                "screenTitle": "",
                "currentScreen": "",
                "lastCommandResult": "accepted",
                "lastCommandError": "",
            },
        ]
        runtime._bridge_item_count.side_effect = [0, 0, 0, 0, 1]

        runtime._craft_item("minecraft:chest", 1, timeout=30.0)

        self.assertEqual(
            [
                mock.call("esc"),
                mock.call("esc"),
            ],
            runtime.baritone.controller.press.call_args_list,
        )
        self.assertEqual(
            [
                mock.call("bridge.craft_item minecraft:chest 1"),
                mock.call("bridge.craft_item minecraft:chest 1"),
                mock.call("bridge.craft_item minecraft:chest 1"),
            ],
            runtime.baritone.command.call_args_list,
        )
        runtime.baritone.wait_for_idle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
