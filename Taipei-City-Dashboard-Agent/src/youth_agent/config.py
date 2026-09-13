"""Application configuration loaded from environment variables.

Every setting is read from os.environ at import time so that missing
values fail fast with a clear message rather than surfacing as cryptic
provider errors deep in a request path.
"""

from __future__ import annotations

import os


def _require(name: str) -> str:
    """Return an env var or raise with an actionable message."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            f"Export it before starting the agent service."
        )
    return value


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"Environment variable {name!r} must be an integer, got {raw!r}"
        ) from None


# ---------------------------------------------------------------------------
# AWS Bedrock
# ---------------------------------------------------------------------------
# NO DEFAULT — guessing a model ID produces misleading "model not found"
# errors that waste debugging time.  Set this explicitly.
BEDROCK_MODEL: str = _require("BEDROCK_MODEL")
BEDROCK_REGION: str = _get("BEDROCK_REGION", "us-west-2")
BEDROCK_API_KEY: str = _get("BEDROCK_API_KEY", "")

# Bridge: pydantic-ai's bedrock provider reads AWS_BEARER_TOKEN_BEDROCK for
# bearer-token auth (the ABSK key hackathon organisers hand out).  Standard
# AWS access keys still work when BEDROCK_API_KEY is empty.
if BEDROCK_API_KEY and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = BEDROCK_API_KEY
if not os.environ.get("AWS_DEFAULT_REGION"):
    os.environ["AWS_DEFAULT_REGION"] = BEDROCK_REGION

# ---------------------------------------------------------------------------
# PostgreSQL connection strings
# ---------------------------------------------------------------------------
# On the br_dashboard Docker network (192.168.128.0/24):
#   postgres-data    — read-only access to youth_* tables
#   postgres-manager — write access to components / query_charts / component_charts
DB_DATA_DSN: str = _require("DB_DATA_DSN")
DB_MANAGER_DSN: str = _require("DB_MANAGER_DSN")

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
AGENT_PORT: int = _int("AGENT_PORT", 8090)

# ---------------------------------------------------------------------------
# Query safety
# ---------------------------------------------------------------------------
SQL_TIMEOUT: str = _get("SQL_TIMEOUT", "30s")
SQL_ROW_LIMIT: int = _int("SQL_ROW_LIMIT", 10_000)

# ---------------------------------------------------------------------------
# Publish retries
# ---------------------------------------------------------------------------
MAX_PUBLISH_RETRIES: int = _int("MAX_PUBLISH_RETRIES", 2)
