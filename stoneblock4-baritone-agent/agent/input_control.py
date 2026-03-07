from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import pydirectinput
import pygetwindow as gw

from agent.config import ControlConfig, WindowConfig

log = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_SW_RESTORE = 9


class InputBackend(Protocol):
    def press(self, key: str, hold_seconds: float) -> None:
        ...

    def type_text(self, text: str, per_char_delay: float) -> None:
        ...

    def left_click(self, hold_seconds: float) -> None:
        ...

    def right_click(self, hold_seconds: float) -> None:
        ...

    def release_all(self) -> None:
        ...


class PyDirectInputBackend:
    def __init__(self, inter_key_delay_seconds: float) -> None:
        pydirectinput.FAILSAFE = False
        self.inter_key_delay_seconds = inter_key_delay_seconds

    def press(self, key: str, hold_seconds: float) -> None:
        pydirectinput.keyDown(key)
        time.sleep(max(0.01, hold_seconds))
        pydirectinput.keyUp(key)
        time.sleep(max(0.0, self.inter_key_delay_seconds))

    def type_text(self, text: str, per_char_delay: float) -> None:
        for ch in text:
            pydirectinput.write(ch)
            time.sleep(max(0.0, per_char_delay))

    def left_click(self, hold_seconds: float) -> None:
        pydirectinput.mouseDown(button="left")
        time.sleep(max(0.01, hold_seconds))
        pydirectinput.mouseUp(button="left")
        time.sleep(max(0.0, self.inter_key_delay_seconds))

    def right_click(self, hold_seconds: float) -> None:
        pydirectinput.mouseDown(button="right")
        time.sleep(max(0.01, hold_seconds))
        pydirectinput.mouseUp(button="right")
        time.sleep(max(0.0, self.inter_key_delay_seconds))

    def release_all(self) -> None:
        # Best-effort release for common keys the agent may use.
        for k in ("w", "a", "s", "d", "space", "shift", "ctrl", "alt", "t", "enter"):
            try:
                pydirectinput.keyUp(k)
            except Exception:
                pass


class AutoHotkeyBackend:
    def __init__(self, ahk_exe: str, window_title_contains: str, inter_key_delay_seconds: float) -> None:
        self.ahk_exe = ahk_exe
        self.window_title_contains = window_title_contains
        self.inter_key_delay_seconds = inter_key_delay_seconds

    @staticmethod
    def _escape_ahk_text(value: str) -> str:
        return value.replace("`", "``").replace('"', '`"')

    def _run_script(self, body: str) -> None:
        title = self._escape_ahk_text(self.window_title_contains)
        script = (
            "#Requires AutoHotkey v2.0\n"
            "SetTitleMatchMode(2)\n"
            f'title := "{title}"\n'
            "if !WinExist(title) {\n"
            "  ExitApp(1)\n"
            "}\n"
            "WinActivate(title)\n"
            "WinWaitActive(title, , 0.8)\n"
            f"{body}\n"
        )
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".ahk", delete=False, encoding="utf-8") as tmp:
                tmp.write(script)
                tmp_path = tmp.name
            proc = subprocess.run([self.ahk_exe, tmp_path], capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"AutoHotkey failed ({proc.returncode}): {proc.stdout} {proc.stderr}".strip()
                )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def press(self, key: str, hold_seconds: float) -> None:
        key_name = self._escape_ahk_text(key)
        hold_ms = int(max(10, hold_seconds * 1000))
        inter_ms = int(max(0, self.inter_key_delay_seconds * 1000))
        self._run_script(
            f'SendInput("{{{key_name} down}}")\n'
            f"Sleep({hold_ms})\n"
            f'SendInput("{{{key_name} up}}")\n'
            f"Sleep({inter_ms})"
        )

    def type_text(self, text: str, per_char_delay: float) -> None:
        payload = self._escape_ahk_text(text)
        delay_ms = int(max(0, per_char_delay * 1000))
        self._run_script(
            f'SendText("{payload}")\n'
            f"if ({delay_ms} > 0) {{ Sleep({delay_ms}) }}"
        )

    def left_click(self, hold_seconds: float) -> None:
        hold_ms = int(max(10, hold_seconds * 1000))
        self._run_script(
            "Click(\"Down Left\")\n"
            f"Sleep({hold_ms})\n"
            "Click(\"Up Left\")"
        )

    def right_click(self, hold_seconds: float) -> None:
        hold_ms = int(max(10, hold_seconds * 1000))
        self._run_script(
            "Click(\"Down Right\")\n"
            f"Sleep({hold_ms})\n"
            "Click(\"Up Right\")"
        )

    def release_all(self) -> None:
        self._run_script(
            'for key in ["w","a","s","d","Space","Shift","Ctrl","Alt"] {\n'
            '  SendInput("{" key " up}")\n'
            "}"
        )


