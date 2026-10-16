"""Attempt-level accumulation of pi's per-message usage.

Every evaluation record reports usage totals over one attempt (all
assistant messages summed: pi reports the real values per message,
others ~0). Summing — rather than taking the last message — survives
timeout and early-exit kills. This module owns the key vocabulary
(which token keys exist, how the nested cost folds); consumers hold an
accumulator per attempt and read `.usage` once at the end.
"""

from __future__ import annotations

from .models import CostBreakdown, PiUsage

_TOKEN_KEYS = ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens")
_COST_KEYS = ("input", "output", "cacheRead", "cacheWrite", "total")


class UsageAccumulator:
    """Fold per-message `PiUsage` into one attempt-level total."""

    def __init__(self) -> None:
        """Start from zero totals."""
        self._tokens: dict[str, int] = {}
        self._cost: dict[str, float] = {}

    def add(self, usage: PiUsage | None) -> None:
        """Add one message's usage; None (messages without usage) is a no-op."""
        if usage is None:
            return
        for key in _TOKEN_KEYS:
            value = getattr(usage, key)
            if value:
                self._tokens[key] = self._tokens.get(key, 0) + value
        cost = usage.cost
        for key in _COST_KEYS:
            value = getattr(cost, key)
            if value:
                self._cost[key] = self._cost.get(key, 0.0) + value

    @property
    def usage(self) -> PiUsage:
        """The accumulated totals as pi's own usage shape.

        `input` is the *uncached* token count — the provider serves
        repeated context from cache and reports it under `cacheRead`, so
        the real context size is input + cacheRead (or just totalTokens).
        Costs are rounded to 6 decimals (per-message floats accumulate
        float noise otherwise).
        """
        tokens = {key: self._tokens.get(key, 0) for key in _TOKEN_KEYS}
        return PiUsage(
            **tokens,
            cost=CostBreakdown(
                **{key: round(self._cost.get(key, 0.0), 6) for key in _COST_KEYS},
            ),
        )
