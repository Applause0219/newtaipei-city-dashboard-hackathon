#!/usr/bin/env python3
"""Scan all youth_* tables and populate youth_asset_manifest.

Standalone script -- not part of the agent runtime.
Connect to postgres-data, discover every youth_* table, compute
deterministic metadata (row count, time range, age schema, null ratios,
distinct counts, domains, etc.) and UPSERT into youth_asset_manifest.

Also creates/replaces the youth_fact UNION ALL view.

Usage:
    DB_DATA_DSN="postgresql://user:pw@host:5432/db" python scripts/build_manifest.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

import asyncpg  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXCLUDE_TABLES = frozenset(
    {
        "youth_asset_manifest",
        "youth_dataset_catalog_deprecated",
        "youth_fact",
    }
)

# Canonical 15-column schema shared by every youth_* data table.
CANONICAL_COLUMNS = [
    "indicator_id",
    "period_start",
    "period_end",
    "period_type",
    "age_lower",
    "age_upper",
    "age_band_raw",
    "gender",
    "area_code",
    "area_level",
    "breakdown",
    "value",
    "unit",
    "value_type",
    "data_time",
]

POLICY_YOUTH = (18, 35)
OPERATIONAL = (15, 40)

_AGGREGATION_RULES = {
    "count": "sum",
    "ratio": "require_denominator",
    "rate": "require_denominator",
    "mean": "require_weight",
    "median": "not_aggregatable",
    "index": "not_aggregatable",
}


def aggregation_rule_for(value_type: str | None) -> str:
    """Map a source value type to a fail-closed aggregation rule."""
    return _AGGREGATION_RULES.get((value_type or "").lower(), "not_aggregatable")

# ---------------------------------------------------------------------------
# Domain heuristic
# ---------------------------------------------------------------------------

_DOMAIN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"pop"), "population"),
    (re.compile(r"labor|employment|unemployment"), "employment"),
    (re.compile(r"housing|rent|house_price"), "housing"),
    (re.compile(r"education|school"), "education"),
    (re.compile(r"health|medical|injury"), "health"),
    (re.compile(r"marriage|divorce|birth|fertility"), "family"),
    (re.compile(r"crime|fraud"), "safety"),
    (re.compile(r"commute|traffic"), "transportation"),
    (re.compile(r"wage|salary|income"), "income"),
]


def derive_domains(table_name: str) -> list[str]:
    """Return a list of domain tags inferred from the table name."""
    name = table_name.removeprefix("youth_")
    domains: list[str] = []
    for pattern, domain in _DOMAIN_RULES:
        if pattern.search(name):
            domains.append(domain)
    return domains


# ---------------------------------------------------------------------------
# Title heuristic
# ---------------------------------------------------------------------------

_TITLE_KEYWORDS: dict[str, str] = {
    "pop": "人口",
    "single_age": "單齡",
    "labor": "勞動",
    "employment": "就業",
    "unemployment": "失業",
    "housing": "住宅",
    "rent": "租金",
    "house_price": "房價",
    "education": "教育",
    "school": "學校",
    "health": "健康",
    "medical": "醫療",
    "injury": "傷害",
    "marriage": "婚姻",
    "divorce": "離婚",
    "birth": "出生",
    "fertility": "生育",
    "crime": "犯罪",
    "fraud": "詐騙",
    "commute": "通勤",
    "traffic": "交通",
    "wage": "薪資",
    "salary": "薪資",
    "income": "收入",
    "insurance": "保險",
    "nhi": "健保",
    "poverty": "貧窮",
    "welfare": "福利",
    "migration": "遷移",
    "net": "淨",
    "rate": "率",
    "count": "數量",
    "avg": "平均",
    "median": "中位數",
}


def derive_title(table_name: str) -> str:
    """Best-effort human-readable title from a table name.

    Replaces known keywords with Chinese equivalents; anything left over
    is kept as-is.  Titles will be refined by LLM in a later step.
    """
    name = table_name.removeprefix("youth_")
    parts = name.split("_")
    translated: list[str] = []
    for part in parts:
        translated.append(_TITLE_KEYWORDS.get(part, part))
    return "".join(translated)


# ---------------------------------------------------------------------------
# Age classification
# ---------------------------------------------------------------------------


def classify_age(
    bins: list[dict[str, Any]],
) -> tuple[str | None, str]:
    """Classify the age bins into one of A/B/C/D or None."""
    if not bins:
        return None, "無年齡維度"

    valid_bins = [b for b in bins if b["lower"] <= b["upper"]]
    if not valid_bins:
        return "D", "沒有可判讀的原始年齡區間"

    all_lower = [b["lower"] for b in valid_bins]
    all_upper = [b["upper"] for b in valid_bins]
    min_age = min(all_lower)
    max_age = max(all_upper)

    # A/B require an original bin that exactly carries the stated scope.
    if any(b["lower"] == 18 and b["upper"] == 35 for b in valid_bins):
        return "A", "完整法定青年 18-35"

    proxy_bins = [
        b for b in valid_bins
        if OPERATIONAL[0] <= b["lower"] <= 20
        and 39 <= b["upper"] <= OPERATIONAL[1]
    ]
    if proxy_bins:
        b = proxy_bins[0]
        return "B", f"青年近似 {b['lower']}-{b['upper']}，完整落在 15-40"

    # C only includes complete original bins wholly inside the boundary.
    youth_bins = [
        b for b in valid_bins
        if b["lower"] >= OPERATIONAL[0] and b["upper"] <= OPERATIONAL[1]
    ]
    if youth_bins:
        lo = min(b["lower"] for b in youth_bins)
        hi = max(b["upper"] for b in youth_bins)
        return "C", f"可用原始青年子區段 {lo}-{hi}"

    # D: out of scope
    return "D", f"超出可用邊界 {min_age}-{max_age}"


# ---------------------------------------------------------------------------
# Table scanning
# ---------------------------------------------------------------------------


async def discover_tables(conn: asyncpg.Connection) -> list[str]:
    """Return sorted list of youth_* data table names."""
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name LIKE 'youth_%'
          AND table_name NOT IN ($1, $2, $3)
        ORDER BY table_name
        """,
        *EXCLUDE_TABLES,
    )
    return [r["table_name"] for r in rows]


