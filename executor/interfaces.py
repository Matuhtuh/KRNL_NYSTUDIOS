"""Executor abstractions for running one deterministic action at a time."""

from __future__ import annotations

from abc import ABC, abstractmethod

from planner.models import Action
from state.models import GameState


class Executor(ABC):
    """Executes planned actions against the game bridge and returns success/failure."""

    @abstractmethod
    def execute_one(self, action: Action, state: GameState) -> bool:
        """Execute one action and report whether it completed successfully."""
