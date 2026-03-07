from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.stoneblock4_knowledge import StoneBlockKnowledgeBase


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        value_f = float(value)
        if value_f > 10_000_000_000:
            return value_f / 1000.0
        return value_f
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _freshness(timestamp_value: Any, stale_after_seconds: float) -> dict[str, Any]:
    observed_at = _parse_timestamp(timestamp_value)
    if observed_at <= 0.0:
        return {"available": False, "fresh": False, "ageSeconds": None}
    age = max(0.0, time.time() - observed_at)
    return {
        "available": True,
        "fresh": age <= max(0.1, float(stale_after_seconds)),
        "ageSeconds": round(age, 3),
        "observedAt": datetime.fromtimestamp(observed_at, timezone.utc).isoformat(),
    }


def _live_status_path(cfg: AppConfig) -> Path:
    state_path = Path(cfg.planner.state_file).resolve()
    return state_path.with_name("live_status.json")


def _bridge_status_path(cfg: AppConfig) -> Path:
    return Path(cfg.baritone.bridge_dir).expanduser().resolve() / "status.json"


def _summarize_inventory(bridge_status: dict[str, Any]) -> dict[str, Any]:
    inventory = bridge_status.get("inventory")
    if not isinstance(inventory, list):
        inventory = []
    non_empty: list[dict[str, Any]] = []
    free_slots = 0
    for slot in inventory:
        if not isinstance(slot, dict):
            continue
        item_id = str(slot.get("itemId", "")).strip()
        count = int(slot.get("count", 0) or 0)
        if not item_id and count <= 0:
            free_slots += 1
            continue
        non_empty.append(
            {
                "slot": int(slot.get("slot", 0) or 0),
                "itemId": item_id,
                "count": count,
            }
        )
    non_empty.sort(key=lambda row: (row["count"], row["itemId"]), reverse=True)
    hotbar = bridge_status.get("hotbar")
    hotbar_summary: list[dict[str, Any]] = []
    if isinstance(hotbar, list):
        for slot in hotbar:
            if not isinstance(slot, dict):
                continue
            hotbar_summary.append(
                {
                    "slot": int(slot.get("slot", 0) or 0),
                    "itemId": str(slot.get("itemId", "")).strip(),
                    "count": int(slot.get("count", 0) or 0),
                }
            )
    return {
        "freeSlots": int(bridge_status.get("inventoryFreeSlots", free_slots) or free_slots),
        "totalSlots": len(inventory),
        "topStacks": non_empty[:12],
        "hotbar": hotbar_summary,
        "keyItemCounts": dict(bridge_status.get("stoneblockKeyItemCounts", {})),
    }


def _task_text(task: dict[str, Any]) -> str:
    task_type = str(task.get("type", "")).strip()
    if not task_type:
        return ""
    if task_type in {"quest_item_acquire", "craft_item", "craft_with_dependencies"}:
        item = str(task.get("item", "")).strip()
        count = int(task.get("count", 1) or 1)
        return f"{task_type} {item} x{count}".strip()
    if task_type == "baritone_command":
        return str(task.get("command", "")).strip()
    if task_type == "run_module":
        return f"run_module {str(task.get('name', '')).strip()}".strip()
    return task_type


def _summarize_current_action(
    live_status: dict[str, Any],
    bridge_status: dict[str, Any],
    knowledge: StoneBlockKnowledgeBase,
) -> dict[str, Any]:
    task = dict(live_status.get("task", {})) if isinstance(live_status.get("task"), dict) else {}
    task_text = _task_text(task)
    stage_hint = str(bridge_status.get("stoneblockStageHint", "")).strip()
    stage = knowledge.get_stage_knowledge(stage_hint, bridge_status=bridge_status)
    active_command = ""
    planner_context: dict[str, Any] = {}
    last_action_evaluation: dict[str, Any] = {}
    baritone = live_status.get("baritone")
    if isinstance(baritone, dict):
        active_command = str(baritone.get("activeCommandBody", "")).strip()
        if isinstance(baritone.get("plannerTaskContext"), dict):
            planner_context = dict(baritone.get("plannerTaskContext"))
        if isinstance(baritone.get("lastActionEvaluation"), dict):
            last_action_evaluation = dict(baritone.get("lastActionEvaluation"))
    explanation = task_text or active_command or str(live_status.get("message", "")).strip()
    if not explanation:
        explanation = "idle"
    action_class = str(planner_context.get("actionClass") or last_action_evaluation.get("actionClass") or "").strip()
    success_criteria = str(
        planner_context.get("successCriteriaUsed")
        or last_action_evaluation.get("successCriteriaUsed")
        or ""
    ).strip()
    evidence = []
    if isinstance(planner_context.get("knowledgeEvidence"), list):
        evidence = list(planner_context.get("knowledgeEvidence"))
    elif isinstance(last_action_evaluation.get("knowledgeEvidence"), list):
        evidence = list(last_action_evaluation.get("knowledgeEvidence"))
    if not evidence:
        evidence = list(stage.get("evidence", []))
    operator_loop = dict(live_status.get("operatorLoop", {})) if isinstance(live_status.get("operatorLoop"), dict) else {}
    return {
        "taskText": task_text,
        "activeCommand": active_command,
        "stageHint": stage_hint,
        "stageLabel": stage.get("label", stage_hint or "unknown"),
        "message": str(live_status.get("message", "")).strip(),
        "error": str(live_status.get("error", "")).strip(),
        "explanation": explanation,
        "actionClass": action_class,
        "successCriteriaUsed": success_criteria,
        "routeId": str(planner_context.get("routeId") or last_action_evaluation.get("routeId") or "").strip(),
        "routeMechanism": str(planner_context.get("routeMechanism") or last_action_evaluation.get("routeMechanism") or "").strip(),
        "successEvaluation": last_action_evaluation,
        "evidence": evidence,
        "operatorLoop": operator_loop,
    }


