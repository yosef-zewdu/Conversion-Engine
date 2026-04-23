"""
LLM call tracer for the Conversion Engine.

Wraps every LLM call with Langfuse trace emission covering:
- model name
- prompt token count
- completion token count
- wall-clock latency

Also enforces the budget guard: when total LLM spend exceeds $20, emits a
warning to the operator log and halts new LLM calls until acknowledged.

Requirements: 11.1, 11.5
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget guard constants
# ---------------------------------------------------------------------------

_BUDGET_LIMIT_USD = 20.0
_budget_acknowledged: bool = False
_total_llm_spend_usd: float = 0.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class LLMCallRecord:
    """Record of a single LLM call for observability purposes.

    Attributes:
        model: Model identifier string (e.g. ``"openrouter/qwen/qwen3-235b-a22b"``).
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        latency_seconds: Wall-clock latency of the call in seconds.
        cost_usd: Estimated cost of the call in USD.
        trace_id: Langfuse trace ID, if emitted.
        prospect_id: Associated prospect ID, if applicable.
        component: Name of the component that made the call (e.g. ``"nurture_sequencer"``).
        metadata: Additional metadata dict.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float
    cost_usd: float = 0.0
    trace_id: str | None = None
    prospect_id: str | None = None
    component: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Budget guard
# ---------------------------------------------------------------------------


def is_budget_exceeded() -> bool:
    """Return True when total LLM spend has exceeded the $20 budget limit.

    Returns:
        ``True`` when ``_total_llm_spend_usd >= _BUDGET_LIMIT_USD`` and the
        budget has not been acknowledged.
    """
    return _total_llm_spend_usd >= _BUDGET_LIMIT_USD and not _budget_acknowledged


def acknowledge_budget_exceeded() -> None:
    """Acknowledge the budget-exceeded state, allowing LLM calls to resume.

    Sets the module-level ``_budget_acknowledged`` flag so that subsequent
    calls are not blocked.
    """
    global _budget_acknowledged
    _budget_acknowledged = True
    logger.info(
        "Budget-exceeded state acknowledged by operator. LLM calls will resume."
    )


def reset_budget_state() -> None:
    """Reset budget tracking state (intended for testing only).

    Resets both ``_total_llm_spend_usd`` and ``_budget_acknowledged`` to their
    initial values.
    """
    global _total_llm_spend_usd, _budget_acknowledged
    _total_llm_spend_usd = 0.0
    _budget_acknowledged = False


def get_total_llm_spend() -> float:
    """Return the current total LLM spend in USD.

    Returns:
        Total accumulated LLM spend across all calls since last reset.
    """
    return _total_llm_spend_usd


# ---------------------------------------------------------------------------
# Langfuse emission
# ---------------------------------------------------------------------------


