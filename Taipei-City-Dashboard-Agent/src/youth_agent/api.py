"""FastAPI application for the Youth Agent service.

Exposes REST + SSE endpoints that drive the PydanticAI agent and return
structured analysis results to the dashboard frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from . import db
from .agent import agent
from .schemas import AnalysisResult, component_id_for_asset
from .search import search_terms

_USAGE_LIMITS = UsageLimits(request_limit=50)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    question: str
    domains: list[str] = Field(default_factory=list)
    max_insights: int = 5


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


# ---------------------------------------------------------------------------
# Lifespan: DB pool init / teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise connection pools on startup; close them on shutdown."""
    from . import config

    logger.info("Initialising database pools...")
    await db.init_pools(config)
    logger.info("Database pools ready.")
    yield
    logger.info("Shutting down database pools...")
    await db.close_pools()
    logger.info("Database pools closed.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Youth Agent Service",
    description="PydanticAI agent for the New Taipei City Youth Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # permissive for local / hackathon development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_RUN_TIMEOUT = 300  # seconds -- generous for multi-step analysis

def _build_prompt(question: str, domains: list[str], max_insights: int) -> str:
    """Augment the raw user question with structured hints for the agent."""
    parts = [question]
    if domains:
        parts.append(f"\n[Domains: {', '.join(domains)}]")
    if max_insights and max_insights != 5:
        parts.append(f"[Max insights: {max_insights}]")
    return "\n".join(parts)


def _format_sse_event(event: Any) -> dict[str, Any] | None:
    """Convert a PydanticAI stream event into an SSE-friendly dict.

    Returns *None* for event types we choose not to surface.
    """
    # Lazy imports -- these symbols may not exist in older pydantic-ai
    # versions, so we tolerate ImportError at call-time.
    try:
        from pydantic_ai import (
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            FinalResultEvent,
        )
    except ImportError:
        return {"type": "event", "detail": str(event)}

    if isinstance(event, FunctionToolCallEvent):
        return {
            "type": "tool_call",
            "tool": event.part.tool_name,
            "args": event.part.args,
        }
    if isinstance(event, FunctionToolResultEvent):
        content = event.part.content
        # Truncate large tool results so the SSE payload stays manageable.
        content_str = str(content)[:2000] if content else ""
        return {
            "type": "tool_result",
            "tool_call_id": event.tool_call_id,
            "content": content_str,
        }
    if isinstance(event, FinalResultEvent):
        return {
            "type": "final_result_start",
            "tool_name": event.tool_name,
        }
    # Other event types (PartStartEvent, PartDeltaEvent, etc.) -- skip
    return None


# ---------------------------------------------------------------------------
# POST /api/v1/agent/search  (fast chatbot integration)
# ---------------------------------------------------------------------------

@app.post("/api/v1/agent/search")
async def search_assets(req: SearchRequest):
    """Keyword search against youth_asset_manifest for the chatbot UI.

    Returns results in the same shape as the Go BE's /vector/component so
    the existing ChatBox.vue can render them without changes.
    """
    if db.data_pool is None:
        raise HTTPException(502, "Data database not connected")

    query = req.query.strip()
    limit = max(1, min(req.limit, 50))

    _BROWSE_ALL = {"青年", "資料", "數據", "全部", "所有", "列表", "清單",
                    "青年資料", "青年數據", "所有資料", "全部資料"}

    async with db.data_pool.acquire() as conn:
        if not query or query in _BROWSE_ALL:
            rows = await conn.fetch(
                "SELECT asset_id, title, row_count FROM youth_asset_manifest "
                "ORDER BY row_count DESC NULLS LAST LIMIT $1",
                limit,
            )
        else:
            chunks = search_terms(query)
            if not chunks:
                chunks = [query]

            score_parts = " + ".join(
                f"(title ILIKE ${i + 1} OR asset_id ILIKE ${i + 1})::int"
                for i in range(len(chunks))
            )
            where_parts = " OR ".join(
                f"(title ILIKE ${i + 1} OR asset_id ILIKE ${i + 1})"
                for i in range(len(chunks))
            )
            params = [f"%{c}%" for c in chunks]

            rows = await conn.fetch(
                f"SELECT asset_id, title, row_count, "
                f"({score_parts}) AS match_score "
                f"FROM youth_asset_manifest "
                f"WHERE {where_parts} "
                f"ORDER BY match_score DESC, row_count DESC NULLS LAST "
                f"LIMIT {limit}",
                *params,
            )

    return {
        "data": [
            {
                "id": component_id_for_asset(row["asset_id"]),
                "index": row["asset_id"],
                "name": row["title"],
                "score": round(max(0.50, 0.99 - i * 0.02), 2),
                "city": "metrotaipei",
            }
            for i, row in enumerate(rows)
        ],
        "status": "success",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/agent/component/{asset_id}/chart  (dynamic chart data)
# ---------------------------------------------------------------------------

import re as _re

_SAFE_TABLE_RE = _re.compile(r"^youth_[a-z0-9_]+$")


def _round_val(v) -> int | float:
    f = float(v or 0)
    return int(round(f)) if abs(f) >= 1 else round(f, 4)


def _simple_three_d(rows: list, label_col: str, val_col: str = "total") -> dict:
    """Single-series three_d from a 1-D aggregation."""
    return {
        "categories": [str(r[label_col] or "") for r in rows],
        "data": [{"name": "數值", "icon": "", "data": [_round_val(r[val_col]) for r in rows]}],
        "status": "success",
    }


@app.get("/api/v1/agent/component/{asset_id}/chart")
async def component_chart(asset_id: str):
    """Generate three_d chart data for a youth asset on the fly."""
    if db.data_pool is None:
        raise HTTPException(502, "Data database not connected")

    async with db.data_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT table_name, title FROM youth_asset_manifest WHERE asset_id = $1",
            asset_id,
        )
        if not row:
            raise HTTPException(404, f"Asset {asset_id} not found")

        table_name = row["table_name"]
        if not _SAFE_TABLE_RE.match(table_name):
            raise HTTPException(400, "Invalid table name")

        has_age = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {table_name} "
            f"WHERE age_band_raw IS NOT NULL LIMIT 1)"
        )
        latest_period = await conn.fetchval(
            f"SELECT MAX(period_start) FROM {table_name} WHERE value IS NOT NULL"
        )
        has_total_gender = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {table_name} "
            f"WHERE gender IN ('total', '總計', '小計') LIMIT 1)"
        )

        conditions = ["value IS NOT NULL"]
        params: list[Any] = []
        if latest_period is not None:
            params.append(latest_period)
            conditions.append(f"period_start = ${len(params)}")
        if has_age:
            conditions.extend(["age_lower >= 15", "age_upper <= 40"])
        if has_total_gender:
            conditions.append("gender IN ('total', '總計', '小計')")

        # Generic search cards are previews, not analyses. Keep every value as
        # an original source row: no age re-binning and no cross-row SUM of
        # rates, means, totals, genders, or time periods.
        rows = await conn.fetch(
            f"SELECT concat_ws(' · ', "
            f"  COALESCE(NULLIF(age_band_raw, ''), NULLIF(area_code, ''), "
            f"           NULLIF(period_start, ''), indicator_id), "
            f"  CASE WHEN area_code IS NOT NULL AND area_code <> 'TW' "
            f"       THEN area_code END, "
            f"  CASE WHEN gender IS NOT NULL AND gender NOT IN "
            f"       ('total', '總計', '小計') THEN gender END, "
            f"  indicator_id, NULLIF(unit, '')"
            f") AS label, value::float8 AS total "
            f"FROM {table_name} WHERE {' AND '.join(conditions)} "
            f"ORDER BY age_lower NULLS LAST, indicator_id, label LIMIT 24",
            *params,
        )
        if rows:
            result = _simple_three_d(rows, "label")
            result["meta"] = {
                "period": latest_period,
                "aggregation": "none",
                "age_scope": "original bins within 15-40" if has_age else None,
            }
            return result

    return {"categories": [], "data": [], "status": "success"}