class DashboardQueryService:
    def __init__(self, cfg: AppConfig, knowledge: StoneBlockKnowledgeBase) -> None:
        self.cfg = cfg
        self.knowledge = knowledge

    def read_live_status(self) -> dict[str, Any]:
        return _read_json(_live_status_path(self.cfg))

    def read_bridge_status(self) -> dict[str, Any]:
        return _read_json(_bridge_status_path(self.cfg))

    def get_bridge_status(self) -> dict[str, Any]:
        status = self.read_bridge_status()
        freshness = _freshness(
            status.get("timestampIso") or status.get("timestampMs"),
            self.cfg.baritone.bridge_status_stale_after_seconds,
        )
        return {
            "freshness": freshness,
            "status": status,
        }

    def get_inventory_summary(self) -> dict[str, Any]:
        bridge = self.read_bridge_status()
        return _summarize_inventory(bridge)

    def get_stage_knowledge(self, stage_hint: str = "") -> dict[str, Any]:
        bridge = self.read_bridge_status()
        return self.knowledge.get_stage_knowledge(stage_hint, bridge_status=bridge)

    def get_item_route(self, item_id: str) -> dict[str, Any]:
        return self.knowledge.get_item_route(item_id)

    def get_next_obtainable_targets(self, *, limit: int = 6) -> dict[str, Any]:
        bridge = self.read_bridge_status()
        return self.knowledge.get_next_obtainable_targets(bridge, limit=limit)

    def search_knowledge(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        return self.knowledge.search_knowledge(query, limit=limit)

    def explain_current_action(self) -> dict[str, Any]:
        live = self.read_live_status()
        bridge = self.read_bridge_status()
        return _summarize_current_action(live, bridge, self.knowledge)

    def snapshot(self, preview_state: dict[str, Any] | None = None) -> dict[str, Any]:
        live = self.read_live_status()
        bridge = self.read_bridge_status()
        bridge_freshness = _freshness(
            bridge.get("timestampIso") or bridge.get("timestampMs"),
            self.cfg.baritone.bridge_status_stale_after_seconds,
        )
        live_freshness = _freshness(
            live.get("timestamp"),
            max(3.0, float(self.cfg.planner.tick_seconds) * 3.0),
        )
        inventory = _summarize_inventory(bridge)
        action = _summarize_current_action(live, bridge, self.knowledge)
        stage = self.knowledge.get_stage_knowledge(str(bridge.get("stoneblockStageHint", "")).strip(), bridge_status=bridge)
        preview_meta = dict(preview_state or {})
        captured_at = preview_meta.get("capturedAt")
        preview_freshness = _freshness(captured_at, 3.0) if captured_at else {"available": False, "fresh": False, "ageSeconds": None}
        live_state = dict(live.get("state", {})) if isinstance(live.get("state"), dict) else {}
        last_error = str(live.get("error") or live_state.get("last_error") or "").strip()
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "preview": {
                **preview_freshness,
                "available": bool(preview_meta.get("available", False)),
                "error": str(preview_meta.get("error", "")).strip(),
                "width": int(preview_meta.get("width", 0) or 0),
                "height": int(preview_meta.get("height", 0) or 0),
                "url": "/api/preview.jpg",
            },
            "bridge": {
                "freshness": bridge_freshness,
                "status": bridge,
            },
            "live": {
                "freshness": live_freshness,
                "status": live,
            },
            "stage": stage,
            "inventory": inventory,
            "currentAction": action,
            "operatorLoop": dict(live.get("operatorLoop", {})) if isinstance(live.get("operatorLoop"), dict) else {},
            "lastError": last_error,
        }
