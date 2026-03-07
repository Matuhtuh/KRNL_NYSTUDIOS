from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.baritone import BaritoneBridge


def make_bridge(*, body: str = "mine minecraft:gravel") -> BaritoneBridge:
    bridge = BaritoneBridge.__new__(BaritoneBridge)
    bridge.config = SimpleNamespace(
        transport="bridge_file",
        bridge_status_stale_after_seconds=4.0,
        bridge_unresponsive_after_seconds=20.0,
        long_command_startup_seconds=0.1,
        require_pathing_transition_for_long_commands=True,
        long_running_prefixes=["mine ", "tunnel ", "build ", "goto ", "explore "],
        auto_eat_enabled=False,
        fight_hostiles=False,
        inventory_cleanup_enabled=False,
        stuck_recovery_enabled=False,
        avoid_players_while_mining=False,
        min_pickaxe_count=0,
        min_main_hand_durability=1,
        poll_seconds=0.01,
        safety_recover_timeout_seconds=0.1,
        safety_poll_seconds=0.01,
    )
    bridge.controller = SimpleNamespace(
        stop_requested=False,
        automation_enabled=True,
        wait_if_paused=lambda: None,
    )
    bridge.perception = SimpleNamespace()
    bridge._active_command_id = "active-123"
    bridge._active_command_body = body
    bridge._active_command_started_at = time.time() - 1.0
    bridge._next_torch_at = 0.0
    bridge._stuck_recovery_attempts = 0
    bridge._ultimine_active_for_command = False
    bridge._combat_supported = None
    bridge._ultimine_supported = None
    bridge._planner_task_context = {}
    bridge._bridge_command_context_by_id = {}
    bridge._observed_bridge_command_ids = set()
    bridge._persistent_aux_commands_applied = set()
    bridge._last_action_evaluation = {}
    bridge._aux_command_ids = set()
    bridge._manual_pause_active = False
    bridge._inventory_cleanup_protected_item_ids = set()
    bridge._sync_manual_pause_with_controller = lambda: None  # type: ignore[method-assign]
    bridge._maybe_place_torch = lambda is_pathing, status=None: None  # type: ignore[method-assign]
    bridge.safety_reasons = lambda status, consider_players=False: []  # type: ignore[method-assign]
    bridge._status_player_can_fly = lambda status: False  # type: ignore[method-assign]
    bridge._hotbar_pickaxe_count = lambda status: 1  # type: ignore[method-assign]
    bridge._main_hand_pickaxe_low_durability = lambda status: False  # type: ignore[method-assign]
    bridge._ensure_pickaxe_hotbar = lambda status: True  # type: ignore[method-assign]
    bridge._attempt_tool_recovery = lambda reason: None  # type: ignore[method-assign]
    bridge.run_recovery_playbook = lambda **kwargs: False  # type: ignore[method-assign]
    bridge._disable_ultimine_after_command = lambda context="": None  # type: ignore[method-assign]
    return bridge


def make_local_in_place_context(
    *,
    action_name: str = "mine minecraft:gravel",
    source_item: str = "minecraft:gravel",
    target_item: str = "minecraft:dirt",
    before_source_count: int = 47,
    before_target_count: int = 1,
    target_total: int = 2,
    stage_hint_before: str = "hammer_to_resources",
) -> dict[str, object]:
    return {
        "actionName": action_name,
        "actionClass": "local_in_place",
        "successCriteriaUsed": "inventory_or_stage_delta",
        "sourceItem": source_item,
        "targetItem": target_item,
        "beforeSourceCount": before_source_count,
        "beforeTargetCount": before_target_count,
        "targetTotal": target_total,
        "stageHintBefore": stage_hint_before,
        "routeId": f"hammer__{source_item.replace(':', '_')}__{target_item.replace(':', '_')}",
        "knowledgeEvidence": [{"path": "hammer.js", "label": "hammer.js"}],
    }