async def _basic_stats(conn: asyncpg.Connection, table: str) -> dict[str, Any]:
    """Row count, time range, period types, area levels, units, value types."""
    row = await conn.fetchrow(
        f"""
        SELECT
            count(*)                          AS row_count,
            min(period_start)                 AS time_start,
            max(period_end)                   AS time_end,
            array_agg(DISTINCT period_type)   AS period_types,
            array_agg(DISTINCT area_level)    AS area_levels,
            array_agg(DISTINCT unit)          AS units,
            array_agg(DISTINCT value_type)    AS value_types
        FROM {table}
        """  # noqa: S608 -- table name comes from information_schema
    )
    return dict(row)  # type: ignore[arg-type]


async def _age_bins(conn: asyncpg.Connection, table: str) -> list[dict[str, Any]]:
    """Distinct (age_lower, age_upper, age_band_raw) tuples."""
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT age_lower, age_upper, age_band_raw
        FROM {table}
        WHERE age_lower IS NOT NULL OR age_upper IS NOT NULL
        ORDER BY age_lower, age_upper
        """  # noqa: S608
    )
    bins: list[dict[str, Any]] = []
    for r in rows:
        lower = r["age_lower"]
        upper = r["age_upper"]
        # Some tables store age columns as TEXT with all NULLs.
        if lower is None and upper is None:
            continue
        try:
            lower_int = int(lower)
            upper_int = int(upper)
        except (TypeError, ValueError):
            continue
        bins.append(
            {
                "label": r["age_band_raw"] or f"{lower_int}-{upper_int}",
                "lower": lower_int,
                "upper": upper_int,
                "lower_inclusive": True,
                "upper_inclusive": True,
                "is_original": True,
            }
        )
    return bins


async def _column_metadata(
    conn: asyncpg.Connection, table: str
) -> tuple[list[str], dict[str, str]]:
    """Column names (ordered) and {column: data_type} mapping."""
    rows = await conn.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    names = [r["column_name"] for r in rows]
    types = {r["column_name"]: r["data_type"] for r in rows}
    return names, types


async def _null_and_distinct(conn: asyncpg.Connection, table: str) -> dict[str, Any]:
    """Null ratios and distinct counts for key columns."""
    row = await conn.fetchrow(
        f"""
        SELECT
            count(*)                       AS total,
            count(age_lower)               AS age_lower_nn,
            count(age_upper)               AS age_upper_nn,
            count(gender)                  AS gender_nn,
            count(area_code)               AS area_code_nn,
            count(breakdown)               AS breakdown_nn,
            count(DISTINCT age_band_raw)   AS age_band_distinct,
            count(DISTINCT gender)         AS gender_distinct,
            count(DISTINCT area_code)      AS area_distinct
        FROM {table}
        """  # noqa: S608
    )
    return dict(row)  # type: ignore[arg-type]


async def _sample_values(conn: asyncpg.Connection, table: str) -> dict[str, Any]:
    """Grab a small sample for each column (first non-null value)."""
    # Use a LIMIT 5 row to build quick sample dict.
    rows = await conn.fetch(
        f"SELECT * FROM {table} LIMIT 5"  # noqa: S608
    )
    if not rows:
        return {}
    samples: dict[str, list[Any]] = {}
    for r in rows:
        for col in r.keys():
            val = r[col]
            if val is not None:
                samples.setdefault(col, [])
                # Convert non-JSON-serializable types to strings.
                if isinstance(val, (int, float, bool)):
                    samples[col].append(val)
                else:
                    samples[col].append(str(val))
    # De-duplicate and cap at 3 per column.
    return {k: list(dict.fromkeys(v))[:3] for k, v in samples.items()}


def _compute_null_ratios(stats: dict[str, Any]) -> dict[str, float | None]:
    """Return null ratio per key dimension column."""
    total = stats["total"]
    if total == 0:
        return {}
    return {
        "age_lower": round(1 - stats["age_lower_nn"] / total, 4),
        "age_upper": round(1 - stats["age_upper_nn"] / total, 4),
        "gender": round(1 - stats["gender_nn"] / total, 4),
        "area_code": round(1 - stats["area_code_nn"] / total, 4),
        "breakdown": round(1 - stats["breakdown_nn"] / total, 4),
    }


def _compute_distinct_counts(stats: dict[str, Any]) -> dict[str, int]:
    return {
        "age_band_raw": stats["age_band_distinct"],
        "gender": stats["gender_distinct"],
        "area_code": stats["area_distinct"],
    }


def _derive_dimensions(
    null_ratios: dict[str, float | None],
    distinct_counts: dict[str, int],
) -> dict[str, Any]:
    """Derive dimensions from deterministic null/distinct metadata."""
    dimensions: dict[str, Any] = {}
    for col in ("gender", "area_code", "age_band_raw"):
        ratio = null_ratios.get(col.replace("age_band_raw", "age_lower"), 1.0)
        if ratio is not None and ratio < 0.95:
            dimensions[col] = {
                "distinct": distinct_counts.get(col, 0),
                "null_ratio": null_ratios.get(col, None),
            }
    return dimensions


async def _measure_metadata(conn: asyncpg.Connection, table: str) -> list[dict[str, Any]]:
    """Return metric definitions and safe aggregation rules for a table."""
    rows = await conn.fetch(
        f"""
        SELECT indicator_id::text AS name, unit::text AS unit,
               value_type::text AS value_type
        FROM {table}
        WHERE indicator_id IS NOT NULL AND value IS NOT NULL
        GROUP BY indicator_id, unit, value_type
        ORDER BY indicator_id, unit, value_type
        """  # noqa: S608 -- table name comes from information_schema
    )
    return [
        {
            "name": r["name"],
            "unit": r["unit"] or "",
            "value_type": r["value_type"] or "unknown",
            "aggregation_rule": aggregation_rule_for(r["value_type"]),
        }
        for r in rows
    ]


async def scan_table(conn: asyncpg.Connection, table: str) -> dict[str, Any]:
    """Run all metadata queries for *table* and return a manifest dict."""
    # asyncpg forbids concurrent queries on a single connection,
    # so run them sequentially.
    basic = await _basic_stats(conn, table)
    age_bins_list = await _age_bins(conn, table)
    col_names, col_types = await _column_metadata(conn, table)
    null_stats = await _null_and_distinct(conn, table)
    samples = await _sample_values(conn, table)
    measures = await _measure_metadata(conn, table)

    age_cls, age_note = classify_age(age_bins_list)
    null_ratios = _compute_null_ratios(null_stats)
    distinct_counts = _compute_distinct_counts(null_stats)
    dimensions = _derive_dimensions(null_ratios, distinct_counts)

    asset_id = table.removeprefix("youth_")

    # period_type: collapse array to single string if uniform
    period_types = basic["period_types"] or []
    period_type_str = period_types[0] if len(period_types) == 1 else json.dumps(period_types)

    return {
        "asset_id": asset_id,
        "title": derive_title(table),
        "domains": derive_domains(table),
        "table_name": table,
        "row_count": basic["row_count"],
        "time_range_start": str(basic["time_start"]) if basic["time_start"] else None,
        "time_range_end": str(basic["time_end"]) if basic["time_end"] else None,
        "period_type": period_type_str,
        "column_names": col_names,
        "sql_data_types": col_types,
        "sample_values": samples,
        "null_ratio": null_ratios,
        "distinct_counts": distinct_counts,
        "age_schema": age_bins_list if age_bins_list else None,
        "age_classification": age_cls,
        "age_note": age_note,
        "dimensions": dimensions,
        "measures": measures,
    }


# ---------------------------------------------------------------------------
# UPSERT
# ---------------------------------------------------------------------------


async def upsert_manifest(conn: asyncpg.Connection, m: dict[str, Any]) -> None:
    """Insert or update a single row in youth_asset_manifest."""
    await conn.execute(
        """
        INSERT INTO youth_asset_manifest (
            asset_id, title, domains, table_name,
            row_count, time_range_start, time_range_end, period_type,
            column_names, sql_data_types, sample_values,
            null_ratio, distinct_counts,
            age_schema, age_classification, age_note,
            dimensions, measures
        ) VALUES (
            $1, $2, $3, $4,
            $5, $6, $7, $8,
            $9, $10::jsonb, $11::jsonb,
            $12::jsonb, $13::jsonb,
            $14::jsonb, $15, $16,
            $17::jsonb, $18::jsonb
        )
        ON CONFLICT (asset_id) DO UPDATE SET
            title            = EXCLUDED.title,
            domains          = EXCLUDED.domains,
            table_name       = EXCLUDED.table_name,
            row_count        = EXCLUDED.row_count,
            time_range_start = EXCLUDED.time_range_start,
            time_range_end   = EXCLUDED.time_range_end,
            period_type      = EXCLUDED.period_type,
            column_names     = EXCLUDED.column_names,
            sql_data_types   = EXCLUDED.sql_data_types,
            sample_values    = EXCLUDED.sample_values,
            null_ratio       = EXCLUDED.null_ratio,
            distinct_counts  = EXCLUDED.distinct_counts,
            age_schema       = EXCLUDED.age_schema,
            age_classification = EXCLUDED.age_classification,
            age_note         = EXCLUDED.age_note,
            dimensions       = EXCLUDED.dimensions,
            measures         = EXCLUDED.measures
        """,
        m["asset_id"],
        m["title"],
        m["domains"],
        m["table_name"],
        m["row_count"],
        m["time_range_start"],
        m["time_range_end"],
        m["period_type"],
        m["column_names"],
        json.dumps(m["sql_data_types"], ensure_ascii=False),
        json.dumps(m["sample_values"], ensure_ascii=False),
        json.dumps(m["null_ratio"], ensure_ascii=False),
        json.dumps(m["distinct_counts"], ensure_ascii=False),
        json.dumps(m["age_schema"] or [], ensure_ascii=False),
        m["age_classification"],
        m["age_note"],
        json.dumps(m["dimensions"], ensure_ascii=False),
        json.dumps(m["measures"], ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# youth_fact VIEW
# ---------------------------------------------------------------------------


def generate_youth_fact_sql(tables: list[str]) -> str:
    """Return the CREATE OR REPLACE VIEW statement for youth_fact."""
    if not tables:
        raise ValueError("No tables provided for youth_fact view")

    fragments: list[str] = []
    for table in tables:
        fragments.append(
            f"""\
