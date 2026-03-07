# Known-Good Release Freeze (Draft)

Single-source freeze note for the first known-good `0.1.8` bridge release.
Status: `Draft` (pending live verification after Minecraft client restart).

## Version Freeze Fields

| Component | Known Local Value | Freeze Field |
| --- | --- | --- |
| Modpack | `FTB StoneBlock 4 v1.8.0` | `MODPACK_VERSION=FTB StoneBlock 4 v1.8.0` |
| Minecraft | `1.21.1` | `MINECRAFT_VERSION=1.21.1` |
| NeoForge | `21.1.219` | `NEOFORGE_VERSION=21.1.219` |
| Baritone | compatible `1.21.1` NeoForge line (`v1.11.x` guidance) | `BARITONE_VERSION=<exact loaded jar version>` |
| Bridge mod | `0.1.8` deployed; `0.1.7` may still be live until restart | `BRIDGE_VERSION=0.1.8` (after status confirms) |
| Agent deps | range-pinned in `requirements.txt` | `AGENT_DEPS_LOCK=runtime/agent-deps-lock.txt` |

## Agent Dependency Placeholders

Current local constraints:
- `mss>=10.1.0,<11`
- `numpy>=2.3.0,<3`
- `opencv-python>=4.10.0.84`
- `pydirectinput>=1.0.4`
- `PyYAML>=6.0`
- `pytesseract>=0.3.13`
- `pygetwindow>=0.0.9`
- `requests>=2.32.0,<3`

Freeze exacts after live verification:
- `mss==<exact>`
- `numpy==<exact>`
- `opencv-python==<exact>`
- `pydirectinput==<exact>`
- `PyYAML==<exact>`
- `pytesseract==<exact>`
- `pygetwindow==<exact>`
- `requests==<exact>`

## Finalize Checklist (Post-Live Verification)

- `[ ]` Restart Minecraft client and load world so deployed bridge jar is active.
- `[ ]` Confirm `config/sb4_baritone_bridge/status.json` reports `bridgeVersion=0.1.8`.
- `[ ]` Capture exact Baritone jar/mod version and replace `BARITONE_VERSION=<...>`.
- `[ ]` Run `runtime/audit_bridge_runtime.ps1` and confirm no jar/version drift.
- `[ ]` Re-run minimum live checks on `0.1.8`: readiness, command ack burst, inventory ops (`swap`, `drop`, `store_to_nearby_chest`).
- `[ ]` Export exact Python deps (`pip freeze > runtime/agent-deps-lock.txt`) and replace all `==<exact>` placeholders.
- `[ ]` Record release identifiers:
  - `BRIDGE_RELEASE_TAG=<tag>`
  - `AGENT_COMMIT_SHA=<sha>`
  - `VERIFIED_AT=<YYYY-MM-DD HH:MM TZ>`
- `[ ]` Flip status in this file from `Draft` to `Final`.
