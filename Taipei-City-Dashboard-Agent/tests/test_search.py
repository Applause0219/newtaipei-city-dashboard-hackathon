import os

os.environ.setdefault("BEDROCK_MODEL", "test-model")
os.environ.setdefault("DB_DATA_DSN", "postgresql://unused")
os.environ.setdefault("DB_MANAGER_DSN", "postgresql://unused")

from youth_agent.search import search_terms


def test_search_terms_expand_housing_synonyms():
    terms = search_terms("青年住房負擔與租屋市場")

    assert "住房" in terms
    assert "住宅" in terms
    assert "housing" in terms
    assert "租屋" in terms
    assert "rental" in terms
    assert "burden" in terms
