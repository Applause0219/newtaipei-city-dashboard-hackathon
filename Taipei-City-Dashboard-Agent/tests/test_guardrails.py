"""Tests for youth_agent.guardrails -- run_guardrails and the six individual checks."""

from __future__ import annotations

import pytest

from youth_agent.guardrails import run_guardrails
from youth_agent.schemas import ComponentSpec, Fact, GuardrailViolation


# ===================================================================
# Helper
# ===================================================================


def make_spec(**overrides) -> ComponentSpec:
    """Return a minimal valid ComponentSpec, merging *overrides*."""
    defaults = dict(
        index="test_001",
        name="Test Component",
        query_type="two_d",
        query_chart=(
            "SELECT area_code AS x_axis, value AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024'"
        ),
        chart_types=["BarChart"],
        chart_colors=["#2e86c1"],
        chart_unit="人",  # 人
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


def _violation_names(violations: list[GuardrailViolation]) -> list[str]:
    """Extract check_name from each violation for concise assertions."""
    return [v.check_name for v in violations]


# ===================================================================
# Check 1 -- age_scope
# ===================================================================


class TestAgeScope:
    """Check 1: age_classification and age_range boundaries."""

    def test_d_classification_violation(self):
        spec = make_spec(age_classification="D")
        violations = run_guardrails(spec)
        assert "age_scope" in _violation_names(violations)

    def test_none_classification_no_age_terms_passes(self):
        spec = make_spec(
            age_classification=None,
            claim="housing prices are rising",
            narrative="data shows upward trend",
        )
        violations = run_guardrails(spec)
        assert "age_scope" not in _violation_names(violations)

    def test_none_classification_with_age_term_violation(self):
        spec = make_spec(
            age_classification=None,
            claim="青年 housing prices are rising",  # contains 青年
            narrative="data shows upward trend",
        )
        violations = run_guardrails(spec)
        assert "age_scope" in _violation_names(violations)

    def test_age_range_outside_operational_violation(self):
        spec = make_spec(age_range="10-50")
        violations = run_guardrails(spec)
        age_violations = [v for v in violations if v.check_name == "age_scope"]
        assert any("OPERATIONAL_YOUTH" in v.reason for v in age_violations)

    def test_age_range_within_operational_passes(self):
        spec = make_spec(age_range="20-34")
        violations = run_guardrails(spec)
        age_violations = [v for v in violations if v.check_name == "age_scope"]
        assert len(age_violations) == 0


# ===================================================================
# Check 2 -- no_interpolation
# ===================================================================


class TestNoInterpolation:
    """Check 2: no arithmetic on age_lower / age_upper columns."""

    def test_midpoint_formula_violation(self):
        sql = (
            "SELECT (age_lower + age_upper) / 2 AS mid_age, value "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024'"
        )
        spec = make_spec(query_chart=sql)
        violations = run_guardrails(spec)
        assert "no_interpolation" in _violation_names(violations)

    def test_age_split_violation(self):
        sql = (
            "SELECT value FROM youth_pop_single_age "
            "WHERE age_lower > 20 AND age_upper < 30 "
            "AND period_start >= '2018' AND period_start <= '2024'"
        )
        spec = make_spec(query_chart=sql)
        violations = run_guardrails(spec)
        assert "no_interpolation" in _violation_names(violations)

    def test_normal_sql_passes(self):
        spec = make_spec()  # default query_chart has no age arithmetic
        violations = run_guardrails(spec)
        assert "no_interpolation" not in _violation_names(violations)


# ===================================================================
# Check 3 -- causal_language
# ===================================================================


class TestCausalLanguage:
    """Check 3: causal markers must not appear in claim/narrative."""

    @pytest.mark.parametrize("marker", ["導致", "造成", "因為"])
    def test_causal_marker_violation(self, marker: str):
        spec = make_spec(
            claim=f"人口減少{marker}勞動力不足",
        )
        violations = run_guardrails(spec)
        causal = [v for v in violations if v.check_name == "causal_language"]
        assert any(marker in v.reason for v in causal)

    def test_clean_claim_passes(self):
        spec = make_spec(
            claim="新北市 {pop_val} 歲青年人口呈下降趨勢",
        )
        violations = run_guardrails(spec)
        assert "causal_language" not in _violation_names(violations)


# ===================================================================
# Check 4 -- numbers_traceable
# ===================================================================


class TestNumbersTraceable:
    """Check 4: raw numerics must use {fact_key}; facts need provenance."""

    def test_raw_number_violation(self):
        spec = make_spec(
            claim="新北市 123456 歲青年人口呈下降趨勢",
        )
        violations = run_guardrails(spec)
        num_violations = [
            v for v in violations if v.check_name == "numbers_traceable"
        ]
        assert any("123456" in v.reason for v in num_violations)

    def test_placeholder_only_passes(self):
        spec = make_spec()  # default uses {pop_val}
        violations = run_guardrails(spec)
        num_violations = [
            v for v in violations if v.check_name == "numbers_traceable"
        ]
        assert len(num_violations) == 0

    def test_empty_source_sql_hash_violation(self):
        bad_fact = Fact(
            value=123456,
            unit="人",
            source_sql_hash="",
            source_cell=[0, 1],
        )
        spec = make_spec(facts={"pop_val": bad_fact})
        violations = run_guardrails(spec)
        num_violations = [
            v for v in violations if v.check_name == "numbers_traceable"
        ]
        assert any("source_sql_hash" in v.reason for v in num_violations)

    def test_invalid_source_cell_violation(self):
        bad_fact = Fact(
            value=123456,
            unit="人",
            source_sql_hash="abc123",
            source_cell=[-1, 0],
        )
        spec = make_spec(facts={"pop_val": bad_fact})
        violations = run_guardrails(spec)
        num_violations = [
            v for v in violations if v.check_name == "numbers_traceable"
        ]
        assert any("source_cell" in v.reason for v in num_violations)

    def test_years_do_not_trigger_violation(self):
        spec = make_spec(
            claim="2024 年新北市 {pop_val} 歲青年人口呈下降趨勢",
        )
        violations = run_guardrails(spec)
        num_violations = [
            v for v in violations if v.check_name == "numbers_traceable"
        ]
        assert len(num_violations) == 0


# ===================================================================
# Check 5 -- aggregation_rule
# ===================================================================


class TestAggregationRule:
    """Check 5: bare SUM across age groups with rate unit is invalid."""

    def test_sum_no_age_group_rate_unit_violation(self):
        sql = (
            "SELECT area_code AS x_axis, SUM(value) AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024' "
            "GROUP BY area_code"
        )
        spec = make_spec(query_chart=sql, chart_unit="%")
        violations = run_guardrails(spec)
        assert "aggregation_rule" in _violation_names(violations)

    def test_sum_grouped_by_age_passes(self):
        sql = (
            "SELECT age_lower AS x_axis, SUM(value) AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024' "
            "GROUP BY age_lower"
        )
        spec = make_spec(query_chart=sql, chart_unit="%")
        violations = run_guardrails(spec)
        assert "aggregation_rule" not in _violation_names(violations)

    def test_sum_ratio_with_explicit_denominator_passes(self):
        sql = (
            "SELECT area_code AS x_axis, "
            "ROUND(SUM(value) * 100.0 / NULLIF(SUM(value), 0), 1) AS data "
            "FROM youth_pop_single_age GROUP BY area_code"
        )
        spec = make_spec(query_chart=sql, chart_unit="%")
        violations = run_guardrails(spec)
        assert "aggregation_rule" not in _violation_names(violations)

    def test_no_sum_passes(self):
        spec = make_spec()  # default query has no SUM
        violations = run_guardrails(spec)
        assert "aggregation_rule" not in _violation_names(violations)

    def test_sum_count_unit_passes(self):
        sql = (
            "SELECT area_code AS x_axis, SUM(value) AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024' "
            "GROUP BY area_code"
        )
        spec = make_spec(query_chart=sql, chart_unit="人")  # 人 (not a rate)
        violations = run_guardrails(spec)
        assert "aggregation_rule" not in _violation_names(violations)


# ===================================================================
# Check 6 -- min_data_points
# ===================================================================


class TestMinDataPoints:
    """Check 6: insight_type demands a minimum time span."""

    def test_trend_too_few_years_violation(self):
        sql = (
            "SELECT area_code AS x_axis, value AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2023' AND period_start <= '2024'"
        )
        spec = make_spec(query_chart=sql, insight_type="trend")
        violations = run_guardrails(spec)
        assert "min_data_points" in _violation_names(violations)

    def test_trend_enough_years_passes(self):
        sql = (
            "SELECT area_code AS x_axis, value AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2018' AND period_start <= '2024'"
        )
        spec = make_spec(query_chart=sql, insight_type="trend")
        violations = run_guardrails(spec)
        assert "min_data_points" not in _violation_names(violations)

    def test_change_point_too_few_years_violation(self):
        sql = (
            "SELECT area_code AS x_axis, value AS data "
            "FROM youth_pop_single_age "
            "WHERE period_start >= '2020' AND period_start <= '2024'"
        )
        spec = make_spec(query_chart=sql, insight_type="change_point")
        violations = run_guardrails(spec)
        assert "min_data_points" in _violation_names(violations)

    def test_empty_insight_type_skipped(self):
        spec = make_spec(insight_type="")
        violations = run_guardrails(spec)
        assert "min_data_points" not in _violation_names(violations)


# ===================================================================
# Integration
# ===================================================================


class TestRunGuardrailsIntegration:
    """End-to-end: run_guardrails collects from all checks."""

    def test_valid_spec_passes_all(self):
        spec = make_spec()
        violations = run_guardrails(spec)
        assert violations == [], (
            f"Expected zero violations but got: "
            f"{[(v.check_name, v.reason) for v in violations]}"
        )

    def test_multiple_violations_no_short_circuit(self):
        spec = make_spec(
            age_classification="D",
            claim="人口減少導致 123456 人勞動力不足",
        )
        violations = run_guardrails(spec)
        names = _violation_names(violations)
        assert "age_scope" in names
        assert "causal_language" in names
        assert "numbers_traceable" in names
        assert len(violations) >= 3
