"""Regression tests for API identifiers exposed to the frontend."""

from __future__ import annotations

from youth_agent.schemas import component_id_for_asset


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
