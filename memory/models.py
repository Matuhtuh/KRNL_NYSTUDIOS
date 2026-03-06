"""Schemas for memory snapshots that support long-term StoneBlock 4 autonomy."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FailureRecord(BaseModel):
    """Failure event captured for planner/recovery analysis and future strategy updates."""

    tick: int = Field(ge=0)
    context: str = Field(..., min_length=3)
    error_type: str = Field(..., min_length=1)
    detail: str = Field(..., min_length=3)
    action_type: str = Field(..., min_length=1)


class AgentMemory(BaseModel):
    """Persistent summary of goals, plans, and failures across control loops."""

    active_goal_id: str | None = None
    current_plan_id: str | None = None
    completed_goals: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    failure_history: list[FailureRecord] = Field(default_factory=list)
