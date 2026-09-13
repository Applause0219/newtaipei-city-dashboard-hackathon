"""AnalysisInsight keeps Data Fact / Analytical Insight / Hypothesis apart."""

import pytest
from pydantic import ValidationError

from youth_agent.schemas import AnalysisInsight

BASE = {
    "title": "25–34 歲人口下降",
    "claim": "25–34 歲人口於 2024–2025 年下降 6.2%。",
    "narrative": "下降速度較 2019–2023 年明顯加快，形成趨勢轉折。",
    "hypothesis": "可能與居住成本或就業機會變化有關，仍需其他資料驗證。",
}


def test_three_registers_accepted():
    ins = AnalysisInsight(**BASE)
    assert ins.hypothesis.startswith("可能")


def test_hypothesis_required():
    data = {k: v for k, v in BASE.items() if k != "hypothesis"}
    with pytest.raises(ValidationError):
        AnalysisInsight(**data)


@pytest.mark.parametrize(
    "hypothesis",
    [
        "與居住成本有關，仍需其他資料驗證。",  # no hedge: reads as fact
        "可能與居住成本有關。",  # no verification note
        "可能與房價上漲 12% 有關，仍需其他資料驗證。",  # number belongs to claim
        "可能由於居住成本上升，仍需其他資料驗證。",  # causal marker
    ],
)
def test_hypothesis_written_as_fact_rejected(hypothesis):
    with pytest.raises(ValidationError):
        AnalysisInsight(**{**BASE, "hypothesis": hypothesis})


def test_causal_language_rejected_in_narrative():
    with pytest.raises(ValidationError):
        AnalysisInsight(**{**BASE, "narrative": "居住成本上升導致人口下降。"})
