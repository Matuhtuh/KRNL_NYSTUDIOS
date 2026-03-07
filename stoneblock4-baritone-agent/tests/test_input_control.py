from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.input_control import GameController


class FakeWindow:
    def __init__(self, hwnd: int, title: str) -> None:
        self._hWnd = hwnd
        self.title = title
        self.isMinimized = False
        self.activate_calls = 0
        self.restore_calls = 0

    def activate(self) -> None:
        self.activate_calls += 1

    def restore(self) -> None:
        self.restore_calls += 1
        self.isMinimized = False


def make_controller(*, attempts: int = 2, backoff: float = 0.0) -> GameController:
    controller = GameController.__new__(GameController)
    controller.window_config = SimpleNamespace(title_contains="FTB StoneBlock 4")
    controller.control_config = SimpleNamespace(
        auto_refocus_enabled=True,
        auto_refocus_attempts=attempts,
        auto_refocus_backoff_seconds=backoff,
        focus_timeout_seconds=0.05,
        toggle_poll_seconds=0.05,
    )
    controller.stop_requested = False
    controller.automation_enabled = True
    controller._state_lock = threading.Lock()
    return controller


class FocusControlTest(unittest.TestCase):
    def test_ensure_window_focus_uses_attach_thread_input_fallback(self) -> None:
        controller = make_controller()
        target = FakeWindow(hwnd=4242, title="FTB StoneBlock 4")
        state = {"foreground": 1111}

        def attach_thread_input(hwnd: int, foreground_hwnd: int) -> bool:
            self.assertEqual(4242, hwnd)
            self.assertEqual(1111, foreground_hwnd)
            state["foreground"] = hwnd
            return True

        controller._get_target_window = lambda: target  # type: ignore[method-assign]
        controller._foreground_hwnd = lambda: state["foreground"]  # type: ignore[method-assign]
        controller._force_foreground_hwnd = lambda hwnd: False  # type: ignore[method-assign]
        controller._attach_thread_input_foreground_hwnd = attach_thread_input  # type: ignore[method-assign]
        controller._wait_for_foreground_hwnd = lambda hwnd, timeout_seconds: state["foreground"] == hwnd  # type: ignore[method-assign]
        controller._window_title = lambda hwnd: {4242: "FTB StoneBlock 4", 1111: "PowerShell"}.get(hwnd, "")  # type: ignore[method-assign]

        with mock.patch("agent.input_control._user32.IsWindow", return_value=1):
            with mock.patch("agent.input_control.time.sleep", return_value=None):
                with self.assertLogs("agent.input_control", level="INFO") as logs:
                    controller.ensure_window_focus()

        self.assertEqual(4242, state["foreground"])
        combined = "\n".join(logs.output)
        self.assertIn('"found_hwnd": 4242', combined)
        self.assertIn('"foreground_hwnd": 4242', combined)
        self.assertIn('"focus_attempt_number": 1', combined)
        self.assertIn('"focus_success_boolean": true', combined)
        self.assertIn('"strategy": "attach_thread_input"', combined)

    def test_ensure_window_focus_fails_after_bounded_attempts(self) -> None:
        controller = make_controller(attempts=2, backoff=0.0)
        target = FakeWindow(hwnd=4242, title="FTB StoneBlock 4")
        state = {"foreground": 1111}

        controller._get_target_window = lambda: target  # type: ignore[method-assign]
        controller._foreground_hwnd = lambda: state["foreground"]  # type: ignore[method-assign]
        controller._force_foreground_hwnd = lambda hwnd: False  # type: ignore[method-assign]
        controller._attach_thread_input_foreground_hwnd = lambda hwnd, foreground_hwnd: False  # type: ignore[method-assign]
        controller._wait_for_foreground_hwnd = lambda hwnd, timeout_seconds: False  # type: ignore[method-assign]
        controller._window_title = lambda hwnd: {4242: "FTB StoneBlock 4", 1111: "Visual Studio Code"}.get(hwnd, "")  # type: ignore[method-assign]

        with mock.patch("agent.input_control._user32.IsWindow", return_value=1):
            with mock.patch("agent.input_control.time.sleep", return_value=None):
                with self.assertLogs("agent.input_control", level="WARNING") as logs:
                    with self.assertRaises(RuntimeError) as ctx:
                        controller.ensure_window_focus()

        self.assertEqual(2, target.activate_calls)
        self.assertIn("failed to focus target window", str(ctx.exception))
        self.assertIn("Visual Studio Code", str(ctx.exception))
        combined = "\n".join(logs.output)
        self.assertEqual(2, combined.count('"focus_success_boolean": false'))

    def test_ensure_window_focus_rejects_title_mismatch(self) -> None:
        controller = make_controller()
        target = FakeWindow(hwnd=4242, title="Minecraft")

        controller._get_target_window = lambda: target  # type: ignore[method-assign]
        controller._foreground_hwnd = lambda: 1111  # type: ignore[method-assign]
        controller._window_title = lambda hwnd: "Minecraft"  # type: ignore[method-assign]

        with mock.patch("agent.input_control._user32.IsWindow", return_value=1):
            with self.assertRaises(RuntimeError) as ctx:
                controller.ensure_window_focus()

        self.assertIn("does not contain configured title 'FTB StoneBlock 4'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
