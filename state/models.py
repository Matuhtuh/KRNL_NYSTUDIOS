"""Pydantic models for deterministic world state consumed by higher-level StoneBlock 4 logic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class InventoryItem(BaseModel):
    """Represents an item stack in the agent inventory for StoneBlock 4 progression decisions."""

    item_id: str = Field(..., description="Namespaced Minecraft item id, e.g. minecraft:cobblestone.")
    count: int = Field(ge=1, description="Stack count.")
    slot: int = Field(ge=0, description="Inventory slot index.")


class EntityState(BaseModel):
    """Represents nearby entities (hostile mobs, passive mobs, dropped items) relevant to safety and tasks."""

    entity_id: str = Field(..., description="Entity registry id, e.g. minecraft:zombie.")
    x: float
    y: float
    z: float
    health: float = Field(ge=0)
    hostile: bool = Field(default=False)


class BlockObservation(BaseModel):
    """Represents observed blocks in local perception used for mining, building, and pathing decisions."""

    block_id: str = Field(..., description="Namespaced block id.")
    x: int
    y: int
    z: int
    breakable: bool = Field(default=True)


class GameState(BaseModel):
    """Immutable snapshot of in-game state used each control cycle by deterministic systems."""

    tick: int = Field(ge=0)
    dimension: str = Field(..., description="Current dimension id.")
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    health: float = Field(ge=0)
    hunger: int = Field(ge=0, le=20)
    inventory: list[InventoryItem] = Field(default_factory=list)
    nearby_entities: list[EntityState] = Field(default_factory=list)
    observed_blocks: list[BlockObservation] = Field(default_factory=list)
    in_danger: bool = Field(
        default=False,
        description="Flag set by perception heuristics when immediate defensive action is recommended.",
    )
    mode: Literal["idle", "mining", "combat", "building", "crafting"] = "idle"

    @field_validator("dimension")
    @classmethod
    def dimension_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dimension must be non-empty")
        return value
