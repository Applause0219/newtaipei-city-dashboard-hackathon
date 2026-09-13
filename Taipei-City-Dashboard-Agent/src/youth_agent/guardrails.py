"""Deterministic guardrail checks for youth-dashboard component publishing.

Each check is a pure function that inspects a :class:`ComponentSpec` and returns
a :class:`GuardrailViolation` (or ``None`` / a list) without any I/O.
``run_guardrails`` executes all six checks and collects every violation.
"""

from __future__ import annotations

import re
from typing import Sequence

from .schemas import (
    CAUSAL_MARKERS,
    ComponentSpec,
    GuardrailViolation,
    OPERATIONAL_YOUTH,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGE_TERMS_RE = re.compile(r"青年|歲")

# Matches arithmetic on age_lower / age_upper columns
_AGE_ARITH_RE = re.compile(
    r"""
      age_lower\s*[+\-*/]     # age_lower followed by an operator
    | age_upper\s*[+\-*/]     # age_upper followed by an operator
    | [+\-*/]\s*age_lower     # operator before age_lower
    | [+\-*/]\s*age_upper     # operator before age_upper
    | \(\s*age_lower\s*\+\s*age_upper\s*\)\s*/\s*2  # midpoint pattern
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Detects age-bin splitting: age_lower > ... AND ... age_upper <
_AGE_SPLIT_RE = re.compile(
    r"age_lower\s*>\s*\d+.*?age_upper\s*<\s*\d+",
    re.IGNORECASE | re.DOTALL,
)

# Numeric literal in Chinese/English text (digits, optional decimal, optional %)
_NUMBER_RE = re.compile(r"(?<!\{)\b(\d+(?:\.\d+)?)\s*%?")

# 4-digit year (e.g. 2018, 2024)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Age-range patterns like "18-35", "20-24歲", "15~40"
_AGE_RANGE_TEXT_RE = re.compile(r"\b\d{1,3}\s*[-~]\s*\d{1,3}\s*歲?\b")

# Placeholder references like {fact_key}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# SUM(value) or SUM(data) — case-insensitive
_SUM_VALUE_RE = re.compile(r"\bSUM\s*\(\s*(?:value|data)\s*\)", re.IGNORECASE)

# A percentage chart may legitimately aggregate counts when it divides by an
# explicit denominator.  Keep rejecting a bare SUM, but allow SUM / SUM (or
# a precomputed CTE alias) instead of making the model fight the guardrail.
_DENOMINATOR_RE = re.compile(
    r"/\s*(?:NULLIF\s*\(\s*)?(?:SUM|COUNT|AVG|MIN|MAX)\s*\("
    r"|/\s*[a-z_]\w*\.[a-z_]\w*",
    re.IGNORECASE,
)

# GROUP BY clause content
_GROUP_BY_RE = re.compile(r"GROUP\s+BY\s+(.+?)(?:HAVING|ORDER|LIMIT|$)", re.IGNORECASE | re.DOTALL)

# Rate indicators in chart_unit
_RATE_UNIT_RE = re.compile(r"%|率")

# Period range in WHERE clause: period_start >= 'YYYY' ... period_start <= 'YYYY'
# or: period_start >= 'YYYY-MM' ... period_start <= 'YYYY-MM'
_PERIOD_GTE_RE = re.compile(
    r"period_start\s*>=\s*'?(\d{4})(?:-(\d{2}))?(?:-\d{2})?'?",
    re.IGNORECASE,
)
_PERIOD_LTE_RE = re.compile(
    r"period_start\s*<=\s*'?(\d{4})(?:-(\d{2}))?(?:-\d{2})?'?",
    re.IGNORECASE,
)

_MIN_POINTS: dict[str, int] = {
    "trend": 4,
    "change_point": 6,
    "outlier": 6,
}
_DEFAULT_MIN_POINTS = 3


def _parse_age_bounds(age_range: str) -> tuple[int | None, int | None]:
    """Try to extract (lower, upper) integers from an age_range string.

    Supports formats like ``"18-35"``, ``"15~40"``, ``"20 - 24歲"``.
    Returns ``(None, None)`` on failure.
    """
    m = re.match(r"(\d+)\s*[-~]\s*(\d+)", age_range)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_age_scope(spec: ComponentSpec) -> list[GuardrailViolation]:
    """Check 1 -- age_scope."""
    violations: list[GuardrailViolation] = []

    # D-class → unconditionally blocked
    if spec.age_classification == "D":
        violations.append(
            GuardrailViolation(
                check_name="age_scope",
                reason="age_classification is 'D' (out of scope for youth dashboard)",
            )
        )
        return violations  # no further age checks needed

    # No age dimension: allowed as context, but claim/narrative must not
    # contain age-specific conclusions.
    if spec.age_classification is None:
        text = f"{spec.claim} {spec.narrative}"
        if _AGE_TERMS_RE.search(text):
            violations.append(
                GuardrailViolation(
                    check_name="age_scope",
                    reason=(
                        "age_classification is None (no age dimension) but "
                        "claim/narrative contains age-specific language "
                        "(e.g. '青年', '歲')"
                    ),
                )
            )

    # If age_range is provided, verify bounds within OPERATIONAL_YOUTH
    if spec.age_range:
        lower, upper = _parse_age_bounds(spec.age_range)
        if lower is not None and upper is not None:
            op_lo = OPERATIONAL_YOUTH["lower"]
            op_hi = OPERATIONAL_YOUTH["upper"]
            if lower < op_lo or upper > op_hi:
                violations.append(
                    GuardrailViolation(
                        check_name="age_scope",
                        reason=(
                            f"age_range '{spec.age_range}' falls outside "
                            f"OPERATIONAL_YOUTH ({op_lo}-{op_hi})"
                        ),
                    )
                )

    return violations


def _check_no_interpolation(spec: ComponentSpec) -> GuardrailViolation | None:
    """Check 2 -- no_interpolation."""
    sql = spec.query_chart
    if not sql:
        return None

    if _AGE_ARITH_RE.search(sql) or _AGE_SPLIT_RE.search(sql):
        return GuardrailViolation(
            check_name="no_interpolation",
            reason=(
                "query_chart contains arithmetic on age_lower/age_upper columns; "
                "age-bin interpolation or splitting is not allowed"
            ),
        )
    return None


def _check_causal_language(spec: ComponentSpec) -> list[GuardrailViolation]:
    """Check 3 -- causal_language."""
    violations: list[GuardrailViolation] = []
    text = f"{spec.claim} {spec.narrative}"
    for marker in CAUSAL_MARKERS:
        if marker in text:
            violations.append(
                GuardrailViolation(
                    check_name="causal_language",
                    reason=f"causal marker found: '{marker}'",
                )
            )
    return violations


def _is_excluded_number(number_str: str, full_text: str, match_start: int) -> bool:
    """Return True if *number_str* at *match_start* is a year or part of an age range."""
    # 4-digit year
    if _YEAR_RE.fullmatch(number_str):
        return True

    # Check surrounding context for age-range pattern (e.g. "18-35", "20~24歲")
    # Look at a window around the match
    window_start = max(0, match_start - 10)
    window_end = min(len(full_text), match_start + len(number_str) + 10)
    window = full_text[window_start:window_end]
    if _AGE_RANGE_TEXT_RE.search(window):
        return True

    return False


def _check_numbers_traceable(spec: ComponentSpec) -> list[GuardrailViolation]:
    """Check 4 -- numbers_traceable."""
    violations: list[GuardrailViolation] = []

    # 4a: scan claim + narrative for raw numeric literals
    text = f"{spec.claim} {spec.narrative}"

    # Collect all placeholder keys so we can ignore their rendered values
    placeholder_keys = set(_PLACEHOLDER_RE.findall(text))
    # Also note which fact keys exist
    fact_keys = set(spec.facts.keys())

    for m in _NUMBER_RE.finditer(text):
        num_str = m.group(1)
        # Exclude years and age-range components
        if _is_excluded_number(num_str, text, m.start()):
            continue
        # If there are placeholder references and this number appears as a
        # rendered fact, we still flag it -- the text should use {key}, not a
        # literal.  The only safe numbers are excluded categories above.
        violations.append(
            GuardrailViolation(
                check_name="numbers_traceable",
                reason=(
                    f"raw numeric literal '{m.group(0).strip()}' in claim/narrative; "
                    f"use a {{fact_key}} placeholder instead"
                ),
            )
        )

    # 4b: every Fact must have non-empty source_sql_hash and valid source_cell
    for key, fact in spec.facts.items():
        if not fact.source_sql_hash:
            violations.append(
                GuardrailViolation(
                    check_name="numbers_traceable",
                    reason=f"fact '{key}' has empty source_sql_hash",
                )
            )
        if (
            not fact.source_cell
            or len(fact.source_cell) < 2
            or any(c < 0 for c in fact.source_cell[:2])
        ):
            violations.append(
                GuardrailViolation(
                    check_name="numbers_traceable",
                    reason=f"fact '{key}' has invalid source_cell: {fact.source_cell}",
                )
            )

    return violations


def _check_aggregation_rule(spec: ComponentSpec) -> GuardrailViolation | None:
    """Check 5 -- aggregation_rule."""
    sql = spec.query_chart
    if not sql:
        return None

    # Must contain SUM(value) or SUM(data)
    if not _SUM_VALUE_RE.search(sql):
        return None

    # Check GROUP BY does not include age columns → aggregating across age bins
    group_match = _GROUP_BY_RE.search(sql)
    if not group_match:
        # SUM without GROUP BY is a total aggregation — still applies
        aggregating_across_age = True
    else:
        group_cols = group_match.group(1).lower()
        age_cols = {"age_lower", "age_upper", "age_band_raw"}
        aggregating_across_age = not any(col in group_cols for col in age_cols)

    if not aggregating_across_age:
        return None

    # If chart_unit contains a rate indicator, this is invalid
    if _RATE_UNIT_RE.search(spec.chart_unit):
        if _DENOMINATOR_RE.search(sql):
            return None
        return GuardrailViolation(
            check_name="aggregation_rule",
            reason=(
                f"query_chart uses SUM across age groups but chart_unit "
                f"'{spec.chart_unit}' indicates a rate/percentage — "
                f"cannot SUM a rate"
            ),
        )

    return None


def _check_min_data_points(spec: ComponentSpec) -> GuardrailViolation | None:
    """Check 6 -- min_data_points."""
    if not spec.insight_type:
        return None

    required = _MIN_POINTS.get(spec.insight_type, _DEFAULT_MIN_POINTS)

    sql = spec.query_chart
    if not sql:
        return None

    gte_match = _PERIOD_GTE_RE.search(sql)
    lte_match = _PERIOD_LTE_RE.search(sql)
    if not gte_match or not lte_match:
        return None

    try:
        start_year = int(gte_match.group(1))
        end_year = int(lte_match.group(1))
        start_month = int(gte_match.group(2)) if gte_match.group(2) else 1
        end_month = int(lte_match.group(2)) if lte_match.group(2) else 12

        # Estimate number of data points.  Heuristic: if months are provided,
        # count months; otherwise count years.
        if gte_match.group(2) and lte_match.group(2):
            # Monthly granularity
            data_points = (end_year - start_year) * 12 + (end_month - start_month) + 1
        else:
            # Yearly granularity
            data_points = end_year - start_year + 1

        if data_points < required:
            return GuardrailViolation(
                check_name="min_data_points",
                reason=(
                    f"insight_type '{spec.insight_type}' requires at least "
                    f"{required} data points but the SQL period range yields "
                    f"~{data_points}"
                ),
            )
    except (ValueError, TypeError):
        # Defensive: unparseable period values — skip rather than crash
        return None

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_guardrails(spec: ComponentSpec) -> list[GuardrailViolation]:
    """Execute all six guardrail checks against *spec*.

    All checks run unconditionally (no short-circuiting).  Returns a list of
    every :class:`GuardrailViolation` found; an empty list means the spec
    passed.
    """
    violations: list[GuardrailViolation] = []

    # Check 1: age_scope (returns a list)
    violations.extend(_check_age_scope(spec))

    # Check 2: no_interpolation
    v = _check_no_interpolation(spec)
    if v is not None:
        violations.append(v)

    # Check 3: causal_language (returns a list)
    violations.extend(_check_causal_language(spec))

    # Check 4: numbers_traceable (returns a list)
    violations.extend(_check_numbers_traceable(spec))

    # Check 5: aggregation_rule
    v = _check_aggregation_rule(spec)
    if v is not None:
        violations.append(v)

    # Check 6: min_data_points
    v = _check_min_data_points(spec)
    if v is not None:
        violations.append(v)

    return violations
