from __future__ import annotations

import json
import logging
import mimetypes
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import pygetwindow as gw

from agent.config import AppConfig
from agent.dashboard_chat import DashboardChatService
from agent.dashboard_control import DashboardControlExecutor
from agent.dashboard_state import DashboardQueryService
from agent.perception import ScreenPerception
from agent.stoneblock4_knowledge import StoneBlockKnowledgeBase

log = logging.getLogger(__name__)


class DashboardRequestBodyError(ValueError):
    pass


class LivePreviewCache:
    def __init__(self, cfg: AppConfig, *, interval_seconds: float = 1.0, stale_after_seconds: float = 3.0) -> None:
        self.cfg = cfg
        self.interval_seconds = max(0.25, float(interval_seconds))
        self.stale_after_seconds = max(self.interval_seconds, float(stale_after_seconds))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "available": False,
            "capturedAt": None,
            "bytes": None,
            "width": 0,
            "height": 0,
            "error": "",
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="dashboard-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _preview_window_token(self) -> str:
        return str(getattr(self.cfg.window, "title_contains", "") or "").strip()

    def _preview_window_bbox(self) -> dict[str, int]:
        token = self._preview_window_token()
        if not token:
            raise RuntimeError("preview target window title is not configured")

        token_lower = token.lower()
        matches = []
        for window in gw.getAllWindows():
            title = str(getattr(window, "title", "") or "").strip()
            if not title or token_lower not in title.lower():
                continue
            width = int(getattr(window, "width", 0) or 0)
            height = int(getattr(window, "height", 0) or 0)
            area = max(0, width) * max(0, height)
            matches.append((bool(getattr(window, "isMinimized", False)), area, window))

        if not matches:
            raise RuntimeError(f"preview target window not found for title '{token}'")

        matches.sort(key=lambda row: (row[0], -row[1]))
        minimized, _, window = matches[0]
        if minimized:
            raise RuntimeError(f"preview target window is minimized for title '{token}'")

        left = int(getattr(window, "left", 0) or 0)
        top = int(getattr(window, "top", 0) or 0)
        width = int(getattr(window, "width", 0) or 0)
        height = int(getattr(window, "height", 0) or 0)
        if width <= 1 or height <= 1:
            raise RuntimeError(f"preview target window has invalid bounds for title '{token}'")
        return {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }

    def _grab_preview_frame(self, perception: ScreenPerception) -> np.ndarray:
        token = self._preview_window_token()
        if not token:
            return perception._grab_full()
        raw = np.array(perception.sct.grab(self._preview_window_bbox()))
        return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

    def _has_recent_frame(self, state: dict[str, Any]) -> bool:
        raw = state.get("bytes")
        if not isinstance(raw, (bytes, bytearray)):
            return False
        captured_at = float(state.get("capturedAt") or 0.0)
        if captured_at <= 0.0:
            return False
        return (time.time() - captured_at) <= self.stale_after_seconds

    def _run(self) -> None:
        perception = ScreenPerception(self.cfg.capture, self.cfg.ocr)
        while not self._stop.is_set():
            try:
                frame = self._grab_preview_frame(perception)
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 76])
                if not ok:
                    raise RuntimeError("jpeg encoding failed")
                with self._lock:
                    self._state = {
                        "available": True,
                        "capturedAt": time.time(),
                        "bytes": encoded.tobytes(),
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                        "error": "",
                    }
            except Exception as exc:
                with self._lock:
                    self._state = {
                        **self._state,
                        "available": self._has_recent_frame(self._state),
                        "capturedAt": self._state.get("capturedAt"),
                        "error": str(exc),
                    }
            self._stop.wait(self.interval_seconds)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        state.pop("bytes", None)
        return state

    def image_bytes(self) -> bytes | None:
        with self._lock:
            state = dict(self._state)
        if not self._has_recent_frame(state):
            return None
        raw = state.get("bytes")
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None


