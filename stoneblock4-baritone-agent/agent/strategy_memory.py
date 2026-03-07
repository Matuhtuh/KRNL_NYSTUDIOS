from __future__ import annotations

import json
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StrategyMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._data: dict[str, Any] = {"items": {}}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return
        if isinstance(raw, dict):
            self._data = raw
            if not isinstance(self._data.get("items"), dict):
                self._data["items"] = {}

    @staticmethod
    def _is_lock_error(exc: OSError) -> bool:
        return isinstance(exc, PermissionError) or int(getattr(exc, "winerror", 0) or 0) in {5, 32, 33}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, indent=2)
        max_attempts = 6
        last_exc: OSError | None = None
        for attempt in range(max_attempts):
            tmp = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}-{time.time_ns()}-{attempt}")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.path)
                return
            except OSError as exc:
                last_exc = exc
                if attempt >= max_attempts - 1 or not self._is_lock_error(exc):
                    raise
                delay = min(0.5, (0.02 * (2**attempt)) + (random.random() * 0.01))
                time.sleep(delay)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc

    @staticmethod
    def _as_non_negative_float(value: Any, default: float = 0.0) -> float:
        try:
            num = float(value)
        except Exception:
            return max(0.0, float(default))
        if not math.isfinite(num):
            return max(0.0, float(default))
        return max(0.0, num)

    @staticmethod
    def _as_non_negative_int(value: Any, default: int = 0) -> int:
        try:
            num = int(value)
        except Exception:
            return max(0, int(default))
        return max(0, num)

    @staticmethod
    def _float_changed(previous: Any, current: float, *, eps: float = 1e-9) -> bool:
        try:
            old = float(previous)
        except Exception:
            return True
        if not math.isfinite(old):
            return True
        return abs(old - current) > eps

    @staticmethod
    def _parse_timestamp(value: Any, *, fallback_ts: float) -> float:
        fallback = max(0.0, float(fallback_ts))
        if isinstance(value, (int, float)):
            ts = float(value)
            if math.isfinite(ts) and ts > 0.0:
                return ts
            return fallback
        text = str(value or "").strip()
        if not text:
            return fallback
        try:
            ts = float(text)
            if math.isfinite(ts) and ts > 0.0:
                return ts
        except Exception:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            if math.isfinite(ts) and ts > 0.0:
                return ts
        except Exception:
            pass
        return fallback

    @staticmethod
    def _iso_utc(ts: float) -> str:
        return datetime.fromtimestamp(max(0.0, float(ts)), tz=timezone.utc).isoformat()

    def _item_entry(self, item_id: str) -> dict[str, Any]:
        self._load()
        items = self._data.setdefault("items", {})
        item = items.get(item_id)
        if not isinstance(item, dict):
            item = {}
            items[item_id] = item
        strategies = item.get("strategies")
        if not isinstance(strategies, dict):
            item["strategies"] = {}
        return item

    def _resolve_optional_budget_params(
        self,
        *,
        max_retries: Any = None,
        decay_seconds: Any = None,
        extras: dict[str, Any] | None = None,
    ) -> tuple[int, float]:
        rest = dict(extras or {})

        if max_retries is None:
            for key in ("budget", "max_budget", "capacity", "max_attempts", "retry_budget"):
                if key in rest:
                    max_retries = rest.get(key)
                    break
        if decay_seconds is None:
            for key in (
                "refill_seconds",
                "retry_decay_seconds",
                "budget_decay_seconds",
                "decay",
                "refill_interval_seconds",
            ):
                if key in rest:
                    decay_seconds = rest.get(key)
                    break

        capacity = self._as_non_negative_int(max_retries, 0)
        refill = self._as_non_negative_float(decay_seconds, 0.0)
        return capacity, refill

    def _optional_retry_budget_state(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        extras: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        token = str(item_id).strip().lower()
        now = self._as_non_negative_float(time.time() if now_ts is None else now_ts, time.time())
        capacity, refill = self._resolve_optional_budget_params(
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            extras=extras,
        )

        item = self._item_entry(token)
        changed = False
        budget = item.get("optional_retry_budget")
        if not isinstance(budget, dict):
            budget = {}
            item["optional_retry_budget"] = budget
            changed = True

        legacy_remaining = item.get("optional_retry_budget_remaining")
        legacy_updated = item.get("optional_retry_budget_last_updated_at")
        if legacy_remaining is not None and "remaining" not in budget:
            budget["remaining"] = legacy_remaining
            changed = True
        if legacy_updated is not None and "last_updated_ts" not in budget and "last_updated_at" not in budget:
            budget["last_updated_at"] = legacy_updated
            changed = True

        remaining_default = float(capacity)
        remaining = self._as_non_negative_float(budget.get("remaining", remaining_default), remaining_default)
        if capacity <= 0:
            remaining = 0.0
        else:
            remaining = min(float(capacity), remaining)

        last_raw = budget.get("last_updated_ts")
        if last_raw is None:
            last_raw = budget.get("last_updated_at")
        if last_raw is None:
            last_raw = legacy_updated
        last_updated_ts = self._parse_timestamp(last_raw, fallback_ts=now)
        if last_updated_ts > now:
            last_updated_ts = now
            changed = True

        if capacity <= 0:
            if remaining > 0.0:
                remaining = 0.0
                changed = True
            if last_updated_ts != now:
                last_updated_ts = now
                changed = True
        elif refill <= 0.0:
            if remaining != float(capacity):
                remaining = float(capacity)
                changed = True
            if abs(last_updated_ts - now) > 1e-6:
                last_updated_ts = now
                changed = True
        else:
            if remaining >= float(capacity):
                if remaining != float(capacity):
                    remaining = float(capacity)
                    changed = True
                if abs(last_updated_ts - now) > 1e-6:
                    last_updated_ts = now
                    changed = True
            elif now > last_updated_ts:
                regained = (now - last_updated_ts) / refill
                if regained > 0.0:
                    remaining = min(float(capacity), remaining + regained)
                    last_updated_ts = now
                    changed = True

        remaining = round(max(0.0, min(float(capacity), remaining if capacity > 0 else 0.0)), 6)
        last_updated_ts = round(max(0.0, last_updated_ts), 6)
        last_updated_at = self._iso_utc(last_updated_ts)

        if self._float_changed(budget.get("remaining"), remaining, eps=1e-6):
            budget["remaining"] = remaining
            changed = True
        if self._float_changed(budget.get("last_updated_ts"), last_updated_ts, eps=1e-6):
            budget["last_updated_ts"] = last_updated_ts
            changed = True
        if str(budget.get("last_updated_at", "")) != last_updated_at:
            budget["last_updated_at"] = last_updated_at
            changed = True
        if self._as_non_negative_int(budget.get("capacity_hint"), -1) != capacity:
            budget["capacity_hint"] = capacity
            changed = True
        if self._float_changed(budget.get("refill_seconds_hint"), refill, eps=1e-6):
            budget["refill_seconds_hint"] = round(refill, 6)
            changed = True

        remaining_attempts = max(0, int(math.floor(remaining + 1e-9)))
        can_retry = remaining >= 1.0 - 1e-9
        retry_ready_in_seconds: float | None = 0.0 if can_retry else None
        full_refill_in_seconds: float | None = 0.0 if remaining >= float(capacity) else None
        if capacity > 0 and refill > 0.0:
            if not can_retry:
                retry_ready_in_seconds = max(0.0, round((1.0 - remaining) * refill, 3))
            if remaining < float(capacity):
                full_refill_in_seconds = max(0.0, round((float(capacity) - remaining) * refill, 3))

        status = {
            "item_id": token,
            "capacity": capacity,
            "max_retries": capacity,
            "refill_seconds": refill,
            "decay_seconds": refill,
            "remaining": remaining,
            "remaining_attempts": remaining_attempts,
            "remaining_retries": remaining_attempts,
            "can_retry": can_retry,
            "last_updated_ts": last_updated_ts,
            "last_updated_at": last_updated_at,
            "retry_ready_in_seconds": retry_ready_in_seconds,
            "full_refill_in_seconds": full_refill_in_seconds,
        }
        return budget, status, changed

    def _strategy_stats(self, item_id: str, strategy: str) -> dict[str, Any]:
        item = self._item_entry(item_id)
        strategies = item.setdefault("strategies", {})
        if not isinstance(strategies, dict):
            strategies = {}
            item["strategies"] = strategies
        return strategies.setdefault(
            strategy,
            {
                "attempts": 0,
                "successes": 0,
                "total_duration_seconds": 0.0,
                "last_error": "",
                "last_success_at": "",
                "last_failure_at": "",
            },
        )

    def get_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = str(item_id).strip().lower()
        if not token:
            capacity, refill = self._resolve_optional_budget_params(
                max_retries=max_retries,
                decay_seconds=decay_seconds,
                extras=kwargs,
            )
            return {
                "item_id": "",
                "capacity": capacity,
                "max_retries": capacity,
                "refill_seconds": refill,
                "decay_seconds": refill,
                "remaining": 0.0,
                "remaining_attempts": 0,
                "remaining_retries": 0,
                "can_retry": False,
                "last_updated_ts": 0.0,
                "last_updated_at": "",
                "retry_ready_in_seconds": None,
                "full_refill_in_seconds": None,
            }
        _, status, changed = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            extras=kwargs,
        )
        if changed:
            self._save()
        return status

    def optional_retry_budget_status(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.get_optional_retry_budget(
            item_id=item_id,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            **kwargs,
        )

    def get_optional_item_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.get_optional_retry_budget(
            item_id=item_id,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            **kwargs,
        )

    def peek_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.get_optional_retry_budget(
            item_id=item_id,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            **kwargs,
        )

    def consume_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        cost: Any = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = str(item_id).strip().lower()
        spend = self._as_non_negative_int(kwargs.get("amount", kwargs.get("count", cost)), 1)
        if spend <= 0:
            spend = 1
        if not token:
            status = self.get_optional_retry_budget(
                item_id=token,
                max_retries=max_retries,
                decay_seconds=decay_seconds,
                now_ts=now_ts,
                **kwargs,
            )
            status.update({"consumed": False, "cost": spend})
            return status

        budget, status, changed = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            extras=kwargs,
        )
        remaining = self._as_non_negative_float(status.get("remaining", 0.0), 0.0)
        consumed = remaining >= float(spend) - 1e-9
        if consumed:
            now = self._as_non_negative_float(time.time() if now_ts is None else now_ts, time.time())
            remaining_after = max(0.0, remaining - float(spend))
            last_updated_ts = round(now, 6)
            last_updated_at = self._iso_utc(last_updated_ts)
            if self._float_changed(budget.get("remaining"), round(remaining_after, 6), eps=1e-6):
                budget["remaining"] = round(remaining_after, 6)
                changed = True
            if self._float_changed(budget.get("last_updated_ts"), last_updated_ts, eps=1e-6):
                budget["last_updated_ts"] = last_updated_ts
                changed = True
            if str(budget.get("last_updated_at", "")) != last_updated_at:
                budget["last_updated_at"] = last_updated_at
                changed = True
            _, status, _ = self._optional_retry_budget_state(
                item_id=token,
                max_retries=max_retries,
                decay_seconds=decay_seconds,
                now_ts=now,
                extras=kwargs,
            )
            changed = True

        if changed:
            self._save()
        status.update({"consumed": consumed, "cost": spend})
        return status

    def consume_optional_item_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        cost: Any = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.consume_optional_retry_budget(
            item_id=item_id,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            cost=cost,
            **kwargs,
        )

    def refund_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        amount: Any = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = str(item_id).strip().lower()
        restore = self._as_non_negative_int(kwargs.get("cost", kwargs.get("count", amount)), 1)
        if restore <= 0:
            restore = 1
        if not token:
            status = self.get_optional_retry_budget(
                item_id=token,
                max_retries=max_retries,
                decay_seconds=decay_seconds,
                now_ts=now_ts,
                **kwargs,
            )
            status.update({"refunded": False, "amount": restore})
            return status

        budget, status, changed = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            extras=kwargs,
        )
        capacity = self._as_non_negative_int(status.get("capacity", 0), 0)
        remaining = self._as_non_negative_float(status.get("remaining", 0.0), 0.0)
        now = self._as_non_negative_float(time.time() if now_ts is None else now_ts, time.time())
        remaining_after = min(float(capacity), remaining + float(restore))
        last_updated_ts = round(now, 6)
        last_updated_at = self._iso_utc(last_updated_ts)
        refunded = remaining_after > remaining + 1e-9
        if self._float_changed(budget.get("remaining"), round(remaining_after, 6), eps=1e-6):
            budget["remaining"] = round(remaining_after, 6)
            changed = True
        if self._float_changed(budget.get("last_updated_ts"), last_updated_ts, eps=1e-6):
            budget["last_updated_ts"] = last_updated_ts
            changed = True
        if str(budget.get("last_updated_at", "")) != last_updated_at:
            budget["last_updated_at"] = last_updated_at
            changed = True
        _, status, _ = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now,
            extras=kwargs,
        )
        if changed:
            self._save()
        status.update({"refunded": refunded, "amount": restore})
        return status

    def refill_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        amount: Any = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.refund_optional_retry_budget(
            item_id=item_id,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            amount=amount,
            **kwargs,
        )

    def reset_optional_retry_budget(
        self,
        *,
        item_id: str,
        max_retries: Any = None,
        decay_seconds: Any = None,
        now_ts: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = str(item_id).strip().lower()
        if not token:
            return self.get_optional_retry_budget(
                item_id=token,
                max_retries=max_retries,
                decay_seconds=decay_seconds,
                now_ts=now_ts,
                **kwargs,
            )

        budget, status, changed = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now_ts,
            extras=kwargs,
        )
        capacity = self._as_non_negative_int(status.get("capacity", 0), 0)
        now = self._as_non_negative_float(time.time() if now_ts is None else now_ts, time.time())
        budget["remaining"] = float(capacity)
        budget["last_updated_ts"] = round(now, 6)
        budget["last_updated_at"] = self._iso_utc(now)
        changed = True
        _, status, _ = self._optional_retry_budget_state(
            item_id=token,
            max_retries=max_retries,
            decay_seconds=decay_seconds,
            now_ts=now,
            extras=kwargs,
        )
        if changed:
            self._save()
        return status

    def clear_optional_retry_budget(self, *, item_id: str) -> bool:
        token = str(item_id).strip().lower()
        if not token:
            return False
        item = self._item_entry(token)
        removed = False
        if "optional_retry_budget" in item:
            item.pop("optional_retry_budget", None)
            removed = True
        if "optional_retry_budget_remaining" in item:
            item.pop("optional_retry_budget_remaining", None)
            removed = True
        if "optional_retry_budget_last_updated_at" in item:
            item.pop("optional_retry_budget_last_updated_at", None)
            removed = True
        if removed:
            self._save()
        return removed

    def record_attempt(
        self,
        *,
        item_id: str,
        strategy: str,
        success: bool,
        duration_seconds: float,
        error: str = "",
    ) -> None:
        token = str(item_id).strip().lower()
        mode = str(strategy).strip().lower()
        if not token or not mode:
            return
        stats = self._strategy_stats(token, mode)
        stats["attempts"] = int(stats.get("attempts", 0)) + 1
        if success:
            stats["successes"] = int(stats.get("successes", 0)) + 1
            stats["last_success_at"] = datetime.now(timezone.utc).isoformat()
            stats["last_error"] = ""
        else:
            stats["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            stats["last_error"] = str(error or "")
        try:
            dur = float(duration_seconds)
        except Exception:
            dur = 0.0
        if dur > 0.0:
            stats["total_duration_seconds"] = float(stats.get("total_duration_seconds", 0.0)) + dur
        self._save()

    @staticmethod
    def _score(stats: dict[str, Any]) -> float:
        attempts = max(0, int(stats.get("attempts", 0)))
        successes = max(0, int(stats.get("successes", 0)))
        total_duration = max(0.0, float(stats.get("total_duration_seconds", 0.0)))
        success_rate = (successes + 1.0) / (attempts + 2.0)
        avg_duration = total_duration / max(1.0, successes)
        speed_bonus = 1.0 / (1.0 + (avg_duration / 45.0))
        experience_bonus = min(0.35, attempts * 0.02)
        return success_rate + (0.35 * speed_bonus) + experience_bonus

    def rank_strategies(
        self,
        *,
        item_id: str,
        candidates: list[str],
        preferred: list[str] | None = None,
        exploration_rate: float = 0.15,
    ) -> list[str]:
        token = str(item_id).strip().lower()
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            mode = str(raw).strip().lower()
            if not mode or mode in seen:
                continue
            seen.add(mode)
            ordered.append(mode)
        if not ordered:
            return []

        self._load()
        preferred_map: dict[str, float] = {}
        pref = [str(v).strip().lower() for v in (preferred or []) if str(v).strip()]
        for idx, key in enumerate(pref):
            preferred_map[key] = max(preferred_map.get(key, 0.0), 0.45 - (idx * 0.1))

        scored: list[tuple[float, str]] = []
        unseen: list[str] = []
        for mode in ordered:
            stats = self._strategy_stats(token, mode)
            if int(stats.get("attempts", 0)) <= 0:
                unseen.append(mode)
            score = self._score(stats) + preferred_map.get(mode, 0.0)
            scored.append((score, mode))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        ranked = [mode for _, mode in scored]

        explore = max(0.0, min(0.8, float(exploration_rate)))
        if unseen and random.random() < explore:
            first_unseen = unseen[0]
            ranked = [first_unseen] + [mode for mode in ranked if mode != first_unseen]
        return ranked
