"""Deterministic executor for ordered small-plan execution against bridge transport."""

from __future__ import annotations

from bridge.interfaces import GameBridge
from planner.models import Action
from state.models import GameState

from .interfaces import Executor


class DeterministicExecutor(Executor):
    """Pass-through deterministic executor with room for future retries/timeouts."""

    def __init__(self, bridge: GameBridge) -> None:
        self.bridge = bridge

    def execute_one(self, action: Action, state: GameState):
        return self.bridge.perform_action(action)