def _emit_llm_trace(record: LLMCallRecord) -> str | None:
    """Emit a Langfuse trace for a single LLM call.

    Args:
        record: The ``LLMCallRecord`` to emit.

    Returns:
        The Langfuse trace ID string, or ``None`` if emission failed.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning(
            "Langfuse keys not configured — skipping LLM trace for model=%r", record.model
        )
        return None

    try:
        from langfuse import Langfuse  # local import to avoid hard startup dependency

        lf = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url)
        trace = lf.trace(
            name=f"llm_call.{record.component}",
            input={"model": record.model, "prompt_tokens": record.prompt_tokens},
            output={
                "completion_tokens": record.completion_tokens,
                "cost_usd": record.cost_usd,
            },
            metadata={
                "model": record.model,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "latency_seconds": record.latency_seconds,
                "cost_usd": record.cost_usd,
                "prospect_id": record.prospect_id,
                "component": record.component,
                **record.metadata,
            },
        )
        lf.flush()
        trace_id = getattr(trace, "id", None) or getattr(trace, "trace_id", None)
        return str(trace_id) if trace_id else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse LLM trace emission failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public tracer
# ---------------------------------------------------------------------------


def trace_llm_call(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_seconds: float,
    cost_usd: float = 0.0,
    prospect_id: str | None = None,
    component: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> LLMCallRecord:
    """Record and trace a completed LLM call.

    Emits a Langfuse trace and updates the global budget accumulator.
    Logs a warning when the budget limit is exceeded (Req 11.5).

    Args:
        model: Model identifier string.
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        latency_seconds: Wall-clock latency of the call in seconds.
        cost_usd: Estimated cost of the call in USD.
        prospect_id: Associated prospect ID, if applicable.
        component: Name of the calling component.
        metadata: Additional metadata to attach to the trace.

    Returns:
        The ``LLMCallRecord`` with the emitted ``trace_id`` populated.
    """
    global _total_llm_spend_usd

    record = LLMCallRecord(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_seconds=latency_seconds,
        cost_usd=cost_usd,
        prospect_id=prospect_id,
        component=component,
        metadata=metadata or {},
    )

    # Emit trace
    record.trace_id = _emit_llm_trace(record)

    # Update budget accumulator
    _total_llm_spend_usd += cost_usd

    # Budget guard (Req 11.5)
    if _total_llm_spend_usd >= _BUDGET_LIMIT_USD and not _budget_acknowledged:
        logger.warning(
            "BUDGET EXCEEDED: total LLM spend $%.4f has exceeded the $%.2f limit. "
            "New LLM calls are halted until the operator calls acknowledge_budget_exceeded().",
            _total_llm_spend_usd,
            _BUDGET_LIMIT_USD,
        )

    return record


def guard_llm_call() -> None:
    """Raise RuntimeError if the budget has been exceeded and not acknowledged.

    Call this at the start of any function that makes an LLM call to enforce
    the budget guard (Req 11.5).

    Raises:
        RuntimeError: When total LLM spend >= $20 and not yet acknowledged.
    """
    if is_budget_exceeded():
        raise RuntimeError(
            f"LLM budget exceeded (${_total_llm_spend_usd:.4f} >= "
            f"${_BUDGET_LIMIT_USD:.2f}). Call acknowledge_budget_exceeded() to resume."
        )


def timed_llm_call(
    fn: Callable[..., Any],
    *args: Any,
    model: str,
    cost_per_1k_prompt: float = 0.0,
    cost_per_1k_completion: float = 0.0,
    prospect_id: str | None = None,
    component: str = "unknown",
    **kwargs: Any,
) -> tuple[Any, LLMCallRecord]:
    """Execute a synchronous LLM call function and record its trace.

    Enforces the budget guard before calling, measures wall-clock latency,
    and emits a Langfuse trace after completion.

    Args:
        fn: The callable that performs the LLM call. Must return a dict with
            ``prompt_tokens``, ``completion_tokens``, and ``model`` keys.
        *args: Positional arguments forwarded to ``fn``.
        model: Model identifier (used when ``fn`` result lacks ``model`` key).
        cost_per_1k_prompt: Cost in USD per 1,000 prompt tokens.
        cost_per_1k_completion: Cost in USD per 1,000 completion tokens.
        prospect_id: Associated prospect ID.
        component: Name of the calling component.
        **kwargs: Keyword arguments forwarded to ``fn``.

    Returns:
        A tuple of ``(fn_result, LLMCallRecord)``.

    Raises:
        RuntimeError: When the budget guard is active.
    """
    guard_llm_call()

    start = time.monotonic()
    result = fn(*args, **kwargs)
    latency = time.monotonic() - start

    prompt_tokens = 0
    completion_tokens = 0
    actual_model = model

    if isinstance(result, dict):
        usage = result.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        actual_model = result.get("model", model)

    cost_usd = (
        prompt_tokens / 1000.0 * cost_per_1k_prompt
        + completion_tokens / 1000.0 * cost_per_1k_completion
    )

    record = trace_llm_call(
        model=actual_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_seconds=latency,
        cost_usd=cost_usd,
        prospect_id=prospect_id,
        component=component,
    )

    return result, record
