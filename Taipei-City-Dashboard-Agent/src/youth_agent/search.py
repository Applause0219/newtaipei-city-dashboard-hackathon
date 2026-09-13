"""Pure search-query helpers used by the manifest search endpoint."""

from __future__ import annotations


_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "住房": ("住宅", "housing"),
    "住屋": ("住宅", "housing"),
    "居住": ("住宅", "housing"),
    "租屋": ("rental", "租賃"),
    "租賃": ("rental",),
    "房價": ("住宅", "price"),
    "負擔": ("burden",),
    "房貸": ("loan",),
    "就業": ("employment", "labor"),
    "失業": ("unemployment", "employment", "labor"),
    "勞動": ("labor", "employment"),
    "工作": ("employment",),
    "薪資": ("salary", "wage"),
}


def search_terms(query: str) -> list[str]:
    """Return literal terms plus aliases for common Chinese search wording."""
    terms = list(dict.fromkeys(
        query[i : i + 2] for i in range(max(1, len(query) - 1))
    ))
    for phrase, aliases in _SEARCH_ALIASES.items():
        if phrase in query:
            terms.extend(alias for alias in aliases if alias not in terms)
    return terms