SELECT indicator_id,
       period_start::text, period_end::text, period_type::text,
       age_lower::text::smallint, age_upper::text::smallint,
       age_band_raw::text, gender::text, area_code::text, area_level::text,
       breakdown::jsonb, value::numeric, unit::text, value_type::text,
       data_time::text,
       '{table}' AS source_table
  FROM public.{table}"""
        )

    body = "\nUNION ALL\n".join(fragments)
    return f"CREATE OR REPLACE VIEW public.youth_fact AS\n{body}\n;"


async def create_youth_fact_view(conn: asyncpg.Connection, tables: list[str]) -> None:
    """Generate and execute the youth_fact VIEW from discovered tables."""
    sql = generate_youth_fact_sql(tables)
    await conn.execute(sql)
    print(f"  youth_fact VIEW created ({len(tables)} tables)")


def write_youth_fact_sql_file(tables: list[str], path: str) -> None:
    """Write the youth_fact VIEW SQL to a file for version control."""
    sql = generate_youth_fact_sql(tables)
    header = (
        "-- Auto-generated by build_manifest.py\n"
        "-- Normalising VIEW that UNION ALLs all youth_* tables.\n"
        "-- Re-run build_manifest.py to regenerate after adding new tables.\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(sql)
        f.write("\n")
    print(f"  Wrote {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    dsn = os.environ.get(
        "DB_DATA_DSN",
        "postgresql://airflow:airflow@localhost:5432/dashboard",
    )
    conn = await asyncpg.connect(dsn)
    try:
        tables = await discover_tables(conn)
        print(f"Found {len(tables)} youth tables")

        for t in tables:
            manifest = await scan_table(conn, t)
            await upsert_manifest(conn, manifest)
            age = manifest["age_classification"] or "-"
            print(f"  [OK] {t}: {manifest['row_count']} rows, age={age}")

        print(f"Done. {len(tables)} assets in manifest.")

        # Create / refresh the youth_fact VIEW.
        await create_youth_fact_view(conn, tables)

        # Also write the SQL file so it can be version-controlled.
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sql_path = os.path.join(script_dir, "create_youth_fact_view.sql")
        write_youth_fact_sql_file(tables, sql_path)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
