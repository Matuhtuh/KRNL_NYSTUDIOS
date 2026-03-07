## StoneBlock 4 Codex Cloud Bundle

This umbrella repository packages the current local StoneBlock automation work into one Git-friendly tree for GitHub and Codex cloud.

Included:

- `stoneblock4-baritone-agent/`
- `stoneblock4-baritone-bridge-mod/`
- `artifacts/bridge-mod/sb4baritonebridge-0.1.8.jar`
- agent runtime evidence, logs, screenshots, cached planner state, and current config snapshot
- bridge mod source plus a mirrored built jar at `stoneblock4-baritone-bridge-mod/build/libs/sb4baritonebridge-0.1.8.jar`

Intentionally omitted:

- machine-local virtual environments and bytecode caches
- nested Git metadata from the bridge repo
- Gradle caches and heavy intermediate build outputs
- the full CurseForge / Minecraft instance

Notes:

- `stoneblock4-baritone-agent/config.yaml` still contains local Windows paths from the source machine.
- The OpenAI key file was removed before this bundle was created. The dashboard is configured for the local Ollama path.
- This repo is intended as a cloud handoff snapshot, not as a perfect clean-room build of the full game environment.
