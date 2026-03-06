"""Executor abstractions for running one deterministic action at a time."""

from __future__ import annotations

from abc import ABC, abstractmethod

from bridge.contracts import ActionResult
from planner.models import Action
from state.models import GameState


class Executor(ABC):
    """Executes planned actions against the game bridge and returns typed results."""

    @abstractmethod
    def execute_one(self, action: Action, state: GameState) -> ActionResult:
        """Execute one action and return full result payload."""
