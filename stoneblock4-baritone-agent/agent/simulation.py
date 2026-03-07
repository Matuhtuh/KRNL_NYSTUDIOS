from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from agent.baritone import BaritoneBridge
from agent.config import AppConfig
from agent.knowledge import load_goals
from agent.perception import Observation
from agent.planner import AgentState, GoalPlanner
from agent.runner import AgentRuntime

log = logging.getLogger(__name__)


@dataclass
class SimulationScenario:
    fail_on_commands: list[str]

    @classmethod
    def from_path(cls, path: str | None) -> "SimulationScenario":
        if not path:
            return cls(fail_on_commands=[])
        p = Path(path).expanduser().resolve()
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        return cls(fail_on_commands=[str(v) for v in raw.get("fail_on_commands", [])])


class SimulatedPerception:
    def __init__(self) -> None:
        self._queue: deque[Observation] = deque()

    def push_chat(self, text: str, confidence: float = 0.95) -> None:
        self._queue.append(
            Observation(
                chat_text=text,
                hud_text="",
                full_text_hint=text,
                chat_confidence=confidence,
                hud_confidence=0.0,
                full_confidence=confidence,
            )
        )

    def observe(self) -> Observation:
        if self._queue:
            return self._queue.popleft()
        return Observation(
            chat_text="",
            hud_text="",
            full_text_hint="",
            chat_confidence=0.0,
            hud_confidence=0.0,
            full_confidence=0.0,
        )

    @staticmethod
    def contains_any(text: str, phrases: list[str]) -> bool:
        hay = (text or "").lower()
        return any(p.lower() in hay for p in phrases)


class SimulatedController:
    def __init__(self, perception: SimulatedPerception, scenario: SimulationScenario) -> None:
        self.stop_requested = False
        self.automation_enabled = True
        self.sent_commands: list[str] = []
        self._perception = perception
        self._scenario = scenario

    def wait_if_paused(self) -> None:
        return

    def send_baritone(self, command_body: str) -> None:
        cmd = command_body.strip()
        self.sent_commands.append(cmd)

        lowered = cmd.lower()
        should_fail = any(pattern.lower() in lowered for pattern in self._scenario.fail_on_commands)
        is_long_running = lowered.startswith(
            ("mine ", "tunnel ", "goto ", "build ", "follow ", "explore ", "proc ", "craft ")
        )

        if "help" in lowered:
            self._perception.push_chat("Baritone command list help", confidence=0.95)
            return
        if should_fail and is_long_running:
            self._perception.push_chat("unable to path", confidence=0.95)
            return
        if not is_long_running:
            return

        # Two success observations to satisfy required_consecutive_matches defaults.
        self._perception.push_chat("Pathing complete", confidence=0.95)
        self._perception.push_chat("Done", confidence=0.95)

    def place_torch(self, hotbar_slot: int, hold_seconds: float | None = None) -> None:
        self.sent_commands.append(f"__place_torch__ slot={int(hotbar_slot)}")

    def send_chat_command(self, raw_command: str) -> None:
        self.sent_commands.append(f"__chat__ {raw_command.strip()}")

    def consume_hotbar_slot(self, hotbar_slot: int, hold_seconds: float) -> None:
        self.sent_commands.append(f"__consume__ slot={int(hotbar_slot)} hold={hold_seconds:.2f}")


@dataclass
class SimulationResult:
    sent_commands: list[str]
    state: AgentState


def run_simulation(cfg: AppConfig, scenario_file: str | None = None) -> SimulationResult:
    scenario = SimulationScenario.from_path(scenario_file)
    sim_perception = SimulatedPerception()
    sim_controller = SimulatedController(sim_perception, scenario)
    sim_baritone_cfg = replace(cfg.baritone, transport="chat")
    bridge = BaritoneBridge(controller=sim_controller, perception=sim_perception, config=sim_baritone_cfg)

    sim_state_path = Path(cfg.planner.state_file).resolve().with_name("simulation_state.json")
    if sim_state_path.exists():
        sim_state_path.unlink()

    sim_planner_cfg = replace(
        cfg.planner,
        state_file=str(sim_state_path),
        max_idle_cycles=1,
        full_auto_enabled=False,
        instance_path=str(cfg.planner.instance_path or "").strip(),
    )
    sim_cfg = replace(cfg, planner=sim_planner_cfg, baritone=sim_baritone_cfg)

    goals = load_goals(Path(cfg.planner.knowledge_file).resolve())
    planner = GoalPlanner(goals)
    runtime = AgentRuntime(sim_cfg, planner, bridge, sim_perception)  # type: ignore[arg-type]
    runtime.run(exit_when_idle=True, max_cycles=300)

    if not sim_state_path.exists():
        raise RuntimeError("simulation did not produce state file")
    state = AgentState.from_json(json.loads(sim_state_path.read_text(encoding="utf-8")))
    log.info("Simulation completed with flags=%s", sorted(state.completed_flags))
    return SimulationResult(sent_commands=sim_controller.sent_commands, state=state)
