"""Interfaces for planners that can combine deterministic context with LLM-assisted strategy."""

from __future__ import annotations

from abc import ABC, abstractmethod

from state.models import GameState

from .models import Goal, Plan


class Planner(ABC):
    """Produces structured plans from goals and game state snapshots."""

    @abstractmethod
    def create_plan(self, goal: Goal, state: GameState) -> Plan:
        """Generate a validated plan for the given goal and current state."""
