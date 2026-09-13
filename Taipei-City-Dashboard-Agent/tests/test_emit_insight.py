import os

os.environ.setdefault("BEDROCK_MODEL", "test-model")
os.environ.setdefault("DB_DATA_DSN", "postgresql://unused")
os.environ.setdefault("DB_MANAGER_DSN", "postgresql://unused")

from unittest.mock import MagicMock

import pytest
from pydantic_ai import FunctionToolResultEvent, ModelRetry
from pydantic_ai.messages import ToolReturnPart

from youth_agent.api import _format_sse_event
from youth_agent.tools import emit_insight

VALID = dict(
    title="青年人口",
    claim="25–34 歲人口於 2024–2025 年下降 6.2%。",
    narrative="下降速度較 2019–2023 年明顯加快，形成趨勢轉折。",
    hypothesis="可能與居住成本或就業機會變化有關，仍需其他資料驗證。",
)


def _ctx():
    ctx = MagicMock()
    ctx.deps = {"published": [], "insights": []}
    return ctx


@pytest.mark.asyncio
async def test_emit_insight_streams_card_with_hypothesis():
    ctx = _ctx()
    content = await emit_insight(ctx, **VALID)

    assert ctx.deps["insights"][0]["hypothesis"] == VALID["hypothesis"]
    event = FunctionToolResultEvent(
        part=ToolReturnPart(tool_name="emit_insight", content=content, tool_call_id="t1")
    )
    sse = _format_sse_event(event)
    assert sse["type"] == "insight"
    assert sse["data"]["hypothesis"] == VALID["hypothesis"]


@pytest.mark.asyncio
async def test_emit_insight_retries_hypothesis_written_as_fact():
    ctx = _ctx()
    with pytest.raises(ModelRetry):
        await emit_insight(ctx, **{**VALID, "hypothesis": "居住成本上升了 10%。"})
    assert ctx.deps["insights"] == []
