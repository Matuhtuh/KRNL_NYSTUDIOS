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
                    self._write(
                        200,
                        {
                            "player": {
                                "tick": 1,
                                "dimension": "minecraft:overworld",
                                "x": 0,
                                "y": 64,
                                "z": 0,
                                "yaw": 0,
                                "pitch": 0,
                                "health": 20,
                                "hunger": 20,
                                "on_ground": True,
                                "in_fluid": False,
                                "held_main_hand_item": "minecraft:stone_pickaxe",
                                "held_off_hand_item": "minecraft:air",
                            },
                            "inventory": {"items": [{"slot": 0, "item_id": "minecraft:cobblestone", "count": 16, "empty": False}]},
                            "nearby_blocks": [{"block_id": "minecraft:stone", "x": 1, "y": 64, "z": 0, "replaceable": False, "hardness": 1.5}],
                            "nearby_entities": [],
                            "open_screen": {"screen_open": False, "screen_class": "none", "title": "No screen", "slot_count": 0},
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
                if "action_type" not in body:
                    self._write(400, {"error_code": "bad_request", "message": "action_type missing"})
                    return
                self._write(
                    200,
                    {
                        "request_id": body.get("request_id", "missing"),
                        "accepted": True,
                        "completed": True,
                        "success": True,
                        "error_code": None,
                        "message": "mock action completed",
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
