"""Schemas for memory snapshots that support long-term StoneBlock 4 autonomy."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bridge.contracts import GameStateSnapshot


class FailureRecord(BaseModel):
    """Failure event captured for planner/recovery analysis and future strategy updates."""

    tick: int = Field(ge=0)
    context: str = Field(..., min_length=3)
    error_type: str = Field(..., min_length=1)
    detail: str = Field(..., min_length=3)
    action_type: str = Field(..., min_length=1)


class AgentMemory(BaseModel):
    """Persistent summary of goals, plans, failures, and stuck-tracking across control loops."""

    active_goal_id: str | None = None
    current_plan_id: str | None = None
    completed_goals: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    failure_history: list[FailureRecord] = Field(default_factory=list)
    action_log: list[str] = Field(default_factory=list)
    last_action_signature: str | None = None
    repeated_failure_count: int = 0
    stuck_counter: int = 0
    research_triggered: bool = False
    recent_snapshots: list[GameStateSnapshot] = Field(default_factory=list)

    def push_snapshot(self, snapshot: GameStateSnapshot, limit: int = 8) -> None:
        """Persist a bounded rolling window of recent snapshots for no-progress checks."""

        self.recent_snapshots.append(snapshot)
        if len(self.recent_snapshots) > limit:
            self.recent_snapshots = self.recent_snapshots[-limit:]
