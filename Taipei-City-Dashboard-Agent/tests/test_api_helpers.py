"""Regression tests for API identifiers exposed to the frontend."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_ai import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
)

from youth_agent.schemas import AnalysisResult, component_id_for_asset


def test_component_id_is_stable_and_asset_specific():
    assets = [
        "employment_structure_age_tw",
        "bureau_activity_ntpc",
        "housing_burden_ntpc",
    ]
    ids = [component_id_for_asset(asset) for asset in assets]

    assert ids == [component_id_for_asset(asset) for asset in assets]
    assert len(ids) == len(set(ids))
    assert all(90_000 <= component_id < 2_147_483_647 for component_id in ids)


def test_format_sse_event_streams_model_reasoning(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL", "test-model")
    monkeypatch.setenv("DB_DATA_DSN", "postgresql://unused")
    monkeypatch.setenv("DB_MANAGER_DSN", "postgresql://unused")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    from youth_agent.api import _format_sse_event

    assert _format_sse_event(
        PartStartEvent(index=2, part=ThinkingPart(content="先找相關資料集。"))
    ) == {
        "type": "reasoning",
        "part_index": 2,
        "content": "先找相關資料集。",
        "append": False,
    }
    assert _format_sse_event(
        PartDeltaEvent(index=2, delta=ThinkingPartDelta(content_delta="再檢查趨勢。"))
    ) == {
        "type": "reasoning",
        "part_index": 2,
        "content": "再檢查趨勢。",
        "append": True,
    }


def test_format_sse_event_exposes_sql_as_structured_args(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL", "test-model")
    monkeypatch.setenv("DB_DATA_DSN", "postgresql://unused")
    monkeypatch.setenv("DB_MANAGER_DSN", "postgresql://unused")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    from youth_agent.api import _format_sse_event

    event = FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name="execute_sql",
            args='{"query":"SELECT count(*) FROM youth_asset_manifest"}',
            tool_call_id="sql-1",
        )
    )

    assert _format_sse_event(event) == {
        "type": "tool_call",
        "tool": "execute_sql",
        "args": {"query": "SELECT count(*) FROM youth_asset_manifest"},
    }


def test_analysis_result_rejects_empty_insight_text():
    with pytest.raises(ValidationError):
        AnalysisResult(question="test", insights=[{}])
