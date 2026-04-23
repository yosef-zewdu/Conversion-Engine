"""
AIMaturityScorer — computes an AI maturity score (0–3) from six weighted signals.

Req 3.1: Compute AI_Maturity_Score integer 0–3 using weighted signal inputs.
Req 3.2: Record per-signal justification alongside the AI_Maturity_Score.
Req 3.3: If score derived from fewer than 2 high-weight signals, set
         ai_maturity_confidence: "low".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from signal_pipeline.models import AIMaturityJustificationEntry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_POSSIBLE: float = 12.0  # 3+3+2+2+1+1

# Ratio thresholds → integer score
_THRESHOLDS: list[tuple[float, int]] = [
    (0.70, 3),
    (0.40, 2),
    (0.15, 1),
    (0.00, 0),
]


# ---------------------------------------------------------------------------
# Input / Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AIMaturityInput:
    """Six weighted signals used to compute the AI maturity score."""

    # High-weight signals
    ai_adjacent_open_roles: Optional[int] = None    # count of AI/ML job postings
    named_ai_ml_leadership: Optional[bool] = None   # CTO/VP Eng with AI/ML background

    # Medium-weight signals
    github_ai_activity: Optional[bool] = None       # public GitHub repos with AI/ML keywords
    executive_ai_commentary: Optional[bool] = None  # CEO/CTO public statements about AI

    # Low-weight signals
    modern_data_ml_stack: Optional[bool] = None     # uses PyTorch, TensorFlow, Spark, etc.
    strategic_communications: Optional[bool] = None # press releases mentioning AI strategy


@dataclass
class AIMaturityResult:
    """Output of AIMaturityScorer.score()."""

    score: int                                          # 0–3
    confidence: str                                     # "high" | "medium" | "low"
    justification: list[AIMaturityJustificationEntry]   # one entry per signal


# ---------------------------------------------------------------------------
# Signal scoring helpers
# ---------------------------------------------------------------------------


def _score_ai_adjacent_open_roles(value: Optional[int]) -> float:
    """
    Map an AI/ML open-role count to a raw score in [0.0, 1.0].

    Args:
        value: Number of AI/ML open roles, or None.

    Returns:
        0.0 for None/0, 0.3 for 1–2, 0.6 for 3–4, 1.0 for 5+.
    """
    if value is None or value == 0:
        return 0.0
    if value <= 2:
        return 0.3
    if value <= 4:
        return 0.6
    return 1.0


def _score_bool_signal(value: Optional[bool]) -> float:
    """
    Map a boolean signal to a raw score in [0.0, 1.0].

    Args:
        value: True → 1.0, False/None → 0.0.
    """
    return 1.0 if value is True else 0.0


def _justify_ai_adjacent_open_roles(value: Optional[int], raw: float) -> str:
    """
    Produce a human-readable justification for the ai_adjacent_open_roles signal.

    Args:
        value: Raw input value.
        raw: Computed raw score.
    """
    if value is None:
        return "No AI/ML open roles data available → no hiring signal"
    if value == 0:
        return "0 AI/ML open roles detected → no AI hiring signal"
    if value <= 2:
        return f"{value} AI/ML open role(s) detected → weak AI hiring signal"
    if value <= 4:
        return f"{value} AI/ML open roles detected → moderate AI hiring signal"
    return f"{value} AI/ML open roles detected → high AI hiring signal"


def _justify_bool(signal_name: str, value: Optional[bool], positive_msg: str, negative_msg: str) -> str:
    """
    Produce a human-readable justification for a boolean signal.

    Args:
        signal_name: Name of the signal (for logging context).
        value: Raw boolean input.
        positive_msg: Message when value is True.
        negative_msg: Message when value is False or None.
    """
    return positive_msg if value is True else negative_msg


# ---------------------------------------------------------------------------
# Weighted sum → integer score
# ---------------------------------------------------------------------------


def _ratio_to_score(ratio: float) -> int:
    """
    Map a weighted-sum ratio in [0.0, 1.0] to an integer score 0–3.

    Args:
        ratio: weighted_sum / max_possible.

    Returns:
        Integer in {0, 1, 2, 3}.
    """
    for threshold, score in _THRESHOLDS:
        if ratio >= threshold:
            return score
    return 0


# ---------------------------------------------------------------------------
# Confidence assignment
# ---------------------------------------------------------------------------


def _assign_confidence(inputs: AIMaturityInput) -> str:
    """
    Assign confidence based on how many high-weight signals are present.

    Counts:
    - ai_adjacent_open_roles > 0 → 1 high-weight signal
    - named_ai_ml_leadership is True → 1 high-weight signal

    Returns:
        "low" if count < 2, "medium" if count == 2.

    Args:
        inputs: The AIMaturityInput being scored.
    """
    count = 0
    if inputs.ai_adjacent_open_roles is not None and inputs.ai_adjacent_open_roles > 0:
        count += 1
    if inputs.named_ai_ml_leadership is True:
        count += 1
    return "low" if count < 2 else "medium"


# ---------------------------------------------------------------------------
# AIMaturityScorer
# ---------------------------------------------------------------------------


class AIMaturityScorer:
    """
    Computes an AI maturity score (integer 0–3) from six weighted signals.

    Implements Req 3.1, 3.2, and 3.3.
    """

    def score(self, inputs: AIMaturityInput) -> AIMaturityResult:
        """
        Compute AI maturity score from weighted signal inputs.

        Returns AIMaturityResult with score, confidence, and per-signal justification.

        Args:
            inputs: AIMaturityInput containing the six signal values.

        Returns:
            AIMaturityResult with integer score 0–3, confidence level, and
            one AIMaturityJustificationEntry per signal.
        """
        # --- Raw scores ---
        roles_raw = _score_ai_adjacent_open_roles(inputs.ai_adjacent_open_roles)
        leadership_raw = _score_bool_signal(inputs.named_ai_ml_leadership)
        github_raw = _score_bool_signal(inputs.github_ai_activity)
        commentary_raw = _score_bool_signal(inputs.executive_ai_commentary)
        stack_raw = _score_bool_signal(inputs.modern_data_ml_stack)
        comms_raw = _score_bool_signal(inputs.strategic_communications)

        # --- Weighted sum ---
        weighted_sum = (
            roles_raw * 3.0
            + leadership_raw * 3.0
            + github_raw * 2.0
            + commentary_raw * 2.0
            + stack_raw * 1.0
            + comms_raw * 1.0
        )

        ratio = weighted_sum / _MAX_POSSIBLE
        final_score = _ratio_to_score(ratio)
        confidence = _assign_confidence(inputs)

        # --- Per-signal justification ---
        justification: list[AIMaturityJustificationEntry] = [
            AIMaturityJustificationEntry(
                signal="ai_adjacent_open_roles",
                weight="high",
                value=inputs.ai_adjacent_open_roles,
                justification=_justify_ai_adjacent_open_roles(inputs.ai_adjacent_open_roles, roles_raw),
            ),
            AIMaturityJustificationEntry(
                signal="named_ai_ml_leadership",
                weight="high",
                value=inputs.named_ai_ml_leadership,
                justification=_justify_bool(
                    "named_ai_ml_leadership",
                    inputs.named_ai_ml_leadership,
                    "Named AI/ML leadership detected → strong organizational AI signal",
                    "No named AI/ML leadership detected → weak organizational AI signal",
                ),
            ),
            AIMaturityJustificationEntry(
                signal="github_ai_activity",
                weight="medium",
                value=inputs.github_ai_activity,
                justification=_justify_bool(
                    "github_ai_activity",
                    inputs.github_ai_activity,
                    "Public GitHub AI/ML activity detected → active AI development signal",
                    "No public GitHub AI/ML activity detected → no active AI development signal",
                ),
            ),
            AIMaturityJustificationEntry(
                signal="executive_ai_commentary",
                weight="medium",
                value=inputs.executive_ai_commentary,
                justification=_justify_bool(
                    "executive_ai_commentary",
                    inputs.executive_ai_commentary,
                    "Executive AI commentary detected → AI is a stated priority",
                    "No executive AI commentary detected → AI not a stated priority",
                ),
            ),
            AIMaturityJustificationEntry(
                signal="modern_data_ml_stack",
                weight="low",
                value=inputs.modern_data_ml_stack,
                justification=_justify_bool(
                    "modern_data_ml_stack",
                    inputs.modern_data_ml_stack,
                    "Modern data/ML stack detected (e.g. PyTorch, Spark) → infrastructure readiness signal",
                    "No modern data/ML stack detected → limited infrastructure readiness",
                ),
            ),
            AIMaturityJustificationEntry(
                signal="strategic_communications",
                weight="low",
                value=inputs.strategic_communications,
                justification=_justify_bool(
                    "strategic_communications",
                    inputs.strategic_communications,
                    "Strategic AI communications detected → public AI commitment signal",
                    "No strategic AI communications detected → no public AI commitment signal",
                ),
            ),
        ]

        return AIMaturityResult(
            score=final_score,
            confidence=confidence,
            justification=justification,
        )
