from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.baritone import BaritoneBridge


def make_bridge() -> BaritoneBridge:
    bridge = BaritoneBridge.__new__(BaritoneBridge)
    bridge.config = SimpleNamespace(
        auto_torch_enabled=True,
        torch_interval_seconds=1.0,
        torch_hotbar_slot=9,
        auto_torch_item_ids=["minecraft:torch"],
    )
    bridge.controller = SimpleNamespace(place_torch=mock.Mock())
    bridge._next_torch_at = time.time() - 1.0
    bridge._last_known_torch_slot = None
    bridge.pause_pathing = lambda: None  # type: ignore[method-assign]
    bridge.resume_pathing = lambda: None  # type: ignore[method-assign]
    bridge._ensure_torch_stock = lambda status: None  # type: ignore[method-assign]
    bridge.read_bridge_status = lambda: None  # type: ignore[method-assign]
    bridge._status_is_fresh = lambda status: True  # type: ignore[method-assign]
    return bridge


class AutoTorchSafetyTest(unittest.TestCase):
    def test_maybe_place_torch_skips_blind_configured_slot_when_hotbar_telemetry_has_no_torch(self) -> None:
        bridge = make_bridge()
        status = {
            "selectedHotbarSlot": 5,
            "hotbar": [
                {"slot": 5, "itemId": "ftbstuff:diamond_hammer", "count": 1},
                {"slot": 9, "itemId": "minecraft:deepslate_brick_wall", "count": 30},
            ],
            "inventory": [
                {"slot": 5, "inHotbar": True, "itemId": "ftbstuff:diamond_hammer", "count": 1},
                {"slot": 9, "inHotbar": True, "itemId": "minecraft:deepslate_brick_wall", "count": 30},
            ],
        }

        with self.assertLogs("agent.baritone", level="WARNING") as logs:
            bridge._maybe_place_torch(is_pathing=True, status=status)

        bridge.controller.place_torch.assert_not_called()
        self.assertIn("Torch placement skipped: no valid hotbar torch slot", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
