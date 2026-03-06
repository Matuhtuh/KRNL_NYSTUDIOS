"""Interfaces for safety checks and stuck recovery in StoneBlock 4 loops."""

from __future__ import annotations

from abc import ABC, abstractmethod

from state.models import GameState


class RecoveryManager(ABC):
    """Evaluates whether immediate recovery actions are required."""

    @abstractmethod
    def requires_recovery(self, state: GameState) -> bool:
        """Return True when agent is unsafe or stuck and should enter recovery flow."""

    @abstractmethod
    def recover(self, state: GameState) -> bool:
        """Attempt deterministic recovery and return whether agent is now stable."""
