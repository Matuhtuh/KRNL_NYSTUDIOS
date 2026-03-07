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
        inventory_cleanup_enabled=True,
        inventory_cleanup_cooldown_seconds=0.0,
        inventory_full_free_slots_threshold=2,
        inventory_cleanup_target_free_slots=3,
        inventory_max_stacks_per_cleanup=1,
        inventory_drop_junk_item_ids=["minecraft:rotten_flesh"],
        inventory_drop_junk_keywords=[],
        inventory_keep_item_keywords=[],
        pickaxe_item_keywords=[],
        food_item_keywords=[],
        auto_torch_item_ids=[],
    )
    bridge._active_command_body = ""
    bridge._drop_supported = True
    bridge._last_inventory_cleanup_at = 0.0
    bridge._inventory_cleanup_protected_item_ids = set()
    bridge.pause_pathing = lambda: None  # type: ignore[method-assign]
    bridge.resume_pathing = lambda: None  # type: ignore[method-assign]
    bridge._status_is_fresh = lambda status: True  # type: ignore[method-assign]
    bridge._status_free_inventory_slots = lambda status: int(status.get("inventoryFreeSlots", 0))  # type: ignore[method-assign]
    return bridge


class InventoryCleanupTest(unittest.TestCase):
    def test_try_inventory_cleanup_logs_rule_name_and_skips_structural_blocks(self) -> None:
        bridge = make_bridge()
        dropped_slots: list[int] = []
        status = {
            "timestampMs": int(time.time() * 1000.0),
            "inventoryFreeSlots": 0,
            "isPathing": False,
            "inventory": [
                {"slot": 12, "itemId": "minecraft:rotten_flesh", "count": 4, "inHotbar": False},
                {"slot": 13, "itemId": "minecraft:cobblestone", "count": 64, "inHotbar": False},
            ],
        }

        bridge._bridge_drop_inventory_slot = lambda slot, count=0, drop_all=True: dropped_slots.append(slot) or True  # type: ignore[method-assign]
        bridge.read_bridge_status = lambda: {  # type: ignore[method-assign]
            "timestampMs": int(time.time() * 1000.0),
            "inventoryFreeSlots": 3,
        }

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            with self.assertLogs("agent.baritone", level="WARNING") as logs:
                improved = bridge.try_inventory_cleanup(status, context="idle_inventory_pressure")

        self.assertTrue(improved)
        self.assertEqual([12], dropped_slots)
        combined = "\n".join(logs.output)
        self.assertIn(
            "Junk-drop fallback engaged for minecraft:rotten_flesh (rule: exact:minecraft:rotten_flesh)",
            combined,
        )
        self.assertNotIn("minecraft:cobblestone", combined)


if __name__ == "__main__":
    unittest.main()
