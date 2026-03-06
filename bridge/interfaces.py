"""Bridge abstraction isolating Minecraft integration from planning and orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod

from planner.models import Action
from state.models import GameState


class GameBridge(ABC):
    """Deterministic interface for reading game state and issuing low-level commands."""

    @abstractmethod
    def read_state(self) -> GameState:
        """Return current game state snapshot suitable for planner/executor logic."""

    @abstractmethod
    def perform_action(self, action: Action) -> bool:
        """Execute a low-level deterministic action and report success."""
