"""Research provider interface; implementations may later call web/search APIs via tools."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ResearchQuery, ResearchResult


class ResearchProvider(ABC):
    """Executes structured research requests for StoneBlock 4 knowledge gaps."""

    @abstractmethod
    def research(self, query: ResearchQuery) -> ResearchResult:
        """Return actionable structured findings for the supplied query."""
