# StoneBlock 4 Autonomous Agent Workspace

This repository now contains two coordinated foundations:

1. **Python AI architecture** for hierarchical planning/execution/recovery/research/builder logic.
2. **NeoForge client bridge mod** for Minecraft Java 1.21.1 that will expose deterministic state/actions to the external AI brain.

## 1) Python agent foundation

The Python side defines strict Pydantic schemas and interfaces for:
- deterministic game control boundaries
- state/perception contracts
- planner/executor contracts
- stuck recovery
- research I/O
- blueprint/build verification
- long-term memory/failure tracking

See package directories at repository root (`state/`, `planner/`, etc.) and `tests/`.

## 2) Minecraft client bridge mod

Located in: `minecraft-bridge-mod/`

Highlights:
- Target: **Minecraft 1.21.1**
- Loader: **NeoForge**
- Scope: **client-side only**
- Purpose: local bridge/body for an external AI process (not a standalone cheating bot)
- Includes DTO contracts, interfaces, and localhost HTTP transport skeleton

Read `minecraft-bridge-mod/README.md` for architecture and communication flow details.

## Design stance across both modules

- LLMs are used for high-level planning/research/build design only.
- Per-tick movement/inventory/combat/build inputs remain deterministic.
- Interfaces and validated schemas come before deep implementation.
- No fake claims of complete automation.

## What to implement next

- Concrete Minecraft state capture and deterministic input wiring in the bridge mod.
- JSON protocol serialization/parsing for bridge endpoints.
- External brain process that consumes `/state` and sends bounded `/action` requests.
- End-to-end integration tests between Python planner/executor and bridge transport.
