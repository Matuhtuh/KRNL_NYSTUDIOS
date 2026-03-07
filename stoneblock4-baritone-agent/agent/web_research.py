from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class WebResearcher:
    def __init__(
        self,
        *,
        cache_file: str | Path,
        ttl_minutes: int = 180,
        timeout_seconds: float = 4.0,
        max_snippets: int = 6,
    ) -> None:
        self.cache_file = Path(cache_file).expanduser().resolve()
        self.ttl_minutes = max(5, int(ttl_minutes))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_snippets = max(1, int(max_snippets))
        self._cache: dict[str, Any] = {"entries": {}}
        self._loaded = False

    def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.cache_file.exists():
            return
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8-sig"))
        except Exception:
            return
        if isinstance(raw, dict):
            self._cache = raw
            if not isinstance(self._cache.get("entries"), dict):
                self._cache["entries"] = {}

    def _save_cache(self) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        tmp.replace(self.cache_file)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _cache_valid(self, entry: dict[str, Any]) -> bool:
        ts_raw = str(entry.get("fetched_at", "")).strip()
        if not ts_raw:
            return False
        try:
            ts = datetime.fromisoformat(ts_raw)
        except Exception:
            return False
        return self._now() - ts <= timedelta(minutes=self.ttl_minutes)

    @staticmethod
    def _topic_texts(node: Any, out: list[str], max_items: int) -> None:
        if len(out) >= max_items:
            return
        if isinstance(node, dict):
            text = str(node.get("Text", "")).strip()
            if text:
                out.append(text)
                if len(out) >= max_items:
                    return
            for key in ("Topics", "RelatedTopics"):
                if key in node:
                    WebResearcher._topic_texts(node.get(key), out, max_items)
        elif isinstance(node, list):
            for entry in node:
                WebResearcher._topic_texts(entry, out, max_items)
                if len(out) >= max_items:
                    return

    @staticmethod
    def _extract_keywords(texts: list[str]) -> list[str]:
        joined = " ".join(texts).lower()
        checks = [
            ("craft", r"\bcraft|crafting|recipe|table\b"),
            ("mine", r"\bmine|mining|ore|tunnel\b"),
            ("smelt", r"\bsmelt|furnace|blast furnace\b"),
            ("hammer", r"\bhammer\b"),
            ("sieve", r"\bsieve|sifter|mesh\b"),
            ("machine", r"\bmachine|automation|factory|processor\b"),
            ("loot", r"\bloot|drop|mob\b"),
            ("farm", r"\bfarm|crop|grow|harvest\b"),
        ]
        out: list[str] = []
        for key, pattern in checks:
            if re.search(pattern, joined):
                out.append(key)
        return out

    @staticmethod
    def _normalize_item_query(item_id: str) -> str:
        token = str(item_id).strip().lower()
        if ":" in token:
            _, path = token.split(":", 1)
        else:
            path = token
        return path.replace("_", " ").strip() or token

    def _fetch(self, query: str) -> dict[str, Any]:
        try:
            import requests
        except Exception:
            return {"snippets": [], "sources": [], "keywords": [], "error": "requests unavailable"}

        snippets: list[str] = []
        sources: list[str] = []
        error = ""
        try:
            response = requests.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            abstract = str(data.get("AbstractText", "")).strip()
            if abstract:
                snippets.append(abstract)
            abstract_url = str(data.get("AbstractURL", "")).strip()
            if abstract_url:
                sources.append(abstract_url)
            related: list[str] = []
            self._topic_texts(data.get("RelatedTopics", []), related, self.max_snippets)
            snippets.extend(related)
        except Exception as exc:
            error = str(exc)

        trimmed: list[str] = []
        for text in snippets:
            line = " ".join(str(text).split()).strip()
            if not line:
                continue
            if line not in trimmed:
                trimmed.append(line)
            if len(trimmed) >= self.max_snippets:
                break

        return {
            "snippets": trimmed,
            "sources": sources[: self.max_snippets],
            "keywords": self._extract_keywords(trimmed),
            "error": error,
        }

    def hints_for_item(self, item_id: str) -> dict[str, Any]:
        token = str(item_id).strip().lower()
        if not token:
            return {"keywords": [], "snippets": [], "sources": [], "error": "empty item"}

        self._load_cache()
        entries = self._cache.setdefault("entries", {})
        key = token
        cached = entries.get(key)
        if isinstance(cached, dict) and self._cache_valid(cached):
            return {
                "keywords": list(cached.get("keywords", [])),
                "snippets": list(cached.get("snippets", [])),
                "sources": list(cached.get("sources", [])),
                "error": str(cached.get("error", "")),
                "cached": True,
            }

        query = f"{self._normalize_item_query(token)} FTB StoneBlock 4 best method"
        result = self._fetch(query)
        entries[key] = {
            "item_id": token,
            "query": query,
            "keywords": list(result.get("keywords", [])),
            "snippets": list(result.get("snippets", [])),
            "sources": list(result.get("sources", [])),
            "error": str(result.get("error", "")),
            "fetched_at": self._now().isoformat(),
        }
        self._save_cache()
        return {
            "keywords": list(entries[key].get("keywords", [])),
            "snippets": list(entries[key].get("snippets", [])),
            "sources": list(entries[key].get("sources", [])),
            "error": str(entries[key].get("error", "")),
            "cached": False,
        }
