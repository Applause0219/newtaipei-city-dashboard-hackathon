"""Tests for youth_agent.sql_guard -- validate_sql, add_safeguards, compute_sql_hash."""

from __future__ import annotations

import pytest

from youth_agent.sql_guard import add_safeguards, compute_sql_hash, validate_sql


# ===================================================================
# validate_sql -- queries that SHOULD PASS
# ===================================================================


class TestValidateSqlPass:
    """Queries that must be accepted by validate_sql."""

    def test_simple_select(self):
        ok, reason = validate_sql("SELECT * FROM youth_pop_single_age")
        assert ok is True, reason

    def test_select_with_where(self):
        sql = (
            "SELECT value FROM youth_labor_participation_age_tw "
            "WHERE period_start >= '2020'"
        )
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_select_with_join(self):
        sql = (
            "SELECT a.value FROM youth_pop_single_age a "
            "JOIN youth_asset_manifest b ON a.indicator_id = b.asset_id"
        )
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_cte(self):
        sql = "WITH t AS (SELECT * FROM youth_pop_single_age) SELECT * FROM t"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_schema_qualified(self):
        ok, reason = validate_sql("SELECT * FROM public.youth_pop_single_age")
        assert ok is True, reason

    def test_youth_asset_manifest(self):
        sql = (
            "SELECT asset_id, title, domains, age_classification "
            "FROM youth_asset_manifest"
        )
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_aggregate_functions(self):
        sql = (
            "SELECT area_code, regr_slope(value, EXTRACT(YEAR FROM period_start)) "
            "FROM youth_pop_single_age GROUP BY area_code"
        )
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_explicit_json_cast_on_source_breakdown(self):
        sql = (
            "SELECT breakdown::jsonb ->> 'category' "
            "FROM youth_labor_statistics_age_tw"
        )
        ok, reason = validate_sql(sql)
        assert ok is True, reason


# ===================================================================
# validate_sql -- queries that SHOULD FAIL
# ===================================================================


class TestValidateSqlFail:
    """Queries that must be rejected by validate_sql."""

    def test_insert(self):
        ok, reason = validate_sql(
            "INSERT INTO youth_pop_single_age VALUES (1)"
        )
        assert ok is False
        assert "INSERT" in reason.upper() or "SELECT" in reason.upper()

    def test_drop(self):
        ok, reason = validate_sql("DROP TABLE youth_pop_single_age")
        assert ok is False
        assert "DROP" in reason.upper() or "SELECT" in reason.upper()

    def test_stacked_statements(self):
        ok, reason = validate_sql(
            "SELECT 1; DROP TABLE youth_pop_single_age"
        )
        assert ok is False
        assert "emicolon" in reason.lower()  # "Semicolons are not allowed"

    def test_non_youth_table(self):
        ok, reason = validate_sql("SELECT * FROM users")
        assert ok is False
        assert "users" in reason.lower()

    def test_dml_in_cte(self):
        sql = (
            "WITH t AS (DELETE FROM youth_pop_single_age RETURNING *) "
            "SELECT * FROM t"
        )
        ok, reason = validate_sql(sql)
        assert ok is False
        assert "DELETE" in reason.upper()

    def test_pg_read_file(self):
        ok, reason = validate_sql("SELECT pg_read_file('/etc/passwd')")
        assert ok is False
        assert "pg_read_file" in reason.lower()

    def test_dblink(self):
        sql = (
            "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)"
        )
        ok, reason = validate_sql(sql)
        assert ok is False
        assert "dblink" in reason.lower()

    def test_set(self):
        ok, reason = validate_sql("SET search_path TO evil")
        assert ok is False
        # First keyword is SET, not SELECT/WITH
        assert "SELECT" in reason.upper() or "SET" in reason.upper()

    def test_empty_sql(self):
        ok, reason = validate_sql("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_json_operator_on_source_breakdown_text(self):
        sql = (
            "SELECT breakdown ->> 'category' "
            "FROM youth_labor_statistics_age_tw"
        )
        ok, reason = validate_sql(sql)
        assert ok is False
        assert "breakdown::jsonb" in reason

        ok, reason = validate_sql(
            "SELECT x.breakdown -> 'category' "
            "FROM youth_labor_statistics_age_tw x"
        )
        assert ok is False
        assert "breakdown::jsonb" in reason


# ===================================================================
# add_safeguards
# ===================================================================


class TestAddSafeguards:
    """Tests for the add_safeguards wrapper."""

    def test_adds_limit_when_missing(self):
        result = add_safeguards(
            "SELECT * FROM youth_pop_single_age",
            row_limit=500,
            wrap_timeout=False,
        )
        assert "LIMIT 500" in result

    def test_does_not_add_limit_when_present(self):
        sql = "SELECT * FROM youth_pop_single_age LIMIT 10"
        result = add_safeguards(sql, row_limit=500, wrap_timeout=False)
        assert "LIMIT 10" in result
        assert "LIMIT 500" not in result

    def test_wrap_timeout_true(self):
        result = add_safeguards(
            "SELECT * FROM youth_pop_single_age",
            wrap_timeout=True,
        )
        assert "SET LOCAL statement_timeout" in result

    def test_wrap_timeout_false(self):
        result = add_safeguards(
            "SELECT * FROM youth_pop_single_age",
            wrap_timeout=False,
        )
        assert "SET LOCAL statement_timeout" not in result

    def test_repairs_postgres_float_round_with_nested_arguments(self):
        sql = (
            "SELECT ROUND((SUM(value) * 100.0 / NULLIF(SUM(value), 0)), 1) AS pct "
            "FROM youth_traffic_injury_ntpc"
        )
        result = add_safeguards(sql, wrap_timeout=False)
        assert "ROUND(((SUM(value) * 100.0 / NULLIF(SUM(value), 0)))::numeric, 1)" in result

    def test_does_not_rewrite_round_inside_sql_text(self):
        sql = "SELECT 'round(value, 1)' AS example FROM youth_traffic_injury_ntpc"
        result = add_safeguards(sql, wrap_timeout=False)
        assert "'round(value, 1)'" in result

    def test_does_not_rewrite_one_argument_round(self):
        sql = "SELECT ROUND(value) AS rounded FROM youth_traffic_injury_ntpc"
        result = add_safeguards(sql, wrap_timeout=False)
        assert "ROUND(value)" in result


# ===================================================================
# compute_sql_hash
# ===================================================================


class TestComputeSqlHash:
    """Tests for the compute_sql_hash normaliser."""

    def test_same_sql_different_whitespace(self):
        h1 = compute_sql_hash("SELECT  *   FROM youth_pop_single_age")
        h2 = compute_sql_hash("SELECT * FROM youth_pop_single_age")
        h3 = compute_sql_hash("  SELECT *\n  FROM youth_pop_single_age  ")
        assert h1 == h2 == h3

    def test_different_sql_different_hash(self):
        h1 = compute_sql_hash("SELECT * FROM youth_pop_single_age")
        h2 = compute_sql_hash("SELECT * FROM youth_labor_participation_age_tw")
        assert h1 != h2
