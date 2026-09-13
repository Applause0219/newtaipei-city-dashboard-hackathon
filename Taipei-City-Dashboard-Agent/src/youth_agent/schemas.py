from __future__ import annotations

import re
import zlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


POLICY_YOUTH = {"lower": 18, "upper": 35}
OPERATIONAL_YOUTH = {"lower": 15, "upper": 40}


def component_id_for_asset(asset_id: str) -> int:
    """Return a stable positive PostgreSQL integer ID for a dataset asset."""
    normalized = asset_id.strip().lower()
    if not normalized:
        raise ValueError("asset_id must not be empty")
    return 90_000 + (zlib.crc32(normalized.encode("utf-8")) & 0xFFFFFFFF) % (
        2_147_483_647 - 90_000
    )


class AgeClass(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class AggregationRule(str, Enum):
    SUM = "sum"
    REQUIRE_DENOMINATOR = "require_denominator"
    REQUIRE_WEIGHT = "require_weight"
    NOT_AGGREGATABLE = "not_aggregatable"


TYPE_TO_RULE: dict[str, AggregationRule] = {
    "count": AggregationRule.SUM,
    "ratio": AggregationRule.REQUIRE_DENOMINATOR,
    "rate": AggregationRule.REQUIRE_DENOMINATOR,
    "mean": AggregationRule.REQUIRE_WEIGHT,
    "median": AggregationRule.NOT_AGGREGATABLE,
    "index": AggregationRule.NOT_AGGREGATABLE,
}

CAUSAL_MARKERS: list[str] = [
    "導致", "造成", "因為", "由於", "使得", "引起", "引發",
    "促使", "促成", "肇因", "因此", "所以", "歸因",
]

QUERY_TYPE_COLUMNS: dict[str, list[str]] = {
    "two_d": ["x_axis", "data"],
    "three_d": ["x_axis", "icon", "y_axis", "data"],
    # Go backend GetBubbleData: one series per y_axis, points (x, y, z);
    # category is a JSON string naming the axes, e.g. {"x":"租金","y":"所得","z":"人口"}.
    "bubble": ["y_axis", "x", "y", "z", "category"],
    "time": ["x_axis", "y_axis", "data"],
}


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    sql_hash: str


class Fact(BaseModel):
    value: float
    unit: str
    source_sql_hash: str
    source_cell: list[int]  # [row_index, col_index]


class GuardrailViolation(BaseModel):
    check_name: str
    reason: str


class ComponentSpec(BaseModel):
    index: str
    name: str
    query_type: str
    query_chart: str
    chart_types: list[str]
    chart_colors: list[str]
    chart_unit: str
    city: str = "metrotaipei"
    source: str = ""
    short_desc: str = ""
    long_desc: str = ""
    time_from: str = ""
    time_to: str = ""
    update_freq: int | None = None
    update_freq_unit: str = ""
    links: list[str] = Field(default_factory=list)
    contributors: list[str] = Field(default_factory=list)
    claim: str = ""
    narrative: str = ""
    facts: dict[str, Fact] = Field(default_factory=dict)
    datasets_used: list[str] = Field(default_factory=list)
    age_classification: str | None = None
    age_range: str = ""
    insight_type: str = ""


class PublishResult(BaseModel):
    success: bool
    index: str
    violations: list[GuardrailViolation] = Field(default_factory=list)


HYPOTHESIS_HEDGES: list[str] = ["可能", "或許", "推測"]


class AnalysisInsight(BaseModel):
    """One insight, split into three registers that must stay distinguishable.

    claim      -> Data Fact           what the data says
    narrative  -> Analytical Insight  what we read from the data
    hypothesis -> Hypothesis          not proven by this data

    The split is enforced here, not left to wording: a hypothesis without a
    hedge, or with numbers, would read as a fact on the card.
    """

    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    source_sql: str = ""

    @field_validator("claim", "narrative", "hypothesis")
    @classmethod
    def _no_causal_language(cls, v: str) -> str:
        found = [m for m in CAUSAL_MARKERS if m in v]
        if found:
            raise ValueError(f"不得使用因果語言：{'、'.join(found)}")
        return v

    @field_validator("hypothesis")
    @classmethod
    def _hypothesis_is_hedged(cls, v: str) -> str:
        if not any(h in v for h in HYPOTHESIS_HEDGES):
            raise ValueError("hypothesis 必須帶保留語氣（可能／或許／推測）")
        if "驗證" not in v:
            raise ValueError("hypothesis 必須註明仍需驗證，例如「仍需其他資料驗證」")
        if re.search(r"\d", v):
            raise ValueError("hypothesis 不得包含數字：數字屬於 Data Fact（claim）")
        return v


class AnalysisResult(BaseModel):
    question: str
    insights: list[AnalysisInsight] = Field(default_factory=list)
    report_markdown: str = ""
    published: list[str] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