def make_status(
    *,
    command_id: str = "active-123",
    command: str = "mine minecraft:gravel",
    target_item: str = "minecraft:dirt",
    target_count: int = 2,
    source_item: str = "minecraft:gravel",
    source_count: int = 46,
    stage_hint: str = "pre_sieving",
) -> dict:
    now_ms = int(time.time() * 1000.0)
    return {
        "timestampMs": now_ms,
        "lastCommandAtMs": now_ms,
        "inWorld": True,
        "baritoneLoaded": True,
        "lastCommandId": command_id,
        "lastCommand": command,
        "lastCommandResult": "accepted",
        "lastCommandError": "",
        "isPathing": False,
        "currentGoal": "",
        "queueDepth": 0,
        "pendingResponseCount": 0,
        "posX": 0.0,
        "posY": 64.0,
        "posZ": 0.0,
        "stoneblockStageHint": stage_hint,
        "inventory": [
            {"slot": 1, "itemId": source_item, "count": source_count},
            {"slot": 2, "itemId": target_item, "count": target_count},
        ],
        "hotbar": [],
    }


class BaritoneActionEvaluationTest(unittest.TestCase):
    def test_local_in_place_conversion_succeeds_without_pathing(self) -> None:
        bridge = make_bridge()
        bridge._bridge_command_context_by_id["active-123"] = make_local_in_place_context()
        bridge.read_bridge_status = lambda: make_status()  # type: ignore[method-assign]

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            result = bridge._wait_for_idle_bridge(timeout=0.2, poll=0.01)

        self.assertTrue(result)
        self.assertEqual("local_in_place", bridge._last_action_evaluation["actionClass"])
        self.assertEqual("inventory_or_stage_delta", bridge._last_action_evaluation["successCriteriaUsed"])
        self.assertFalse(bridge._last_action_evaluation["pathingObserved"])
        self.assertFalse(bridge._last_action_evaluation["movementObserved"])
        self.assertTrue(bridge._last_action_evaluation["finalSuccess"])
        self.assertEqual(2, bridge._last_action_evaluation["afterTargetCount"])

    def test_local_in_place_conversion_fails_without_item_or_stage_delta(self) -> None:
        bridge = make_bridge()
        bridge._bridge_command_context_by_id["active-123"] = make_local_in_place_context()
        bridge.read_bridge_status = lambda: make_status(target_count=1, source_count=47, stage_hint="hammer_to_resources")  # type: ignore[method-assign]

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            result = bridge._wait_for_idle_bridge(timeout=0.2, poll=0.01)

        self.assertFalse(result)
        self.assertEqual("local_in_place", bridge._last_action_evaluation["actionClass"])
        self.assertFalse(bridge._last_action_evaluation["finalSuccess"])
        self.assertEqual(1, bridge._last_action_evaluation["afterTargetCount"])

    def test_pathing_actions_still_require_pathing_or_movement(self) -> None:
        bridge = make_bridge(body="tunnel 2 2 8")
        bridge.read_bridge_status = lambda: make_status(command="tunnel 2 2 8", target_item="minecraft:cobblestone", target_count=0)  # type: ignore[method-assign]

        with mock.patch("agent.baritone.time.sleep", return_value=None):
            result = bridge._wait_for_idle_bridge(timeout=0.2, poll=0.01)

        self.assertFalse(result)
        self.assertEqual("destructive_mining", bridge._last_action_evaluation["actionClass"])
        self.assertEqual("pathing_or_movement", bridge._last_action_evaluation["successCriteriaUsed"])
        self.assertFalse(bridge._last_action_evaluation["pathingObserved"])
        self.assertFalse(bridge._last_action_evaluation["movementObserved"])

    def test_local_in_place_zero_target_count_is_preserved(self) -> None:
        bridge = make_bridge(body="mine minecraft:dirt")
        status = make_status(
            command="mine minecraft:dirt",
            source_item="minecraft:dirt",
            source_count=1,
            target_item="minecraft:sand",
            target_count=0,
            stage_hint="pre_sieving",
        )

        evaluation = bridge._contextual_action_evaluation(
            status,
            action_meta=make_local_in_place_context(
                action_name="mine minecraft:dirt",
                source_item="minecraft:dirt",
                target_item="minecraft:sand",
                before_source_count=2,
                before_target_count=0,
                target_total=12,
                stage_hint_before="pre_sieving",
            ),
            movement_observed=False,
            pathing_observed=False,
        )

        self.assertEqual(0, evaluation["beforeTargetCount"])
        self.assertEqual(0, evaluation["afterTargetCount"])

    def test_local_in_place_source_drop_without_target_gain_is_failure(self) -> None:
        bridge = make_bridge(body="mine minecraft:dirt")
        status = make_status(
            command="mine minecraft:dirt",
            source_item="minecraft:dirt",
            source_count=1,
            target_item="minecraft:sand",
            target_count=0,
            stage_hint="pre_sieving",
        )

        evaluation = bridge._contextual_action_evaluation(
            status,
            action_meta=make_local_in_place_context(
                action_name="mine minecraft:dirt",
                source_item="minecraft:dirt",
                target_item="minecraft:sand",
                before_source_count=2,
                before_target_count=0,
                target_total=12,
                stage_hint_before="pre_sieving",
            ),
            movement_observed=False,
            pathing_observed=False,
        )

        self.assertEqual(2, evaluation["beforeSourceCount"])
        self.assertEqual(1, evaluation["afterSourceCount"])
        self.assertEqual(0, evaluation["beforeTargetCount"])
        self.assertEqual(0, evaluation["afterTargetCount"])
        self.assertFalse(evaluation["finalSuccess"])

    def test_local_in_place_zero_to_positive_target_gain_is_success(self) -> None:
        bridge = make_bridge(body="mine minecraft:dirt")
        status = make_status(
            command="mine minecraft:dirt",
            source_item="minecraft:dirt",
            source_count=1,
            target_item="minecraft:sand",
            target_count=1,
            stage_hint="pre_sieving",
        )

        evaluation = bridge._contextual_action_evaluation(
            status,
            action_meta=make_local_in_place_context(
                action_name="mine minecraft:dirt",
                source_item="minecraft:dirt",
                target_item="minecraft:sand",
                before_source_count=2,
                before_target_count=0,
                target_total=12,
                stage_hint_before="pre_sieving",
            ),
            movement_observed=False,
            pathing_observed=False,
        )

        self.assertEqual(0, evaluation["beforeTargetCount"])
        self.assertEqual(1, evaluation["afterTargetCount"])
        self.assertTrue(evaluation["finalSuccess"])

    def test_action_completion_diagnostic_keeps_zero_counts(self) -> None:
        bridge = make_bridge(body="mine minecraft:dirt")
        bridge._bridge_command_context_by_id["active-123"] = make_local_in_place_context(
            action_name="mine minecraft:dirt",
            source_item="minecraft:dirt",
            target_item="minecraft:sand",
            before_source_count=2,
            before_target_count=0,
            target_total=12,
            stage_hint_before="pre_sieving",
        )
        bridge.read_bridge_status = lambda: make_status(
            command="mine minecraft:dirt",
            source_item="minecraft:dirt",
            source_count=1,
            target_item="minecraft:sand",
            target_count=0,
            stage_hint="pre_sieving",
        )  # type: ignore[method-assign]

        with self.assertLogs("agent.baritone", level="INFO") as captured:
            with mock.patch("agent.baritone.time.sleep", return_value=None):
                result = bridge._wait_for_idle_bridge(timeout=0.2, poll=0.01)

        self.assertFalse(result)
        self.assertEqual(0, bridge._last_action_evaluation["beforeTargetCount"])
        self.assertEqual(0, bridge._last_action_evaluation["afterTargetCount"])
        self.assertFalse(bridge._last_action_evaluation["finalSuccess"])
        joined_logs = "\n".join(line for line in captured.output if "action_completion_diagnostic" in line)
        self.assertIn('"beforeTargetCount": 0', joined_logs)
        self.assertIn('"afterTargetCount": 0', joined_logs)

    def test_action_class_normalization_maps_knowledge_machine_and_stationary(self) -> None:
        bridge = make_bridge()

        self.assertEqual("nearby_interaction", bridge._normalize_action_class("local_stationary"))
        self.assertEqual("machine_processing", bridge._normalize_action_class("machine"))


if __name__ == "__main__":
    unittest.main()
