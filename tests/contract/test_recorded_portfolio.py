from __future__ import annotations

from copy import deepcopy

from agentic_investment_os.adapters.recorded_portfolio import RecordedPortfolioSource
from agentic_investment_os.portfolio.construction import (
    PortfolioInputSet,
    PortfolioRefusalReason,
)
from tests._portfolio import recorded_portfolio_inputs
from tests._universe import mutable_mapping_list, typed_universe_inputs

_EXPECTED_RISK_INPUTS = 3
_HASH_LENGTH = 64


def test_recorded_portfolio_source_validates_complete_canonical_as_of_inputs() -> None:
    position_snapshot = typed_universe_inputs().position_snapshot
    payload = recorded_portfolio_inputs(position_snapshot)

    loaded = RecordedPortfolioSource(payload).load(position_snapshot)

    assert isinstance(loaded, PortfolioInputSet)
    assert loaded.position_snapshot is position_snapshot
    assert loaded.cash_currency == "USD"
    assert len(loaded.risk_inputs) == _EXPECTED_RISK_INPUTS
    assert len(loaded.input_id) == _HASH_LENGTH


def test_recorded_portfolio_source_refuses_noncanonical_or_mismatched_material() -> None:
    position_snapshot = typed_universe_inputs().position_snapshot
    noncanonical = recorded_portfolio_inputs(position_snapshot)
    noncanonical["cash"] = "1e5"
    mismatched = deepcopy(recorded_portfolio_inputs(position_snapshot))
    mismatched["position_snapshot_hash"] = "f" * 64

    assert (
        RecordedPortfolioSource(noncanonical).load(position_snapshot)
        is PortfolioRefusalReason.INVALID_REQUEST
    )
    assert (
        RecordedPortfolioSource(mismatched).load(position_snapshot)
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
    )


def test_recorded_portfolio_source_refuses_hostile_nested_shapes_without_partial_admission() -> (
    None
):
    position_snapshot = typed_universe_inputs().position_snapshot
    payload = recorded_portfolio_inputs(position_snapshot)
    invalid: list[dict[str, object]] = []

    missing_root = deepcopy(payload)
    del missing_root["source_identity"]
    invalid.append(missing_root)
    for field, root_value in (
        ("risk_inputs", []),
        ("observed_at", "not-an-instant"),
        ("cash", "-1"),
    ):
        corrupted = deepcopy(payload)
        corrupted[field] = root_value
        invalid.append(corrupted)
    for field, risk_value in (
        ("identity", {}),
        ("price", "0"),
        ("price_unit", "shares"),
        ("sector", "INVALID SECTOR"),
        ("adjusted_closes", []),
    ):
        corrupted = deepcopy(payload)
        _risk_items(corrupted)[0][field] = risk_value
        invalid.append(corrupted)
    malformed_close = deepcopy(payload)
    _closes(malformed_close)[0] = {}
    invalid.append(malformed_close)
    for field, close_value in (("observed_at", "invalid"), ("price", "0")):
        corrupted = deepcopy(payload)
        _closes(corrupted)[0][field] = close_value
        invalid.append(corrupted)
    malformed_event = deepcopy(payload)
    _risk_items(malformed_event)[0]["material_events"] = [{}]
    invalid.append(malformed_event)
    contradictory_event = deepcopy(payload)
    _risk_items(contradictory_event)[0]["material_events"] = [
        {
            "event_id": "earnings",
            "event_type": "company_release",
            "releases_at": "2026-08-22T20:00:00.000000+00:00",
            "source_identity": "issuer-calendar-v1",
            "calendar_available_at": "2026-08-21T19:40:00.000000+00:00",
            "release_artifact_id": None,
            "release_available_at": None,
            "fresh_research_request_id": "a" * 64,
            "fresh_research_resolution_id": "b" * 64,
        }
    ]
    invalid.append(contradictory_event)
    future_self_certified = deepcopy(payload)
    _risk_items(future_self_certified)[0]["material_events"] = [
        {
            "event_id": "earnings",
            "event_type": "company_release",
            "releases_at": "2026-08-22T20:00:00.000000+00:00",
            "source_identity": "issuer-calendar-v1",
            "calendar_available_at": "2026-08-21T19:40:00.000000+00:00",
            "release_artifact_id": "a" * 64,
            "release_available_at": "2026-08-21T19:40:00.000000+00:00",
            "fresh_research_request_id": "b" * 64,
            "fresh_research_resolution_id": "c" * 64,
        }
    ]
    invalid.append(future_self_certified)

    assert all(
        RecordedPortfolioSource(item).load(position_snapshot)
        is PortfolioRefusalReason.INVALID_REQUEST
        for item in invalid
    )


def test_recorded_portfolio_source_rejects_contradictory_typed_material() -> None:
    position_snapshot = typed_universe_inputs().position_snapshot
    payload = recorded_portfolio_inputs(position_snapshot)
    duplicate_risk = deepcopy(payload)
    _risk_items(duplicate_risk).append(deepcopy(_risk_items(duplicate_risk)[0]))
    duplicate_close = deepcopy(payload)
    _closes(duplicate_close)[1] = deepcopy(_closes(duplicate_close)[0])
    backwards_availability = deepcopy(payload)
    backwards_availability["available_at"] = "2026-08-21T19:00:00.000000+00:00"

    assert (
        RecordedPortfolioSource(duplicate_close).load(position_snapshot)
        is PortfolioRefusalReason.INVALID_REQUEST
    )
    assert all(
        RecordedPortfolioSource(item).load(position_snapshot)
        is PortfolioRefusalReason.CONTRADICTORY_INPUT
        for item in (duplicate_risk, backwards_availability)
    )


def test_recorded_portfolio_source_admits_a_complete_fresh_event_record() -> None:
    position_snapshot = typed_universe_inputs().position_snapshot
    payload = recorded_portfolio_inputs(position_snapshot)
    _risk_items(payload)[0]["material_events"] = [
        {
            "event_id": "earnings",
            "event_type": "company_release",
            "releases_at": "2026-08-20T20:00:00.000000+00:00",
            "source_identity": "issuer-calendar-v1",
            "calendar_available_at": "2026-08-19T19:40:00.000000+00:00",
            "release_artifact_id": "a" * 64,
            "release_available_at": "2026-08-20T20:01:00.000000+00:00",
            "fresh_research_request_id": "b" * 64,
            "fresh_research_resolution_id": "c" * 64,
        }
    ]

    loaded = RecordedPortfolioSource(payload).load(position_snapshot)

    assert isinstance(loaded, PortfolioInputSet)
    assert len(loaded.risk_inputs[0].material_events) == 1
    assert loaded.risk_inputs[0].material_events[0].blocks_new_position is False


def _risk_items(payload: dict[str, object]) -> list[dict[str, object]]:
    return mutable_mapping_list(payload["risk_inputs"])


def _closes(payload: dict[str, object]) -> list[dict[str, object]]:
    return mutable_mapping_list(_risk_items(payload)[0]["adjusted_closes"])
