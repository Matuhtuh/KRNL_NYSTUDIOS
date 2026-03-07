# StoneBlock 4 Baritone + Screenwatch Agent

Hybrid controller for your request:
- Baritone execution (`#mine`, `#goto`, etc.)
- Screen-watch perception (OCR with confidence)
- External input control (focus-gated, emergency stop)
- Optional direct companion-mod transport (`bridge_file`) that avoids chat injection
- Goal planner with persistent state/retries
- Optional simulation mode before live runs

## Detected Pack Reality

Your local instance is:
- `FTB StoneBlock 4 v1.8.0`
- `Minecraft 1.21.1`
- `NeoForge 21.1.219`

Baritone should be pinned to a compatible `1.21.1` NeoForge line (commonly `v1.11.x`).

## Release Freeze Tracking

Known-good release freeze values live in `runtime/KNOWN_GOOD_RELEASE.md`.
Use that file to lock exact Minecraft/NeoForge/Baritone/bridge/agent dependency versions after live `0.1.8` bridge verification.

## What Is Implemented

- Input safety:
  - foreground focus verification
  - automatic focus recovery/reacquire attempts before failing a task
  - emergency stop via WinAPI key polling
  - pause/resume automation toggle via hotkey
  - retry-safe key release on stop
  - input backend options: `pydirectinput`, `ahk`, `noop`
- Perception:
  - OCR confidence scoring via Tesseract TSV
  - Otsu + adaptive threshold fallback
- Runtime reliability:
  - task retries with exponential backoff + jitter
  - goal retry budget and safe-pause state
  - persisted state fields include retry/error metadata
  - live runtime status file (`runtime/live_status.json`) for continuous observability
  - explicit recovery playbooks for stuck pathing/safety timeout scenarios
  - bridge safety checks (health/food/air/fire/lava/player proximity)
  - long-command pathing transition detection for bridge transport
  - configurable periodic torch placement while mining/tunneling
- Inventory-aware bridge automation:
  - bridge reports full player inventory + offhand telemetry (not only hotbar)
  - Python agent can swap inventory items into hotbar via bridge command (`bridge.swap_to_hotbar`)
  - auto-eat/torch/tool-recovery now pull from inventory when hotbar is missing required items
  - automatic inventory overflow cleanup via bridge drop command (`bridge.drop_item`) using configurable junk/keep rules
- Full automation pipeline:
  - dynamic quest DAG generation from FTB SNBT chapters
  - quest item task conversion into executable runtime tasks
  - recipe dependency planner over datapack/KubeJS JSON recipes
  - segmented building task support (`build_tunnel`) with bridge telemetry-aware checks
  - crafting command template fallback (`craft_item` / `craft_with_dependencies`) with dependency expansion
  - module-based machine/farm/bootstrap task packs
  - quest auto-claim command attempts
- Modpack awareness tooling:
  - `extract-pack-model` command parses local quest chapters + world engine quest map
- Simulation:
  - `simulate` command runs goals without touching live Minecraft input
  - when no planner goals are ready, optional idle autonomy module can continue safe resource grinding (`planner.idle_autonomous_*`)

## Setup

```powershell
cd C:\Users\suret\stoneblock4-baritone-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
```

Install Tesseract OCR and set `ocr.tesseract_cmd` in `config.yaml` if needed.

If you want AutoHotkey input backend:
- install AutoHotkey v2
- set `control.input_backend: "ahk"`
- set `control.ahk_exe` path if not on PATH

## Companion Mod Transport (Recommended)

A NeoForge client companion mod is included in:
- `C:\Users\suret\stoneblock4-baritone-bridge-mod`

Build and install that jar, then set in `config.yaml`:

```yaml
baritone:
  transport: "bridge_file"
  bridge_dir: "C:/Users/suret/curseforge/minecraft/Instances/FTB StoneBlock 4/config/sb4_baritone_bridge"
```

With `bridge_file`, the Python agent sends JSON command files and reads status JSON instead of typing chat commands.

## Commands

OCR sanity check:

```powershell
python main.py --config config.yaml observe
```

