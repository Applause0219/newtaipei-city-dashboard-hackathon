"""Light integration-test stubs for the publish_component flow.

Most of these tests require a live database connection and are skipped
when ``DB_DATA_DSN`` is not set.  The two validation-only tests
(city, query_type) mock the DB layer so they can run anywhere.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from youth_agent.schemas import ComponentSpec, Fact, PublishResult

DB_AVAILABLE = os.environ.get("DB_DATA_DSN") is not None


# ===================================================================
# Helper
# ===================================================================


def _make_publish_spec(**overrides) -> ComponentSpec:
    """Return a minimal valid ComponentSpec for publish tests."""
    defaults = dict(
        index="test_pub_001",
        name="Publish Test Component",
        query_type="two_d",
        query_chart=(
            "SELECT area_code AS x_axis, value AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024'"
        ),
        chart_types=["BarChart"],
        chart_colors=["#2e86c1"],
        chart_unit="人",
        city="metrotaipei",
        claim="新北市 {pop_val} 歲青年人口呈下降趨勢",
        narrative="依據 {pop_val} 的資料，人口持續減少",
        facts={
            "pop_val": Fact(
                value=123456,
                unit="人",
                source_sql_hash="abc123",
                source_cell=[0, 1],
            )
        },
        datasets_used=["pop_single_age"],
        age_classification="B",
        age_range="15-39",
        insight_type="trend",
    )
    defaults.update(overrides)
    return ComponentSpec(**defaults)


# ===================================================================
# Tests that run without a database (mock the DB layer)
# ===================================================================


@pytest.mark.asyncio
async def test_publish_validates_city():
    """publish_component must reject an unknown city value."""
    from youth_agent.tools import publish_component

    spec = _make_publish_spec(city="wrong")
    ctx = AsyncMock()

    result: PublishResult = await publish_component(ctx, spec)

    assert result.success is False
    assert any(v.check_name == "known_city" for v in result.violations)


@pytest.mark.asyncio
async def test_publish_validates_query_type():
    """publish_component must reject an invalid query_type."""
    from youth_agent.tools import publish_component

    spec = _make_publish_spec(query_type="invalid")
    ctx = AsyncMock()

    result: PublishResult = await publish_component(ctx, spec)

    assert result.success is False
    assert any(v.check_name == "valid_query_type" for v in result.violations)


@pytest.mark.asyncio
async def test_publish_repairs_incomplete_existing_component():
    """An existing components row must not hide missing dashboard config."""
    from youth_agent import db
    from youth_agent.tools import publish_component

    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_):
            return False

    conn = AsyncMock()
    conn.fetchval.side_effect = [True, False, True]
    conn.transaction = Mock(return_value=AsyncContext(None))
    pool = Mock()
    pool.acquire = Mock(return_value=AsyncContext(conn))

    with (
        patch.object(db, "manager_pool", pool),
        patch("youth_agent.tools.run_guardrails", return_value=[]),
        patch(
            "youth_agent.tools.execute_readonly",
            new=AsyncMock(
                return_value=[
                    {"x_axis": "A", "data": 1},
                    {"x_axis": "B", "data": 2},
                ]
            ),
        ),
    ):
        ctx = AsyncMock()
        ctx.deps = []
        result = await publish_component(
            ctx, _make_publish_spec(chart_types=["line"])
        )

    assert result.success is True
    assert ctx.deps == ["test_pub_001"]
    assert any(
        "INSERT INTO query_charts" in call.args[0]
        for call in conn.execute.await_args_list
    )
    query_insert = next(
        call
        for call in conn.execute.await_args_list
        if "INSERT INTO query_charts" in call.args[0]
    )
    assert query_insert.args[8:10] == ("max", "now")
    chart_update = next(
        call
        for call in conn.execute.await_args_list
        if "UPDATE component_charts" in call.args[0]
    )
    assert chart_update.args[2] == ["BarChart"]


# ===================================================================
# Tests that require a live database
# ===================================================================


@pytest.mark.asyncio
async def test_publish_validates_column_order():
    """Column order returned by query_chart must match QUERY_TYPE_COLUMNS.

    This test requires a live database because it actually executes the
    chart SQL via execute_readonly to inspect column names.  We skip at
    runtime if the data_pool is not initialised (env var alone is not enough).
    """
    from youth_agent import db
    from youth_agent.tools import publish_component

    if db.data_pool is None:
        pytest.skip("data_pool not initialised (no live DB)")

    # Deliberately swap the column aliases so order is wrong
    bad_sql = (
        "SELECT value AS data, area_code AS x_axis "
        "FROM youth_pop_single_age "
        "WHERE period_start >= '2018' AND period_start <= '2024'"
    )
    spec = _make_publish_spec(query_chart=bad_sql)
    ctx = AsyncMock()

    result: PublishResult = await publish_component(ctx, spec)

    assert result.success is False
    assert any(v.check_name == "column_order" for v in result.violations)
