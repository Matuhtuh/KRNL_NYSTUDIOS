"""Validation tests for core schemas used by the StoneBlock 4 autonomous-agent foundation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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
    assert state.inventory[0].item_id == "minecraft:cobblestone"


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
    payload = {
        "plan_id": "plan_bootstrap_ore",
        "goal": {"goal_id": "goal_1", "description": "Acquire early-game iron", "priority": 7},
        "subtasks": [
            {
                "subtask_id": "sub_1",
                "title": "Mine compressed stone",
                "success_criteria": ["Collected at least 64 cobblestone"],
                "actions": [
                    {"action_type": "move_to", "parameters": {"x": 0, "y": 64, "z": 0}, "timeout_ticks": 120},
                    {"action_type": "mine_block", "parameters": {"block": "minecraft:stone"}, "timeout_ticks": 300},
                ],
            }
        ],
        "rationale": "Early mining unlocks key StoneBlock 4 resources and machine progression.",
    }

    plan = Plan.model_validate(payload)
    assert plan.goal.goal_id == "goal_1"
    assert len(plan.subtasks[0].actions) == 2


def test_invalid_plan_with_duplicate_subtasks() -> None:
    payload = {
        "plan_id": "plan_bad",
        "goal": {"goal_id": "goal_1", "description": "Test", "priority": 5},
        "subtasks": [
            {
                "subtask_id": "dup",
                "title": "One",
                "success_criteria": ["ok"],
                "actions": [{"action_type": "wait", "parameters": {}, "timeout_ticks": 1}],
            },
            {
                "subtask_id": "dup",
                "title": "Two",
                "success_criteria": ["ok"],
                "actions": [{"action_type": "wait", "parameters": {}, "timeout_ticks": 1}],
            },
        ],
        "rationale": "Ensure duplicate ids are rejected.",
    }

    with pytest.raises(ValidationError):
        Plan.model_validate(payload)


def test_additional_schema_examples() -> None:
    query = ResearchQuery(query_id="q1", objective="Unlock lava generation", local_context={"stage": "early"})
    failure = FailureRecord(
        tick=100,
        context="combat",
        error_type="LowHealth",
        detail="Agent repeatedly took damage from zombies in confined tunnel.",
        action_type="attack_entity",
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
