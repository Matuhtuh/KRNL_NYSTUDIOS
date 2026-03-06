"""No-progress detection from consecutive snapshots for stuck handling."""

from __future__ import annotations

from math import sqrt

from bridge.contracts import GameStateSnapshot


class NoProgressDetector:
    """Detects lack of meaningful change across recent snapshots."""

    def __init__(self, min_move_delta: float = 0.1) -> None:
        self.min_move_delta = min_move_delta

    def no_progress(self, snapshots: list[GameStateSnapshot]) -> bool:
        if len(snapshots) < 3:
            return False
        recent = snapshots[-3:]
        moved = any(self._distance(recent[i - 1], recent[i]) >= self.min_move_delta for i in range(1, len(recent)))
        inventory_changed = any(self._inventory_key(recent[i - 1]) != self._inventory_key(recent[i]) for i in range(1, len(recent)))
        screen_changed = any(recent[i - 1].open_screen != recent[i].open_screen for i in range(1, len(recent)))
        return not moved and not inventory_changed and not screen_changed

    @staticmethod
    def _distance(a: GameStateSnapshot, b: GameStateSnapshot) -> float:
        dx = b.player.x - a.player.x
        dy = b.player.y - a.player.y
        dz = b.player.z - a.player.z
        return sqrt(dx * dx + dy * dy + dz * dz)

    @staticmethod
    def _inventory_key(snapshot: GameStateSnapshot) -> tuple[tuple[int, str, int], ...]:
        return tuple((item.slot, item.item_id, item.count) for item in snapshot.inventory.items if not item.empty)
