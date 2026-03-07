from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.dashboard_state import DashboardQueryService


class FakeKnowledge:
    def get_stage_knowledge(self, stage_hint: str, bridge_status: dict | None = None) -> dict:
        return {
            "stageHint": stage_hint or "hammer_to_resources",
            "label": "Hammer To Resources",
            "summary": "Local in-place conversion stage.",
            "focusItems": ["minecraft:gravel"],
            "targetBands": {"minecraft:gravel": {"min": 16, "max": 96}},
            "nextStageHint": "pre_sieving",
            "routes": [],
            "evidence": [],
        }

    def get_item_route(self, item_id: str) -> dict:
        return {"itemId": item_id, "routes": []}

    def get_next_obtainable_targets(self, bridge_status: dict | None = None, *, limit: int = 6) -> dict:
        return {"targets": [{"itemId": "minecraft:gravel"}][:limit]}


def _cfg(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        planner=SimpleNamespace(state_file=str(root / "runtime/state.json"), tick_seconds=2.0),
        baritone=SimpleNamespace(
            bridge_dir=str(root / "bridge"),
            bridge_status_stale_after_seconds=4.0,
        ),
    )


class DashboardQueryServiceTest(unittest.TestCase):
    def test_snapshot_marks_stale_preview_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir(parents=True, exist_ok=True)
            live_path = root / "runtime/live_status.json"
            live_path.parent.mkdir(parents=True, exist_ok=True)

            old_ts = time.time() - 20.0
            bridge_status = {
                "timestampMs": int(old_ts * 1000),
                "bridgeOk": True,
                "inWorld": True,
                "stoneblockStageHint": "hammer_to_resources",
                "inventoryFreeSlots": 3,
                "stoneblockKeyItemCounts": {"minecraft:gravel": 4},
                "inventory": [],
                "hotbar": [],
                "isPathing": False,
            }
            live_status = {
                "timestamp": "2026-03-07T00:00:00+00:00",
                "phase": "task_running",
                "task": {"type": "quest_item_acquire", "item": "minecraft:gravel", "count": 12},
                "message": "running task",
                "error": "sample error",
                "baritone": {"activeCommandBody": "mine minecraft:gravel"},
                "operatorLoop": {
                    "currentTask": "quest_item_acquire minecraft:gravel x12",
                    "nextSelectedTask": "quest_item_acquire minecraft:dirt x12",
                    "nextTaskReason": "planner selected ready goal stone_chain",
                    "lastBlocker": "inventory full",
                    "deferredTasks": [{"kind": "goal", "id": "quest_sand", "remainingSeconds": 42.0}],
                },
            }
            (bridge_dir / "status.json").write_text(json.dumps(bridge_status), encoding="utf-8")
            live_path.write_text(json.dumps(live_status), encoding="utf-8")

            service = DashboardQueryService(_cfg(root), FakeKnowledge())
            snapshot = service.snapshot(
                preview_state={
                    "available": True,
                    "capturedAt": time.time() - 10.0,
                    "width": 1920,
                    "height": 1080,
                    "error": "",
                }
            )

            self.assertFalse(snapshot["bridge"]["freshness"]["fresh"])
            self.assertFalse(snapshot["preview"]["fresh"])
            self.assertEqual("sample error", snapshot["lastError"])
            self.assertIn("quest_item_acquire", snapshot["currentAction"]["taskText"])
            self.assertEqual("quest_item_acquire minecraft:dirt x12", snapshot["operatorLoop"]["nextSelectedTask"])

    def test_explain_current_action_includes_knowledge_backed_success_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir(parents=True, exist_ok=True)
            live_path = root / "runtime/live_status.json"
            live_path.parent.mkdir(parents=True, exist_ok=True)

            now_ms = int(time.time() * 1000.0)
            bridge_status = {
                "timestampMs": now_ms,
                "bridgeOk": True,
                "inWorld": True,
                "stoneblockStageHint": "pre_sieving",
                "inventoryFreeSlots": 2,
                "inventory": [],
                "hotbar": [],
            }
            live_status = {
                "timestamp": "2026-03-07T00:00:00+00:00",
                "phase": "task_running",
                "task": {"type": "quest_item_acquire", "item": "minecraft:dirt", "count": 12},
                "message": "running task",
                "error": "",
                "baritone": {
                    "activeCommandBody": "mine minecraft:gravel",
                    "plannerTaskContext": {
                        "actionClass": "local_in_place",
                        "successCriteriaUsed": "inventory_or_stage_delta",
                        "routeId": "hammer__minecraft_gravel__minecraft_dirt",
                        "routeMechanism": "hammer",
                        "knowledgeEvidence": [{"path": "hammer.js", "label": "hammer.js"}],
                    },
                    "lastActionEvaluation": {
                        "actionClass": "local_in_place",
                        "successCriteriaUsed": "inventory_or_stage_delta",
                        "finalSuccess": True,
                        "beforeTargetCount": 1,
                        "afterTargetCount": 2,
                        "knowledgeEvidence": [{"path": "hammer.js", "label": "hammer.js"}],
                    },
                },
                "operatorLoop": {
                    "currentTask": "quest_item_acquire minecraft:dirt x12",
                    "nextSelectedTask": "quest_item_acquire minecraft:sand x12",
                    "nextTaskReason": "task completed successfully; runtime will continue",
                },
            }
            (bridge_dir / "status.json").write_text(json.dumps(bridge_status), encoding="utf-8")
            live_path.write_text(json.dumps(live_status), encoding="utf-8")

            service = DashboardQueryService(_cfg(root), FakeKnowledge())
            explanation = service.explain_current_action()

            self.assertEqual("local_in_place", explanation["actionClass"])
            self.assertEqual("inventory_or_stage_delta", explanation["successCriteriaUsed"])
            self.assertEqual("hammer__minecraft_gravel__minecraft_dirt", explanation["routeId"])
            self.assertTrue(explanation["successEvaluation"]["finalSuccess"])
            self.assertEqual("hammer.js", explanation["evidence"][0]["label"])
            self.assertEqual("quest_item_acquire minecraft:sand x12", explanation["operatorLoop"]["nextSelectedTask"])

    def test_explain_current_action_preserves_zero_before_target_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir(parents=True, exist_ok=True)
            live_path = root / "runtime/live_status.json"
            live_path.parent.mkdir(parents=True, exist_ok=True)

            now_ms = int(time.time() * 1000.0)
            bridge_status = {
                "timestampMs": now_ms,
                "bridgeOk": True,
                "inWorld": True,
                "stoneblockStageHint": "pre_sieving",
                "inventoryFreeSlots": 2,
                "inventory": [],
                "hotbar": [],
            }
            live_status = {
                "timestamp": "2026-03-07T00:00:00+00:00",
                "phase": "task_running",
                "task": {"type": "quest_item_acquire", "item": "minecraft:sand", "count": 12},
                "message": "running task",
                "error": "",
                "baritone": {
                    "activeCommandBody": "mine minecraft:dirt",
                    "plannerTaskContext": {
                        "actionClass": "local_in_place",
                        "successCriteriaUsed": "inventory_or_stage_delta",
                        "routeId": "hammer__minecraft_dirt__minecraft_sand",
                        "routeMechanism": "hammer",
                        "knowledgeEvidence": [{"path": "hammer.js", "label": "hammer.js"}],
                    },
                    "lastActionEvaluation": {
                        "actionClass": "local_in_place",
                        "successCriteriaUsed": "inventory_or_stage_delta",
                        "finalSuccess": False,
                        "beforeSourceCount": 2,
                        "afterSourceCount": 1,
                        "beforeTargetCount": 0,
                        "afterTargetCount": 0,
                        "knowledgeEvidence": [{"path": "hammer.js", "label": "hammer.js"}],
                    },
                },
            }
            (bridge_dir / "status.json").write_text(json.dumps(bridge_status), encoding="utf-8")
            live_path.write_text(json.dumps(live_status), encoding="utf-8")

            service = DashboardQueryService(_cfg(root), FakeKnowledge())
            explanation = service.explain_current_action()

            self.assertEqual(0, explanation["successEvaluation"]["beforeTargetCount"])
            self.assertEqual(0, explanation["successEvaluation"]["afterTargetCount"])
            self.assertFalse(explanation["successEvaluation"]["finalSuccess"])


if __name__ == "__main__":
    unittest.main()
