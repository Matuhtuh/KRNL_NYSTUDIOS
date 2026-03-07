from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from agent.runner import AgentRuntime


def make_runtime(*, action_context: dict[str, object], status: dict | None = None) -> AgentRuntime:
    runtime = AgentRuntime.__new__(AgentRuntime)
    live_status = dict(
        status
        or {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
        }
    )
    runtime.config = SimpleNamespace(
        baritone=SimpleNamespace(
            transport="bridge_file",
            poll_seconds=1.0,
            safety_poll_seconds=1.0,
            safety_recover_timeout_seconds=45.0,
            inventory_cleanup_enabled=False,
            inventory_full_free_slots_threshold=2,
        ),
        control=SimpleNamespace(torch_click_hold_seconds=0.05),
        planner=SimpleNamespace(debug_learning_log_file="C:/tmp/debug_learning_log.jsonl"),
    )
    runtime.baritone = SimpleNamespace(
        read_bridge_status=mock.Mock(return_value=live_status),
        _status_is_fresh=mock.Mock(return_value=True),
        _status_free_inventory_slots=mock.Mock(return_value=int(live_status.get("inventoryFreeSlots", 0) or 0)),
        command=mock.Mock(),
        wait_for_idle=mock.Mock(return_value=True),
        evaluate_planner_task_context=mock.Mock(return_value={"finalSuccess": True}),
        set_planner_task_context=mock.Mock(),
        clear_planner_task_context=mock.Mock(),
        try_inventory_cleanup=mock.Mock(return_value=False),
        controller=SimpleNamespace(
            press=mock.Mock(),
            place_torch=mock.Mock(),
            left_click=mock.Mock(),
            stop_requested=False,
            automation_enabled=True,
            wait_if_paused=lambda: None,
        ),
    )
    runtime._stoneblock_route_context = mock.Mock(return_value=dict(action_context))
    runtime._ensure_any_item_hotbar_slot = mock.Mock(return_value=("ftbstuff:diamond_hammer", 5))
    runtime._bridge_item_count = mock.Mock()
    runtime._inside_base_protection_zone = mock.Mock(return_value=True)
    runtime._attempt_in_place_stoneblock_hammer_conversion = mock.Mock(return_value=False)
    runtime._assert_outside_base_protection = mock.Mock()
    runtime._wait_for_window_focus = mock.Mock()
    runtime._store_inventory_in_chest = mock.Mock()
    runtime._store_to_chest_bridge_supported = None
    runtime._idle_inventory_pressure_failures = 0
    runtime._operator_loop_state = {}
    return runtime


