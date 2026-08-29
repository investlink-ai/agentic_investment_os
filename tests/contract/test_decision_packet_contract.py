from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.portfolio.publication import (
    DecisionPacketAccountScope,
    DecisionPacketValidityWindow,
    DecisionPublicationResult,
    PacketDirection,
    PacketSignature,
    construct_decision_publication,
    parse_champion_decision_record,
    parse_decision_packet,
    validate_decision_publication,
)
from tests._decision import TEST_DECISION_ACCOUNT_SCOPE, TEST_PACKET_CRYPTOGRAPHY
from tests._portfolio import (
    SYNTHETIC_FORECAST_IDS,
    SYNTHETIC_SPY,
    synthetic_portfolio_cycle,
)

_ISSUED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 30, tzinfo=UTC))
_SYNTHETIC_VERIFIER_FAILURE = "synthetic verifier failure"
_EXPECTED_DECISION_RECORD_ID = "63e76881a225e4873bcca90cee8a461172ac827de7c1dd47e53d6eea0a21d49c"
_EXPECTED_BENCHMARK_STATE_ID = "8ea61f31ad62764e998529210ebd3cf728d54a9f06f1a383bf0b395a99ff0059"
_EXPECTED_SOURCE_FINGERPRINT = "3d0dddbcf34ed07d35ada7aeb5bba0e4f0e181216f5d91144f9d08ce1ddffa05"
_EXPECTED_PACKET_ID = "5124c958caaa6c5df60f9b9262d34681a051cf3f95485e66f474a81c9415f481"
_EXPECTED_PACKET_CONTENT_HASH = "af066985063c110a23e70cb0e09c9050aa561a83c769564e4f3b8d1c450db909"
_EXPECTED_SIGNATURE = "55ca6ae3d522779aec566fa7cef9eeab6c2d47532e6edd2df9cd036d2acc3726"
_EXPECTED_SIGNING_BYTES_HASH = "49b22ffd086ed69815630570fbe1b83ac529f1a82b2af5a41a87557af8d42090"


