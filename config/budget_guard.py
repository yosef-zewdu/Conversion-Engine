"""
Budget guard — tracks cumulative LLM spend and halts new calls when the
$20 limit is exceeded (Requirement 11.5).

Spend is tracked in-process via a module-level counter. On each LLM call
completion, the caller reports token usage; the guard computes cost using
per-model rates and accumulates the total.

When total spend exceeds BUDGET_LIMIT_USD:
  - emit a warning to the operator log
  - raise BudgetExceededError so node_llm can abort the call
  - the error is caught by the graph and a fallback reply is used

Usage::

    from config.budget_guard import BudgetGuard

    guard = get_budget_guard()
    guard.check()                          # raises if over budget
    guard.record(prompt_tokens=500,
                 completion_tokens=200,
                 model="qwen/qwen3-235b-a22b")
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUDGET_LIMIT_USD: float = 20.0

# Cost per 1K tokens (prompt / completion) by model prefix.
# OpenRouter pricing as of April 2026 — update if rates change.
_MODEL_RATES: dict[str, tuple[float, float]] = {
    "qwen/qwen3-235b-a22b":        (0.00014, 0.00060),
    "qwen/qwen3":                  (0.00014, 0.00060),
    "deepseek/deepseek-v3":        (0.00014, 0.00028),
    "deepseek":                    (0.00014, 0.00028),
    "anthropic/claude":            (0.00300, 0.01500),
    "openai/gpt-4":                (0.00300, 0.06000),
    "openai/gpt-3.5":              (0.00050, 0.00150),
    # fallback
    "_default":                    (0.00100, 0.00200),
}


class BudgetExceededError(RuntimeError):
    """Raised when cumulative LLM spend exceeds BUDGET_LIMIT_USD."""

    def __init__(self, total_usd: float) -> None:
        super().__init__(
            f"LLM budget exceeded: ${total_usd:.4f} >= ${BUDGET_LIMIT_USD:.2f}. "
            "Halting new LLM calls until operator acknowledges."
        )
        self.total_usd = total_usd


class BudgetGuard:
    """Thread-safe cumulative LLM spend tracker.

    Args:
        limit_usd: Spend limit in USD. Defaults to BUDGET_LIMIT_USD.
        acknowledged: Set to True to resume after operator acknowledgement.
    """

    def __init__(self, limit_usd: float = BUDGET_LIMIT_USD) -> None:
        self._limit = limit_usd
        self._total_usd: float = 0.0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._call_count: int = 0
        self._lock = threading.Lock()
        self._acknowledged: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Check whether the budget has been exceeded.

        Raises:
            BudgetExceededError: When total spend >= limit and not acknowledged.
        """
        with self._lock:
            if self._total_usd >= self._limit and not self._acknowledged:
                logger.warning(
                    "BUDGET GUARD: LLM spend $%.4f has exceeded limit $%.2f. "
                    "Call budget_guard.acknowledge() to resume.",
                    self._total_usd,
                    self._limit,
                )
                raise BudgetExceededError(self._total_usd)

    def record(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model: str = "_default",
    ) -> float:
        """Record token usage from a completed LLM call and update spend.

        Args:
            prompt_tokens: Number of prompt tokens used.
            completion_tokens: Number of completion tokens used.
            model: Model identifier string (used to look up per-token rate).

        Returns:
            Cost of this call in USD.
        """
        prompt_rate, completion_rate = self._rate_for(model)
        call_cost = (
            (prompt_tokens / 1000) * prompt_rate
            + (completion_tokens / 1000) * completion_rate
        )

        with self._lock:
            self._total_usd += call_cost
            self._total_prompt_tokens += prompt_tokens
            self._total_completion_tokens += completion_tokens
            self._call_count += 1
            total = self._total_usd

        logger.info(
            "LLM spend: call_cost=$%.4f total=$%.4f "
            "prompt_tokens=%d completion_tokens=%d model=%s",
            call_cost,
            total,
            prompt_tokens,
            completion_tokens,
            model,
        )

        if total >= self._limit and not self._acknowledged:
            logger.warning(
                "BUDGET GUARD: total LLM spend $%.4f has reached the $%.2f limit. "
                "New LLM calls will be blocked until acknowledged.",
                total,
                self._limit,
            )

        return call_cost

    def acknowledge(self) -> None:
        """Operator acknowledgement — allows LLM calls to resume.

        Call this after reviewing the spend and deciding to continue.
        """
        with self._lock:
            self._acknowledged = True
            logger.warning(
                "BUDGET GUARD: operator acknowledged spend of $%.4f. "
                "LLM calls resumed.",
                self._total_usd,
            )

    def reset_acknowledgement(self) -> None:
        """Reset acknowledgement flag (e.g. after adding budget)."""
        with self._lock:
            self._acknowledged = False

    @property
    def total_usd(self) -> float:
        """Current cumulative spend in USD."""
        with self._lock:
            return self._total_usd

    @property
    def call_count(self) -> int:
        """Total number of LLM calls recorded."""
        with self._lock:
            return self._call_count

    def summary(self) -> dict:
        """Return a summary dict for logging and Langfuse emission.

        Returns:
            Dict with total_usd, call_count, prompt_tokens, completion_tokens.
        """
        with self._lock:
            return {
                "total_usd": round(self._total_usd, 6),
                "limit_usd": self._limit,
                "call_count": self._call_count,
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "acknowledged": self._acknowledged,
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rate_for(model: str) -> tuple[float, float]:
        """Look up prompt/completion rates for a model string.

        Args:
            model: Model identifier (e.g. "qwen/qwen3-235b-a22b").

        Returns:
            Tuple of (prompt_rate_per_1k, completion_rate_per_1k).
        """
        model_lower = model.lower()
        for prefix, rates in _MODEL_RATES.items():
            if prefix != "_default" and model_lower.startswith(prefix):
                return rates
        return _MODEL_RATES["_default"]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_guard: Optional[BudgetGuard] = None
_guard_lock = threading.Lock()


def get_budget_guard() -> BudgetGuard:
    """Return the module-level BudgetGuard singleton.

    Returns:
        The shared BudgetGuard instance.
    """
    global _guard
    with _guard_lock:
        if _guard is None:
            limit = float(os.environ.get("LLM_BUDGET_USD", str(BUDGET_LIMIT_USD)))
            _guard = BudgetGuard(limit_usd=limit)
    return _guard
