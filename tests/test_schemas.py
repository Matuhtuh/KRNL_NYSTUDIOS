"""Validation tests for core schemas used by the StoneBlock 4 autonomous-agent foundation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bridge.contracts import ActionRequest
from builder.models import Blueprint, MaterialEstimate
from memory.models import FailureRecord
from planner.models import Plan
from research.models import ResearchQuery
from state.models import GameState, InventoryItem


def test_valid_game_state_schema() -> None:
    state = GameState(
        tick=10,
        dimension="minecraft:overworld",
        x=1.0,
        y=64.0,
        z=1.0,
        yaw=0.0,
        pitch=0.0,
        health=20.0,
        hunger=20,
        inventory=[InventoryItem(item_id="minecraft:cobblestone", count=32, slot=0)],
    )
    assert state.tick == 10


def test_invalid_game_state_hunger_rejected() -> None:
    with pytest.raises(ValidationError):
        GameState(
            tick=1,
            dimension="minecraft:overworld",
            x=0,
            y=64,
            z=0,
            yaw=0,
            pitch=0,
            health=20,
            hunger=21,
        )


def test_valid_plan_payload() -> None:
    plan = Plan.model_validate(
        {
            "plan_id": "plan_1",
            "goal": {"goal_id": "goal_1", "description": "Acquire starter resources", "priority": 5},
            "subtasks": [
                {
                    "subtask_id": "sub_1",
                    "title": "Probe bridge action",
                    "success_criteria": ["Bridge accepts noop"],
                    "actions": [{"action_type": "noop", "parameters": {}, "timeout_ticks": 10}],
                }
            ],
            "rationale": "Minimal bridge test loop.",
        }
    )
    assert plan.subtasks[0].actions[0].action_type == "noop"


def test_invalid_plan_with_duplicate_subtasks() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "plan_id": "plan_bad",
                "goal": {"goal_id": "goal_1", "description": "Test", "priority": 5},
                "subtasks": [
                    {"subtask_id": "dup", "title": "One", "success_criteria": ["ok"], "actions": [{"action_type": "noop"}]},
                    {"subtask_id": "dup", "title": "Two", "success_criteria": ["ok"], "actions": [{"action_type": "noop"}]},
                ],
                "rationale": "Ensure duplicate ids are rejected.",
            }
        )


def test_action_request_contract() -> None:
    request = ActionRequest(request_id="r1", action_type="noop", parameters={}, timeout_ticks=10)
    assert request.request_id == "r1"


def test_additional_schema_examples() -> None:
    query = ResearchQuery(query_id="q1", objective="Unlock lava generation", local_context={"stage": "early"})
    failure = FailureRecord(
        tick=100,
        context="combat",
        error_type="LowHealth",
        detail="Agent repeatedly took damage from zombies in confined tunnel.",
        action_type="interact_use",
    )
    blueprint = Blueprint(
        blueprint_id="bp1",
        name="Starter Platform",
        origin_x=0,
        origin_y=70,
        origin_z=0,
        placements=[{"block_id": "minecraft:cobblestone", "x": 0, "y": 0, "z": 0}],
        materials=[MaterialEstimate(block_id="minecraft:cobblestone", required_count=1)],
    )

    assert query.objective.startswith("Unlock")
    assert failure.error_type == "LowHealth"
    assert blueprint.materials[0].required_count == 1
