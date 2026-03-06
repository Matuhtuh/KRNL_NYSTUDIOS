"""Builder interfaces separating design generation from world-state verification."""

from __future__ import annotations

from abc import ABC, abstractmethod

from state.models import GameState

from .models import Blueprint


class BlueprintGenerator(ABC):
    """Generates blueprints from high-level build objectives and constraints."""

    @abstractmethod
    def generate(self, objective: str, state: GameState) -> Blueprint:
        """Create a structured blueprint ready for execution and verification."""


class BuildVerifier(ABC):
    """Validates built structures and produces repair-oriented assessments."""

    @abstractmethod
    def verify(self, blueprint: Blueprint, state: GameState) -> bool:
        """Return True when structure matches blueprint and no repair is needed."""