One-shot Baritone command:

```powershell
python main.py --config config.yaml send "mine minecraft:cobblestone 128"
```

Run planner loop:

```powershell
python main.py --config config.yaml run
```

Arm + run (recommended for real sessions):

```powershell
python main.py --config config.yaml --verbose arm-run --bridge-timeout 180
```

Live dashboard (recommended while running):

```powershell
python main.py --config config.yaml dashboard
```

Run simulation (no real key/mouse injection):

```powershell
python main.py --config config.yaml simulate
```

Simulation with forced failures:

```powershell
python main.py --config config.yaml simulate --scenario runtime/sim_fail.yaml
```

Extract local pack model:

```powershell
python main.py --config config.yaml extract-pack-model --instance "C:\Users\suret\curseforge\minecraft\Instances\FTB StoneBlock 4" --out runtime/pack_model.json
```

Emergency stop:
- press `F8` (or your configured key)

Pause/resume automation:
- press your configured `control.toggle_pause_key` (current default in this repo: `HOME`)

Launcher scripts:

```powershell
# starts automation and attaches live dashboard in the same terminal
powershell -ExecutionPolicy Bypass -File runtime/start_live_automation.ps1
# starts automation in background only (no live dashboard)
powershell -ExecutionPolicy Bypass -File runtime/start_live_automation.ps1 -NoDashboard
# starts automation and dashboard in a separate new terminal window
powershell -ExecutionPolicy Bypass -File runtime/start_live_automation.ps1 -DashboardInNewWindow
powershell -ExecutionPolicy Bypass -File runtime/stop_live_automation.ps1
```

## First Real Run (Recommended)

1. Start Minecraft and be in-world, windowed/borderless.
2. Run `observe` and verify OCR confidence is not near zero.
3. Run `send "help"` and confirm Baritone response appears in chat.
   If using `bridge_file`, verify `status.json` updates under `config/sb4_baritone_bridge`.
4. Run `simulate` and ensure planner finishes goals in dry-run.
5. Run `arm-run` for live execution (or `runtime/start_live_automation.ps1`).
6. If failures repeat, inspect `runtime/state.json` (`last_error`, `goal_retries`, `safe_paused`).
7. For live diagnosis, run `dashboard` in a second terminal (reads `runtime/live_status.json` + bridge status).

## StoneBlock Notes

- Keep torches in the configured hotbar slot (`baritone.torch_hotbar_slot`, default `9`).
- Keep at least one edible food stack if possible; if food is only in inventory, bridge-mode can now pull it into configured hotbar food slot automatically.
- The bot now enforces survival and stealth guards:
  - pauses/fails tasks if health/food/air is unsafe
  - avoids long mining/building tasks when another player is too close
- Hostile combat can be enabled with `baritone.fight_hostiles`:
  - engages nearby hostiles only when health/food are above configured combat thresholds
  - otherwise disengages, tries to eat, and increases avoidance radius instead of forcing a fight
  - default bridge combat templates use `bridge.fight_hostile` for direct nearest-hostile swings
- Tool safety hardening:
  - sends Baritone `autoTool` + `itemSaver` settings before long mining tasks
  - emergency attempts inventory swap first when hotbar tool is missing/low
- Building logic:
  - progression and modules now use `build_tunnel` segments instead of single huge tunnel commands
  - when bridge pathing telemetry is unreliable, long-command completion can fall back to command acknowledgement
- Inventory overflow handling:
  - when free slots drop below threshold, the bot can auto-drop configured junk blocks and keep tools/food/torches
  - behavior is configurable under `baritone.inventory_*` in `config.yaml`
- Bridge mode blocks legacy Baritone `proc ...` commands (to prevent `Too many arguments` spam on this pack version).
- Full-auto quest generation is controlled under `planner.full_auto_*` in `config.yaml`.
  By default it targets early/medium progression chapters and material/automation branches.

## Files You Will Edit Most

- `config.yaml`
- `knowledge/stoneblock4_progression.yaml`
- `runtime/state.json`
- `runtime/pack_model.json` (generated)