class StoneBlockBaseProtectionTest(unittest.TestCase):
    def test_local_in_place_route_does_not_relocate_after_failed_in_place_attempt(self) -> None:
        runtime = make_runtime(
            action_context={
                "actionName": "mine minecraft:dirt",
                "actionClass": "local_in_place",
                "routeId": "hammer__minecraft_dirt__minecraft_sand",
                "successCriteriaUsed": "inventory_or_stage_delta",
            }
        )
        runtime._bridge_item_count.side_effect = [2, 0]

        with self.assertLogs("agent.runner", level="INFO") as logs:
            result = runtime._acquire_stoneblock_hammer_chain_requirement("minecraft:sand", 12)

        self.assertFalse(result)
        runtime._attempt_in_place_stoneblock_hammer_conversion.assert_called_once()
        runtime._assert_outside_base_protection.assert_not_called()
        runtime.baritone.command.assert_not_called()
        combined = "\n".join(logs.output)
        self.assertIn('"event": "base_protection_decision"', combined)
        self.assertIn('"actionName": "mine minecraft:dirt"', combined)
        self.assertIn('"actionClass": "local_in_place"', combined)
        self.assertIn('"routeId": "hammer__minecraft_dirt__minecraft_sand"', combined)
        self.assertIn('"relocationRequired": false', combined)
        self.assertIn('"relocationReason": "local_in_place_attempt_failed_no_relocation_fallback"', combined)
        self.assertIn('"protectedRule": "base_protection_outside_required_for_destructive_breaking_only"', combined)
        self.assertIn('"localHousekeepingBoolean": true', combined)
        self.assertIn('"stageHint": "pre_sieving"', combined)

    def test_relocation_still_occurs_for_non_local_action_classes(self) -> None:
        runtime = make_runtime(
            action_context={
                "actionName": "mine minecraft:dirt",
                "actionClass": "destructive_mining",
                "routeId": "manual__mine_minecraft_dirt",
                "successCriteriaUsed": "pathing_or_movement",
            }
        )
        runtime._bridge_item_count.side_effect = [2, 0, 1]

        with self.assertLogs("agent.runner", level="INFO") as logs:
            result = runtime._acquire_stoneblock_hammer_chain_requirement("minecraft:sand", 1)

        self.assertTrue(result)
        runtime._attempt_in_place_stoneblock_hammer_conversion.assert_not_called()
        runtime._wait_for_window_focus.assert_called_once()
        runtime._assert_outside_base_protection.assert_called_once_with(
            "stoneblock hammering for minecraft:sand",
            status=runtime.baritone.read_bridge_status.return_value,
        )
        runtime.baritone.command.assert_called_once_with("mine minecraft:dirt")
        combined = "\n".join(logs.output)
        self.assertIn('"relocationRequired": true', combined)
        self.assertIn('"relocationReason": "protected_base_zone_requires_outside_execution"', combined)

    def test_gather_strategy_refuses_generic_fallback_for_local_in_place_route(self) -> None:
        runtime = make_runtime(
            action_context={
                "actionName": "mine minecraft:dirt",
                "actionClass": "local_in_place",
                "routeId": "hammer__minecraft_dirt__minecraft_sand",
                "successCriteriaUsed": "inventory_or_stage_delta",
            }
        )
        runtime._bridge_item_count.side_effect = [2, 0]
        runtime._acquire_stoneblock_hammer_chain_requirement = mock.Mock(return_value=False)
        runtime._acquire_base_requirement = mock.Mock(side_effect=AssertionError("generic gather should not run"))

        with self.assertRaisesRegex(RuntimeError, "refusing generic gather fallback"):
            runtime._run_quest_strategy("gather", "minecraft:sand", 12)

        runtime._acquire_stoneblock_hammer_chain_requirement.assert_called_once_with("minecraft:sand", 12)
        runtime._acquire_base_requirement.assert_not_called()

    def test_local_in_place_inventory_headroom_forces_storage_when_target_missing_and_inventory_full(self) -> None:
        runtime = make_runtime(
            action_context={
                "actionName": "local_hammer minecraft:dirt -> minecraft:sand",
                "actionClass": "local_in_place",
                "routeId": "hammer__minecraft_dirt__minecraft_sand",
                "successCriteriaUsed": "inventory_or_stage_delta",
            },
            status={
                "timestampMs": 1,
                "stoneblockStageHint": "pre_sieving",
                "inventory": [],
                "inventoryFreeSlots": 0,
            },
        )
        refreshed = {
            "timestampMs": 2,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
            "inventoryFreeSlots": 3,
        }
        runtime.baritone._status_free_inventory_slots.side_effect = [0, 3]
        runtime.baritone.read_bridge_status.side_effect = [refreshed]
        runtime._bridge_item_count.side_effect = [0, 0]

        result = runtime._ensure_local_conversion_inventory_headroom(
            source_item="minecraft:dirt",
            target_item="minecraft:sand",
            route_id="hammer__minecraft_dirt__minecraft_sand",
            stage_hint="pre_sieving",
            status={"timestampMs": 1, "stoneblockStageHint": "pre_sieving", "inventory": [], "inventoryFreeSlots": 0},
        )

        runtime._store_inventory_in_chest.assert_called_once_with(radius=8, max_stacks=24, include_hotbar=False, optional=False)
        self.assertEqual(refreshed, result)

    def test_in_place_hammer_uses_local_left_click_without_baritone_mine(self) -> None:
        status = {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
            "inventoryFreeSlots": 1,
        }
        runtime = make_runtime(
            action_context={
                "actionName": "local_hammer minecraft:dirt -> minecraft:sand",
                "actionClass": "local_in_place",
                "routeId": "hammer__minecraft_dirt__minecraft_sand",
                "successCriteriaUsed": "inventory_or_stage_delta",
            },
            status=status,
        )
        runtime._attempt_in_place_stoneblock_hammer_conversion = AgentRuntime._attempt_in_place_stoneblock_hammer_conversion.__get__(
            runtime,
            AgentRuntime,
        )
        runtime._ensure_item_hotbar_slot = mock.Mock(side_effect=[4])
        runtime._bridge_item_count = mock.Mock(side_effect=[0, 2, 0, 1, 1])
        runtime.baritone.read_bridge_status.side_effect = [status, status, status]
        runtime.baritone.evaluate_planner_task_context.return_value = {
            "finalSuccess": True,
            "beforeTargetCount": 0,
            "afterTargetCount": 1,
        }

        with mock.patch("agent.runner.time.sleep", return_value=None):
            result = runtime._attempt_in_place_stoneblock_hammer_conversion(
                source_item="minecraft:dirt",
                target_item="minecraft:sand",
                target_total=12,
                hammer_slot=5,
                attempts=1,
                status=status,
                before_target=0,
            )

        self.assertTrue(result)
        runtime.baritone.controller.left_click.assert_called_once()
        runtime.baritone.command.assert_not_called()
        runtime.baritone.wait_for_idle.assert_not_called()
        runtime.baritone.set_planner_task_context.assert_called_once()
        runtime.baritone.clear_planner_task_context.assert_called_once()

    def test_idle_inventory_pressure_uses_chest_placement_path_instead_of_optional_noop(self) -> None:
        status = {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
            "inventoryFreeSlots": 0,
        }
        runtime = make_runtime(
            action_context={
                "actionName": "mine minecraft:dirt",
                "actionClass": "local_in_place",
                "routeId": "hammer__minecraft_dirt__minecraft_sand",
                "successCriteriaUsed": "inventory_or_stage_delta",
            },
            status=status,
        )
        runtime._write_live_status = mock.Mock()
        state = SimpleNamespace(current_goal_id=None, last_error=None)
        refreshed = dict(status)
        refreshed["inventoryFreeSlots"] = 4
        runtime.baritone._status_free_inventory_slots.side_effect = [0, 4]
        runtime.baritone.read_bridge_status.side_effect = [status, refreshed]

        improved = runtime._run_idle_inventory_pressure(state)

        self.assertTrue(improved)
        runtime._store_inventory_in_chest.assert_called_once_with(radius=8, max_stacks=24, include_hotbar=False, optional=False)

    def test_idle_inventory_pressure_skips_when_free_slots_meet_threshold(self) -> None:
        status = {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
            "inventoryFreeSlots": 2,
        }
        runtime = make_runtime(
            action_context={
                "actionName": "craft minecraft:chest",
                "actionClass": "nearby_interaction",
                "routeId": "",
                "successCriteriaUsed": "ack_and_local_state_delta",
            },
            status=status,
        )
        runtime._write_live_status = mock.Mock()
        state = SimpleNamespace(current_goal_id=None, last_error=None, safe_paused=False)

        improved = runtime._run_idle_inventory_pressure(state)

        self.assertFalse(improved)
        self.assertEqual(0, runtime._idle_inventory_pressure_failures)
        runtime._store_inventory_in_chest.assert_not_called()
        runtime.baritone.try_inventory_cleanup.assert_not_called()
        runtime._write_live_status.assert_not_called()

    def test_idle_inventory_pressure_repeated_failure_enters_safe_pause_with_blocker(self) -> None:
        status = {
            "timestampMs": 1,
            "stoneblockStageHint": "pre_sieving",
            "inventory": [],
            "inventoryFreeSlots": 0,
        }
        runtime = make_runtime(
            action_context={
                "actionName": "craft minecraft:chest",
                "actionClass": "nearby_interaction",
                "routeId": "",
                "successCriteriaUsed": "ack_and_local_state_delta",
            },
            status=status,
        )
        runtime._write_live_status = mock.Mock()
        runtime._set_operator_loop_state = mock.Mock()
        runtime._log_operator_loop_decision = mock.Mock()
        runtime._store_inventory_in_chest.side_effect = RuntimeError("craft failed for minecraft:chest x1")
        runtime.baritone._status_free_inventory_slots.side_effect = [0, 0, 0, 0, 0, 0]
        runtime.baritone.read_bridge_status.side_effect = [status, status, status, status, status, status]
        state = SimpleNamespace(current_goal_id=None, last_error=None, safe_paused=False)

        self.assertFalse(runtime._run_idle_inventory_pressure(state))
        self.assertFalse(state.safe_paused)
        self.assertFalse(runtime._run_idle_inventory_pressure(state))
        self.assertFalse(state.safe_paused)
        self.assertFalse(runtime._run_idle_inventory_pressure(state))
        self.assertTrue(state.safe_paused)
        self.assertIn("idle inventory pressure unresolved after 3 attempts", str(state.last_error))
        runtime._set_operator_loop_state.assert_called()
        runtime._log_operator_loop_decision.assert_called()


if __name__ == "__main__":
    unittest.main()
