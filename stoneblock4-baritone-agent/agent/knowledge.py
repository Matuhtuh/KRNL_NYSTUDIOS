from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Goal:
    id: str
    priority: int
    description: str
    prerequisites: list[str]
    completion_flags: list[str]
    tasks: list[dict[str, Any]]


def load_goals(path: str | Path) -> list[Goal]:
    p = Path(path).expanduser().resolve()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    goals = []
    for g in raw.get("goals", []):
        goals.append(
            Goal(
                id=str(g["id"]),
                priority=int(g.get("priority", 0)),
                description=str(g.get("description", "")),
                prerequisites=[str(v) for v in g.get("prerequisites", [])],
                completion_flags=[str(v) for v in g.get("completion_flags", [])],
                tasks=list(g.get("tasks", [])),
            )
        )
    goals.sort(key=lambda x: x.priority, reverse=True)
    return goals
