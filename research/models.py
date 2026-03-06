"""Schemas for research requests/results used by planner and recovery workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchQuery(BaseModel):
    """Structured question about StoneBlock 4 progression triggered by failures or uncertainty."""

    query_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=3)
    local_context: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ResearchResult(BaseModel):
    """Normalized result that can feed planning without exposing raw untrusted web text directly."""

    query_id: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=5)
    recommended_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
