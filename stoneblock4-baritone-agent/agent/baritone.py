from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import BaritoneConfig
from agent.input_control import GameController
from agent.perception import ScreenPerception

log = logging.getLogger(__name__)


class BaritoneCommandValidationError(RuntimeError):
    pass


@dataclass
class BaritoneBridge:
    controller: GameController
    perception: ScreenPerception
    config: BaritoneConfig
    _active_command_id: str | None = field(default=None, init=False)
    _active_command_body: str = field(default="", init=False)
    _active_command_started_at: float = field(default=0.0, init=False)
    _next_torch_at: float = field(default=0.0, init=False)
    _last_eat_attempt_at: float = field(default=0.0, init=False)
    _last_torch_craft_attempt_at: float = field(default=0.0, init=False)
    _last_known_torch_slot: int | None = field(default=None, init=False)
    _last_tool_recovery_at: float = field(default=0.0, init=False)
    _last_combat_attempt_at: float = field(default=0.0, init=False)
    _last_inventory_cleanup_at: float = field(default=0.0, init=False)
    _combat_supported: bool | None = field(default=None, init=False)
    _proc_supported: bool | None = field(default=None, init=False)
    _drop_supported: bool | None = field(default=None, init=False)
    _ultimine_supported: bool | None = field(default=None, init=False)
    _ultimine_active_for_command: bool = field(default=False, init=False)
    _ultimine_warned_unsupported: bool = field(default=False, init=False)
    _stuck_recovery_attempts: int = field(default=0, init=False)
    _aux_command_ids: set[str] = field(default_factory=set, init=False)
    _unsupported_aux_prefixes: set[str] = field(default_factory=set, init=False)
    _manual_pause_active: bool = field(default=False, init=False)
    _inventory_cleanup_protected_item_ids: set[str] = field(default_factory=set, init=False)
    _planner_task_context: dict[str, Any] = field(default_factory=dict, init=False)
    _bridge_command_context_by_id: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _observed_bridge_command_ids: set[str] = field(default_factory=set, init=False)
    _persistent_aux_commands_applied: set[str] = field(default_factory=set, init=False)
    _last_action_evaluation: dict[str, Any] = field(default_factory=dict, init=False)

    def _bridge_paths(self) -> tuple[Path, Path, Path]:
        bridge_dir = Path(self.config.bridge_dir).expanduser().resolve()
        return bridge_dir, bridge_dir / "inbox", bridge_dir / "outbox"

    def _status_path(self) -> Path:
        bridge_dir, _, _ = self._bridge_paths()
        return bridge_dir / "status.json"

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None

    @staticmethod
    def _delete_file_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            return

    @staticmethod
    def _bridge_ack_candidate_paths(outbox_dir: Path, command_id: str) -> list[Path]:
        direct = outbox_dir / f"{command_id}.json"
        candidates: list[Path] = [direct]
        for path in sorted(outbox_dir.glob(f"{command_id}_*.json")):
            if path not in candidates:
                candidates.append(path)
        return candidates

    def _load_bridge_ack(self, outbox_dir: Path, command_id: str) -> tuple[dict[str, Any] | None, Path | None]:
        for candidate in self._bridge_ack_candidate_paths(outbox_dir, command_id):
            ack = self._load_json(candidate)
            if ack:
                return ack, candidate
        return None, None

    def _delete_bridge_ack_files(self, outbox_dir: Path, command_id: str) -> None:
        for candidate in self._bridge_ack_candidate_paths(outbox_dir, command_id):
            self._delete_file_quietly(candidate)

    @staticmethod
    def _merge_context_dict(target: dict[str, Any], incoming: Any) -> None:
        if not isinstance(incoming, dict):
            return
        for raw_key, raw_value in incoming.items():
            key = str(raw_key).strip()
            if not key:
                continue
            value = raw_value
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            if value is None:
                continue
            target[key] = value

    def set_planner_task_context(self, context: dict[str, Any] | None = None, **fields: Any) -> None:
        payload: dict[str, Any] = {}
        self._merge_context_dict(payload, context)
        self._merge_context_dict(payload, fields)
        self._planner_task_context = payload

    def clear_planner_task_context(self) -> None:
        self._planner_task_context = {}

    def _current_planner_task_context(self) -> dict[str, Any]:
        context: dict[str, Any] = {}
        self._merge_context_dict(context, self._planner_task_context)
        if context:
            return context

        for source in (self, self.controller):
            self._merge_context_dict(context, getattr(source, "planner_task_context", None))
            self._merge_context_dict(context, getattr(source, "current_planner_task", None))
            self._merge_context_dict(context, getattr(source, "plannerTaskContext", None))
            self._merge_context_dict(context, getattr(source, "plannerTask", None))
            if context:
                return context

        scalar_attrs: tuple[tuple[str, str], ...] = (
            ("planner_goal_id", "goalId"),
            ("current_goal_id", "goalId"),
            ("planner_task_id", "taskId"),
            ("current_task_id", "taskId"),
            ("planner_task_type", "taskType"),
            ("current_task_type", "taskType"),
            ("planner_task_attempt", "attempt"),
            ("current_task_attempt", "attempt"),
            ("planner_task_index", "taskIndex"),
            ("current_task_index", "taskIndex"),
        )
        for source in (self, self.controller):
            for attr, key in scalar_attrs:
                value = getattr(source, attr, None)
                if value is None:
                    continue
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        continue
                context.setdefault(key, value)
        return context

    def _remember_bridge_command_context(self, command_id: str) -> None:
        command_token = str(command_id).strip()
        if not command_token:
            return
        context = self._current_planner_task_context()
        if not context:
            return
        self._bridge_command_context_by_id[command_token] = dict(context)
        if len(self._bridge_command_context_by_id) > 512:
            self._bridge_command_context_by_id.clear()

    def _planner_context_for_command_id(self, command_id: str) -> dict[str, Any]:
        command_token = str(command_id).strip()
        if command_token:
            remembered = self._bridge_command_context_by_id.get(command_token)
            if remembered:
                return dict(remembered)
        return self._current_planner_task_context()

    def _log_bridge_command_correlation(
        self,
        *,
        phase: str,
        command_id: str,
        command_body: str = "",
        track_active: bool | None = None,
        status: dict[str, Any] | None = None,
    ) -> None:
        command_token = str(command_id).strip()
        if not command_token:
            return

        phase_token = str(phase).strip().lower() or "unknown"
        if phase_token == "observed":
            if command_token in self._observed_bridge_command_ids:
                return
            self._observed_bridge_command_ids.add(command_token)
            if len(self._observed_bridge_command_ids) > 512:
                self._observed_bridge_command_ids.clear()

        payload: dict[str, Any] = {
            "event": "bridge_command_id_planner_task_link",
            "phase": phase_token,
            "bridgeCommandId": command_token,
        }
        body = str(command_body).strip()
        if body:
            payload["command"] = body
        if track_active is not None:
            payload["trackActive"] = bool(track_active)
        if isinstance(status, dict):
            status_command = str(status.get("lastCommand", "")).strip()
            if status_command:
                payload["statusCommand"] = status_command
            status_result = str(status.get("lastCommandResult", "")).strip()
            if status_result:
                payload["statusResult"] = status_result
        planner_context = self._planner_context_for_command_id(command_token)
        if planner_context:
            payload["plannerTask"] = planner_context

        log.info("bridge_command_correlation %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))

    def _bridge_command(
        self,
        body: str,
        track_active: bool = True,
        *,
        ack_timeout_seconds: float | None = None,
    ) -> str:
        bridge_dir, inbox_dir, outbox_dir = self._bridge_paths()
        bridge_dir.mkdir(parents=True, exist_ok=True)
        inbox_dir.mkdir(parents=True, exist_ok=True)
        outbox_dir.mkdir(parents=True, exist_ok=True)

        command_id = uuid.uuid4().hex
        payload = {
            "id": command_id,
            "command": body.strip(),
            "createdAtMs": int(time.time() * 1000),
            "trackActive": bool(track_active),
        }
        command_path = inbox_dir / f"{command_id}.json"
        self._write_json_atomic(command_path, payload)
        self._remember_bridge_command_context(command_id)
        self._log_bridge_command_correlation(
            phase="created",
            command_id=command_id,
            command_body=body.strip(),
            track_active=track_active,
        )
        if track_active:
            self._active_command_id = command_id
            self._active_command_body = body.strip()
            self._active_command_started_at = time.time()
            self._aux_command_ids.clear()
        log.info("Queued bridge command: %s", body.strip())

        ack_timeout = self.config.bridge_ack_timeout_seconds if ack_timeout_seconds is None else ack_timeout_seconds
        deadline = time.time() + max(0.15, float(ack_timeout))
        while time.time() < deadline:
            ack, ack_path = self._load_bridge_ack(outbox_dir, command_id)
            if ack:
                status = str(ack.get("status", "")).lower()
                if status in {"accepted", "ok"}:
                    if ack_path is not None:
                        self._delete_file_quietly(ack_path)
                    self._delete_bridge_ack_files(outbox_dir, command_id)
                    return command_id
                if status in {"rejected", "error"}:
                    if ack_path is not None:
                        self._delete_file_quietly(ack_path)
                    self._delete_bridge_ack_files(outbox_dir, command_id)
                    raise RuntimeError(f"bridge rejected command: {ack.get('error', status)}")
            status = self.read_bridge_status()
            if self._bridge_status_ack_matches_command(status, command_id=command_id, command_body=body):
                self._delete_bridge_ack_files(outbox_dir, command_id)
                return command_id
            if self._bridge_status_reject_matches_command(status, command_id=command_id, command_body=body):
                self._delete_bridge_ack_files(outbox_dir, command_id)
                err = str((status or {}).get("lastCommandError", "")).strip()
                result = str((status or {}).get("lastCommandResult", "")).strip().lower() or "rejected"
                raise RuntimeError(f"bridge rejected command: {err or result}")
            time.sleep(0.1)
        status = self.read_bridge_status()
        if self._bridge_status_ack_matches_command(status, command_id=command_id, command_body=body):
            return command_id
        if self._bridge_status_reject_matches_command(status, command_id=command_id, command_body=body):
            err = str((status or {}).get("lastCommandError", "")).strip()
            result = str((status or {}).get("lastCommandResult", "")).strip().lower() or "rejected"
            raise RuntimeError(f"bridge rejected command: {err or result}")
        self._delete_file_quietly(command_path)
        self._delete_bridge_ack_files(outbox_dir, command_id)
        raise RuntimeError("timed out waiting for bridge command acknowledgment")

    def read_bridge_status(self) -> dict[str, Any] | None:
        if self.config.transport.strip().lower() != "bridge_file":
            return None
        return self._load_json(self._status_path())

    @staticmethod
    def _status_age_seconds(status: dict[str, Any]) -> float:
        ts = float(status.get("timestampMs", 0.0))
        if ts <= 0.0:
            return 9999.0
        return (time.time() * 1000.0 - ts) / 1000.0

    @staticmethod
    def _command_age_seconds(status: dict[str, Any]) -> float:
        ts = float(status.get("lastCommandAtMs", 0.0))
        if ts <= 0.0:
            return 9999.0
        return (time.time() * 1000.0 - ts) / 1000.0

    def _status_has_recent_ack(self, status: dict[str, Any] | None) -> bool:
        if not isinstance(status, dict):
            return False
        result = str(status.get("lastCommandResult", "")).strip().lower()
        if result not in {"accepted", "ok"}:
            return False
        return self._command_age_seconds(status) <= max(2.0, self.config.bridge_unresponsive_after_seconds)

    def _status_is_fresh(self, status: dict[str, Any] | None) -> bool:
        if not status:
            return False
        return self._status_age_seconds(status) <= max(0.5, self.config.bridge_status_stale_after_seconds)

    def _bridge_status_ack_matches_command(
        self,
        status: dict[str, Any] | None,
        *,
        command_id: str,
        command_body: str,
    ) -> bool:
        if not isinstance(status, dict):
            return False
        if not self._status_is_fresh(status) and not self._status_has_recent_ack(status):
            return False
        result = str(status.get("lastCommandResult", "")).strip().lower()
        if result not in {"accepted", "ok"}:
            return False
        status_command_id = str(status.get("lastCommandId", "")).strip()
        if status_command_id and status_command_id == str(command_id).strip():
            return True
        status_command = str(status.get("lastCommand", "")).strip()
        if not status_command:
            return False
        return status_command == str(command_body).strip()

    def _bridge_status_reject_matches_command(
        self,
        status: dict[str, Any] | None,
        *,
        command_id: str,
        command_body: str,
    ) -> bool:
        if not isinstance(status, dict):
            return False
        if not self._status_is_fresh(status) and not self._status_has_recent_ack(status):
            return False
        result = str(status.get("lastCommandResult", "")).strip().lower()
        if result not in {"rejected", "error"}:
            return False
        status_command_id = str(status.get("lastCommandId", "")).strip()
        if status_command_id and status_command_id == str(command_id).strip():
            return True
        status_command = str(status.get("lastCommand", "")).strip()
        return bool(status_command) and status_command == str(command_body).strip()

    def _bridge_status_reissued_active_command(
        self,
        status: dict[str, Any] | None,
        *,
        command_id: str,
        command_body: str,
    ) -> bool:
        if not isinstance(status, dict):
            return False
        if not self._status_is_fresh(status) and not self._status_has_recent_ack(status):
            return False
        status_command_id = str(status.get("lastCommandId", "")).strip()
        if not status_command_id or status_command_id == str(command_id).strip():
            return False
        active_command_id = str(self._active_command_id or "").strip()
        if not active_command_id or status_command_id != active_command_id:
            return False
        status_command = str(status.get("lastCommand", "")).strip()
        body = str(command_body).strip()
        if not body or status_command != body:
            return False
        return not self._looks_like_aux_command(status_command)

    def _bridge_readiness_issues(self, status: dict[str, Any] | None) -> list[str]:
        if not status:
            return ["bridge status not available"]
        issues: list[str] = []
        age_seconds = self._status_age_seconds(status)
        if age_seconds > max(0.5, self.config.bridge_status_stale_after_seconds):
            issues.append(f"bridge status stale ({age_seconds:.2f}s)")
        if not bool(status.get("bridgeOk", False)):
            issues.append("bridge status not ok")
        if not bool(status.get("inWorld", False)):
            details: list[str] = []
            client_state = str(status.get("clientStateHint", "")).strip()
            if client_state:
                details.append(client_state)
            screen = (
                str(status.get("screenTitle", "")).strip()
                or str(status.get("currentScreen", "")).strip()
                or str(status.get("screenName", "")).strip()
                or str(status.get("screenClass", "")).strip()
            )
            if screen:
                details.append(f"screen={screen}")
            window_active = status.get("windowActive")
            if window_active is not None and not bool(window_active):
                details.append("window_inactive")
            if details:
                issues.append(f"player is not in-world ({'; '.join(details)})")
            else:
                issues.append("player is not in-world")
        if not bool(status.get("baritoneLoaded", False)):
            err = str(status.get("baritoneError", "")).strip()
            issues.append(f"baritone not loaded: {err or 'unknown error'}")
        return issues

    def wait_for_bridge_ready(self, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.time() + max(0.5, timeout_seconds)
        last_issues: list[str] = ["bridge status not available"]
        warned_at = 0.0
        while time.time() < deadline:
            status = self.read_bridge_status()
            issues = self._bridge_readiness_issues(status)
            if not issues and status is not None:
                return status
            last_issues = issues
            now = time.time()
            if now - warned_at >= 3.0:
                log.warning("Waiting for bridge readiness: %s", "; ".join(issues))
                warned_at = now
            time.sleep(0.25)
        raise RuntimeError("; ".join(last_issues))

    def assert_bridge_ready(self) -> None:
        if self.config.transport.strip().lower() != "bridge_file":
            return
        status = self.read_bridge_status()
        issues = self._bridge_readiness_issues(status)
        if issues:
            raise RuntimeError("; ".join(issues))

    @staticmethod
    def _is_legacy_proc_command(command_body: str) -> bool:
        parts = command_body.strip().lower().split()
        return bool(parts) and parts[0] == "proc"

    @staticmethod
    def _split_command(command_body: str) -> tuple[str, list[str]]:
        parts = str(command_body).strip().split()
        if not parts:
            return "", []
        return parts[0].lower(), parts[1:]

    def _validate_command_arity(self, command_body: str) -> None:
        op, args = self._split_command(command_body)
        if not op:
            raise BaritoneCommandValidationError("empty command body")

        if op == "proc" and not self.config.allow_legacy_proc_commands:
            raise BaritoneCommandValidationError(
                "legacy proc command disabled (set baritone.allow_legacy_proc_commands=true to override)"
            )
        if op == "bridge.craft_item" and not (1 <= len(args) <= 2):
            raise BaritoneCommandValidationError(
                f"invalid bridge.craft_item arity: expected 1-2 args, got {len(args)}"
            )
        if op == "bridge.swap_to_hotbar" and len(args) != 2:
            raise BaritoneCommandValidationError(
                f"invalid bridge.swap_to_hotbar arity: expected 2 args, got {len(args)}"
            )
        if op == "bridge.drop_item" and not (1 <= len(args) <= 2):
            raise BaritoneCommandValidationError(
                f"invalid bridge.drop_item arity: expected 1-2 args, got {len(args)}"
            )
        if op == "bridge.fight_hostile" and len(args) > 2:
            raise BaritoneCommandValidationError(
                f"invalid bridge.fight_hostile arity: expected 0-2 args, got {len(args)}"
            )
        if op == "bridge.store_to_nearby_chest" and len(args) > 3:
            raise BaritoneCommandValidationError(
                f"invalid bridge.store_to_nearby_chest arity: expected 0-3 args, got {len(args)}"
            )
        if op == "bridge.ultimine" and len(args) != 1:
            raise BaritoneCommandValidationError(
                f"invalid bridge.ultimine arity: expected 1 arg, got {len(args)}"
            )

    def _bridge_supports_proc_command(self, status: dict[str, Any]) -> bool:
        version = str(status.get("bridgeVersion", "")).strip()
        if not version:
            return False
        return self._parse_version_triplet(version) >= (0, 2, 0)

    def _aux_command_supported(self, body: str, status: dict[str, Any] | None = None) -> bool:
        command = body.strip()
        if not command:
            return False
        op, _ = self._split_command(command)

        if self._is_legacy_proc_command(command) and not self.config.allow_legacy_proc_commands:
            prefix = "proc"
            if prefix not in self._unsupported_aux_prefixes:
                log.warning(
                    "Ignoring legacy Baritone command prefix '%s'. "
                    "Use bridge-native commands or set allow_legacy_proc_commands=true.",
                    prefix,
                )
                self._unsupported_aux_prefixes.add(prefix)
            return False

        if self.config.transport.strip().lower() != "bridge_file":
            return True

        if op.startswith("bridge."):
            local_status = status if isinstance(status, dict) else self.read_bridge_status()
            bridge_version = ""
            if isinstance(local_status, dict):
                bridge_version = str(local_status.get("bridgeVersion", "")).strip()

            required_versions: dict[str, tuple[int, int, int]] = {
                "bridge.craft_item": (0, 1, 4),
                "bridge.drop_item": (0, 1, 5),
                "bridge.fight_hostile": (0, 1, 2),
                "bridge.store_to_nearby_chest": (0, 1, 6),
                "bridge.ultimine": (0, 1, 8),
            }
            required = required_versions.get(op)
            if required is None:
                return True

            supported = False
            if isinstance(local_status, dict):
                supported = self._bridge_supports_command(local_status, op, required)
            if supported:
                self._unsupported_aux_prefixes.discard(op)
                return True

            if op not in self._unsupported_aux_prefixes:
                if bridge_version:
                    log.warning(
                        "Ignoring unsupported bridge command prefix '%s' on bridge version %s (requires >=%d.%d.%d).",
                        op,
                        bridge_version,
                        required[0],
                        required[1],
                        required[2],
                    )
                else:
                    log.warning(
                        "Ignoring bridge command prefix '%s' because bridge version is unknown (requires >=%d.%d.%d).",
                        op,
                        required[0],
                        required[1],
                        required[2],
                    )
                self._unsupported_aux_prefixes.add(op)
            return False

        if not self._is_legacy_proc_command(command):
            return True

        if self._proc_supported is None:
            local_status = status if isinstance(status, dict) else self.read_bridge_status()
            if local_status and self._status_is_fresh(local_status):
                if self._bridge_supports_proc_command(local_status):
                    self._proc_supported = True
                else:
                    self._proc_supported = False
            else:
                self._proc_supported = False

        if self._proc_supported:
            return True

        prefix = "proc"
        if prefix not in self._unsupported_aux_prefixes:
            version = ""
            if isinstance(status, dict):
                version = str(status.get("bridgeVersion", "")).strip()
            if not version:
                live = self.read_bridge_status()
                if isinstance(live, dict):
                    version = str(live.get("bridgeVersion", "")).strip()
            if version:
                log.warning(
                    "Ignoring unsupported Baritone command prefix '%s' on bridge version %s. "
                    "Use bridge-native commands instead.",
                    prefix,
                    version,
                )
            else:
                log.warning(
                    "Ignoring unsupported Baritone command prefix '%s' on bridge transport. "
                    "Use bridge-native commands instead.",
                    prefix,
                )
            self._unsupported_aux_prefixes.add(prefix)
        return False

    def _sync_manual_pause_with_controller(self) -> None:
        # Keep Baritone pathing state aligned with runtime toggle state.
        try:
            enabled = self.controller.is_automation_enabled()
        except Exception:
            enabled = bool(self.controller.automation_enabled)

        if not enabled and not self._manual_pause_active:
            try:
                self.pause_pathing()
            finally:
                self._manual_pause_active = True
            return

        if enabled and self._manual_pause_active:
            try:
                self.resume_pathing()
            finally:
                self._manual_pause_active = False

    def sync_manual_pause_with_controller(self) -> None:
        self._sync_manual_pause_with_controller()

    def _send_aux_command(self, body: str) -> bool:
        cmd = body.strip()
        if not cmd:
            return False
        self._validate_command_arity(cmd)
        transport = self.config.transport.strip().lower()
        if not self._aux_command_supported(cmd):
            return False
        if transport == "bridge_file":
            aux_id = self._bridge_command(cmd, track_active=False)
            self._aux_command_ids.add(aux_id)
            if len(self._aux_command_ids) > 128:
                self._aux_command_ids.clear()
            return True
        self.controller.send_baritone(cmd)
        return True

    @staticmethod
    def _is_persistent_aux_command(body: str) -> bool:
        cmd = str(body).strip().lower()
        if not cmd:
            return False
        return cmd.startswith("set ")

    def _send_bridge_aux_command_direct(self, body: str, *, ack_timeout_seconds: float | None = None) -> bool:
        cmd = body.strip()
        if not cmd:
            return False
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        self._validate_command_arity(cmd)
        aux_id = self._bridge_command(cmd, track_active=False, ack_timeout_seconds=ack_timeout_seconds)
        self._aux_command_ids.add(aux_id)
        if len(self._aux_command_ids) > 128:
            self._aux_command_ids.clear()
        return True

    def _should_auto_ultimine(self, body: str) -> bool:
        if not self.config.ultimine_auto_enabled:
            return False
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        return self._matches_prefixes(body, self.config.ultimine_for_prefixes)

    def _try_enable_ultimine_for_command(self, command_body: str) -> bool:
        if not self._should_auto_ultimine(command_body):
            return False
        enable_cmd = str(self.config.ultimine_enable_command).strip()
        if not enable_cmd:
            return False

        status = self.read_bridge_status()
        if isinstance(status, dict) and self._status_is_fresh(status):
            if not self._bridge_supports_ultimine(status):
                self._ultimine_supported = False
                if not self._ultimine_warned_unsupported:
                    version = str(status.get("bridgeVersion", "")).strip() or "<unknown>"
                    log.warning(
                        "Ultimine bridge control unsupported on bridge version %s; continuing without Ultimine automation.",
                        version,
                    )
                    self._ultimine_warned_unsupported = True
                return False
        elif self._ultimine_supported is False:
            return False

        if not self._aux_command_supported(enable_cmd, status=status if isinstance(status, dict) else None):
            self._ultimine_supported = False
            if not self._ultimine_warned_unsupported:
                log.warning(
                    "Ultimine bridge command '%s' is unsupported; continuing without Ultimine automation.",
                    enable_cmd,
                )
                self._ultimine_warned_unsupported = True
            return False

        enable_is_bridge_cmd = enable_cmd.lower().startswith("bridge.")
        try:
            if enable_is_bridge_cmd and self.config.transport.strip().lower() == "bridge_file":
                sent = self._send_bridge_aux_command_direct(enable_cmd, ack_timeout_seconds=0.75)
            else:
                sent = self._send_aux_command(enable_cmd)
        except Exception as exc:
            log.warning("Failed to enable Ultimine for '%s': %s", command_body, exc)
            return False
        if not sent:
            return False

        self._ultimine_supported = True
        self._ultimine_warned_unsupported = False
        self._ultimine_active_for_command = True
        return True

    def _disable_ultimine_after_command(self, *, context: str) -> None:
        if not self._ultimine_active_for_command:
            return
        self._ultimine_active_for_command = False

        if self.config.transport.strip().lower() != "bridge_file":
            return
        disable_cmd = str(self.config.ultimine_disable_command).strip()
        if not disable_cmd:
            return

        try:
            disable_is_bridge_cmd = disable_cmd.lower().startswith("bridge.")
            if self._ultimine_supported is True and disable_is_bridge_cmd:
                sent = self._send_bridge_aux_command_direct(disable_cmd, ack_timeout_seconds=0.75)
            else:
                sent = self._send_aux_command(disable_cmd)
            if not sent:
                log.warning(
                    "Ultimine disable command was not sent (%s): %s",
                    context,
                    disable_cmd,
                )
        except Exception as exc:
            log.warning("Failed to disable Ultimine after %s: %s", context, exc)

    def _chat_control_command(self, body: str) -> None:
        cmd = body.strip()
        if not cmd:
            return
        try:
            transport = self.config.transport.strip().lower()
            if transport == "bridge_file":
                self._send_aux_command(cmd)
            else:
                self.controller.send_baritone(cmd)
        except Exception as exc:
            log.warning("Failed to send chat control command '%s': %s", cmd, exc)

    def pause_pathing(self) -> None:
        self._chat_control_command("pause")

    def resume_pathing(self) -> None:
        self._chat_control_command("resume")

    @staticmethod
    def _shutdown_status_snapshot(status: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(status, dict):
            return {
                "postStopIsPathing": None,
                "postStopCurrentGoal": "",
                "postStopLastCommand": "",
                "postStopLastCommandResult": "",
                "postStopUltimineActive": None,
            }
        return {
            "postStopIsPathing": bool(status.get("isPathing", False)),
            "postStopCurrentGoal": str(status.get("currentGoal", "")).strip(),
            "postStopLastCommand": str(status.get("lastCommand", "")).strip(),
            "postStopLastCommandResult": str(status.get("lastCommandResult", "")).strip(),
            "postStopUltimineActive": status.get("ultimineActive"),
        }

    def _shutdown_verified(self, status: dict[str, Any] | None) -> bool:
        if not isinstance(status, dict):
            return False
        if not self._status_is_fresh(status) and not self._status_has_recent_ack(status):
            return False
        return not bool(status.get("isPathing", False)) and not str(status.get("currentGoal", "")).strip()

    def fail_closed_stop(
        self,
        *,
        reason: str,
        source: str = "runtime_shutdown",
        verify_timeout_seconds: float = 2.0,
        poll_seconds: float = 0.1,
    ) -> dict[str, Any]:
        transport = self.config.transport.strip().lower()
        result: dict[str, Any] = {
            "reason": str(reason).strip() or "unspecified",
            "source": str(source).strip() or "runtime_shutdown",
            "transport": transport,
            "bridgeStatusAvailable": False,
            "bridgeStatusFresh": False,
            "pauseSent": False,
            "pauseError": "",
            "stopSent": False,
            "stopError": "",
            "shutdownVerified": False,
        }
        log.info(
            "baritone_shutdown_begin %s",
            json.dumps(
                {
                    "event": "baritone_shutdown_begin",
                    "reason": result["reason"],
                    "source": result["source"],
                    "transport": transport,
                    "activeCommandBody": self._active_command_body,
                    "activeCommandId": self._active_command_id,
                },
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            ),
        )

        try:
            self._disable_ultimine_after_command(context=f"shutdown:{result['source']}")
        except Exception as exc:
            result["ultimineDisableError"] = str(exc)
            log.warning("Failed to disable Ultimine during shutdown: %s", exc)
        self._ultimine_active_for_command = False

        ack_timeout_seconds = min(1.0, max(0.15, float(self.config.bridge_ack_timeout_seconds)))
        for command_name in ("pause", "stop"):
            sent = False
            error = ""
            try:
                if transport == "bridge_file":
                    sent = self._send_bridge_aux_command_direct(
                        command_name,
                        ack_timeout_seconds=ack_timeout_seconds,
                    )
                else:
                    sent = self._send_aux_command(command_name)
            except Exception as exc:
                error = str(exc)

            result[f"{command_name}Sent"] = bool(sent)
            result[f"{command_name}Error"] = error
            payload = {
                "event": "baritone_shutdown_command",
                "reason": result["reason"],
                "source": result["source"],
                "command": command_name,
                "commandSent": bool(sent),
                "commandError": error,
            }
            if error:
                log.warning(
                    "baritone_shutdown_command %s",
                    json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str),
                )
            else:
                log.info(
                    "baritone_shutdown_command %s",
                    json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str),
                )

        last_status: dict[str, Any] | None = None
        if transport == "bridge_file":
            deadline = time.time() + max(0.0, float(verify_timeout_seconds))
            while True:
                status = self.read_bridge_status()
                if isinstance(status, dict):
                    last_status = status
                    result["bridgeStatusAvailable"] = True
                    result["bridgeStatusFresh"] = bool(
                        self._status_is_fresh(status) or self._status_has_recent_ack(status)
                    )
                    if self._shutdown_verified(status):
                        break
                if time.time() >= deadline:
                    break
                time.sleep(max(0.05, float(poll_seconds)))

        result.update(self._shutdown_status_snapshot(last_status))
        result["shutdownVerified"] = self._shutdown_verified(last_status)

        postcheck_payload = {
            "event": "baritone_shutdown_postcheck",
            "reason": result["reason"],
            "source": result["source"],
            "bridgeStatusAvailable": result["bridgeStatusAvailable"],
            "bridgeStatusFresh": result["bridgeStatusFresh"],
            "postStopIsPathing": result["postStopIsPathing"],
            "postStopCurrentGoal": result["postStopCurrentGoal"],
            "postStopLastCommand": result["postStopLastCommand"],
            "postStopLastCommandResult": result["postStopLastCommandResult"],
            "postStopUltimineActive": result["postStopUltimineActive"],
            "shutdownVerified": result["shutdownVerified"],
        }
        if result["shutdownVerified"]:
            self._active_command_id = None
            self._active_command_body = ""
            self._active_command_started_at = 0.0
            self._next_torch_at = 0.0
            self._stuck_recovery_attempts = 0
            log.info(
                "baritone_shutdown_postcheck %s",
                json.dumps(postcheck_payload, ensure_ascii=True, sort_keys=True, default=str),
            )
        else:
            log.warning(
                "baritone_shutdown_postcheck %s",
                json.dumps(postcheck_payload, ensure_ascii=True, sort_keys=True, default=str),
            )

        log.info(
            "baritone_shutdown_end %s",
            json.dumps(result, ensure_ascii=True, sort_keys=True, default=str),
        )
        return result

    def active_command_state(self) -> dict[str, Any]:
        planner_context = self._planner_context_for_command_id(str(self._active_command_id or "").strip())
        return {
            "activeCommandId": self._active_command_id,
            "activeCommandBody": self._active_command_body,
            "activeCommandStartedAt": self._active_command_started_at,
            "nextTorchAt": self._next_torch_at,
            "stuckRecoveryAttempts": self._stuck_recovery_attempts,
            "combatSupported": self._combat_supported,
            "ultimineSupported": self._ultimine_supported,
            "ultimineActiveForCommand": self._ultimine_active_for_command,
            "plannerTaskContext": planner_context,
            "lastActionEvaluation": dict(self._last_action_evaluation),
        }

    def _matches_prefixes(self, body: str, prefixes: list[str]) -> bool:
        lowered = body.strip().lower()
        return any(lowered.startswith(p.strip().lower()) for p in prefixes if str(p).strip())

    def is_long_running_command(self, body: str) -> bool:
        return self._matches_prefixes(body, self.config.long_running_prefixes)

    @staticmethod
    def _command_requires_mining_tools(body: str) -> bool:
        lowered = body.strip().lower()
        return lowered.startswith(("mine ", "tunnel ", "build ", "explore "))

    @staticmethod
    def _classify_active_command(body: str) -> dict[str, Any]:
        lowered = body.strip().lower()
        if lowered.startswith("mine "):
            return {
                "actionCategory": "destructive_mining",
                "actionClass": "destructive_mining",
                "destructiveMiningBoolean": True,
                "pathingBoolean": True,
            }
        if lowered.startswith(("tunnel ", "build ")):
            return {
                "actionCategory": "destructive_mining",
                "actionClass": "destructive_mining",
                "destructiveMiningBoolean": True,
                "pathingBoolean": True,
            }
        if lowered.startswith(("goto ", "explore ")):
            return {
                "actionCategory": "pathing",
                "actionClass": "pathing",
                "destructiveMiningBoolean": False,
                "pathingBoolean": True,
            }
        if lowered.startswith(("bridge.store_to_nearby_chest ", "bridge.swap_to_hotbar ", "bridge.craft_item ")):
            return {
                "actionCategory": "nearby_interaction",
                "actionClass": "nearby_interaction",
                "destructiveMiningBoolean": False,
                "pathingBoolean": False,
            }
        if lowered.startswith("bridge."):
            return {
                "actionCategory": "bridge_aux",
                "actionClass": "nearby_interaction",
                "destructiveMiningBoolean": False,
                "pathingBoolean": False,
            }
        if lowered.startswith("pause"):
            return {
                "actionCategory": "pause",
                "actionClass": "nearby_interaction",
                "destructiveMiningBoolean": False,
                "pathingBoolean": False,
            }
        return {
            "actionCategory": "unknown",
            "actionClass": "nearby_interaction",
            "destructiveMiningBoolean": False,
            "pathingBoolean": False,
        }

    @staticmethod
    def _normalize_action_class(action_class: Any) -> str:
        token = str(action_class or "").strip().lower()
        mapping = {
            "local_in_place": "local_in_place",
            "local_stationary": "nearby_interaction",
            "nearby_interaction": "nearby_interaction",
            "pathing": "pathing",
            "destructive_mining": "destructive_mining",
            "destructive_pathing": "destructive_mining",
            "machine": "machine_processing",
            "machine_processing": "machine_processing",
        }
        return mapping.get(token, "")

    @classmethod
    def _success_criteria_for_action_class(cls, action_class: str) -> str:
        normalized = cls._normalize_action_class(action_class)
        mapping = {
            "local_in_place": "inventory_or_stage_delta",
            "nearby_interaction": "ack_and_local_state_delta",
            "pathing": "pathing_or_movement",
            "destructive_mining": "pathing_or_movement",
            "machine_processing": "inventory_or_machine_state_delta",
        }
        return mapping.get(normalized, "pathing_or_movement")

    @staticmethod
    def _status_inventory_count(status: dict[str, Any] | None, item_id: str) -> int:
        if not isinstance(status, dict):
            return -1
        target = str(item_id).strip().lower()
        if not target:
            return -1
        inventory = status.get("inventory")
        if not isinstance(inventory, list):
            return -1
        total = 0
        for entry in inventory:
            if not isinstance(entry, dict):
                continue
            current = str(entry.get("itemId", "")).strip().lower()
            if current != target:
                continue
            total += max(0, int(entry.get("count", 0) or 0))
        return total

    @staticmethod
    def _known_int(value: Any, *, default: int = -1) -> int:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _active_action_metadata(self, *, command_id: str = "", body: str = "") -> dict[str, Any]:
        fallback = self._classify_active_command(body)
        planner_context = self._planner_context_for_command_id(command_id)
        action_class = self._normalize_action_class(planner_context.get("actionClass")) or str(
            fallback.get("actionClass", "")
        )
        success_criteria = str(planner_context.get("successCriteriaUsed", "")).strip() or self._success_criteria_for_action_class(action_class)
        metadata = dict(fallback)
        metadata.update(
            {
                "actionName": str(planner_context.get("actionName", "")).strip() or body.strip(),
                "actionClass": action_class or str(fallback.get("actionClass", "")),
                "successCriteriaUsed": success_criteria,
                "routeId": str(planner_context.get("routeId", "")).strip(),
                "routeMechanism": str(planner_context.get("routeMechanism", "")).strip(),
                "sourceItem": str(planner_context.get("sourceItem", "")).strip(),
                "targetItem": str(planner_context.get("targetItem", "")).strip(),
                "stageHintBefore": str(planner_context.get("stageHintBefore", "")).strip(),
                "beforeSourceCount": planner_context.get("beforeSourceCount"),
                "beforeTargetCount": planner_context.get("beforeTargetCount"),
                "targetTotal": planner_context.get("targetTotal"),
                "knowledgeEvidence": list(planner_context.get("knowledgeEvidence", []))
                if isinstance(planner_context.get("knowledgeEvidence"), list)
                else [],
            }
        )
        return metadata

    def _record_action_evaluation(self, payload: dict[str, Any], *, success: bool) -> None:
        self._last_action_evaluation = dict(payload)
        rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        if success:
            log.info("action_completion_diagnostic %s", rendered)
        else:
            log.error("action_completion_diagnostic %s", rendered)

    def evaluate_planner_task_context(
        self,
        status: dict[str, Any],
        *,
        movement_observed: bool,
        pathing_observed: bool,
    ) -> dict[str, Any]:
        action_meta = self._active_action_metadata(
            command_id="",
            body=str(self._current_planner_task_context().get("actionName", "")).strip(),
        )
        action_class = self._normalize_action_class(action_meta.get("actionClass"))
        if action_class in {"local_in_place", "nearby_interaction", "machine_processing"}:
            evaluation = self._contextual_action_evaluation(
                status,
                action_meta=action_meta,
                movement_observed=movement_observed,
                pathing_observed=pathing_observed,
            )
        else:
            final_success = bool(movement_observed or pathing_observed)
            evaluation = self._default_action_evaluation(
                status,
                action_meta=action_meta,
                movement_observed=movement_observed,
                pathing_observed=pathing_observed,
                final_success=final_success,
            )
        self._record_action_evaluation(evaluation, success=bool(evaluation.get("finalSuccess", False)))
        return evaluation

    def _contextual_action_evaluation(
        self,
        status: dict[str, Any],
        *,
        action_meta: dict[str, Any],
        movement_observed: bool,
        pathing_observed: bool,
    ) -> dict[str, Any]:
        before_source = self._known_int(action_meta.get("beforeSourceCount"), default=-1)
        before_target = self._known_int(action_meta.get("beforeTargetCount"), default=-1)
        target_total = self._known_int(action_meta.get("targetTotal"), default=-1)
        source_item = str(action_meta.get("sourceItem", "")).strip()
        target_item = str(action_meta.get("targetItem", "")).strip()
        stage_hint_before = str(action_meta.get("stageHintBefore", "")).strip()
        stage_hint_after = str(status.get("stoneblockStageHint", "")).strip()
        after_source = self._status_inventory_count(status, source_item) if source_item else -1
        after_target = self._status_inventory_count(status, target_item) if target_item else -1
        final_success = False
        if target_total >= 0 and after_target >= target_total:
            final_success = True
        elif before_target >= 0 and after_target > before_target:
            final_success = True
        elif before_target < 0 and before_source >= 0 and after_source >= 0 and after_source < before_source:
            final_success = True
        elif before_target < 0 and stage_hint_before and stage_hint_after and stage_hint_after != stage_hint_before:
            final_success = True
        return {
            "actionName": str(action_meta.get("actionName", "")).strip() or self._active_command_body,
            "actionClass": str(action_meta.get("actionClass", "")).strip() or "unknown",
            "successCriteriaUsed": str(action_meta.get("successCriteriaUsed", "")).strip() or "unknown",
            "beforeSourceCount": before_source,
            "afterSourceCount": after_source,
            "beforeTargetCount": before_target,
            "afterTargetCount": after_target,
            "stageHintBefore": stage_hint_before or "unknown",
            "stageHintAfter": stage_hint_after or "unknown",
            "movementObserved": bool(movement_observed),
            "pathingObserved": bool(pathing_observed),
            "finalSuccess": bool(final_success),
            "routeId": str(action_meta.get("routeId", "")).strip(),
            "routeMechanism": str(action_meta.get("routeMechanism", "")).strip(),
            "sourceItem": source_item,
            "targetItem": target_item,
            "knowledgeEvidence": list(action_meta.get("knowledgeEvidence", [])),
        }

    def _default_action_evaluation(
        self,
        status: dict[str, Any],
        *,
        action_meta: dict[str, Any],
        movement_observed: bool,
        pathing_observed: bool,
        final_success: bool,
    ) -> dict[str, Any]:
        return {
            "actionName": str(action_meta.get("actionName", "")).strip() or self._active_command_body,
            "actionClass": str(action_meta.get("actionClass", "")).strip() or "unknown",
            "successCriteriaUsed": str(action_meta.get("successCriteriaUsed", "")).strip() or "pathing_or_movement",
            "beforeSourceCount": self._known_int(action_meta.get("beforeSourceCount"), default=-1),
            "afterSourceCount": self._known_int(action_meta.get("beforeSourceCount"), default=-1),
            "beforeTargetCount": self._known_int(action_meta.get("beforeTargetCount"), default=-1),
            "afterTargetCount": self._known_int(action_meta.get("beforeTargetCount"), default=-1),
            "stageHintBefore": str(action_meta.get("stageHintBefore", "")).strip() or "unknown",
            "stageHintAfter": str(status.get("stoneblockStageHint", "")).strip() or "unknown",
            "movementObserved": bool(movement_observed),
            "pathingObserved": bool(pathing_observed),
            "finalSuccess": bool(final_success),
            "routeId": str(action_meta.get("routeId", "")).strip(),
            "routeMechanism": str(action_meta.get("routeMechanism", "")).strip(),
            "sourceItem": str(action_meta.get("sourceItem", "")).strip(),
            "targetItem": str(action_meta.get("targetItem", "")).strip(),
            "knowledgeEvidence": list(action_meta.get("knowledgeEvidence", [])),
        }

    def _should_auto_torch(self, body: str) -> bool:
        if not self.config.auto_torch_enabled:
            return False
        if self.config.torch_interval_seconds <= 0:
            return False
        return self._matches_prefixes(body, self.config.torch_for_prefixes)

    @staticmethod
    def _status_hotbar(status: dict[str, Any]) -> list[dict[str, Any]]:
        hotbar = status.get("hotbar", [])
        if isinstance(hotbar, list):
            return [h for h in hotbar if isinstance(h, dict)]
        return []

    @staticmethod
    def _status_inventory(status: dict[str, Any]) -> list[dict[str, Any]]:
        inventory = status.get("inventory", [])
        if isinstance(inventory, list):
            return [v for v in inventory if isinstance(v, dict)]
        return []

    @staticmethod
    def _slot_count(status: dict[str, Any], slot: int) -> int:
        for entry in BaritoneBridge._status_hotbar(status):
            if int(entry.get("slot", 0)) == int(slot):
                return int(entry.get("count", 0))
        return 0

    @staticmethod
    def _inventory_slot_count(status: dict[str, Any], inventory_slot: int) -> int:
        for entry in BaritoneBridge._status_inventory(status):
            if int(entry.get("slot", 0)) == int(inventory_slot):
                return int(entry.get("count", 0))
        return 0

    @staticmethod
    def _inventory_entry(status: dict[str, Any], inventory_slot: int) -> dict[str, Any] | None:
        for entry in BaritoneBridge._status_inventory(status):
            if int(entry.get("slot", 0)) == int(inventory_slot):
                return entry
        return None

    @staticmethod
    def _status_selected_slot(status: dict[str, Any]) -> int:
        raw = status.get("selectedHotbarSlot", 0)
        if not isinstance(raw, (int, float)):
            return 0
        slot = int(raw)
        if 1 <= slot <= 9:
            return slot
        return 0

    @staticmethod
    def _is_torch_item_id(item_id: str, exact_ids: set[str]) -> bool:
        lowered = item_id.strip().lower()
        if not lowered:
            return False
        if lowered in exact_ids:
            return True
        return lowered.endswith(":torch") or lowered.endswith("_torch")

    @staticmethod
    def _looks_like_aux_command(command_body: str) -> bool:
        cmd = command_body.strip().lower()
        if not cmd:
            return False
        if cmd in {"pause", "resume", "stop", "cancel"}:
            return True
        return (
            cmd.startswith("set ")
            or cmd.startswith("proc ")
            or cmd.startswith("bridge.swap_to_hotbar ")
            or cmd.startswith("bridge.drop_item ")
            or cmd.startswith("bridge.craft_item ")
            or cmd.startswith("bridge.fight_hostile")
            or cmd.startswith("bridge.store_to_nearby_chest")
            or cmd.startswith("bridge.ultimine ")
        )

    @staticmethod
    def _status_player_can_fly(status: dict[str, Any] | None) -> bool:
        if not isinstance(status, dict):
            return False
        return bool(status.get("mayfly", False)) or bool(status.get("flying", False))

    def _total_torch_count(self, status: dict[str, Any]) -> int:
        torch_ids = {v.strip().lower() for v in self.config.auto_torch_item_ids if str(v).strip()}
        if not torch_ids:
            torch_ids = {"minecraft:torch"}
        total = 0
        found = False
        for entry in self._status_hotbar(status):
            item_id = str(entry.get("itemId", "")).strip().lower()
            if self._is_torch_item_id(item_id, torch_ids):
                found = True
                total += max(0, int(entry.get("count", 0)))
        if not found and "hotbar" not in status:
            return -1
        return total

    def _find_edible_hotbar_slot(self, status: dict[str, Any]) -> int | None:
        selected_slot = self._status_selected_slot(status)
        edible_slots: list[int] = []
        keyword_candidates: list[int] = []
        food_keywords = [k.strip().lower() for k in self.config.food_item_keywords if str(k).strip()]
        for entry in self._status_hotbar(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 9:
                continue
            if int(entry.get("count", 0)) <= 0:
                continue
            if bool(entry.get("edible", False)):
                edible_slots.append(slot)
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if item_id and any(k in item_id for k in food_keywords):
                keyword_candidates.append(slot)
        if selected_slot in edible_slots:
            return selected_slot
        if edible_slots:
            return edible_slots[0]
        if selected_slot in keyword_candidates:
            return selected_slot
        if keyword_candidates:
            return keyword_candidates[0]
        return None

    def _find_edible_inventory_slot(self, status: dict[str, Any]) -> int | None:
        food_keywords = [k.strip().lower() for k in self.config.food_item_keywords if str(k).strip()]
        selected_slot = self._status_selected_slot(status)
        exact_candidates: list[int] = []
        keyword_candidates: list[int] = []
        for entry in self._status_inventory(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 36:
                continue
            if bool(entry.get("inHotbar", False)):
                continue
            if int(entry.get("count", 0)) <= 0:
                continue
            if bool(entry.get("edible", False)):
                exact_candidates.append(slot)
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if item_id and any(k in item_id for k in food_keywords):
                keyword_candidates.append(slot)
        if selected_slot and selected_slot >= 10 and selected_slot in exact_candidates:
            return selected_slot
        if exact_candidates:
            return exact_candidates[0]
        if keyword_candidates:
            return keyword_candidates[0]
        return None

    def _find_torch_inventory_slot(self, status: dict[str, Any]) -> int | None:
        torch_ids = {v.strip().lower() for v in self.config.auto_torch_item_ids if str(v).strip()}
        if not torch_ids:
            torch_ids = {"minecraft:torch"}
        candidates: list[int] = []
        for entry in self._status_inventory(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 36:
                continue
            if bool(entry.get("inHotbar", False)):
                continue
            if int(entry.get("count", 0)) <= 0:
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if self._is_torch_item_id(item_id, torch_ids):
                candidates.append(slot)
        if candidates:
            return candidates[0]
        return None

    def _find_pickaxe_inventory_slot(self, status: dict[str, Any]) -> int | None:
        keywords = [k.strip().lower() for k in self.config.pickaxe_item_keywords if str(k).strip()]
        if not keywords:
            return None
        for entry in self._status_inventory(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 36:
                continue
            if bool(entry.get("inHotbar", False)):
                continue
            if int(entry.get("count", 0)) <= 0:
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if item_id and any(k in item_id for k in keywords):
                return slot
        return None

    def _bridge_swap_to_hotbar(self, source_inventory_slot: int, target_hotbar_slot: int) -> bool:
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        if source_inventory_slot < 1 or source_inventory_slot > 36:
            return False
        if target_hotbar_slot < 1 or target_hotbar_slot > 9:
            return False
        baseline = self.read_bridge_status()
        source_item_id = ""
        source_before = -1
        target_before = -1
        if baseline and self._status_is_fresh(baseline):
            source_entry = self._inventory_entry(baseline, source_inventory_slot)
            if source_entry is not None:
                source_item_id = str(source_entry.get("itemId", "")).strip().lower()
                source_before = int(source_entry.get("count", 0))
            target_before = self._inventory_slot_count(baseline, target_hotbar_slot)

        try:
            self._send_aux_command(f"bridge.swap_to_hotbar {source_inventory_slot} {target_hotbar_slot}")
        except Exception as exc:
            log.warning(
                "Inventory swap command failed source=%d target=%d: %s",
                source_inventory_slot,
                target_hotbar_slot,
                exc,
            )
            return False

        deadline = time.time() + 2.0
        while time.time() < deadline:
            refreshed = self.read_bridge_status()
            if refreshed and self._status_is_fresh(refreshed):
                entry = self._inventory_entry(refreshed, target_hotbar_slot)
                source_after = self._inventory_slot_count(refreshed, source_inventory_slot)
                if entry is not None and int(entry.get("count", 0)) > 0:
                    target_item_id = str(entry.get("itemId", "")).strip().lower()
                    if source_item_id and target_item_id == source_item_id:
                        return True
                    if source_before >= 0 and source_after < source_before:
                        return True
                    if target_before >= 0 and int(entry.get("count", 0)) != target_before:
                        return True
            time.sleep(0.1)
        return False

    def _ensure_edible_hotbar_slot(self, status: dict[str, Any]) -> int | None:
        slot = self._find_edible_hotbar_slot(status)
        if slot is not None:
            return slot
        inv_slot = self._find_edible_inventory_slot(status)
        if inv_slot is None:
            return None
        target_slot = int(self.config.food_hotbar_slot)
        if target_slot < 1 or target_slot > 9:
            target_slot = 8
        if self._bridge_swap_to_hotbar(inv_slot, target_slot):
            refreshed = self.read_bridge_status()
            if refreshed and self._status_is_fresh(refreshed):
                slot = self._find_edible_hotbar_slot(refreshed)
                if slot is not None:
                    return slot
            return target_slot
        return None

    def _ensure_torch_hotbar_slot(self, status: dict[str, Any] | None) -> int | None:
        if status is None:
            return self._find_torch_hotbar_slot(None)
        slot = self._find_torch_hotbar_slot(status)
        if slot is not None:
            return slot
        inv_slot = self._find_torch_inventory_slot(status)
        if inv_slot is None:
            return None
        target = int(self.config.torch_hotbar_slot)
        if target < 1 or target > 9:
            target = 9
        if self._bridge_swap_to_hotbar(inv_slot, target):
            refreshed = self.read_bridge_status()
            if refreshed and self._status_is_fresh(refreshed):
                slot = self._find_torch_hotbar_slot(refreshed)
                if slot is not None:
                    return slot
            return target
        return None

    def _ensure_pickaxe_hotbar(self, status: dict[str, Any]) -> bool:
        pickaxe_count = self._hotbar_pickaxe_count(status)
        required_pickaxes = max(0, int(self.config.min_pickaxe_count))
        if pickaxe_count < 0 or pickaxe_count >= required_pickaxes:
            return True
        inv_slot = self._find_pickaxe_inventory_slot(status)
        if inv_slot is None:
            return False
        target = int(self.config.pickaxe_hotbar_slot)
        if target < 1 or target > 9:
            target = 1
        return self._bridge_swap_to_hotbar(inv_slot, target)

    def _find_torch_hotbar_slot(self, status: dict[str, Any] | None) -> int | None:
        if status:
            selected_slot = self._status_selected_slot(status)
            torch_ids = {v.strip().lower() for v in self.config.auto_torch_item_ids if str(v).strip()}
            if not torch_ids:
                torch_ids = {"minecraft:torch"}
            exact_slots: list[int] = []
            for entry in self._status_hotbar(status):
                slot = int(entry.get("slot", 0))
                if slot < 1 or slot > 9:
                    continue
                if int(entry.get("count", 0)) <= 0:
                    continue
                item_id = str(entry.get("itemId", "")).strip().lower()
                if self._is_torch_item_id(item_id, torch_ids):
                    exact_slots.append(slot)
            if selected_slot in exact_slots:
                self._last_known_torch_slot = selected_slot
                return selected_slot
            if exact_slots:
                self._last_known_torch_slot = exact_slots[0]
                return exact_slots[0]
        if self._last_known_torch_slot and 1 <= self._last_known_torch_slot <= 9:
            return self._last_known_torch_slot
        if status and "hotbar" in status:
            # Telemetry is available and no torch was found; avoid blind placement on a wrong slot.
            return None
        slot = int(self.config.torch_hotbar_slot)
        if 1 <= slot <= 9:
            return slot
        return None

    def _ensure_torch_stock(self, status: dict[str, Any] | None) -> None:
        if not self.config.auto_torch_craft_if_missing:
            return
        if status is None:
            return
        now = time.time()
        if now - self._last_torch_craft_attempt_at < max(8.0, self.config.torch_interval_seconds):
            return
        torch_count = self._total_torch_count(status)
        if torch_count < 0:
            return
        if torch_count >= max(1, self.config.auto_torch_min_count):
            return
        craft_cmd = self.config.auto_torch_craft_command.strip()
        if not craft_cmd:
            return
        if not self._aux_command_supported(craft_cmd, status=status):
            return

        self._last_torch_craft_attempt_at = now
        log.warning("Torch stock low (%d). Attempting: %s", torch_count, craft_cmd)
        resumed = False
        try:
            self._chat_control_command("pause")
            sent = self._send_aux_command(craft_cmd)
            if not sent:
                return
            self._chat_control_command("resume")
            resumed = True
        except Exception as exc:
            log.warning("Torch auto-craft failed: %s", exc)
        finally:
            if not resumed:
                self._chat_control_command("resume")

    def _hotbar_pickaxe_count(self, status: dict[str, Any]) -> int:
        if "hotbar" not in status:
            return -1
        keywords = [k.strip().lower() for k in self.config.pickaxe_item_keywords if str(k).strip()]
        if not keywords:
            return 0
        total = 0
        for entry in self._status_hotbar(status):
            if int(entry.get("count", 0)) <= 0:
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if any(k in item_id for k in keywords):
                total += int(entry.get("count", 1))
        return total

    def _main_hand_pickaxe_low_durability(self, status: dict[str, Any]) -> bool:
        durability_threshold = max(0, int(self.config.min_main_hand_durability))
        if durability_threshold <= 0:
            return False
        remaining = int(status.get("mainHandRemainingDurability", -1))
        if remaining < 0:
            return False
        item_id = str(status.get("mainHandItem", "")).strip().lower()
        keywords = [k.strip().lower() for k in self.config.pickaxe_item_keywords if str(k).strip()]
        if keywords and item_id and not any(k in item_id for k in keywords):
            return False
        return remaining < durability_threshold

    @staticmethod
    def _hostile_snapshot(status: dict[str, Any]) -> tuple[str, float] | None:
        try:
            hostile_dist = float(status.get("nearestHostileDistance", -1.0))
        except (TypeError, ValueError):
            return None
        if hostile_dist < 0.0:
            return None
        hostile_type = str(status.get("nearestHostileType", "")).strip().lower() or "hostile_mob"
        return hostile_type, hostile_dist

    @staticmethod
    def _hostile_simple_name(hostile_type: str) -> str:
        token = hostile_type.strip().lower()
        if not token:
            return "hostile"
        if ":" in token:
            token = token.split(":", 1)[1]
        token = token.replace(" ", "_")
        token = "".join(ch for ch in token if ch.isalnum() or ch == "_")
        return token or "hostile"

    def _combat_ready(self, status: dict[str, Any]) -> bool:
        health = float(status.get("health", 20.0))
        food = int(status.get("foodLevel", 20))
        if health < float(self.config.fight_min_health):
            return False
        if food < int(self.config.fight_min_food_level):
            return False
        if bool(status.get("onFire", False)):
            return False
        if bool(status.get("inLava", False)):
            return False
        return True

    def _render_combat_commands(self, hostile_type: str) -> list[str]:
        hostile_id = hostile_type.strip().lower() or "hostile_mob"
        hostile_simple = self._hostile_simple_name(hostile_id)
        rendered: list[str] = []
        for template in self.config.fight_command_templates:
            raw = str(template).strip()
            if not raw:
                continue
            cmd = (
                raw.replace("{hostile_type}", hostile_id)
                .replace("{hostile_id}", hostile_id)
                .replace("{hostile_simple}", hostile_simple)
                .strip()
            )
            if cmd and cmd not in rendered:
                rendered.append(cmd)
        return rendered

    @staticmethod
    def _parse_version_triplet(raw: str) -> tuple[int, int, int]:
        cleaned = (raw or "").strip().lower().lstrip("v")
        parts = cleaned.split(".")
        nums: list[int] = []
        for p in parts[:3]:
            match = re.match(r"(\d+)", p.strip())
            token = match.group(1) if match else ""
            if not token:
                nums.append(0)
            else:
                nums.append(int(token))
        while len(nums) < 3:
            nums.append(0)
        return nums[0], nums[1], nums[2]

    @staticmethod
    def _status_bridge_capabilities(status: dict[str, Any]) -> set[str]:
        raw_caps = status.get("bridgeCapabilities", [])
        if not isinstance(raw_caps, list):
            return set()
        capabilities: set[str] = set()
        for raw in raw_caps:
            token = str(raw).strip().lower()
            if token:
                capabilities.add(token)
        return capabilities

    def _bridge_supports_command(
        self,
        status: dict[str, Any],
        command_name: str,
        min_version: tuple[int, int, int],
    ) -> bool:
        command_token = str(command_name).strip().lower()
        if command_token and command_token in self._status_bridge_capabilities(status):
            return True
        version = str(status.get("bridgeVersion", "")).strip()
        if not version:
            return False
        return self._parse_version_triplet(version) >= min_version

    def _bridge_supports_fight_hostile(self, status: dict[str, Any]) -> bool:
        return self._bridge_supports_command(status, "bridge.fight_hostile", (0, 1, 2))

    def _bridge_supports_drop_item(self, status: dict[str, Any]) -> bool:
        return self._bridge_supports_command(status, "bridge.drop_item", (0, 1, 5))

    def _bridge_supports_store_to_nearby_chest(self, status: dict[str, Any]) -> bool:
        return self._bridge_supports_command(status, "bridge.store_to_nearby_chest", (0, 1, 6))

    def _bridge_supports_craft_item(self, status: dict[str, Any]) -> bool:
        return self._bridge_supports_command(status, "bridge.craft_item", (0, 1, 4))

    def _bridge_supports_ultimine(self, status: dict[str, Any]) -> bool:
        return self._bridge_supports_command(status, "bridge.ultimine", (0, 1, 8))

    @staticmethod
    def _status_free_inventory_slots(status: dict[str, Any]) -> int:
        slots = 0
        for entry in BaritoneBridge._status_inventory(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 36:
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            count = int(entry.get("count", 0))
            if not item_id or count <= 0:
                slots += 1
        return slots

    @staticmethod
    def _matches_any_keyword(item_id: str, keywords: list[str]) -> bool:
        token = str(item_id).strip().lower()
        if not token:
            return False
        for keyword in keywords:
            probe = str(keyword).strip().lower()
            if probe and probe in token:
                return True
        return False

    def push_inventory_cleanup_protection(self, item_ids: list[str] | set[str]) -> set[str]:
        previous = set(self._inventory_cleanup_protected_item_ids)
        for raw in item_ids:
            token = str(raw).strip().lower()
            if token:
                self._inventory_cleanup_protected_item_ids.add(token)
        return previous

    def restore_inventory_cleanup_protection(self, previous: set[str]) -> None:
        self._inventory_cleanup_protected_item_ids = set(previous)

    def _inventory_item_protected(self, item_id: str) -> bool:
        token = str(item_id).strip().lower()
        if not token:
            return True
        if token in self._inventory_cleanup_protected_item_ids:
            return True
        keep_keywords = [k for k in self.config.inventory_keep_item_keywords if str(k).strip()]
        if self._matches_any_keyword(token, keep_keywords):
            return True
        if self._matches_any_keyword(token, self.config.pickaxe_item_keywords):
            return True
        if self._matches_any_keyword(token, self.config.food_item_keywords):
            return True
        torch_ids = {v.strip().lower() for v in self.config.auto_torch_item_ids if str(v).strip()}
        if self._is_torch_item_id(token, torch_ids):
            return True
        return False

    def _inventory_drop_rule_name(self, item_id: str) -> str:
        token = str(item_id).strip().lower()
        if not token:
            return ""
        exact_drop = {v.strip().lower() for v in self.config.inventory_drop_junk_item_ids if str(v).strip()}
        if token in exact_drop:
            return f"exact:{token}"
        for keyword in [v.strip().lower() for v in self.config.inventory_drop_junk_keywords if str(v).strip()]:
            if keyword and keyword in token:
                return f"keyword:{keyword}"
        return ""

    def _inventory_drop_candidates(self, status: dict[str, Any]) -> list[tuple[int, str, int, str]]:
        exact_drop = {v.strip().lower() for v in self.config.inventory_drop_junk_item_ids if str(v).strip()}
        keyword_drop = [v.strip().lower() for v in self.config.inventory_drop_junk_keywords if str(v).strip()]
        non_hotbar: list[tuple[int, str, int, str]] = []
        hotbar: list[tuple[int, str, int, str]] = []
        for entry in self._status_inventory(status):
            slot = int(entry.get("slot", 0))
            if slot < 1 or slot > 36:
                continue
            count = int(entry.get("count", 0))
            if count <= 0:
                continue
            item_id = str(entry.get("itemId", "")).strip().lower()
            if not item_id:
                continue
            if self._inventory_item_protected(item_id):
                continue
            is_exact = item_id in exact_drop
            is_keyword = self._matches_any_keyword(item_id, keyword_drop)
            if not (is_exact or is_keyword):
                continue
            rule_name = self._inventory_drop_rule_name(item_id)
            if not rule_name:
                continue
            row = (slot, item_id, count, rule_name)
            if bool(entry.get("inHotbar", False)):
                # Only allow hotbar drops for explicit exact junk ids.
                if is_exact:
                    hotbar.append(row)
            else:
                non_hotbar.append(row)
        non_hotbar.sort(key=lambda t: t[0])
        hotbar.sort(key=lambda t: t[0])
        return non_hotbar + hotbar

    def _bridge_drop_inventory_slot(self, inventory_slot: int, *, count: int = 0, drop_all: bool = True) -> bool:
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        if inventory_slot < 1 or inventory_slot > 36:
            return False
        if drop_all:
            cmd = f"bridge.drop_item {inventory_slot} all"
        else:
            cmd = f"bridge.drop_item {inventory_slot} {max(1, int(count))}"
        try:
            return self._send_aux_command(cmd)
        except Exception as exc:
            log.warning("Inventory drop command failed '%s': %s", cmd, exc)
            return False

    def try_inventory_cleanup(self, status: dict[str, Any], *, context: str = "") -> bool:
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        if not self.config.inventory_cleanup_enabled:
            return False
        if not self._status_is_fresh(status):
            return False
        if self._drop_supported is False:
            return False
        context_token = str(context).strip().lower()
        active_body = str(self._active_command_body).strip().lower()
        active_long_running = bool(active_body) and self.is_long_running_command(active_body)
        allow_passive_cleanup = (
            context_token.startswith("idle")
            or context_token.startswith("pre_task")
            or context_token.startswith("store_")
            or context_token.startswith("recovery")
        )
        if not active_long_running and not allow_passive_cleanup:
            return False

        if self._drop_supported is None:
            version = str(status.get("bridgeVersion", "")).strip()
            if not version:
                # Unknown version is treated conservatively, but not cached as unsupported.
                return False
            if self._parse_version_triplet(version) < (0, 1, 5):
                self._drop_supported = False
                log.warning(
                    "Bridge version '%s' does not support bridge.drop_item. "
                    "Inventory cleanup disabled until restart.",
                    version,
                )
                return False
            self._drop_supported = True

        now = time.time()
        cooldown = max(0.5, float(self.config.inventory_cleanup_cooldown_seconds))
        if now - self._last_inventory_cleanup_at < cooldown:
            return False

        free_slots = self._status_free_inventory_slots(status)
        threshold = max(0, int(self.config.inventory_full_free_slots_threshold))
        if free_slots > threshold:
            return False

        max_drop = max(1, int(self.config.inventory_max_stacks_per_cleanup))
        target_free_slots = max(
            threshold + 1,
            int(self.config.inventory_cleanup_target_free_slots),
        )
        target_free_slots = min(36, target_free_slots)

        candidates = self._inventory_drop_candidates(status)
        if not candidates:
            log.warning(
                "Inventory cleanup needed (%d free slots) but no junk candidates were eligible",
                free_slots,
            )
            self._last_inventory_cleanup_at = now
            return False

        dropped = 0
        improved = False
        was_pathing = bool(status.get("isPathing", False))
        if was_pathing:
            self.pause_pathing()
        try:
            current_status = status
            current_free = free_slots
            for slot, item_id, item_count, rule_name in candidates:
                if dropped >= max_drop:
                    break
                if current_free >= target_free_slots:
                    break
                log.warning("Junk-drop fallback engaged for %s (rule: %s)", item_id, rule_name)
                if not self._bridge_drop_inventory_slot(slot, drop_all=True):
                    continue
                dropped += 1
                time.sleep(0.12)
                refreshed = self.read_bridge_status()
                if refreshed and self._status_is_fresh(refreshed):
                    current_status = refreshed
                    refreshed_free = self._status_free_inventory_slots(current_status)
                    if refreshed_free > current_free:
                        improved = True
                    current_free = refreshed_free
                log.warning(
                    "Inventory cleanup (%s): dropped %s x%d from slot %d (free=%d/%d)",
                    context or "runtime",
                    item_id,
                    item_count,
                    slot,
                    current_free,
                    target_free_slots,
                )
            if improved:
                return True
            return False
        finally:
            self._last_inventory_cleanup_at = time.time()
            if was_pathing:
                self.resume_pathing()

    def try_hostile_combat(self, status: dict[str, Any], *, context: str = "") -> bool:
        if self.config.transport.strip().lower() != "bridge_file":
            return False
        if not self.config.fight_hostiles:
            return False
        if self._combat_supported is False:
            return False
        if self._combat_supported is None:
            version = str(status.get("bridgeVersion", "")).strip()
            if not version:
                # Unknown version is treated conservatively, but not cached as unsupported.
                return False
            if self._parse_version_triplet(version) < (0, 1, 2):
                self._combat_supported = False
                log.warning(
                    "Bridge version '%s' does not support bridge.fight_hostile. "
                    "Using hostile avoidance only.",
                    version,
                )
                return False
            self._combat_supported = True

        hostile = self._hostile_snapshot(status)
        if hostile is None:
            return False
        hostile_type, hostile_dist = hostile
        trigger_distance = max(0.5, float(self.config.fight_hostile_max_distance))
        if hostile_dist > trigger_distance:
            return False

        now = time.time()
        cooldown = max(0.5, float(self.config.fight_attempt_cooldown_seconds))
        if now - self._last_combat_attempt_at < cooldown:
            return False
        self._last_combat_attempt_at = now

        if not self._combat_ready(status):
            log.warning(
                "Combat skipped (%s): hostile '%s' at %.1fm but survival state is unsafe",
                context or "runtime",
                hostile_type,
                hostile_dist,
            )
            try:
                self.pause_pathing()
                time.sleep(max(0.05, float(self.config.fight_pause_seconds)))
                if self.config.auto_eat_enabled:
                    self._attempt_auto_eat(status)
                self._send_aux_command("set mobAvoidanceCoefficient 3.5")
                self._send_aux_command("set mobAvoidanceRadius 24")
                self._send_aux_command("stop")
            except Exception as exc:
                log.warning("Combat disengage failed: %s", exc)
            finally:
                self.resume_pathing()
            return False

        commands = self._render_combat_commands(hostile_type)
        if not commands:
            return False

        self.pause_pathing()
        try:
            time.sleep(max(0.05, float(self.config.fight_pause_seconds)))
            for cmd in commands:
                try:
                    self._send_aux_command(cmd)
                    self._combat_supported = True
                    log.warning(
                        "Combat engage (%s): %s [hostile='%s' dist=%.1fm]",
                        context or "runtime",
                        cmd,
                        hostile_type,
                        hostile_dist,
                    )
                    return True
                except Exception as exc:
                    log.debug("Combat command failed '%s': %s", cmd, exc)
            self._combat_supported = False
            log.warning(
                "All combat command templates were rejected. Falling back to hostile avoidance until restart."
            )
            return False
        finally:
            self.resume_pathing()

    def _attempt_tool_recovery(self, reason: str) -> bool:
        commands = [c.strip() for c in self.config.tool_recovery_commands if str(c).strip()]
        if self.config.transport.strip().lower() == "bridge_file":
            status_for_filter = self.read_bridge_status()
            commands = [c for c in commands if self._aux_command_supported(c, status=status_for_filter)]
        if not commands and self.config.transport.strip().lower() != "bridge_file":
            return False
        now = time.time()
        if now - self._last_tool_recovery_at < 8.0:
            return False
        self._last_tool_recovery_at = now
        log.warning("Tool recovery triggered: %s", reason)
        try:
            self.pause_pathing()
            if self.config.transport.strip().lower() == "bridge_file":
                status = self.read_bridge_status()
                if status and self._status_is_fresh(status):
                    if self._ensure_pickaxe_hotbar(status) and self._tooling_looks_recovered(timeout_seconds=2.0):
                        return True
            for cmd in commands:
                try:
                    sent = self._send_aux_command(cmd)
                    if not sent:
                        continue
                    log.info("Tool recovery command sent: %s", cmd)
                    if self._tooling_looks_recovered(timeout_seconds=4.0):
                        return True
                    log.warning("Tool recovery command did not restore usable pickaxe yet: %s", cmd)
                except Exception as exc:
                    log.warning("Tool recovery command failed '%s': %s", cmd, exc)
            return False
        finally:
            self.resume_pathing()

    def _tooling_looks_recovered(self, timeout_seconds: float = 4.0) -> bool:
        if self.config.transport.strip().lower() != "bridge_file":
            return True
        deadline = time.time() + max(0.5, timeout_seconds)
        required_pickaxes = max(0, self.config.min_pickaxe_count)
        while time.time() < deadline:
            status = self.read_bridge_status()
            if status and self._status_is_fresh(status):
                pickaxe_count = self._hotbar_pickaxe_count(status)
                low_durability = self._main_hand_pickaxe_low_durability(status)
                has_enough_pickaxes = pickaxe_count < 0 or pickaxe_count >= required_pickaxes
                if has_enough_pickaxes and not low_durability:
                    return True
            time.sleep(0.15)
        return False

    def _attempt_auto_eat(self, status: dict[str, Any]) -> bool:
        if not self.config.auto_eat_enabled:
            return False
        now = time.time()
        if now - self._last_eat_attempt_at < max(0.5, self.config.eat_attempt_cooldown_seconds):
            return False

        raw_food_level = status.get("foodLevel")
        food_known = isinstance(raw_food_level, (int, float))
        food_level = int(raw_food_level) if food_known else 20
        raw_health = status.get("health")
        raw_max_health = status.get("maxHealth")
        health_known = isinstance(raw_health, (int, float)) and isinstance(raw_max_health, (int, float))
        health_needs_regen = bool(health_known and float(raw_max_health) > 0.0 and float(raw_health) + 0.25 < float(raw_max_health))
        if food_known:
            low_food = food_level < max(0, int(self.config.auto_eat_food_below))
            regen_food_window = health_needs_regen and food_level < 20
            if not (low_food or regen_food_window):
                return False
        elif now - self._last_eat_attempt_at < max(1.0, self.config.eat_fallback_interval_seconds):
            return False
        slot = self._ensure_edible_hotbar_slot(status)
        if slot is None:
            if food_known:
                log.warning("Food is low (%d) but no edible item is visible in hotbar", food_level)
            else:
                log.warning("Auto-eat fallback skipped: no edible item visible in hotbar")
            self._last_eat_attempt_at = now
            return False

        self._last_eat_attempt_at = now
        before_count = self._slot_count(status, slot)
        self.pause_pathing()
        try:
            self.controller.consume_hotbar_slot(slot, hold_seconds=self.config.eat_hold_seconds)
        except Exception as exc:
            log.warning("Auto-eat failed on slot %d: %s", slot, exc)
            self.resume_pathing()
            return False

        ate = False
        deadline = time.time() + max(1.0, self.config.eat_hold_seconds + 1.0)
        while time.time() < deadline:
            refreshed = self.read_bridge_status()
            if refreshed and self._status_is_fresh(refreshed):
                updated_food_raw = refreshed.get("foodLevel")
                if isinstance(updated_food_raw, (int, float)):
                    updated_food = int(updated_food_raw)
                    if updated_food > food_level:
                        ate = True
                        break
                if before_count >= 0:
                    updated_count = self._slot_count(refreshed, slot)
                    if 0 <= updated_count < before_count:
                        ate = True
                        break
            time.sleep(0.15)
        self.resume_pathing()
        if ate:
            log.info("Auto-eat succeeded: food %d -> higher via slot %d", food_level, slot)
            return True
        if not food_known:
            log.info("Auto-eat fallback action issued from slot %d (no hunger telemetry)", slot)
            return True
        if food_known:
            log.warning("Auto-eat attempted but hunger did not increase")
        else:
            log.warning("Auto-eat attempted on fallback path without telemetry confirmation")
        return ate

    def try_auto_eat(self, status: dict[str, Any]) -> bool:
        return self._attempt_auto_eat(status)

    def _maybe_place_torch(self, is_pathing: bool, status: dict[str, Any] | None = None) -> None:
        if not is_pathing:
            return
        if self._next_torch_at <= 0.0:
            return
        now = time.time()
        if now < self._next_torch_at:
            return
        self._ensure_torch_stock(status)
        refreshed_after_stock = self.read_bridge_status()
        if refreshed_after_stock and self._status_is_fresh(refreshed_after_stock):
            status = refreshed_after_stock

        preferred_slot = self._ensure_torch_hotbar_slot(status)
        candidates: list[int] = []
        if preferred_slot is not None:
            candidates.append(preferred_slot)
        cfg_slot = int(self.config.torch_hotbar_slot)
        blind_cfg_fallback_allowed = status is None or "hotbar" not in status
        if blind_cfg_fallback_allowed and 1 <= cfg_slot <= 9 and cfg_slot not in candidates:
            candidates.append(cfg_slot)
        if not candidates:
            self._next_torch_at = now + max(2.0, self.config.torch_interval_seconds)
            log.warning("Torch placement skipped: no valid hotbar torch slot")
            return

        placed = False
        for slot in candidates[:2]:
            resumed = False
            before_count = self._slot_count(status, slot) if status is not None else -1
            try:
                self.pause_pathing()
                time.sleep(0.12)
                self.controller.place_torch(slot)
                self.resume_pathing()
                resumed = True
                self._last_known_torch_slot = slot

                refreshed = self.read_bridge_status()
                if refreshed and self._status_is_fresh(refreshed) and before_count >= 0:
                    after_count = self._slot_count(refreshed, slot)
                    if after_count >= 0 and after_count < before_count:
                        placed = True
                        log.info("Torch placed from slot %d (%d -> %d)", slot, before_count, after_count)
                        break
                    log.warning("Torch verify uncertain on slot %d (%d -> %d)", slot, before_count, after_count)
                else:
                    placed = True
                    log.info("Torch placement action issued from hotbar slot %d", slot)
                    break
            except Exception as exc:
                log.warning("Torch placement failed on slot %d: %s", slot, exc)
            finally:
                if not resumed:
                    self.resume_pathing()

        if not placed:
            log.warning("Torch placement attempts exhausted with no confirmed placement")
            self._next_torch_at = now + max(3.0, self.config.torch_interval_seconds * 0.5)
            return
        self._next_torch_at = now + max(2.0, self.config.torch_interval_seconds)

    def safety_reasons(self, status: dict[str, Any], consider_players: bool) -> list[str]:
        if not self.config.enable_safety_monitoring:
            return []
        reasons: list[str] = []

        if "health" in status:
            health = float(status.get("health", 20.0))
            max_health = float(status.get("maxHealth", 0.0))
            health_known = max_health > 0.0 and health >= 0.0
            if health_known and health < self.config.min_health:
                reasons.append(f"health {health:.1f} < {self.config.min_health:.1f}")
        if "foodLevel" in status:
            food = int(status.get("foodLevel", 20))
            if food < self.config.min_food_level:
                reasons.append(f"food {food} < {self.config.min_food_level}")
        if "airSupply" in status:
            air = int(status.get("airSupply", 300))
            if air < self.config.min_air_supply:
                reasons.append(f"air {air} < {self.config.min_air_supply}")

        if self.config.pause_on_fire and bool(status.get("onFire", False)):
            reasons.append("player on fire")
        if self.config.pause_in_lava and bool(status.get("inLava", False)):
            reasons.append("player in lava")

        hostile = self._hostile_snapshot(status)
        if self.config.avoid_hostiles and hostile is not None:
            hostile_type, hostile_dist = hostile
            if hostile_dist < self.config.nearby_hostile_min_distance:
                engage_threshold = max(0.5, float(self.config.fight_hostile_max_distance))
                can_engage = (
                    self.config.fight_hostiles
                    and self._combat_supported is not False
                    and hostile_dist <= engage_threshold
                    and self._combat_ready(status)
                )
                if not can_engage:
                    reasons.append(
                        f"nearby hostile '{hostile_type}' at {hostile_dist:.1f}m (< {self.config.nearby_hostile_min_distance:.1f}m)"
                    )

        if consider_players and self.config.avoid_players_while_mining:
            nearest = float(status.get("nearestOtherPlayerDistance", -1.0))
            if nearest >= 0.0 and nearest < self.config.nearby_player_min_distance:
                name = str(status.get("nearestOtherPlayerName", "")).strip() or "unknown"
                reasons.append(
                    f"nearby player '{name}' at {nearest:.1f}m (< {self.config.nearby_player_min_distance:.1f}m)"
                )

        return reasons

    def _apply_pre_mining_commands(self, body: str) -> None:
        if not self.is_long_running_command(body):
            return
        if self.config.transport.strip().lower() == "bridge_file":
            return
        for pre_cmd in self.config.pre_mining_commands:
            cmd = str(pre_cmd).strip()
            if not cmd:
                continue
            if self._is_persistent_aux_command(cmd) and cmd.lower() in self._persistent_aux_commands_applied:
                continue
            try:
                sent = self._send_aux_command(cmd)
                if sent and self._is_persistent_aux_command(cmd):
                    self._persistent_aux_commands_applied.add(cmd.lower())
            except Exception as exc:
                log.warning("Pre-mining command failed '%s': %s", cmd, exc)

    def command(self, body: str) -> None:
        command_body = body.strip()
        if not command_body:
            return
        self._disable_ultimine_after_command(context="new_command_preflight")
        self._validate_command_arity(command_body)
        if not self._aux_command_supported(command_body):
            transport = self.config.transport.strip().lower()
            raise RuntimeError(f"unsupported command for transport '{transport}': {command_body}")
        self._apply_pre_mining_commands(command_body)

        self._active_command_body = command_body
        self._active_command_started_at = time.time()
        self._aux_command_ids.clear()
        self._stuck_recovery_attempts = 0
        if self._should_auto_torch(command_body):
            self._next_torch_at = time.time() + max(0.5, self.config.torch_initial_delay_seconds)
        else:
            self._next_torch_at = 0.0

        ultimine_enabled = False
        try:
            ultimine_enabled = self._try_enable_ultimine_for_command(command_body)
            transport = self.config.transport.strip().lower()
            if transport == "bridge_file":
                self._bridge_command(command_body, track_active=True)
                return
            self.controller.send_baritone(command_body)
            self._active_command_id = None
        except Exception:
            if ultimine_enabled:
                self._disable_ultimine_after_command(context="command_error")
            raise

    @staticmethod
    def _status_position(status: dict[str, Any]) -> tuple[float, float, float] | None:
        keys = ("posX", "posY", "posZ")
        if not all(k in status for k in keys):
            return None
        try:
            return (
                float(status.get("posX", 0.0)),
                float(status.get("posY", 0.0)),
                float(status.get("posZ", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _distance_sq(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        dz = a[2] - b[2]
        return dx * dx + dy * dy + dz * dz

    def run_recovery_playbook(
        self,
        context: str,
        status: dict[str, Any] | None = None,
        reissue_active_command: bool = False,
    ) -> bool:
        pause_seconds = max(0.1, float(self.config.stuck_recovery_pause_seconds))
        log.warning("Running recovery playbook: %s", context)
        recovered = False
        try:
            self.pause_pathing()
            time.sleep(pause_seconds)
            current_status = status
            refreshed = self.read_bridge_status()
            if refreshed and self._status_is_fresh(refreshed):
                current_status = refreshed

            if isinstance(current_status, dict):
                if self.config.auto_eat_enabled:
                    try:
                        self._attempt_auto_eat(current_status)
                    except Exception as exc:
                        log.warning("Recovery auto-eat step failed: %s", exc)
                if self.config.fight_hostiles:
                    try:
                        self.try_hostile_combat(current_status, context=f"recovery:{context}")
                    except Exception as exc:
                        log.warning("Recovery combat step failed: %s", exc)
                try:
                    self._ensure_pickaxe_hotbar(current_status)
                except Exception as exc:
                    log.warning("Recovery pickaxe swap step failed: %s", exc)
                try:
                    self._ensure_torch_hotbar_slot(current_status)
                except Exception as exc:
                    log.warning("Recovery torch swap step failed: %s", exc)
                if self.config.inventory_cleanup_enabled:
                    try:
                        self.try_inventory_cleanup(current_status, context=f"recovery:{context}")
                    except Exception as exc:
                        log.warning("Recovery inventory cleanup step failed: %s", exc)

            self._send_aux_command("stop")
            time.sleep(max(0.1, pause_seconds * 0.5))
            if reissue_active_command and self._active_command_body.strip():
                body = self._active_command_body.strip()
                if self.config.transport.strip().lower() == "bridge_file":
                    self._bridge_command(body, track_active=True)
                else:
                    self.controller.send_baritone(body)
            recovered = True
            return True
        except Exception as exc:
            log.warning("Recovery playbook '%s' failed: %s", context, exc)
            return False
        finally:
            if recovered:
                log.info("Recovery playbook succeeded: %s", context)
            self.resume_pathing()

    def _wait_for_idle_chat(self, timeout: float, poll: float) -> bool:
        deadline = time.time() + timeout
        success_hits = 0
        min_conf = self.config.min_phrase_confidence
        required_hits = max(1, self.config.required_consecutive_matches)

        while time.time() < deadline:
            self._sync_manual_pause_with_controller()
            try:
                self.controller.wait_if_paused()
            except RuntimeError:
                return False
            self._sync_manual_pause_with_controller()
            obs = self.perception.observe()
            combined = " ".join([obs.chat_text, obs.full_text_hint]).strip()
            conf = max(obs.chat_confidence, obs.full_confidence)

            if conf >= min_conf and self.perception.contains_any(combined, self.config.failure_phrases):
                log.error("Detected baritone failure text: %s", combined)
                return False
            lowered = combined.lower()
            if conf >= min_conf and ("too many arguments" in lowered or "usage:" in lowered):
                log.error("Detected command arity failure text: %s", combined)
                return False

            if conf >= min_conf and self.perception.contains_any(combined, self.config.idle_success_phrases):
                success_hits += 1
                if success_hits >= required_hits:
                    return True
            else:
                success_hits = 0

            self._maybe_place_torch(is_pathing=True)

            if self.controller.stop_requested:
                log.warning("Stop requested while waiting for idle")
                return False

            time.sleep(max(0.1, poll))

        log.error("Timeout waiting for baritone idle state")
        return False

    def _wait_for_idle_bridge(self, timeout: float, poll: float) -> bool:
        command_id = self._active_command_id
        if not command_id:
            return True

        long_running = self.is_long_running_command(self._active_command_body)
        require_pathing_transition = long_running and self.config.require_pathing_transition_for_long_commands
        seen_pathing = False
        seen_matching_status = False
        accepted = False
        startup_deadline = self._active_command_started_at + max(0.5, self.config.long_command_startup_seconds)
        deadline = time.time() + timeout
        last_status: dict[str, Any] = {}
        last_fresh_status_at = time.time()
        command_start_pos: tuple[float, float, float] | None = None
        last_progress_pos: tuple[float, float, float] | None = None
        last_progress_at = 0.0
        max_distance_from_start_sq = 0.0
        accepted_without_ack_logged = False

        while time.time() < deadline:
            if self.controller.stop_requested:
                log.warning("Stop requested while waiting for bridge idle")
                return False

            self._sync_manual_pause_with_controller()
            try:
                self.controller.wait_if_paused()
            except RuntimeError:
                return False
            self._sync_manual_pause_with_controller()

            status = self.read_bridge_status()
            if not status:
                if time.time() - last_fresh_status_at > max(2.0, self.config.bridge_unresponsive_after_seconds):
                    raise RuntimeError(
                        f"bridge unresponsive: no status updates for {time.time() - last_fresh_status_at:.1f}s"
                    )
                time.sleep(max(0.1, poll))
                continue

            age_seconds = self._status_age_seconds(status)
            if (
                age_seconds > max(0.5, self.config.bridge_status_stale_after_seconds)
                and not self._status_has_recent_ack(status)
            ):
                if time.time() - last_fresh_status_at > max(2.0, self.config.bridge_unresponsive_after_seconds):
                    raise RuntimeError(f"bridge status stale ({age_seconds:.2f}s)")
                time.sleep(max(0.1, poll))
                continue
            last_status = status
            last_fresh_status_at = time.time()

            if not bool(status.get("inWorld", True)):
                raise RuntimeError("player is not in-world")
            if not bool(status.get("baritoneLoaded", True)):
                err = str(status.get("baritoneError", "")).strip()
                raise RuntimeError(f"baritone not loaded: {err or 'unknown error'}")

            status_command_id = str(status.get("lastCommandId", "")).strip()
            if status_command_id:
                self._log_bridge_command_correlation(
                    phase="observed",
                    command_id=status_command_id,
                    status=status,
                )
            matches_active_command = status_command_id == command_id
            result = str(status.get("lastCommandResult", "")).lower()
            if not matches_active_command:
                status_command_text = str(status.get("lastCommand", "")).strip()
                known_aux = status_command_id in self._aux_command_ids or self._looks_like_aux_command(status_command_text)
                if self._bridge_status_reissued_active_command(
                    status,
                    command_id=command_id,
                    command_body=self._active_command_body,
                ):
                    log.info(
                        "Bridge active command was reissued during wait for '%s': adopting new id %s -> %s",
                        self._active_command_body,
                        command_id,
                        status_command_id,
                    )
                    command_id = status_command_id
                    matches_active_command = True
                    seen_matching_status = True
                    startup_deadline = self._active_command_started_at + max(
                        0.5, self.config.long_command_startup_seconds
                    )
                    reset_pos = self._status_position(status)
                    command_start_pos = reset_pos
                    last_progress_pos = reset_pos
                    last_progress_at = time.time()
                    max_distance_from_start_sq = 0.0
                    seen_pathing = bool(status.get("isPathing", False))
                elif seen_matching_status and not known_aux:
                    log.error(
                        "Bridge command id changed while waiting for '%s': expected=%s got=%s (%s)",
                        self._active_command_body,
                        command_id,
                        status_command_id,
                        status_command_text or "<empty>",
                    )
                    return False
                if not seen_matching_status:
                    time.sleep(max(0.1, poll))
                    continue
            if matches_active_command:
                seen_matching_status = True
                if result in {"rejected", "error"}:
                    err = str(status.get("lastCommandError", "")).strip()
                    log.error("Bridge command failed: %s", err or result)
                    return False
                if result in {"accepted", "ok"}:
                    accepted = True
            if not seen_matching_status and self._bridge_status_ack_matches_command(
                status,
                command_id=command_id,
                command_body=self._active_command_body,
            ):
                seen_matching_status = True
                accepted = True
                if not accepted_without_ack_logged:
                    log.info(
                        "Observed accepted bridge status without explicit ack for '%s'",
                        self._active_command_body,
                    )
                    accepted_without_ack_logged = True

            is_pathing = bool(status.get("isPathing", False))
            if is_pathing:
                seen_pathing = True
            pos = self._status_position(status)
            if pos is not None:
                if command_start_pos is None:
                    command_start_pos = pos
                else:
                    max_distance_from_start_sq = max(
                        max_distance_from_start_sq,
                        self._distance_sq(pos, command_start_pos),
                    )
                if last_progress_pos is None:
                    last_progress_pos = pos
                elif self._distance_sq(pos, last_progress_pos) >= 1.0:
                    last_progress_pos = pos
                    last_progress_at = time.time()
            self._maybe_place_torch(is_pathing=is_pathing, status=status)
            action_meta = self._active_action_metadata(command_id=command_id, body=self._active_command_body)

            if self.config.auto_eat_enabled:
                try:
                    self._attempt_auto_eat(status)
                except Exception as exc:
                    log.warning("Auto-eat check failed: %s", exc)
            if self.config.fight_hostiles:
                try:
                    self.try_hostile_combat(status, context="wait_for_idle")
                except Exception as exc:
                    log.warning("Combat check failed: %s", exc)
            if self.config.inventory_cleanup_enabled:
                try:
                    self.try_inventory_cleanup(status, context="wait_for_idle")
                except Exception as exc:
                    log.warning("Inventory cleanup check failed: %s", exc)

            if long_running:
                can_fly = self._status_player_can_fly(status)
                goal = str(status.get("currentGoal", "")).strip()
                no_pending = int(status.get("pendingResponseCount", 0) or 0) <= 0 and int(status.get("queueDepth", 0) or 0) <= 0
                moved_recently = last_progress_at > 0.0 and (time.time() - last_progress_at) <= max(2.0, poll * 4.0)
                moved_from_start = max_distance_from_start_sq >= 1.0
                movement_observed = bool(moved_recently or moved_from_start)
                pathing_observed = bool(seen_pathing or is_pathing)
                if action_meta.get("actionClass") in {"local_in_place", "nearby_interaction", "machine_processing"}:
                    evaluation = self._contextual_action_evaluation(
                        status,
                        action_meta=action_meta,
                        movement_observed=movement_observed,
                        pathing_observed=pathing_observed,
                    )
                    completed_local_action = matches_active_command and accepted and not is_pathing and no_pending
                    if completed_local_action and bool(evaluation.get("finalSuccess", False)):
                        self._record_action_evaluation(evaluation, success=True)
                        return True
                    if completed_local_action and time.time() > startup_deadline and not bool(evaluation.get("finalSuccess", False)):
                        self._record_action_evaluation(evaluation, success=False)
                        return False
                if self._command_requires_mining_tools(self._active_command_body):
                    pickaxe_count = self._hotbar_pickaxe_count(status)
                    low_durability = self._main_hand_pickaxe_low_durability(status)
                    if 0 <= pickaxe_count < max(0, self.config.min_pickaxe_count):
                        if not self._ensure_pickaxe_hotbar(status):
                            self._attempt_tool_recovery(
                                f"pickaxe count {pickaxe_count} < {self.config.min_pickaxe_count}"
                            )
                    elif low_durability:
                        remaining = int(status.get("mainHandRemainingDurability", -1))
                        self._attempt_tool_recovery(
                            f"main-hand durability {remaining} < {self.config.min_main_hand_durability}"
                        )
                if self.config.stuck_recovery_enabled and is_pathing:
                    stalled_since = last_progress_at if last_progress_at > 0.0 else self._active_command_started_at
                    no_progress_for = time.time() - stalled_since
                    if no_progress_for >= max(2.0, float(self.config.stuck_no_progress_seconds)):
                        max_attempts = max(1, int(self.config.stuck_recovery_max_attempts))
                        if self._stuck_recovery_attempts >= max_attempts:
                            log.error(
                                "No pathing progress for %.1fs and recovery attempts exhausted (%d)",
                                no_progress_for,
                                max_attempts,
                            )
                            return False
                        self._stuck_recovery_attempts += 1
                        log.warning(
                            "Detected pathing stall for %.1fs. Recovery attempt %d/%d",
                            no_progress_for,
                            self._stuck_recovery_attempts,
                            max_attempts,
                        )
                        recovered = self.run_recovery_playbook(
                            context="stuck_no_progress",
                            status=status,
                            reissue_active_command=True,
                        )
                        if not recovered:
                            return False
                        last_progress_at = time.time()
                        refreshed_after_recover = self.read_bridge_status()
                        if refreshed_after_recover and self._status_is_fresh(refreshed_after_recover):
                            pos_after = self._status_position(refreshed_after_recover)
                            if pos_after is not None:
                                last_progress_pos = pos_after

            safety_reasons = self.safety_reasons(status, consider_players=long_running)
            if safety_reasons:
                player_distance = float(status.get("nearestOtherPlayerDistance", -1.0))
                player_name = str(status.get("nearestOtherPlayerName", "")).strip() or "unknown"
                action_payload: dict[str, Any] = {
                    "event": "safety_hold_diagnostic",
                    "actionName": self._active_command_body,
                    "currentStage": str(status.get("stoneblockStageHint", "")).strip() or "unknown",
                    "nearbyPlayerSameDimensionAvailable": False,
                    "thresholdMeters": float(self.config.nearby_player_min_distance),
                    "thresholdSource": "config.nearby_player_min_distance",
                }
                action_payload.update(
                    {
                        "actionCategory": action_meta.get("actionCategory", "unknown"),
                        "actionClass": action_meta.get("actionClass", "unknown"),
                        "destructiveMiningBoolean": bool(action_meta.get("destructiveMiningBoolean", False)),
                        "pathingBoolean": bool(action_meta.get("pathingBoolean", False)),
                    }
                )
                if player_distance >= 0.0:
                    action_payload["nearbyPlayerDistance"] = round(player_distance, 3)
                    action_payload["nearbyPlayerName"] = player_name
                    action_payload["nearbyPlayerSameDimension"] = None
                log.warning(
                    "safety_hold_diagnostic %s",
                    json.dumps(action_payload, ensure_ascii=True, sort_keys=True, default=str),
                )
                log.warning("Safety hold during '%s': %s", self._active_command_body, "; ".join(safety_reasons))
                self.pause_pathing()
                hold_deadline = time.time() + max(1.0, self.config.safety_recover_timeout_seconds)
                while time.time() < hold_deadline:
                    if self.controller.stop_requested:
                        return False
                    try:
                        self.controller.wait_if_paused()
                    except RuntimeError:
                        return False
                    time.sleep(max(0.1, self.config.safety_poll_seconds))
                    refreshed = self.read_bridge_status()
                    if not refreshed:
                        continue
                    if self._status_age_seconds(refreshed) > max(0.5, self.config.bridge_status_stale_after_seconds):
                        continue
                    last_status = refreshed
                    safety_reasons = self.safety_reasons(refreshed, consider_players=long_running)
                    if not safety_reasons:
                        self.resume_pathing()
                        break
                if safety_reasons:
                    recovered = self.run_recovery_playbook(
                        context="safety_hold_timeout",
                        status=last_status if last_status else status,
                        reissue_active_command=long_running,
                    )
                    if recovered:
                        continue
                    log.error(
                        "Safety conditions did not recover during '%s': %s",
                        self._active_command_body,
                        "; ".join(safety_reasons),
                    )
                    return False

            if long_running:
                if matches_active_command and seen_pathing and not is_pathing and accepted:
                    self._record_action_evaluation(
                        self._default_action_evaluation(
                            status,
                            action_meta=action_meta,
                            movement_observed=movement_observed,
                            pathing_observed=pathing_observed,
                            final_success=True,
                        ),
                        success=True,
                    )
                    return True
                if (
                    matches_active_command
                    and not seen_pathing
                    and not is_pathing
                    and accepted
                    and (not require_pathing_transition or can_fly)
                ):
                    self._record_action_evaluation(
                        self._default_action_evaluation(
                            status,
                            action_meta=action_meta,
                            movement_observed=movement_observed,
                            pathing_observed=pathing_observed,
                            final_success=True,
                        ),
                        success=True,
                    )
                    return True
                if not seen_pathing and time.time() > startup_deadline:
                    if matches_active_command and accepted and can_fly and (not goal or no_pending):
                        log.warning(
                            "No pathing transition observed for long command '%s'. "
                            "Accepting flying bridge acknowledgement fallback (goal=%s, moved_recently=%s, moved_from_start=%s).",
                            self._active_command_body,
                            goal or "<empty>",
                            moved_recently,
                            moved_from_start,
                        )
                        self._record_action_evaluation(
                            self._default_action_evaluation(
                                status,
                                action_meta=action_meta,
                                movement_observed=movement_observed,
                                pathing_observed=pathing_observed,
                                final_success=True,
                            ),
                            success=True,
                        )
                        return True
                    if matches_active_command and accepted and moved_from_start and (not goal or moved_recently):
                        log.warning(
                            "No pathing transition observed for long command '%s'. "
                            "Accepting bridge acknowledgement fallback after observed movement "
                            "(goal=%s, moved_recently=%s, moved_from_start=%s, maxDistance=%.2f).",
                            self._active_command_body,
                            goal or "<empty>",
                            moved_recently,
                            moved_from_start,
                            max_distance_from_start_sq ** 0.5,
                        )
                        self._record_action_evaluation(
                            self._default_action_evaluation(
                                status,
                                action_meta=action_meta,
                                movement_observed=movement_observed,
                                pathing_observed=pathing_observed,
                                final_success=True,
                            ),
                            success=True,
                        )
                        return True
                    if matches_active_command and accepted and no_pending and not moved_recently and not moved_from_start and not can_fly:
                        if self._matches_prefixes(
                            self._active_command_body,
                            ["tunnel ", "build ", "goto ", "explore "],
                        ):
                            log.error(
                                "Long-running command acknowledged without pathing or observed movement: '%s' "
                                "(goal=%s, no_pending=%s). Refusing false-positive fallback.",
                                self._active_command_body,
                                goal or "<empty>",
                                no_pending,
                            )
                            self._record_action_evaluation(
                                self._default_action_evaluation(
                                    status,
                                    action_meta=action_meta,
                                    movement_observed=movement_observed,
                                    pathing_observed=pathing_observed,
                                    final_success=False,
                                ),
                                success=False,
                            )
                            return False
                        log.error(
                            "Long-running command never entered pathing or observed movement: %s",
                            self._active_command_body,
                        )
                        self._record_action_evaluation(
                            self._default_action_evaluation(
                                status,
                                action_meta=action_meta,
                                movement_observed=movement_observed,
                                pathing_observed=pathing_observed,
                                final_success=False,
                            ),
                            success=False,
                        )
                        return False
            else:
                if matches_active_command and accepted and not is_pathing:
                    self._record_action_evaluation(
                        self._default_action_evaluation(
                            status,
                            action_meta=action_meta,
                            movement_observed=False,
                            pathing_observed=bool(seen_pathing or is_pathing),
                            final_success=True,
                        ),
                        success=True,
                    )
                    return True

            time.sleep(max(0.1, poll))

        if seen_matching_status:
            log.error(
                "Timeout waiting for bridge idle state (cmd=%s result=%s pathing=%s goal=%s)",
                self._active_command_body,
                last_status.get("lastCommandResult", ""),
                last_status.get("isPathing", ""),
                last_status.get("currentGoal", ""),
            )
        else:
            log.error("Timeout waiting for bridge idle state before command became active")
        return False

    def wait_for_idle(self, timeout_seconds: float | None = None, poll_seconds: float | None = None) -> bool:
        timeout = timeout_seconds if timeout_seconds is not None else self.config.idle_timeout_seconds
        poll = poll_seconds if poll_seconds is not None else self.config.poll_seconds
        try:
            transport = self.config.transport.strip().lower()
            if transport == "bridge_file":
                return self._wait_for_idle_bridge(timeout, poll)
            return self._wait_for_idle_chat(timeout, poll)
        finally:
            self._disable_ultimine_after_command(context="wait_for_idle")