class NoopBackend:
    def press(self, key: str, hold_seconds: float) -> None:
        log.debug("NOOP press key=%s hold=%.3fs", key, hold_seconds)

    def type_text(self, text: str, per_char_delay: float) -> None:
        log.debug("NOOP type text='%s' delay=%.3fs", text, per_char_delay)

    def left_click(self, hold_seconds: float) -> None:
        log.debug("NOOP left_click hold=%.3fs", hold_seconds)

    def right_click(self, hold_seconds: float) -> None:
        log.debug("NOOP right_click hold=%.3fs", hold_seconds)

    def release_all(self) -> None:
        pass


def _vk_from_key_name(name: str) -> int:
    s = (name or "").strip().upper()
    if len(s) == 1 and ("A" <= s <= "Z" or "0" <= s <= "9"):
        return ord(s)
    if s.startswith("F"):
        try:
            n = int(s[1:])
            if 1 <= n <= 24:
                return 0x6F + n
        except ValueError:
            pass
    mapping = {
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "SPACE": 0x20,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "HOME": 0x24,
        "PAUSE": 0x13,
    }
    if s in mapping:
        return mapping[s]
    raise ValueError(f"Unsupported key name: {name}")


@dataclass
class GameController:
    window_config: WindowConfig
    control_config: ControlConfig
    stop_requested: bool = False
    automation_enabled: bool = True

    def __post_init__(self) -> None:
        self._backend = self._create_backend(self.control_config.input_backend)
        self._stop_vk = _vk_from_key_name(self.control_config.emergency_stop_key)
        self._toggle_vk = _vk_from_key_name(self.control_config.toggle_pause_key)
        self._stop_down_prev = False
        self._toggle_down_prev = False
        self._state_lock = threading.Lock()
        self._stop_thread = threading.Thread(target=self._poll_hotkeys, daemon=True)
        self._stop_thread.start()

    def _create_backend(self, name: str) -> InputBackend:
        backend = (name or "").strip().lower()
        if backend == "ahk":
            return AutoHotkeyBackend(
                ahk_exe=self.control_config.ahk_exe,
                window_title_contains=self.window_config.title_contains,
                inter_key_delay_seconds=self.control_config.inter_key_delay_seconds,
            )
        if backend == "noop":
            return NoopBackend()
        return PyDirectInputBackend(inter_key_delay_seconds=self.control_config.inter_key_delay_seconds)

    def _poll_hotkeys(self) -> None:
        while not self.stop_requested:
            stop_down = bool(_user32.GetAsyncKeyState(self._stop_vk) & 0x8000)
            if stop_down and not self._stop_down_prev:
                self.request_stop()
                return
            self._stop_down_prev = stop_down

            toggle_down = bool(_user32.GetAsyncKeyState(self._toggle_vk) & 0x8000)
            if toggle_down and not self._toggle_down_prev:
                self.toggle_pause()
            self._toggle_down_prev = toggle_down

            time.sleep(max(0.01, min(self.control_config.stop_poll_seconds, self.control_config.toggle_poll_seconds)))

    def request_stop(self) -> None:
        self.stop_requested = True
        self._backend.release_all()
        log.warning("Emergency stop requested via %s", self.control_config.emergency_stop_key)

    def toggle_pause(self) -> bool:
        with self._state_lock:
            self.automation_enabled = not self.automation_enabled
            enabled = self.automation_enabled
        self._backend.release_all()
        state = "enabled" if enabled else "paused"
        log.warning("Automation %s via %s", state, self.control_config.toggle_pause_key)
        return enabled

    def is_automation_enabled(self) -> bool:
        with self._state_lock:
            return bool(self.automation_enabled)

    def wait_if_paused(self) -> None:
        while not self.stop_requested:
            with self._state_lock:
                if self.automation_enabled:
                    return
            time.sleep(max(0.02, self.control_config.toggle_poll_seconds))
        raise RuntimeError("Stop requested")

    @staticmethod
    def _foreground_hwnd() -> int:
        return int(_user32.GetForegroundWindow())

    @staticmethod
    def _current_thread_id() -> int:
        return int(_kernel32.GetCurrentThreadId())

    @staticmethod
    def _window_thread_id(hwnd: int) -> int:
        if int(hwnd) <= 0:
            return 0
        process_id = ctypes.c_ulong(0)
        try:
            return int(_user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(process_id)))
        except Exception:
            return 0

    @staticmethod
    def _force_foreground_hwnd(hwnd: int) -> bool:
        if int(hwnd) <= 0:
            return False
        try:
            _user32.ShowWindow(int(hwnd), _SW_RESTORE)
            _user32.BringWindowToTop(int(hwnd))
            _user32.SetForegroundWindow(int(hwnd))
            _user32.SetActiveWindow(int(hwnd))
        except Exception:
            return False
        return int(hwnd) == int(_user32.GetForegroundWindow())

    def _window_title(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    def _configured_window_title_token(self) -> str:
        return str(self.window_config.title_contains or "").strip()

    def _wait_for_foreground_hwnd(self, hwnd: int, timeout_seconds: float) -> bool:
        deadline = time.time() + max(0.05, float(timeout_seconds))
        while time.time() < deadline:
            if int(hwnd) == self._foreground_hwnd():
                return True
            time.sleep(0.05)
        return int(hwnd) == self._foreground_hwnd()

    def _attach_thread_input_foreground_hwnd(self, hwnd: int, foreground_hwnd: int) -> bool:
        if int(hwnd) <= 0:
            return False

        current_thread_id = self._current_thread_id()
        target_thread_id = self._window_thread_id(hwnd)
        foreground_thread_id = self._window_thread_id(foreground_hwnd)
        attached_thread_ids: list[int] = []
        try:
            for thread_id in (foreground_thread_id, target_thread_id):
                if thread_id <= 0 or thread_id == current_thread_id or thread_id in attached_thread_ids:
                    continue
                if bool(_user32.AttachThreadInput(current_thread_id, thread_id, True)):
                    attached_thread_ids.append(thread_id)
            _user32.ShowWindow(int(hwnd), _SW_RESTORE)
            _user32.BringWindowToTop(int(hwnd))
            _user32.SetForegroundWindow(int(hwnd))
            _user32.SetActiveWindow(int(hwnd))
            try:
                _user32.SetFocus(int(hwnd))
            except Exception:
                pass
        except Exception:
            return False
        finally:
            for thread_id in reversed(attached_thread_ids):
                try:
                    _user32.AttachThreadInput(current_thread_id, thread_id, False)
                except Exception:
                    pass
        return int(hwnd) == self._foreground_hwnd()

    def _log_focus_attempt(
        self,
        *,
        attempt_number: int,
        found_hwnd: int,
        foreground_hwnd: int,
        success: bool,
        strategy: str,
        target_title: str,
        error: str = "",
    ) -> None:
        payload: dict[str, object] = {
            "event": "window_focus_attempt",
            "focus_attempt_number": int(attempt_number),
            "found_hwnd": int(found_hwnd),
            "foreground_hwnd": int(foreground_hwnd),
            "focus_success_boolean": bool(success),
            "strategy": str(strategy).strip() or "unknown",
            "configured_title_contains": self._configured_window_title_token(),
            "target_title": target_title,
            "foreground_title": self._window_title(foreground_hwnd) if int(foreground_hwnd) > 0 else "",
        }
        if error:
            payload["error"] = error
        level = logging.INFO if success else logging.WARNING
        log.log(level, "window_focus %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))

    def _get_target_window(self) -> gw.Win32Window:
        preferred = self._configured_window_title_token().lower()
        windows = [w for w in gw.getAllWindows() if getattr(w, "title", "")]

        def _pick_by_tokens(tokens: list[str]) -> gw.Win32Window | None:
            clean = [t.strip().lower() for t in tokens if t and t.strip()]
            if not clean:
                return None
            for w in windows:
                title = str(getattr(w, "title", "")).strip()
                if not title:
                    continue
                lowered = title.lower()
                if any(tok in lowered for tok in clean):
                    return w
            return None

        target: gw.Win32Window | None = None
        if preferred:
            target = _pick_by_tokens([preferred])
        else:
            # Fallback tokens for modded clients where title may omit "Minecraft".
            target = _pick_by_tokens(["ftb stoneblock", "stoneblock", "minecraft"])
        if target is not None:
            return target

        sample_titles = [str(getattr(w, "title", "")).strip() for w in windows[:12] if str(getattr(w, "title", "")).strip()]
        raise RuntimeError(
            f"No game window found containing title: {self.window_config.title_contains}. "
            f"Visible titles sample={sample_titles}"
        )

    def is_window_focused(self) -> bool:
        try:
            target = self._get_target_window()
        except RuntimeError:
            return False
        return int(target._hWnd) == self._foreground_hwnd()

    def ensure_window_focus(self) -> None:
        target = self._get_target_window()
        hwnd = int(target._hWnd)
        if hwnd <= 0:
            raise RuntimeError("resolved invalid Minecraft window handle")

        try:
            is_valid_hwnd = bool(_user32.IsWindow(int(hwnd)))
        except Exception:
            is_valid_hwnd = True
        if not is_valid_hwnd:
            raise RuntimeError(f"resolved stale Minecraft window handle: hwnd={hwnd}")

        target_title = str(getattr(target, "title", "")).strip() or self._window_title(hwnd)
        configured_title = self._configured_window_title_token()
        if configured_title and configured_title.lower() not in target_title.lower():
            raise RuntimeError(
                f"resolved window title '{target_title}' does not contain configured title '{configured_title}'"
            )

        foreground_hwnd = self._foreground_hwnd()
        if hwnd == foreground_hwnd:
            self._log_focus_attempt(
                attempt_number=0,
                found_hwnd=hwnd,
                foreground_hwnd=foreground_hwnd,
                success=True,
                strategy="already_foreground",
                target_title=target_title,
            )
            return

        attempts = 1
        if getattr(self.control_config, "auto_refocus_enabled", False):
            attempts = max(1, int(self.control_config.auto_refocus_attempts))
        backoff = max(0.0, float(self.control_config.auto_refocus_backoff_seconds)) if attempts > 1 else 0.0
        focus_timeout = max(0.2, float(self.control_config.focus_timeout_seconds))
        last_error = ""

        for attempt in range(1, attempts + 1):
            try:
                if bool(getattr(target, "isMinimized", False)):
                    target.restore()
            except Exception:
                pass
            try:
                target.activate()
            except Exception:
                pass

            success = False
            strategy = "set_foreground"
            try:
                self._force_foreground_hwnd(hwnd)
                success = self._wait_for_foreground_hwnd(hwnd, focus_timeout)
                if not success:
                    strategy = "attach_thread_input"
                    self._attach_thread_input_foreground_hwnd(hwnd, self._foreground_hwnd())
                    success = self._wait_for_foreground_hwnd(hwnd, focus_timeout)
            except Exception as exc:
                last_error = str(exc)

            foreground_hwnd = self._foreground_hwnd()
            self._log_focus_attempt(
                attempt_number=attempt,
                found_hwnd=hwnd,
                foreground_hwnd=foreground_hwnd,
                success=success,
                strategy=strategy,
                target_title=target_title,
                error=last_error,
            )
            if success:
                return
            if attempt < attempts and backoff > 0.0:
                time.sleep(backoff)

        current_hwnd = self._foreground_hwnd()
        current_title = self._window_title(current_hwnd)
        detail = f"failed to focus target window; target='{target_title}' hwnd={hwnd}; foreground='{current_title}' hwnd={current_hwnd}"
        if last_error:
            detail = f"{detail}; error={last_error}"
        raise RuntimeError(detail)

    def _guard(self) -> None:
        if self.stop_requested:
            raise RuntimeError("Stop requested")
        with self._state_lock:
            if not self.automation_enabled:
                raise RuntimeError("Automation paused")
        if not self.is_window_focused():
            if self.control_config.auto_refocus_enabled:
                try:
                    self.ensure_window_focus()
                    return
                except Exception as exc:
                    raise RuntimeError(f"Minecraft window not focused: {exc}") from exc
            raise RuntimeError("Minecraft window not focused")

    def _attempt_auto_refocus(self) -> bool:
        try:
            self.ensure_window_focus()
            return self.is_window_focused()
        except Exception as exc:
            log.warning("Auto-refocus failed: %s", exc)
            return False

    def press(self, key: str, hold_seconds: float | None = None) -> None:
        self._guard()
        time.sleep(max(0.0, self.control_config.pre_action_delay_seconds))
        self._backend.press(key, hold_seconds if hold_seconds is not None else self.control_config.key_hold_seconds)

    def type_text(self, text: str) -> None:
        self._guard()
        self._backend.type_text(text, self.control_config.type_delay_seconds)

    def send_chat_command(self, raw_command: str) -> None:
        cmd = raw_command.strip()
        if not cmd:
            return
        self.ensure_window_focus()
        self.press(self.control_config.chat_key)
        self.type_text(cmd)
        self.press("enter")
        log.info("Sent command: %s", cmd)

    def send_baritone(self, command_body: str) -> None:
        prefix = self.control_config.command_prefix
        self.send_chat_command(f"{prefix}{command_body.strip()}")

    def place_torch(self, hotbar_slot: int, hold_seconds: float | None = None) -> None:
        slot = int(hotbar_slot)
        if slot < 1 or slot > 9:
            raise ValueError(f"Invalid hotbar slot for torch placement: {slot}")
        self.ensure_window_focus()
        self.press(str(slot), hold_seconds=self.control_config.key_hold_seconds)
        self._guard()
        time.sleep(max(0.0, self.control_config.pre_action_delay_seconds))
        self._guard()
        self._backend.right_click(hold_seconds if hold_seconds is not None else self.control_config.torch_click_hold_seconds)

    def left_click(self, hold_seconds: float | None = None) -> None:
        self.ensure_window_focus()
        self._guard()
        time.sleep(max(0.0, self.control_config.pre_action_delay_seconds))
        self._guard()
        self._backend.left_click(hold_seconds if hold_seconds is not None else self.control_config.torch_click_hold_seconds)

    def consume_hotbar_slot(self, hotbar_slot: int, hold_seconds: float) -> None:
        slot = int(hotbar_slot)
        if slot < 1 or slot > 9:
            raise ValueError(f"Invalid hotbar slot for consume action: {slot}")
        self.ensure_window_focus()
        self.press(str(slot), hold_seconds=self.control_config.key_hold_seconds)
        self._guard()
        time.sleep(max(0.0, self.control_config.pre_action_delay_seconds))
        self._guard()
        self._backend.right_click(max(0.2, hold_seconds))