def _publication() -> DecisionPublicationResult:
    cycle_result = synthetic_portfolio_cycle()
    result = construct_decision_publication(
        cycle_result,
        forecast_ids=SYNTHETIC_FORECAST_IDS,
        model_fingerprint="c" * 64,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            cycle_result.balanced.require_house_view().cycle,
            _ISSUED_AT,
            UtcInstant.from_datetime(_ISSUED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(result, DecisionPublicationResult)
    assert result.packet is not None
    return result


def _changed(
    payload: dict[str, object],
    path: tuple[str | int, ...],
    value: object,
) -> dict[str, object]:
    result = deepcopy(payload)
    parent: object = result
    for segment in path[:-1]:
        if type(segment) is int:
            assert type(parent) is list
            parent = parent[segment]
        else:
            assert type(parent) is dict
            parent = parent[segment]
    field = path[-1]
    if type(field) is int:
        assert type(parent) is list
        parent[field] = value
    else:
        assert type(parent) is dict
        parent[field] = value
    return result


def test_publication_contract_has_one_exact_material_and_signature_oracle() -> None:
    cycle = synthetic_portfolio_cycle()
    publication = _publication()
    packet = publication.packet
    assert packet is not None

    assert publication.decision_record.decision_record_id == _EXPECTED_DECISION_RECORD_ID
    assert publication.decision_record.benchmark_state_id == _EXPECTED_BENCHMARK_STATE_ID
    assert publication.decision_record.source_fingerprint == _EXPECTED_SOURCE_FINGERPRINT
    assert packet.packet_id == _EXPECTED_PACKET_ID
    assert packet.content_hash == _EXPECTED_PACKET_CONTENT_HASH
    assert packet.signature.value == _EXPECTED_SIGNATURE
    assert hashlib.sha256(packet.signing_bytes()).hexdigest() == _EXPECTED_SIGNING_BYTES_HASH
    assert packet.to_payload()["risk_limits"] == {
        "maximum_gross_weight": "0.8",
        "maximum_name_weight": "0.08",
        "maximum_sector_weight": "0.25",
        "maximum_common_cause_weight": "0.25",
        "maximum_correlation_cluster_weight": "0.25",
    }
    assert validate_decision_publication(publication, cycle)


def test_no_action_publication_reconstructs_without_packet_authority() -> None:
    cycle = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    result = construct_decision_publication(
        cycle,
        forecast_ids=(),
        model_fingerprint="c" * 64,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            cycle.balanced.require_house_view().cycle,
            _ISSUED_AT,
            UtcInstant.from_datetime(_ISSUED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )

    assert isinstance(result, DecisionPublicationResult)
    assert result.packet is None
    assert validate_decision_publication(result, cycle)
    assert not validate_decision_publication(
        result,
        synthetic_portfolio_cycle(
            run_id="2" * 64,
            with_authorized_adjustments=False,
        ),
    )
    assert not validate_decision_publication(
        result,
        None,  # type: ignore[arg-type]  # Exercise a hostile runtime protocol value.
    )


def test_packet_preserves_one_authorized_balanced_reduction() -> None:
    cycle = synthetic_portfolio_cycle(with_authorized_decrease=True)
    result = construct_decision_publication(
        cycle,
        forecast_ids=SYNTHETIC_FORECAST_IDS,
        model_fingerprint="c" * 64,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            cycle.balanced.require_house_view().cycle,
            _ISSUED_AT,
            UtcInstant.from_datetime(_ISSUED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )

    assert isinstance(result, DecisionPublicationResult)
    assert result.packet is not None
    reduction = next(item for item in result.packet.instructions if item.identity != SYNTHETIC_SPY)
    assert reduction.direction is PacketDirection.DECREASE
    assert reduction.authorized_weight < reduction.current_weight
    assert validate_decision_publication(result, cycle)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("record_kind",), "research_lab_decision"),
        (("authority_scope",), "growth_shadow"),
        (("run_id",), "invalid"),
        (("run_id",), 1),
        (("cycle",), {"cycle_type": "crypto_decision_window"}),
        (("evidence_cutoff",), "2026-08-21T20:00:00+00:00"),
        (("forecast_ids",), [SYNTHETIC_FORECAST_IDS[1], SYNTHETIC_FORECAST_IDS[0]]),
        (("forecast_ids",), ["invalid"]),
        (("forecast_ids",), [1]),
        (("target_band_ids",), {}),
        (("benchmark_identity",), {"symbol": "SPY"}),
        (("benchmark_state_id",), "invalid"),
        (("cash",), "NaN"),
        (("cash_currency",), "AUD"),
        (("shadow_account_ids", 0, "account_kind"), "balanced"),
        (("shadow_account_ids", 0, "account_kind"), 1),
        (("shadow_account_ids", 0, "account_id"), "invalid"),
        (("shadow_account_ids", 0, "account_id"), 1),
        (("shadow_account_ids",), {}),
        (("shadow_account_ids", 0), {}),
        (("constitution_version",), True),
        (("constitution_version",), 0),
        (("data_regime",), ""),
        (("source_fingerprint",), "invalid"),
        (("model_fingerprint",), "invalid"),
        (("material_fingerprints", "model"), "invalid"),
        (("material_fingerprints",), {}),
        (("material_fingerprints", "model"), 1),
        (("decision_record_id",), "0" * 64),
    ],
)
def test_champion_decision_contract_rejects_hostile_authority_material(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    payload = _publication().decision_record.to_payload()

    assert parse_champion_decision_record(_changed(payload, path, value)) is None


def test_champion_decision_contract_requires_exact_fields_and_mapping() -> None:
    payload = _publication().decision_record.to_payload()
    missing = deepcopy(payload)
    del missing["model_fingerprint"]
    extra = deepcopy(payload)
    extra["display_symbol"] = "SPY"

    assert parse_champion_decision_record(None) is None
    assert parse_champion_decision_record(missing) is None
    assert parse_champion_decision_record(extra) is None
    assert parse_champion_decision_record(payload) == _publication().decision_record


def test_champion_decision_record_rejects_non_hash_forecast_identity() -> None:
    with pytest.raises(ValueError, match="invalid Champion Decision Record"):
        replace(
            _publication().decision_record,
            forecast_ids=("invalid",),
            decision_record_id="",
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("record_kind",), "scenario_forecast"),
        (("authority_scope",), "research_lab"),
        (("authority_scope",), "growth_shadow"),
        (("risk_profile",), "growth"),
        (("asset_class",), "crypto_spot"),
        (("quantity_unit",), "fractional_share"),
        (("order_policy",), "market_gtc"),
        (("leverage_allowed",), True),
        (("leverage_allowed",), 0),
        (("packet_id",), "invalid"),
        (("run_id",), "invalid"),
        (("cycle",), {"cycle_type": "crypto_decision_window"}),
        (("issued_at",), "2026-08-21T20:30:00+00:00"),
        (("expires_at",), _ISSUED_AT.isoformat()),
        (("account_scope", "broker"), "other"),
        (("account_scope", "broker"), 1),
        (("account_scope", "environment"), "live"),
        (("account_scope", "scope_id"), "paper-account-number"),
        (("decision_record_id",), "invalid"),
        (("risk_limits", "maximum_gross_weight"), "0"),
        (("risk_limits", "maximum_name_weight"), "NaN"),
        (("risk_limits",), {}),
        (("instructions",), {}),
        (("instructions",), []),
        (("instructions", 0), {}),
        (("instructions", 0, "identity"), {"symbol": "AAPL"}),
        (("instructions", 0, "direction"), "short"),
        (("instructions", 0, "direction"), 1),
        (("instructions", 0, "target_band_id"), "invalid"),
        (("instructions", 0, "target_band_id"), 1),
        (("instructions", 0, "current_weight"), "NaN"),
        (("instructions", 0, "authorized_weight"), "0.99"),
        (("instructions", 0, "lower_weight"), "0.99"),
        (("instructions", 0, "reason"), "in_band"),
        (("instructions", 0, "reason"), 1),
        (("signature", "scheme"), "none"),
        (("signature", "key_id"), "invalid"),
        (("signature", "value"), "0" * 64),
        (("signature", "value"), 1),
        (("content_hash",), "0" * 64),
    ],
)
def test_decision_packet_contract_rejects_hostile_or_expanding_authority(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    packet = _publication().packet
    assert packet is not None

    assert (
        parse_decision_packet(
            _changed(packet.to_payload(), path, value),
            verifier=TEST_PACKET_CRYPTOGRAPHY,
        )
        is None
    )


def test_decision_packet_contract_requires_every_exact_field_and_no_optional_authority() -> None:
    packet = _publication().packet
    assert packet is not None
    payload = packet.to_payload()
    missing_unit = deepcopy(payload)
    del missing_unit["quantity_unit"]
    extra = deepcopy(payload)
    extra["display_symbol"] = "AAPL"

    assert parse_decision_packet(None, verifier=TEST_PACKET_CRYPTOGRAPHY) is None
    assert parse_decision_packet(missing_unit, verifier=TEST_PACKET_CRYPTOGRAPHY) is None
    assert parse_decision_packet(extra, verifier=TEST_PACKET_CRYPTOGRAPHY) is None
    assert parse_decision_packet(payload, verifier=TEST_PACKET_CRYPTOGRAPHY) == packet


class _RaisingVerifier:
    def verify(self, material: bytes, signature: PacketSignature) -> bool:
        _ = (material, signature)
        raise RuntimeError(_SYNTHETIC_VERIFIER_FAILURE)


class _NonBooleanVerifier:
    def verify(self, material: bytes, signature: PacketSignature) -> bool:
        _ = (material, signature)
        return 1  # type: ignore[return-value]  # Exercise hostile runtime protocol output.


def test_packet_verifier_failure_or_non_boolean_success_rejects_the_packet() -> None:
    packet = _publication().packet
    assert packet is not None
    payload = packet.to_payload()

    assert parse_decision_packet(payload, verifier=_RaisingVerifier()) is None
    assert parse_decision_packet(payload, verifier=_NonBooleanVerifier()) is None


def test_account_scope_rejects_real_or_live_account_authority() -> None:
    with pytest.raises(ValueError, match="invalid DecisionPacket account scope"):
        DecisionPacketAccountScope("alpaca", "live", "a" * 64)
    with pytest.raises(ValueError, match="invalid DecisionPacket account scope"):
        DecisionPacketAccountScope("alpaca", "paper", "real-account-identifier")
