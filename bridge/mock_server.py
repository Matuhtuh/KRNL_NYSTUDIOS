"""Mock local bridge HTTP server for deterministic end-to-end loop tests."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


class MockBridgeServer:
    """Small test server that emulates bridge endpoints without Minecraft runtime."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8877) -> None:
        self.host = host
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None
        self.last_action: dict | None = None
        self.tick = 0
        self.player_x = 0.0
        self.player_yaw = 0.0
        self.player_pitch = 0.0
        self.selected_slot = 0
        self.inventory_count = 16

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _write(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                if self.path == "/heartbeat":
                    self._write(200, {"status": "ok", "protocol_version": "v1alpha1", "bridge_mode": "mock"})
                    return
                if self.path == "/state":
                    outer.tick += 1
                    self._write(
                        200,
                        {
                            "player": {
                                "tick": outer.tick,
                                "dimension": "minecraft:overworld",
                                "x": outer.player_x,
                                "y": 64,
                                "z": 0,
                                "yaw": outer.player_yaw,
                                "pitch": outer.player_pitch,
                                "health": 20,
                                "hunger": 20,
                                "on_ground": True,
                                "in_fluid": False,
                                "held_main_hand_item": "minecraft:stone_pickaxe",
                                "held_off_hand_item": "minecraft:air",
                                "selected_hotbar_slot": outer.selected_slot,
                            },
                            "inventory": {
                                "items": [{"slot": 0, "item_id": "minecraft:cobblestone", "count": outer.inventory_count, "empty": False}],
                                "hotbar": [{"slot": 0, "item_id": "minecraft:stone_pickaxe", "count": 1, "empty": False}],
                                "partial": False,
                                "note": None,
                            },
                            "nearby_blocks": [{"block_id": "minecraft:stone", "x": 1, "y": 64, "z": 0, "replaceable": False, "hardness": 1.5}],
                            "nearby_entities": [],
                            "open_screen": {"screen_open": False, "screen_class": "none", "title": "No screen", "slot_count": 0},
                            "observation_radius": 4,
                            "partial": False,
                            "warnings": [],
                        },
                    )
                    return
                self._write(404, {"error_code": "not_found", "message": "unknown endpoint"})

            def do_POST(self):  # noqa: N802
                if self.path != "/action":
                    self._write(404, {"error_code": "not_found", "message": "unknown endpoint"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.last_action = body
                action_type = body.get("action_type")
                params = body.get("parameters", {})
                if not action_type:
                    self._write(400, {"error_code": "bad_request", "message": "action_type missing"})
                    return

                supported = {"noop", "select_hotbar_slot", "turn_to_yaw_pitch", "move_forward_short", "interact_use", "inventory_click", "mine_block", "place_block"}
                if action_type not in supported:
                    self._write(400, {"error_code": "unsupported_action", "message": "unsupported action"})
                    return

                post = ["no_observable_state_change"]
                if action_type == "select_hotbar_slot" and isinstance(params.get("slot"), int):
                    outer.selected_slot = params["slot"]
                    post = ["state_changed"]
                elif action_type == "turn_to_yaw_pitch":
                    outer.player_yaw = float(params.get("yaw", outer.player_yaw))
                    outer.player_pitch = float(params.get("pitch", outer.player_pitch))
                    post = ["state_changed"]
                elif action_type == "move_forward_short":
                    outer.player_x += 0.25
                    post = ["state_changed"]
                elif action_type == "interact_use":
                    outer.inventory_count += 1
                    post = ["state_changed"]

                self._write(
                    200,
                    {
                        "request_id": body.get("request_id", "missing"),
                        "accepted": True,
                        "completed": True,
                        "success": True,
                        "error_code": None,
                        "message": "mock action completed",
                        "preconditions": [],
                        "postconditions": post,
                    },
                )

            def log_message(self, format, *args):
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
