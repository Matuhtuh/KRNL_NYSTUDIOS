"""Schemas supporting StoneBlock 4 structure planning, placement, and repair verification."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BlockPlacement(BaseModel):
    """Single block placement instruction in a local blueprint coordinate frame."""

    block_id: str = Field(..., min_length=1)
    x: int
    y: int
    z: int


class MaterialEstimate(BaseModel):
    """Material quantities required to complete a blueprint."""

    block_id: str = Field(..., min_length=1)
    required_count: int = Field(ge=1)


class Blueprint(BaseModel):
    """Build plan that can be executed deterministically and validated against the world."""

    blueprint_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    origin_x: int
    origin_y: int
    origin_z: int
    placements: list[BlockPlacement] = Field(..., min_length=1)
    materials: list[MaterialEstimate] = Field(default_factory=list)
