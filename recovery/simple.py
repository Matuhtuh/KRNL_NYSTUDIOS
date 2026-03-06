"""Simple deterministic recovery checks for first local integration loop."""

from __future__ import annotations

from state.models import GameState

from .interfaces import RecoveryManager


class SimpleRecoveryManager(RecoveryManager):
    """Triggers recovery on immediate danger only; no pathfinding or combat automation."""

    def requires_recovery(self, state: GameState) -> bool:
        return state.in_danger or state.health <= 4

    def recover(self, state: GameState) -> bool:
        return False
