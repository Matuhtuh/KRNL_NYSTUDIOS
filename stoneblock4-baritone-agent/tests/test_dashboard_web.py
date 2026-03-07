from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.dashboard_web import DashboardHTTPServer, LivePreviewCache


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        window=SimpleNamespace(title_contains="FTB StoneBlock 4"),
        capture=SimpleNamespace(),
        ocr=SimpleNamespace(),
    )


class _FakeWindow:
    def __init__(self, *, title: str, left: int, top: int, width: int, height: int, minimized: bool = False) -> None:
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.isMinimized = minimized


class _PreviewStub:
    def __init__(self, raw: bytes | None) -> None:
        self._raw = raw

    def image_bytes(self) -> bytes | None:
        return self._raw


class _StateStub:
    def __init__(self, preview: _PreviewStub, static_root: Path, chat=None) -> None:
        self.preview = preview
        self.static_root = static_root
        self.chat = chat


class _ChatStub:
    def __init__(self, *, stream_events=None, stream_error: Exception | None = None) -> None:
        self.stream_events = list(stream_events or [])
        self.stream_error = stream_error

    def create_session(self, profile: str = "") -> dict:
        return {
            "sessionId": "session-1",
            "createdAt": "2026-03-07T00:00:00+00:00",
            "profile": profile or "operator_cheap",
            "chatStatus": {
                "apiConfigured": False,
                "reason": "Set OPENAI_API_KEY or populate runtime/openai_api_key.txt",
                "model": "gpt-4.1-nano",
            },
        }

    def stream_message(self, session_id: str, message: str):
        if self.stream_error is not None:
            raise self.stream_error
        for event in self.stream_events:
            yield event

    def confirm_action(self, session_id: str, token: str) -> dict:
        return {"status": "ok", "intent": "action_result", "text": f"confirmed {token}"}


class LivePreviewCacheTest(unittest.TestCase):
    def test_preview_window_bbox_uses_matching_non_minimized_window(self) -> None:
        cache = LivePreviewCache(_cfg())
        windows = [
            _FakeWindow(title="Other Window", left=0, top=0, width=800, height=600),
            _FakeWindow(title="FTB StoneBlock 4", left=100, top=200, width=1280, height=720),
            _FakeWindow(title="FTB StoneBlock 4 (Paused)", left=50, top=60, width=640, height=480, minimized=True),
        ]

        with patch("agent.dashboard_web.gw.getAllWindows", return_value=windows):
            bbox = cache._preview_window_bbox()

        self.assertEqual(
            {"left": 100, "top": 200, "width": 1280, "height": 720},
            bbox,
        )

    def test_image_bytes_rejects_stale_cached_frame(self) -> None:
        cache = LivePreviewCache(_cfg(), stale_after_seconds=3.0)
        cache._state = {
            "available": True,
            "capturedAt": time.time() - 10.0,
            "bytes": b"old-frame",
            "width": 1280,
            "height": 720,
            "error": "capture failed",
        }

        self.assertIsNone(cache.image_bytes())


class DashboardPreviewEndpointTest(unittest.TestCase):
    @staticmethod
    def _stop_server(server: DashboardHTTPServer, thread: threading.Thread) -> None:
        server.shutdown()
        thread.join(timeout=2.0)
        server.server_close()

    def _start_server(self, preview: _PreviewStub, *, chat=None) -> tuple[DashboardHTTPServer, threading.Thread]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        state = _StateStub(preview=preview, static_root=Path(tmpdir.name), chat=chat)
        server = DashboardHTTPServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_server, server, thread)
        return server, thread

    def test_preview_endpoint_sets_no_cache_headers(self) -> None:
        server, _ = self._start_server(_PreviewStub(b"jpeg-bytes"))
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        conn.request("GET", "/api/preview.jpg")
        resp = conn.getresponse()
        body = resp.read()

        self.assertEqual(200, resp.status)
        self.assertEqual("image/jpeg", resp.getheader("Content-Type"))
        self.assertEqual("no-store, no-cache, must-revalidate, max-age=0", resp.getheader("Cache-Control"))
        self.assertEqual(b"jpeg-bytes", body)

    def test_preview_endpoint_returns_503_when_preview_unavailable(self) -> None:
        server, _ = self._start_server(_PreviewStub(None))
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        conn.request("GET", "/api/preview.jpg")
        resp = conn.getresponse()
        resp.read()

        self.assertEqual(503, resp.status)

    def test_chat_message_route_streams_tool_and_final_events(self) -> None:
        chat = _ChatStub(
            stream_events=[
                {"event": "status", "data": {"phase": "request_started", "sessionId": "session-1", "model": "gpt-4.1-nano"}},
                {"event": "tool", "data": {"name": "get_bridge_status", "summary": "ok"}},
                {"event": "delta", "data": {"text": "Hello"}},
                {"event": "final", "data": {"status": "ok", "intent": "answer", "text": "Hello world"}},
            ]
        )
        server, _ = self._start_server(_PreviewStub(b"jpeg-bytes"), chat=chat)
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        body = json.dumps({"sessionId": "session-1", "message": "what am i doing now?"})
        conn.request("POST", "/api/chat/message", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = resp.read(4096).decode("utf-8", errors="replace")

        self.assertEqual(200, resp.status)
        self.assertEqual("text/event-stream; charset=utf-8", resp.getheader("Content-Type"))
        self.assertIn("event: tool", payload)
        self.assertIn("event: delta", payload)
        self.assertIn("event: final", payload)
        self.assertIn("Hello world", payload)

    def test_chat_message_route_surfaces_final_error_event_on_stream_failure(self) -> None:
        chat = _ChatStub(stream_error=RuntimeError("boom"))
        server, _ = self._start_server(_PreviewStub(b"jpeg-bytes"), chat=chat)
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        body = json.dumps({"sessionId": "session-1", "message": "what am i doing now?"})
        conn.request("POST", "/api/chat/message", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = resp.read(4096).decode("utf-8", errors="replace")

        self.assertEqual(200, resp.status)
        self.assertIn("event: final", payload)
        self.assertIn("Chat stream failed.", payload)
        self.assertIn("boom", payload)

    def test_chat_message_route_returns_400_for_invalid_json_body(self) -> None:
        chat = _ChatStub()
        server, _ = self._start_server(_PreviewStub(b"jpeg-bytes"), chat=chat)
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        conn.request("POST", "/api/chat/message", body="{bad json", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8", errors="replace")

        self.assertEqual(400, resp.status)
        self.assertIn("request body is not valid JSON", payload)

    def test_chat_session_route_includes_chat_status_payload(self) -> None:
        chat = _ChatStub()
        server, _ = self._start_server(_PreviewStub(b"jpeg-bytes"), chat=chat)
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        self.addCleanup(conn.close)

        conn.request("POST", "/api/chat/session", body="{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))

        self.assertEqual(200, resp.status)
        self.assertIn("sessionId", payload)
        self.assertIn("profile", payload)
        self.assertIn("chatStatus", payload)
        self.assertFalse(payload["chatStatus"]["apiConfigured"])


if __name__ == "__main__":
    unittest.main()
