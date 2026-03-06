"""HTTP bridge client for deterministic local communication with the NeoForge client mod."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from planner.models import Action
from state.models import BlockObservation, EntityState, GameState, InventoryItem

from .contracts import ActionRequest, ActionResult, ErrorResponse, GameStateSnapshot, HeartbeatResponse
from .interfaces import GameBridge


class HttpGameBridge(GameBridge):
    """Localhost HTTP bridge client for early reliable debugging across Python and Java."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765") -> None:
        self.base_url = base_url.rstrip("/")

    def heartbeat(self) -> HeartbeatResponse:
        payload = self._request_json("GET", "/heartbeat")
        return HeartbeatResponse.model_validate(payload)

    def read_state_snapshot(self) -> GameStateSnapshot:
        payload = self._request_json("GET", "/state")
        return GameStateSnapshot.model_validate(payload)

    def read_state(self) -> GameState:
        snapshot = self.read_state_snapshot()
        return GameState(
            tick=snapshot.player.tick,
            dimension=snapshot.player.dimension,
            x=snapshot.player.x,
            y=snapshot.player.y,
            z=snapshot.player.z,
            yaw=snapshot.player.yaw,
            pitch=snapshot.player.pitch,
            health=snapshot.player.health,
            hunger=snapshot.player.hunger,
            inventory=[
                InventoryItem(item_id=i.item_id, count=max(i.count, 1), slot=i.slot)
                for i in snapshot.inventory.items
                if not i.empty and i.count > 0
            ],
            nearby_entities=[
                EntityState(
                    entity_id=e.entity_id,
                    x=e.x,
                    y=e.y,
                    z=e.z,
                    health=e.health,
                    hostile=e.hostile,
                )
                for e in snapshot.nearby_entities
            ],
            observed_blocks=[
                BlockObservation(
                    block_id=b.block_id,
                    x=b.x,
                    y=b.y,
                    z=b.z,
                    breakable=b.hardness >= 0,
                )
                for b in snapshot.nearby_blocks
            ],
            in_danger=any(entity.hostile for entity in snapshot.nearby_entities),
        )

    def perform_action(self, action: Action) -> ActionResult:
        request = ActionRequest(
            request_id=f"req_{action.action_type}",
            action_type=action.action_type,
            parameters=action.parameters,
            timeout_ticks=action.timeout_ticks,
        )
        payload = self._request_json("POST", "/action", request.model_dump())
        return ActionResult.model_validate(payload)

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        raw = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=raw,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            if body:
                error = ErrorResponse.model_validate(json.loads(body))
                raise RuntimeError(f"bridge error {error.error_code}: {error.message}") from exc
            raise RuntimeError(f"bridge request failed with status {exc.code}") from exc
