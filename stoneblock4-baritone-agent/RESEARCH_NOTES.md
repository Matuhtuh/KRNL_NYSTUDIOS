# Subagent Research Integration Notes

This file records how subagent research was applied to the current code.

## 1) Baritone Compatibility

Applied decisions:
- Keep chat bridge as fallback, but add companion-mod file bridge for direct API execution.
- Added stronger completion/failure detection and confidence gating.
- Added simulation and retry-safe pause for pack-specific Baritone failures.

Relevant source set:
- `baritone v1.11.2` release notes (1.21/1.21.1 NeoForge support)
- `baritone v1.15.0` release notes (newer MC lines)
- Baritone API/usage docs and issue threads for modpack incompatibilities

## 2) Input Reliability (Windows)

Applied decisions:
- Removed `keyboard` dependency.
- Implemented emergency-stop polling via WinAPI `GetAsyncKeyState`.
- Added focus gating/foreground verification for every command send.
- Added optional `ahk` backend (AutoHotkey v2) and `noop` backend for simulation.

Code:
- `agent/input_control.py`
- `config.example.yaml` (`control.input_backend`, focus timing, stop polling)

## 3) Perception Strategy

Applied decisions:
- OCR now returns confidence.
- Added Otsu + adaptive threshold fallback path.
- Baritone waits use confidence threshold + consecutive success matches.

Code:
- `agent/perception.py`
- `agent/baritone.py`

## 4) Runtime Architecture / Recovery

Applied decisions:
- Added retry budgets and exponential backoff with jitter.
- Added goal-level retry accounting and safe-pause state.
- Added persistent `last_error` and `goal_retries` in state file.
- Wait tasks default to single-attempt unless explicitly overridden.

Code:
- `agent/runner.py`
- `agent/planner.py`

## 5) Modpack-Aware Progression Data

Applied decisions:
- Added parser to extract local StoneBlock quest/model data and World Engine mapping.
- Added CLI command to generate planner-ready JSON model from your local instance.

Code:
- `agent/pack_parser.py`
- `main.py` command: `extract-pack-model`

## 6) Simulation Workflow

Applied decisions:
- Added end-to-end planner simulation with virtual controller/perception.
- Added failure scenario support to validate retries/safe pause.

Code:
- `agent/simulation.py`
- `main.py` command: `simulate`
- `scenarios/sim_fail_example.yaml`

## 7) Companion Mod Integration

Applied decisions:
- Added a NeoForge client companion mod project at:
  - `C:/Users/suret/stoneblock4-baritone-bridge-mod`
- Mod executes Baritone commands via API reflection and emits JSON status/results.
- Python agent added `baritone.transport: bridge_file` mode to use this interface.
