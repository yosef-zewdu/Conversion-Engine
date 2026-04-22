"""
Startup environment variable validation.

Exits with a descriptive error if any required variable is missing or empty.
Call validate_env() once at application startup before processing any prospect.
"""
import os
import sys

REQUIRED_ENV_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "HUBSPOT_ACCESS_TOKEN",
    "CAL_API_KEY",
    "RESEND_API_KEY",
    "AT_API_KEY",
    "OPENROUTER_API_KEY",
]


def validate_env() -> None:
    """
    Validate all required environment variables are present and non-empty.
    Exits with a descriptive error message if any are missing.
    """
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]

    if missing:
        for var in missing:
            print(f"ERROR: Required environment variable '{var}' is missing or empty.", file=sys.stderr)
        sys.exit(
            f"Startup aborted: {len(missing)} required environment variable(s) not set: "
            + ", ".join(missing)
        )
