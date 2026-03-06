"""Shared Python-side transport contracts for Python<->NeoForge bridge communication."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NearbyBlockObservation(BaseModel):
    """Observed block near player used for mining/build verification in StoneBlock 4."""

    block_id: str = Field(..., min_length=1)
    x: int
    y: int
    z: int
    replaceable: bool = False
    hardness: float = Field(default=0.0)


class NearbyEntityObservation(BaseModel):
    """Observed entity near player used for safety and task interruption logic."""

    entity_id: str = Field(..., min_length=1)
    name: str = Field(default="unknown", min_length=1)
    x: float
    y: float
    z: float
    health: float = Field(default=0.0)
    hostile: bool = False


class PlayerStateSnapshot(BaseModel):
    """Player-centric state contract served by client bridge endpoints."""

    tick: int = Field(ge=0)
    dimension: str = Field(..., min_length=1)
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    health: float = Field(ge=0)
    hunger: int = Field(ge=0, le=20)
    on_ground: bool = False
    in_fluid: bool = False
    held_main_hand_item: str = Field(default="minecraft:air", min_length=1)
    held_off_hand_item: str = Field(default="minecraft:air", min_length=1)
    selected_hotbar_slot: int = Field(default=0, ge=0, le=8)


class InventoryItemSnapshot(BaseModel):
    """One inventory slot snapshot returned by bridge."""

    slot: int = Field(ge=0)
    item_id: str = Field(..., min_length=1)
    count: int = Field(ge=0)
    empty: bool


class InventorySnapshot(BaseModel):
    """Inventory snapshot used by deterministic planning/execution logic."""

    items: list[InventoryItemSnapshot] = Field(default_factory=list)
    hotbar: list[InventoryItemSnapshot] = Field(default_factory=list)
    partial: bool = False
    note: str | None = None


class OpenScreenState(BaseModel):
    """Current GUI/screen state used for deterministic UI-aware actions."""

    screen_open: bool
    screen_class: str = Field(default="none", min_length=1)
    title: str = Field(default="No screen", min_length=1)
    slot_count: int = Field(default=0, ge=0)


class GameStateSnapshot(BaseModel):
    """Top-level bridge state payload consumed by Python agent loop."""

    player: PlayerStateSnapshot
    inventory: InventorySnapshot
    nearby_blocks: list[NearbyBlockObservation] = Field(default_factory=list)
    nearby_entities: list[NearbyEntityObservation] = Field(default_factory=list)
    open_screen: OpenScreenState
    observation_radius: int = Field(default=4, ge=1)
    partial: bool = False
    warnings: list[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    """Deterministic action command sent from Python executor to client bridge."""

    request_id: str = Field(..., min_length=1)
    action_type: str = Field(..., min_length=1)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    timeout_ticks: int = Field(default=100, ge=1)


class ActionResult(BaseModel):
    """Single action execution outcome returned by bridge."""

    request_id: str = Field(..., min_length=1)
    accepted: bool
    completed: bool
    success: bool
    error_code: str | None = None
    message: str = Field(..., min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Structured error payload for malformed requests or server exceptions."""

    error_code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class HeartbeatResponse(BaseModel):
    """Health payload allowing loop bootstrap and local bridge diagnostics."""

    status: str = Field(..., min_length=1)
    protocol_version: str = Field(..., min_length=1)
    bridge_mode: str = Field(..., min_length=1)
    bridge_status: str = Field(..., min_length=1)
    detail: str = ""
