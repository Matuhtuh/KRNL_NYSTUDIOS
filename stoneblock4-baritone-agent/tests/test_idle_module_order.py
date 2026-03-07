from __future__ import annotations

import unittest

from agent.modules import module_tasks


class IdleRecoveryHousekeepingOrderTest(unittest.TestCase):
    def test_idle_recovery_housekeeping_runs_dirt_before_sand(self) -> None:
        tasks = module_tasks("idle_recovery_housekeeping", {"cycles": 1})
        quest_items = [task.get("item") for task in tasks if task.get("type") == "quest_item_acquire"]

        self.assertEqual(
            [
                "minecraft:gravel",
                "minecraft:dirt",
                "minecraft:sand",
                "minecraft:cobblestone",
            ],
            quest_items,
        )


if __name__ == "__main__":
    unittest.main()
