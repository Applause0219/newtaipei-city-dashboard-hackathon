"""Regression tests for deterministic manifest metadata."""

from __future__ import annotations

import pytest

from scripts.build_manifest import aggregation_rule_for, classify_age


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [
        (18, 35, "A"),
        (15, 39, "B"),
        (20, 24, "C"),
        (15, 44, "D"),
        (10, 19, "D"),
        (35, 44, "D"),
    ],
)
def test_age_classification_respects_original_bins(lower, upper, expected):
    classification, _ = classify_age([{"lower": lower, "upper": upper}])
    assert classification == expected


@pytest.mark.parametrize(
    ("value_type", "expected"),
    [
        ("count", "sum"),
        ("rate", "require_denominator"),
        ("mean", "require_weight"),
        ("median", "not_aggregatable"),
        (None, "not_aggregatable"),
    ],
)
def test_measure_rules_fail_closed(value_type, expected):
    assert aggregation_rule_for(value_type) == expected
