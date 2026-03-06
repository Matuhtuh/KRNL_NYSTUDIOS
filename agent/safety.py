"""Deterministic safety checks on truthful bridge state prior to action execution."""

from __future__ import annotations

from bridge.contracts import GameStateSnapshot
from planner.models import Action

TOOL_REQUIRED_ACTIONS = {"mine_block", "place_block"}


def evaluate_safety(snapshot: GameStateSnapshot, action: Action | None = None) -> list[str]:
    """Return a list of blocking safety issues for the current state and optional action."""

    issues: list[str] = []
    if snapshot.player.health <= 6:
        issues.append("low_health")
    if snapshot.player.hunger <= 4:
        issues.append("low_hunger")
    if snapshot.open_screen.screen_open and action and action.action_type != "inventory_click":
        issues.append("open_screen_blocks_action")
    if action and action.action_type in TOOL_REQUIRED_ACTIONS and snapshot.player.held_main_hand_item == "minecraft:air":
        issues.append("missing_main_hand_tool")
    return issues
