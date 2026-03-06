"""Lightweight local operator console for supervising the Python brain."""

from __future__ import annotations

from dataclasses import dataclass

from planner.models import Goal


@dataclass
class OperatorConsole:
    """Command handler for supervised autonomy operations."""

    loop: object
    _goal_seq: int = 0

    def handle(self, command: str) -> dict[str, object]:
        text = command.strip()
        if not text:
            return {"ok": False, "message": "empty command"}

        parts = text.split(maxsplit=1)
        verb = parts[0].lower()
        payload = parts[1] if len(parts) > 1 else ""

        if verb == "pause":
            self.loop.pause()
            return {"ok": True, "message": "loop paused"}

        if verb == "resume":
            self.loop.resume()
            return {"ok": True, "message": "loop resumed"}

        if verb == "cancel":
            self.loop.cancel_plan()
            return {"ok": True, "message": "plan cancelled"}

        if verb == "status":
            return {"ok": True, "status": self.loop.status_summary()}

        if verb in {"goal", "set_goal"}:
            description = payload.strip()
            if len(description) < 3:
                return {"ok": False, "message": "goal description must be at least 3 chars"}
            self._goal_seq += 1
            goal = Goal(goal_id=f"op_goal_{self._goal_seq}", description=description, priority=5)
            self.loop.set_goal(goal)
            return {"ok": True, "message": f"goal set: {goal.goal_id}", "goal_id": goal.goal_id}

        return {"ok": False, "message": f"unknown command: {verb}"}
