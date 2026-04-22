"""
Schema validation and pretty-printing for HiringSignalBrief and CompetitorGapBrief.

Validation failures raise pydantic.ValidationError with descriptive messages.
Invalid documents are never written to HubSpot or Langfuse.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief


def validate_hiring_signal_brief(data: dict) -> HiringSignalBrief:
    """Validate *data* against the HiringSignalBrief schema.

    Returns a validated :class:`HiringSignalBrief` on success.

    Raises :class:`pydantic.ValidationError` with a descriptive message
    identifying every failing field on failure.  Callers MUST NOT write
    to HubSpot or Langfuse when this function raises.
    """
    return HiringSignalBrief.model_validate(data)


def validate_competitor_gap_brief(data: dict) -> CompetitorGapBrief:
    """Validate *data* against the CompetitorGapBrief schema.

    Returns a validated :class:`CompetitorGapBrief` on success.

    Raises :class:`pydantic.ValidationError` with a descriptive message
    identifying every failing field on failure.  Callers MUST NOT write
    to HubSpot or Langfuse when this function raises.
    """
    return CompetitorGapBrief.model_validate(data)


def pretty_print_hiring_signal_brief(brief: HiringSignalBrief) -> str:
    """Return a human-readable, indented JSON string for *brief*.

    The output is round-trip safe: deserializing the returned string
    produces an object equal to *brief*.
    """
    return json.dumps(
        json.loads(brief.model_dump_json(exclude_none=False)),
        indent=2,
        ensure_ascii=False,
    )


def pretty_print_competitor_gap_brief(brief: CompetitorGapBrief) -> str:
    """Return a human-readable, indented JSON string for *brief*.

    The output is round-trip safe: deserializing the returned string
    produces an object equal to *brief*.
    """
    return json.dumps(
        json.loads(brief.model_dump_json(exclude_none=False)),
        indent=2,
        ensure_ascii=False,
    )
