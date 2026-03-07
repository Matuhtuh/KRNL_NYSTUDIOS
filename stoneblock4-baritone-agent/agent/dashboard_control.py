from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.config import AppConfig
from agent.full_auto import build_full_auto_goals
from agent.knowledge import load_goals
from agent.pack_parser import build_pack_model
from agent.planner import AgentState


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temp_path.write_text(body, encoding="utf-8")
    os.replace(temp_path, path)


def _load_state(path: Path) -> AgentState:
    if not path.exists():
        return AgentState()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid state file: {path}")
    return AgentState.from_json(raw)


def _save_state(path: Path, state: AgentState) -> None:
    _atomic_write_json(path, state.to_json())


def _load_runtime_goals(cfg: AppConfig) -> dict[str, Any]:
    goals = load_goals(Path(cfg.planner.knowledge_file).resolve())
    if cfg.planner.full_auto_enabled and str(cfg.planner.instance_path).strip():
        pack_model_path = Path(cfg.planner.pack_model_file).resolve()
        build_pack_model(cfg.planner.instance_path, pack_model_path)
        full_goals = build_full_auto_goals(
            pack_model_path=pack_model_path,
            include_chapter_files=cfg.planner.full_auto_include_chapters,
            max_quests=cfg.planner.full_auto_max_quests,
            include_optional=cfg.planner.full_auto_include_optional_quests,
            strategy_memory_file=cfg.planner.strategy_memory_file,
        )
        existing = {goal.id for goal in goals}
        for goal in full_goals:
            if goal.id not in existing:
                goals.append(goal)
    goals.sort(key=lambda goal: goal.priority, reverse=True)
    return {goal.id: goal for goal in goals}


class DashboardControlExecutor:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.workspace = _project_root()
        self.state_path = Path(cfg.planner.state_file).resolve()

    def _run_json_command(self, args: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            args,
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = str(proc.stdout or "").strip()
        stderr = str(proc.stderr or "").strip()
        parsed: dict[str, Any] | None = None
        if stdout:
            try:
                payload = json.loads(stdout)
                if isinstance(payload, dict):
                    parsed = payload
            except Exception:
                parsed = None
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "json": parsed,
        }

    def _stop_script(self) -> list[str]:
        script = self.workspace / "runtime" / "stop_live_automation.ps1"
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]

    def _start_script(self) -> list[str]:
        script = self.workspace / "runtime" / "start_live_automation.ps1"
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), "-NoDashboard"]

    def _goal_exists(self, goal_id: str) -> bool:
        goals = _load_runtime_goals(self.cfg)
        return str(goal_id).strip() in goals

    def _goal_ready(self, goal_id: str) -> bool:
        goals = _load_runtime_goals(self.cfg)
        goal = goals.get(str(goal_id).strip())
        if goal is None:
            return False
        state = _load_state(self.state_path)
        return all(req in state.completed_flags for req in goal.prerequisites) and goal.id not in state.completed_goals

    def pause_automation(self, *, reason: str = "") -> dict[str, Any]:
        stop_result = self._run_json_command(self._stop_script())
        state = _load_state(self.state_path)
        state.safe_paused = True
        state.current_goal_id = None
        if reason:
            state.last_error = reason
        _save_state(self.state_path, state)
        return {
            "action": "pause_automation",
            "reason": reason,
            "stopResult": stop_result,
            "state": state.to_json(),
        }

    def stop_automation(self, *, reason: str = "") -> dict[str, Any]:
        stop_result = self._run_json_command(self._stop_script())
        state = _load_state(self.state_path)
        state.current_goal_id = None
        if reason:
            state.last_error = reason
        _save_state(self.state_path, state)
        return {
            "action": "stop_automation",
            "reason": reason,
            "stopResult": stop_result,
            "state": state.to_json(),
        }

    def resume_automation(self, *, reason: str = "") -> dict[str, Any]:
        original = _load_state(self.state_path)
        state = AgentState.from_json(original.to_json())
        state.safe_paused = False
        state.current_goal_id = None
        if reason:
            state.last_error = reason
        _save_state(self.state_path, state)
        start_result = self._run_json_command(self._start_script())
        if not bool(start_result.get("ok", False)):
            _save_state(self.state_path, original)
        return {
            "action": "resume_automation",
            "reason": reason,
            "launchResult": start_result,
            "state": (_load_state(self.state_path)).to_json(),
        }

    def start_goal(self, goal_id: str, *, reason: str = "") -> dict[str, Any]:
        target_goal = str(goal_id).strip()
        if not target_goal:
            raise ValueError("goal_id is required")
        if not self._goal_exists(target_goal):
            raise ValueError(f"unknown goal_id: {target_goal}")
        if not self._goal_ready(target_goal):
            raise ValueError(f"goal_id is not currently ready: {target_goal}")
        original = _load_state(self.state_path)
        state = AgentState.from_json(original.to_json())
        state.safe_paused = False
        state.current_goal_id = target_goal
        if reason:
            state.last_error = reason
        _save_state(self.state_path, state)
        start_result = self._run_json_command(self._start_script())
        if not bool(start_result.get("ok", False)):
            _save_state(self.state_path, original)
        return {
            "action": "start_goal",
            "goalId": target_goal,
            "reason": reason,
            "launchResult": start_result,
            "state": (_load_state(self.state_path)).to_json(),
        }
