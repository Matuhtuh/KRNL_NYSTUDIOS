# FTB Stoneblock 4 Automation AI

Starter repository for building an automation-focused AI assistant tailored to **FTB Stoneblock 4** workflows.

## Goals
- Automate repetitive Stoneblock 4 planning and progression tasks.
- Track milestones, resources, and machine chains.
- Provide strategy suggestions for early, mid, and late game.
- Allow future integrations with logs, screenshots, and world-state notes.

## Initial Project Structure
- `src/` — core application code.
- `prompts/` — reusable prompt templates and system instructions.
- `knowledge/` — curated game notes, recipes, and progression docs.
- `scripts/` — helper scripts for local tooling and data prep.

## Quick Start
```bash
# from repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you have not created dependencies yet, add them to `requirements.txt` first.

## Repository Transfer (Codex CLI)
From your local terminal after cloning or connecting this repo:

```bash
git remote add origin <your-new-repo-url>
git push -u origin work
```

If your default branch should be `main`, rename and push:

```bash
git branch -m main
git push -u origin main
```

## Next Steps
1. Define the AI's first supported workflows in `docs/ROADMAP.md`.
2. Add a baseline CLI app under `src/`.
3. Add a small regression test suite for prompts and planners.