class DashboardServerState:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.knowledge = StoneBlockKnowledgeBase(cfg)
        self.queries = DashboardQueryService(cfg, self.knowledge)
        self.controls = DashboardControlExecutor(cfg)
        self.chat = DashboardChatService(cfg, self.queries, self.controls)
        self.preview = LivePreviewCache(cfg)
        self.static_root = Path(__file__).resolve().parent / "dashboard_web_static"

    def start(self) -> None:
        self.preview.start()

    def stop(self) -> None:
        self.preview.stop()

    def snapshot(self) -> dict[str, Any]:
        return self.queries.snapshot(preview_state=self.preview.snapshot())


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], state: DashboardServerState) -> None:
        super().__init__(server_address, DashboardRequestHandler)
        self.state = state

    def handle_error(self, request: object, client_address: object) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            log.debug("dashboard client disconnected early: %s (%s)", client_address, exc.__class__.__name__)
            return
        super().handle_error(request, client_address)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.info("dashboard %s - %s", self.address_string(), format % args)

    def _json_response(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_no_cache_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DashboardRequestBodyError("request body is not valid UTF-8") from exc
        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise DashboardRequestBodyError("request body is not valid JSON") from exc
        return payload if isinstance(payload, dict) else {}

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_open(self, *, keep_alive: bool = True) -> None:
        self.send_response(200)
        self._send_no_cache_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive" if keep_alive else "close")
        self.end_headers()
        if not keep_alive:
            self.close_connection = True

    def _sse_event(self, event: str, data: dict[str, Any]) -> None:
        body = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._serve_file(self.server.state.static_root / "index.html")
            return
        if parsed.path.startswith("/static/"):
            rel = parsed.path.removeprefix("/static/").strip("/")
            self._serve_file(self.server.state.static_root / rel)
            return
        if parsed.path == "/api/snapshot":
            self._json_response(self.server.state.snapshot())
            return
        if parsed.path == "/api/bridge-status":
            self._json_response(self.server.state.queries.get_bridge_status())
            return
        if parsed.path == "/api/inventory-summary":
            self._json_response(self.server.state.queries.get_inventory_summary())
            return
        if parsed.path == "/api/current-action":
            self._json_response(self.server.state.queries.explain_current_action())
            return
        if parsed.path == "/api/preview.jpg":
            raw = self.server.state.preview.image_bytes()
            if raw is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "preview unavailable")
                return
            self.send_response(200)
            self._send_no_cache_headers()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/knowledge/stage":
            params = parse_qs(parsed.query)
            stage_hint = str((params.get("stage_hint") or [""])[0])
            self._json_response(self.server.state.queries.get_stage_knowledge(stage_hint))
            return
        if parsed.path == "/api/knowledge/item-route":
            params = parse_qs(parsed.query)
            item_id = str((params.get("item_id") or [""])[0])
            self._json_response(self.server.state.queries.get_item_route(item_id))
            return
        if parsed.path == "/api/knowledge/next-targets":
            params = parse_qs(parsed.query)
            limit = int((params.get("limit") or ["6"])[0] or 6)
            self._json_response(self.server.state.queries.get_next_obtainable_targets(limit=limit))
            return
        if parsed.path == "/events/status":
            self._sse_open(keep_alive=True)
            try:
                while True:
                    self._sse_event("snapshot", self.server.state.snapshot())
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError):
                return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/chat/session":
                body = self._read_json_body()
                profile = str(body.get("profile", "")).strip() if isinstance(body, dict) else ""
                payload = self.server.state.chat.create_session(profile=profile)
                log.info(
                    "dashboard_chat_route %s",
                    json.dumps(
                        {
                            "event": "dashboard_chat_route",
                            "handler": "/api/chat/session",
                            "phase": "session_created",
                            "sessionId": str(payload.get("sessionId", "")).strip(),
                            "profile": str(payload.get("profile", "")).strip(),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
                self._json_response(payload)
                return
            if parsed.path == "/api/chat/message":
                body = self._read_json_body()
                session_id = str(body.get("sessionId", "")).strip()
                message = str(body.get("message", "")).strip()
                log.info(
                    "dashboard_chat_route %s",
                    json.dumps(
                        {
                            "event": "dashboard_chat_route",
                            "handler": "/api/chat/message",
                            "phase": "request_begin",
                            "sessionId": session_id,
                            "messageLength": len(message),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
                self._sse_open(keep_alive=False)
                try:
                    for event in self.server.state.chat.stream_message(session_id, message):
                        self._sse_event(str(event.get("event", "message")), dict(event.get("data", {})))
                except Exception as exc:
                    log.error(
                        "dashboard_chat_route %s",
                        json.dumps(
                            {
                                "event": "dashboard_chat_route",
                                "handler": "/api/chat/message",
                                "phase": "request_error",
                                "sessionId": session_id,
                                "error": str(exc),
                            },
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    )
                    self._sse_event("final", {"status": "error", "intent": "error", "text": "Chat stream failed.", "error": str(exc)})
                return
            if parsed.path == "/api/chat/confirm":
                body = self._read_json_body()
                session_id = str(body.get("sessionId", "")).strip()
                token = str(body.get("confirmationToken", "")).strip()
                try:
                    payload = self.server.state.chat.confirm_action(session_id, token)
                    log.info(
                        "dashboard_chat_route %s",
                        json.dumps(
                            {
                                "event": "dashboard_chat_route",
                                "handler": "/api/chat/confirm",
                                "phase": "confirm_ok",
                                "sessionId": session_id,
                            },
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    )
                    self._json_response(payload)
                except Exception as exc:
                    log.warning(
                        "dashboard_chat_route %s",
                        json.dumps(
                            {
                                "event": "dashboard_chat_route",
                                "handler": "/api/chat/confirm",
                                "phase": "confirm_error",
                                "sessionId": session_id,
                                "error": str(exc),
                            },
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    )
                    self._json_response({"status": "error", "error": str(exc)}, status=400)
                return
        except DashboardRequestBodyError as exc:
            self._json_response({"status": "error", "error": str(exc)}, status=400)
            return
        self.send_error(404)


def run_dashboard_web(cfg: AppConfig, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    state = DashboardServerState(cfg)
    state.start()
    server = DashboardHTTPServer((host, int(port)), state)
    log.info("Dashboard web UI listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    finally:
        state.stop()
        server.server_close()