# ---------------------------------------------------------------------------
# POST /api/v1/agent/analyze
# ---------------------------------------------------------------------------

@app.post("/api/v1/agent/analyze", response_model=AnalysisResult)
async def analyze(req: AnalyzeRequest):
    """Run the agent to completion and return the structured result."""
    prompt = _build_prompt(req.question, req.domains, req.max_insights)
    published: list[str] = []
    try:
        result = await asyncio.wait_for(
            agent.run(prompt, deps=published, usage_limits=_USAGE_LIMITS),
            timeout=_AGENT_RUN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Agent did not finish within {_AGENT_RUN_TIMEOUT}s",
        )
    except UsageLimitExceeded as exc:
        raise HTTPException(
            status_code=422,
            detail="Agent 分析步驟過多，已中止。請嘗試更具體的問題。",
        ) from exc
    output: AnalysisResult = result.output
    # Ensure the original question is always populated.
    if not output.question:
        output.question = req.question
    output.published = list(dict.fromkeys(published))
    return output


# ---------------------------------------------------------------------------
# GET /api/v1/agent/stream  (Server-Sent Events)
# ---------------------------------------------------------------------------

@app.get("/api/v1/agent/stream")
async def stream(
    question: str = Query(..., description="Analysis question in Chinese"),
    domains: str = Query("", description="Comma-separated domain filter"),
    max_insights: int = Query(5, ge=1, le=20),
):
    """SSE endpoint that streams tool calls and guardrail results as they
    happen, followed by the final AnalysisResult.

    Uses ``agent.run()`` with ``event_stream_handler`` (pydantic-ai >= 0.1)
    to capture intermediate events.  Falls back to a single-shot run if
    streaming support is not available.
    """
    domain_list = [d.strip() for d in domains.split(",") if d.strip()]
    prompt = _build_prompt(question, domain_list, max_insights)

    async def event_generator():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        # -- handler fed into agent.run() --------------------------------
        async def _event_handler(ctx, event_stream):
            """Push formatted events to the queue as they arrive."""
            async for event in event_stream:
                formatted = _format_sse_event(event)
                if formatted is not None:
                    await queue.put(formatted)

        # -- background task that drives the agent run -------------------
        async def _run_agent():
            published: list[str] = []
            try:
                result = await asyncio.wait_for(
                    agent.run(
                        prompt,
                        deps=published,
                        usage_limits=_USAGE_LIMITS,
                        event_stream_handler=_event_handler,
                    ),
                    timeout=_AGENT_RUN_TIMEOUT,
                )
                output = result.output
                if not output.question:
                    output.question = question
                output.published = list(dict.fromkeys(published))
                await queue.put({
                    "type": "result",
                    "data": output.model_dump(mode="json"),
                })
            except asyncio.TimeoutError:
                await queue.put({
                    "type": "error",
                    "detail": f"Agent timed out after {_AGENT_RUN_TIMEOUT}s",
                })
            except UsageLimitExceeded:
                await queue.put({
                    "type": "error",
                    "detail": "Agent 分析步驟過多，已中止。請嘗試更具體的問題。",
                })
            except Exception as exc:
                logger.exception("Agent run failed during SSE stream")
                await queue.put({
                    "type": "error",
                    "detail": str(exc),
                })
            finally:
                await queue.put(None)  # sentinel -- close the generator

        task = asyncio.create_task(_run_agent())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                payload = json.dumps(item, ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/agent/health
# ---------------------------------------------------------------------------

@app.get("/api/v1/agent/health")
async def health():
    """Report service health: DB connectivity, model config, credential
    expiry."""
    # -- Database connectivity ------------------------------------------
    db_connected = False
    try:
        if db.data_pool is not None:
            async with db.data_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_connected = True
    except Exception:
        logger.warning("Health check: data_pool ping failed", exc_info=True)

    # -- Bedrock model & auth mode --------------------------------------
    bedrock_model = os.environ.get("BEDROCK_MODEL", "unknown")
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        auth_mode = "bearer_token"
    elif os.environ.get("AWS_ACCESS_KEY_ID"):
        auth_mode = "iam_credentials"
    else:
        auth_mode = "none"

    # -- Credential expiry (only meaningful for IAM credentials) --------
    credentials_expire_at: str | None = None
    if auth_mode == "iam_credentials":
        try:
            credentials_expire_at = os.environ.get("AWS_CREDENTIAL_EXPIRATION")
            if credentials_expire_at is None:
                import boto3

                session = boto3.Session(region_name="us-west-2")
                creds = session.get_credentials()
                if creds is not None:
                    expiry = getattr(creds, "_expiry_time", None)
                    if expiry is not None:
                        credentials_expire_at = expiry.isoformat()
        except Exception:
            logger.debug("Could not determine credential expiry", exc_info=True)

    return {
        "db_connected": db_connected,
        "bedrock_model": bedrock_model,
        "auth_mode": auth_mode,
        "credentials_expire_at": credentials_expire_at,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point (registered as ``youth-agent`` in pyproject.toml)."""
    import uvicorn
    from .config import AGENT_PORT

    uvicorn.run(
        "youth_agent.api:app",
        host="0.0.0.0",
        port=AGENT_PORT,
    )
