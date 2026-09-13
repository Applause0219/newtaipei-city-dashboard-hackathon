import os

os.environ.setdefault("BEDROCK_MODEL", "test-model")
os.environ.setdefault("DB_DATA_DSN", "postgresql://unused")
os.environ.setdefault("DB_MANAGER_DSN", "postgresql://unused")

from youth_agent.schemas import QUERY_TYPE_COLUMNS
from youth_agent.tools import _CHART_TYPES_BY_QUERY, _DEFAULT_CHART_TYPE


def test_bubble_matches_go_backend_columns():
    # Go models.BubbleData reads exactly these columns (componentData.go).
    assert QUERY_TYPE_COLUMNS["bubble"] == ["y_axis", "x", "y", "z", "category"]


def test_bubble_chart_is_allowed_and_default():
    assert "BubbleChart" in _CHART_TYPES_BY_QUERY["bubble"]
    assert _DEFAULT_CHART_TYPE["bubble"] == "BubbleChart"
