"""Bridge abstraction isolating Minecraft integration from planning and orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod

from planner.models import Action
from state.models import GameState

from .contracts import ActionResult, GameStateSnapshot


class GameBridge(ABC):
    """Deterministic interface for reading game state and issuing low-level commands."""

    @abstractmethod
    def read_state_snapshot(self) -> GameStateSnapshot:
        """Return raw bridge snapshot used for safety checks and progress detection."""

    @abstractmethod
    def read_state(self) -> GameState:
        """Return normalized internal state snapshot for planning/execution logic."""

    @abstractmethod
    def perform_action(self, action: Action) -> ActionResult:
        """Execute a low-level deterministic action and return typed action result."""
