"""No-op research provider for deterministic local tests without web calls."""

from __future__ import annotations

from .interfaces import ResearchProvider
from .models import ResearchQuery, ResearchResult


class NoopResearchProvider(ResearchProvider):
    """Records research hook execution while returning empty guidance."""

    def __init__(self) -> None:
        self.queries: list[ResearchQuery] = []

    def research(self, query: ResearchQuery) -> ResearchResult:
        self.queries.append(query)
        return ResearchResult(
            query_id=query.query_id,
            summary="Research hook triggered; external web research not implemented in this stage.",
            recommended_steps=[],
            confidence=0.0,
            sources=[],
        )
