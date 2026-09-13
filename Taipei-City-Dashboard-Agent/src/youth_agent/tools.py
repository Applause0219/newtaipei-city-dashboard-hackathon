"""PydanticAI tools for the youth agent service.

Two tools are exposed for registration with
``Agent(tools=[execute_sql, publish_component])``:

* **execute_sql** -- validate, safeguard, and run a read-only SQL query.
* **publish_component** -- run guardrails then atomically INSERT into the
  three dashboard tables (``components``, ``query_charts``,
  ``component_charts``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext

from . import config, db, sql_guard
from .db import execute_readonly
from .guardrails import run_guardrails
from .schemas import (
    QUERY_TYPE_COLUMNS,
    AnalysisInsight,
    ComponentSpec,
    GuardrailViolation,
    PublishResult,
    QueryResult,
)

logger = logging.getLogger(__name__)

# Only values that the frontend renderer actually supports.  A wrong city
# silently renders an empty card -- one of the trickiest failure modes.
_KNOWN_CITIES: frozenset[str] = frozenset({"metrotaipei"})
_CHART_TYPES_BY_QUERY = {
    "two_d": frozenset(
        {
            "BarChart",
            "ColumnChart",
            "DistrictChart",
            "DonutChart",
            "TreemapChart",
            "RadarChart",
            "PolarAreaChart",
            "NegativeColumnChart",
        }
    ),
    "three_d": frozenset(
        {
            "ColumnChart",
            "DistrictChart",
            "BarPercentChart",
            "RadarChart",
            "HeatmapChart",
            "IndicatorChart",
            "PolarAreaChart",
            "TextUnitChart",
            "NegativeColumnChart",
            "QuartileChart",
        }
    ),
    "time": frozenset(
        {"TimelineSeparateChart", "TimelineStackedChart", "ColumnLineChart"}
    ),
}
_DEFAULT_CHART_TYPE = {
    "two_d": "BarChart",
    "three_d": "ColumnChart",
    "time": "TimelineSeparateChart",
}
_FRONTEND_TIME_FROM = frozenset(
    {
        "max",
        "day_start",
        "week_start",
        "month_start",
        "year_start",
        "day_ago",
        "week_ago",
        "month_ago",
        "quarter_ago",
        "halfyear_ago",
        "year_ago",
        "twoyear_ago",
        "fiveyear_ago",
        "tenyear_ago",
    }
)


# ---------------------------------------------------------------------------
# Tool 1: execute_sql
# ---------------------------------------------------------------------------


async def execute_sql(ctx: RunContext[Any], query: str) -> QueryResult | str:
    """Execute a read-only SQL query against the youth data tables.

    The query is validated by :mod:`sql_guard` (table whitelist, DML
    block, etc.), wrapped with a statement timeout and automatic row
    limit, then executed via the read-only data pool.

    Returns a :class:`QueryResult` on success or an **error string** if
    the query is blocked or execution fails.
    """
    # --- validate --------------------------------------------------------
    ok, reason = sql_guard.validate_sql(query)
    if not ok:
        return f"SQL blocked: {reason}"

    # --- safeguard & hash ------------------------------------------------
    # wrap_timeout=False: execute_readonly handles SET LOCAL statement_timeout
    # itself (via its timeout parameter) and requires single-statement SQL.
    safe_sql = sql_guard.add_safeguards(
        query, row_limit=config.SQL_ROW_LIMIT, wrap_timeout=False
    )
    sql_hash = sql_guard.compute_sql_hash(query)

    # --- execute ---------------------------------------------------------
    try:
        records = await execute_readonly(safe_sql, timeout=config.SQL_TIMEOUT)
    except Exception as exc:
        logger.warning("Query execution failed: %s", exc, exc_info=True)
        return f"Query execution failed: {exc}"

    if not records:
        return QueryResult(
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            sql_hash=sql_hash,
        )

    columns = list(records[0].keys())
    rows = [list(r.values()) for r in records]
    truncated = len(rows) >= config.SQL_ROW_LIMIT

    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        sql_hash=sql_hash,
    )


# ---------------------------------------------------------------------------
# Tool 2: publish_component
# ---------------------------------------------------------------------------


async def publish_component(
    ctx: RunContext[Any],
    spec: ComponentSpec,
) -> PublishResult:
    """Publish a component specification to the dashboard.

    Sequence:

    1. Run ``guardrails.run_guardrails`` -- any violation short-circuits.
    2. Assert ``city`` is a known value (wrong value silently renders an
       empty card).
    3. Assert ``query_type`` is valid.
    4. Execute the chart SQL with ``LIMIT 1`` and verify the column names
       and **order** exactly match :data:`QUERY_TYPE_COLUMNS`.
    5. INSERT into ``components``, ``query_charts``, and
       ``component_charts`` inside **one transaction** -- all three must
       succeed or all roll back (a missing ``component_charts`` row
       silently renders nothing).
    """
    # --- 1. guardrails ---------------------------------------------------
    violations = run_guardrails(spec)
    if violations:
        return PublishResult(
            success=False, index=spec.index, violations=violations
        )

    # --- 2. known city ---------------------------------------------------
    if spec.city not in _KNOWN_CITIES:
        return PublishResult(
            success=False,
            index=spec.index,
            violations=[
                GuardrailViolation(
                    check_name="known_city",
                    reason=(
                        f"city must be one of {sorted(_KNOWN_CITIES)}, "
                        f"got {spec.city!r}"
                    ),
                )
            ],
        )

    # --- 3. valid query_type ---------------------------------------------
    if spec.query_type not in QUERY_TYPE_COLUMNS:
        return PublishResult(
            success=False,
            index=spec.index,
            violations=[
                GuardrailViolation(
                    check_name="valid_query_type",
                    reason=(
                        f"query_type must be one of "
                        f"{list(QUERY_TYPE_COLUMNS)}, "
                        f"got {spec.query_type!r}"
                    ),
                )
            ],
        )

    chart_types = spec.chart_types
    if not chart_types or any(
        chart_type not in _CHART_TYPES_BY_QUERY[spec.query_type]
        for chart_type in chart_types
    ):
        chart_types = [_DEFAULT_CHART_TYPE[spec.query_type]]
    time_from = spec.time_from if spec.time_from in _FRONTEND_TIME_FROM else "max"

    # --- 4. column-order contract + data quality --------------------------
    expected_cols = QUERY_TYPE_COLUMNS[spec.query_type]
    query_chart = sql_guard.repair_common_sql(spec.query_chart)
    _MIN_ROWS = {"two_d": 2, "three_d": 2, "time": 4}
    try:
        ok, reason = sql_guard.validate_sql(spec.query_chart)
        if not ok:
            return PublishResult(
                success=False,
                index=spec.index,
                violations=[
                    GuardrailViolation(
                        check_name="query_chart_sql",
                        reason=f"query_chart SQL blocked: {reason}",
                    )
                ],
            )

        test_sql = sql_guard.add_safeguards(
            spec.query_chart, row_limit=200, wrap_timeout=False
        )
        records = await execute_readonly(test_sql, timeout=config.SQL_TIMEOUT)

        if not records:
            return PublishResult(
                success=False,
                index=spec.index,
                violations=[
                    GuardrailViolation(
                        check_name="empty_result",
                        reason=(
                            "query_chart returned 0 rows — chart will be "
                            "blank. Fix the SQL WHERE clause or check the "
                            "table has data."
                        ),
                    )
                ],
            )

        actual_cols = list(records[0].keys())
        if actual_cols != expected_cols:
            return PublishResult(
                success=False,
                index=spec.index,
                violations=[
                    GuardrailViolation(
                        check_name="column_order",
                        reason=(
                            f"query_chart columns {actual_cols} do not "
                            f"match expected {expected_cols} for "
                            f"query_type={spec.query_type!r}"
                        ),
                    )
                ],
            )

        min_rows = _MIN_ROWS.get(spec.query_type, 2)
        if len(records) < min_rows:
            return PublishResult(
                success=False,
                index=spec.index,
                violations=[
                    GuardrailViolation(
                        check_name="too_few_rows",
                        reason=(
                            f"query_chart returned {len(records)} rows, "
                            f"need at least {min_rows} for "
                            f"query_type={spec.query_type!r}. "
                            f"Broaden the filter or pick a different dataset."
                        ),
                    )
                ],
            )

        x_vals = [r["x_axis"] for r in records]
        if spec.query_type == "two_d":
            dupes = [v for v in set(x_vals) if x_vals.count(v) > 1]
            if dupes:
                return PublishResult(
                    success=False,
                    index=spec.index,
                    violations=[
                        GuardrailViolation(
                            check_name="duplicate_x_axis",
                            reason=(
                                f"two_d chart has duplicate x_axis values "
                                f"{dupes[:5]} — add GROUP BY or fix the query."
                            ),
                        )
                    ],
                )
        elif spec.query_type == "time":
            y_vals = [r["y_axis"] for r in records]
            pairs = list(zip(x_vals, y_vals))
            dupe_pairs = [p for p in set(pairs) if pairs.count(p) > 1]
            if dupe_pairs:
                return PublishResult(
                    success=False,
                    index=spec.index,
                    violations=[
                        GuardrailViolation(
                            check_name="duplicate_x_axis",
                            reason=(
                                f"time chart has duplicate (x_axis, y_axis) "
                                f"pairs — add GROUP BY or DISTINCT. "
                                f"Examples: {dupe_pairs[:3]}"
                            ),
                        )
                    ],
                )

    except Exception as exc:
        return PublishResult(
            success=False,
            index=spec.index,
            violations=[
                GuardrailViolation(
                    check_name="query_chart_validation",
                    reason=f"Failed to validate query_chart SQL: {exc}",
                )
            ],
        )

    # --- 5. atomic publish (3 INSERTs in one transaction) ----------------
    try:
        # Access db.manager_pool at runtime (not at import time) so we see
        # the pool that init_pools() created after module import.
        pool = db.manager_pool
        if pool is None:
            raise RuntimeError("manager_pool is not initialised")
        async with pool.acquire() as conn:
            async with conn.transaction():
                component_exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM components WHERE index = $1)",
                    spec.index,
                )
                query_chart_exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM query_charts "
                    "WHERE index = $1 AND city = $2)",
                    spec.index,
                    spec.city,
                )
                component_chart_exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM component_charts WHERE index = $1)",
                    spec.index,
                )

                if not component_exists:
                    await conn.execute(
                        "INSERT INTO components (index, name) VALUES ($1, $2)",
                        spec.index,
                        spec.name,
                    )
                if not query_chart_exists:
                    await conn.execute(
                        """INSERT INTO query_charts (
                            index, query_type, query_chart, city, source,
                            short_desc, long_desc, time_from, time_to,
                            update_freq, update_freq_unit, links, contributors,
                            created_at, updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11, $12, $13,
                            NOW(), NOW()
                        )""",
                        spec.index,
                        spec.query_type,
                        query_chart,
                        spec.city,
                        spec.source,
                        spec.short_desc,
                        spec.long_desc,
                        time_from,
                        "now",
                        spec.update_freq,
                        spec.update_freq_unit,
                        spec.links,
                        spec.contributors,
                    )
                else:
                    await conn.execute(
                        "UPDATE query_charts SET time_from = $3, time_to = 'now' "
                        "WHERE index = $1 AND city = $2",
                        spec.index,
                        spec.city,
                        time_from,
                    )
                if not component_chart_exists:
                    await conn.execute(
                        """INSERT INTO component_charts
                            (index, color, types, unit)
                        VALUES ($1, $2, $3, $4)""",
                        spec.index,
                        spec.chart_colors,
                        chart_types,
                        spec.chart_unit,
                    )
                else:
                    await conn.execute(
                        "UPDATE component_charts SET types = $2 WHERE index = $1",
                        spec.index,
                        chart_types,
                    )

                # ponytail: keep existing rows; add versioning only if repeated
                # analyses must replace previously published dashboard cards.
                if component_exists and query_chart_exists and component_chart_exists:
                    logger.info(
                        "Component %s already exists; publish is idempotent",
                        spec.index,
                    )
    except Exception as exc:
        logger.error(
            "Publish transaction failed for %s: %s",
            spec.index,
            exc,
            exc_info=True,
        )
        return PublishResult(
            success=False,
            index=spec.index,
            violations=[
                GuardrailViolation(
                    check_name="publish_transaction",
                    reason=f"Transaction failed: {exc}",
                )
            ],
        )

    # --- 6. post-publish verification -------------------------------------
    try:
        pool = db.manager_pool
        async with pool.acquire() as conn:
            missing = []
            for tbl in ("components", "component_charts"):
                exists = await conn.fetchval(
                    f"SELECT EXISTS (SELECT 1 FROM {tbl} WHERE index = $1)",
                    spec.index,
                )
                if not exists:
                    missing.append(tbl)
            qc_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM query_charts "
                "WHERE index = $1 AND city = $2)",
                spec.index,
                spec.city,
            )
            if not qc_exists:
                missing.append("query_charts")
            if missing:
                logger.error(
                    "Post-publish check failed for %s: missing in %s",
                    spec.index,
                    missing,
                )
                return PublishResult(
                    success=False,
                    index=spec.index,
                    violations=[
                        GuardrailViolation(
                            check_name="post_publish_verify",
                            reason=(
                                f"Published but rows missing in {missing}. "
                                f"Dashboard will crash — retrying."
                            ),
                        )
                    ],
                )
    except Exception as exc:
        logger.warning("Post-publish verify failed: %s", exc)

    logger.info("Published component %s successfully", spec.index)
    if isinstance(ctx.deps, dict) and "published" in ctx.deps:
        ctx.deps["published"].append(spec.index)
    elif isinstance(ctx.deps, list):
        ctx.deps.append(spec.index)
    return PublishResult(success=True, index=spec.index)


# ---------------------------------------------------------------------------
# Tool 3: emit_insight
# ---------------------------------------------------------------------------


INSIGHT_EMITTED_PREFIX = "Insight emitted: "


async def emit_insight(
    ctx: RunContext[Any],
    title: str,
    claim: str,
    narrative: str,
    hypothesis: str,
    source_sql: str = "",
) -> str:
    """Emit a single insight to the user immediately via SSE stream.

    Call this as soon as you have a complete insight -- do NOT wait until
    the end.  The frontend renders each insight card the moment it arrives.

    Args:
        title: Short insight title.
        claim: Data Fact -- what the query result says, with numbers.
        narrative: Analytical Insight -- a reading of the data, no causes.
        hypothesis: Hypothesis -- hedged (可能/或許/推測), says 仍需其他資料驗證,
            and contains no numbers.
        source_sql: The SQL that produced the numbers in claim.
    """
    try:
        insight = AnalysisInsight(
            title=title,
            claim=claim,
            narrative=narrative,
            hypothesis=hypothesis,
            source_sql=source_sql,
        )
    except ValidationError as exc:
        # Hand the rule violation back to the model instead of crashing the run;
        # the card is only streamed once it passes, so users never see a
        # hypothesis written as a fact.
        raise ModelRetry(
            "; ".join(err["msg"] for err in exc.errors())
        ) from exc
    payload = insight.model_dump()
    if isinstance(ctx.deps, dict) and "insights" in ctx.deps:
        ctx.deps["insights"].append(payload)
    return INSIGHT_EMITTED_PREFIX + json.dumps(payload, ensure_ascii=False)
