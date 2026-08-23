from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast, override

import pytest

from agentic_investment_os.adapters.recorded_universe import RecordedUniverseSource
from agentic_investment_os.application.lifecycle import Advance
from agentic_investment_os.domain.identity import AssetClass, CryptoDecisionWindow, MarketSession
from agentic_investment_os.domain.lifecycle import (
    AdvanceAttempt,
    AdvanceCommand,
    AdvanceDisposition,
    AdvanceFailureReason,
    AdvanceReceipt,
    AdvanceRecovery,
    AdvanceRequest,
    AppendLifecycleRecord,
    InputRefusal,
    InputRefusalCode,
    InvalidLifecycleStateError,
    LifecycleCheckpoint,
    LifecycleCommand,
    LifecycleDecision,
    LifecycleEvent,
    LifecycleEventKind,
    LifecyclePhase,
    parse_advance_receipt,
    parse_lifecycle_checkpoint,
)
from tests._universe import (
    exact_text,
    pinned_run_identity,
    recorded_universe,
    typed_universe_policy,
    universe_snapshot,
)

SHA256_HEX_LENGTH = 64
PINNED_SEQUENCE = 2
RECEIPT_RECORDED_AT = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


class _StringSubclass(str):
    __slots__ = ()


class _IntSubclass(int):
    __slots__ = ()


class _NoneSpoof:
    @override
    def __eq__(self, other: object) -> bool:
        return other is None

    @override
    def __hash__(self) -> int:
        return hash(None)


def _request(key: str = "concurrent-request") -> AdvanceRequest:
    parsed = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key=key,
    )
    assert isinstance(parsed, AdvanceRequest)
    return parsed


def _advance(ledger: ConcurrentCompletionLedger) -> Advance:
    return Advance(
        ledger=ledger,
        configuration_version=1,
        configuration_hash="a" * SHA256_HEX_LENGTH,
        universe_source=RecordedUniverseSource(recorded_universe()),
        enabled_asset_classes=(AssetClass.US_EQUITY,),
        universe_policy=typed_universe_policy(),
        clock=FixedClock(),
    )


def _cycle() -> dict[str, object]:
    return MarketSession(date(2026, 8, 21)).to_payload()


def _reseal_receipt_with_pinned_identity(envelope: dict[str, object]) -> None:
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    cycle = pinned["cycle"]
    cycle_text = json.dumps(cycle, sort_keys=True, separators=(",", ":"))
    run_material = (
        pinned["configuration_hash"],
        pinned["configuration_version"],
        "champion",
        cycle_text,
        pinned["data_regime"],
        pinned["evidence_cutoff"],
        pinned["instrument_snapshot_hash"],
        pinned["position_snapshot_hash"],
        pinned["eligibility_policy_hash"],
    )
    pinned["run_id"] = hashlib.sha256(json.dumps(run_material).encode()).hexdigest()
    material = {key: item for key, item in envelope.items() if key != "content_hash"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    envelope["content_hash"] = hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 22, 0, tzinfo=UTC)


@dataclass
class ConcurrentCompletionLedger:
    completion_point: str
    receipt: AdvanceReceipt
    steps: int = 0

    def advance_step(
        self,
        command: LifecycleCommand,
        attempt: AdvanceAttempt,
        _recorded_at: datetime,
    ) -> LifecycleDecision:
        assert isinstance(command, AdvanceCommand)
        if self.completion_point == "start" and self.steps == 0:
            return self.receipt
        if self.completion_point == "reconcile_failure" and self.steps == 1:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "reconcile" and self.steps == 1:
            return self.receipt
        if self.completion_point == "pin_failure" and self.steps == PINNED_SEQUENCE:
            return AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)
        if self.completion_point == "pin_observed" and self.steps == PINNED_SEQUENCE:
            return self.receipt

        event_kind, phase = (
            (LifecycleEventKind.ADVANCE_REQUESTED, None)
            if self.steps == 0
            else (
                LifecycleEventKind.PHASE_COMPLETED,
                LifecyclePhase.RECONCILE_PRIOR_STATE,
            )
        )
        next_attempt = (
            attempt
            if self.completion_point == "incomplete_reconcile" and self.steps == 1
            else AdvanceAttempt(AdvanceRecovery.FRESH, self.steps)
        )
        decision = AppendLifecycleRecord(
            LifecycleEvent(
                command.request.stream_id,
                self.steps,
                command.request,
                command.pinned_run_identity,
                event_kind,
                None if phase is None else LifecycleCheckpoint.equity(phase),
            ),
            next_attempt,
        )
        self.steps += 1
        return decision


def test_advance_request_validates_the_complete_boundary() -> None:
    request = AdvanceRequest.parse(
        session="2026-08-21",
        mode="champion",
        idempotency_key="session-2026-08-21",
    )

    assert isinstance(request, AdvanceRequest)
    assert request.session.isoformat() == "2026-08-21"
    assert request.mode.value == "champion"
    assert request.idempotency_key.value == "session-2026-08-21"
    assert request.stream_id == "b24b0e025ab67f2594db49e7f5e1c7cfe8170645fe5b9defe068e8c715d7a9e5"
    identity = pinned_run_identity(
        request,
        configuration_hash="a" * SHA256_HEX_LENGTH,
    )
    assert identity.run_id == "303ab24fb94d601573699667036c493b05804e2af80943071ae1afe120c5ad05"

    invalid_cases = (
        (
            {"session": "21-08-2026", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "2026-08-21", "mode": "research-lab", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_MODE,
        ),
        (
            {"session": "2026-08-21", "mode": "champion", "idempotency_key": "contains space"},
            InputRefusalCode.INVALID_IDEMPOTENCY_KEY,
        ),
        (
            {"session": None, "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
        (
            {"session": "20260821", "mode": "champion", "idempotency_key": "valid-key"},
            InputRefusalCode.INVALID_SESSION,
        ),
    )

    for values, expected_code in invalid_cases:
        refusal = AdvanceRequest.parse(**values)
        assert isinstance(refusal, InputRefusal)
        assert refusal.code is expected_code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("envelope_schema_version", True),
        ("envelope_schema_version", 1.0),
        ("payload_schema_version", True),
        ("payload_schema_version", 1.0),
        ("record_kind", 3),
        ("payload_discriminator", 3),
        ("payload_discriminator", "crypto_decision_window_phase"),
        ("authority_scope", 3),
        ("content_hash", 3),
        ("content_hash", "f" * SHA256_HEX_LENGTH),
    ],
)
def test_lifecycle_checkpoint_parser_rejects_hostile_or_changed_envelopes(
    field: str,
    value: object,
) -> None:
    checkpoint = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE)
    payload = checkpoint.to_payload()
    payload[field] = value

    assert parse_lifecycle_checkpoint(payload) is None


@pytest.mark.parametrize(
    "case",
    [
        "non_mapping",
        "non_string_key",
        "wrong_envelope_fields",
        "non_mapping_payload",
        "wrong_payload_fields",
        "non_string_phase",
    ],
)
def test_lifecycle_checkpoint_parser_rejects_hostile_shapes(case: str) -> None:
    value: object = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE).to_payload()
    if case == "non_mapping":
        value = []
    else:
        assert isinstance(value, dict)
        if case == "non_string_key":
            value[1] = "hostile"
        elif case == "wrong_envelope_fields":
            value["extra"] = "hostile"
        elif case == "non_mapping_payload":
            value["payload"] = []
        else:
            phase_payload = value["payload"]
            assert isinstance(phase_payload, dict)
            if case == "wrong_payload_fields":
                phase_payload["extra"] = "hostile"
            else:
                assert case == "non_string_phase"
                phase_payload["phase"] = True

    assert parse_lifecycle_checkpoint(value) is None


def test_lifecycle_checkpoint_rejects_an_untyped_phase_and_unknown_phase_payload() -> None:
    # Deliberately violate the static enum contract to exercise constructor validation.
    with pytest.raises(ValueError, match="invalid lifecycle checkpoint phase"):
        LifecycleCheckpoint(cast("LifecyclePhase", "SnapshotUniverse"))

    payload = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE).to_payload()
    phase_payload = payload["payload"]
    assert isinstance(phase_payload, dict)
    phase_payload["phase"] = "UnknownPhase"

    assert parse_lifecycle_checkpoint(payload) is None


def test_lifecycle_event_uses_a_hashed_common_envelope() -> None:
    request = _request()
    identity = pinned_run_identity(request)
    snapshot = universe_snapshot(identity)
    checkpoint = LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE)
    assert parse_lifecycle_checkpoint(checkpoint.to_payload()) == checkpoint
    event = LifecycleEvent(
        request.stream_id,
        3,
        request,
        identity,
        LifecycleEventKind.UNIVERSE_SNAPSHOTTED,
        checkpoint,
        snapshot,
    )

    envelope = event.to_envelope(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))

    assert envelope["payload_discriminator"] == "equity_market_session_lifecycle_event"
    assert envelope["authority_scope"] == "investment_operating_system"
    assert envelope["available_at"] == envelope["event_at"]
    assert envelope["content_hash"] != identity.run_id
    event_payload = envelope["payload"]
    assert isinstance(event_payload, dict)
    assert event_payload["completed_phase"] == checkpoint.to_payload()
    assert event_payload["universe_snapshot_id"] == snapshot.snapshot_id
    with pytest.raises(ValueError, match="timezone-aware"):
        event.to_envelope(datetime(2026, 8, 21, 22, 0))  # noqa: DTZ001


def test_advance_receipt_rejects_incomplete_success_and_failure_shapes() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            LifecycleCheckpoint.equity(LifecyclePhase.PIN_RUN_INPUTS),
            identity,
            None,
        )
    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt(
            AdvanceDisposition.ADVANCED,
            None,
            identity,
            None,
            AdvanceRecovery.FRESH,
            snapshot.snapshot_id,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt(
            AdvanceDisposition.FAILED_CLOSED,
            None,
            None,
            AdvanceFailureReason.INVALID_DURABLE_STATE,
            AdvanceRecovery.RESUMED,
        )
    crypto_cycle = CryptoDecisionWindow(
        datetime(2026, 8, 22, tzinfo=UTC),
        datetime(2026, 8, 23, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(AdvanceFailureReason.UNSUPPORTED_CYCLE)
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.INVALID_SESSION,
            cycle=crypto_cycle,
        )
    with pytest.raises(ValueError, match="failed receipt requires one bounded reason"):
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.UNSUPPORTED_CYCLE,
            cycle=MarketSession(date(2026, 8, 21)),
        )

    assert (
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).recovery
        is AdvanceRecovery.FRESH
    )


def test_advance_receipts_round_trip_through_one_versioned_public_envelope() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    crypto_cycle = CryptoDecisionWindow(
        datetime(2026, 8, 22, tzinfo=UTC),
        datetime(2026, 8, 22, tzinfo=UTC) + timedelta(hours=1),
    )
    receipts = (
        AdvanceReceipt.advanced(identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT),
        AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_IDEMPOTENCY_KEY),
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.STALE_UNIVERSE_INPUT,
            cycle=MarketSession(date(2026, 8, 21)),
        ),
        AdvanceReceipt.failed_closed(
            AdvanceFailureReason.UNSUPPORTED_CYCLE,
            cycle=crypto_cycle,
        ),
    )

    envelopes = tuple(receipt.to_payload() for receipt in receipts)

    assert tuple(parse_advance_receipt(envelope) for envelope in envelopes) == receipts
    assert all(envelope["envelope_schema_version"] == 1 for envelope in envelopes)
    assert all(envelope["record_kind"] == "lifecycle_receipt" for envelope in envelopes)
    assert all(
        envelope["authority_scope"] == "investment_operating_system" for envelope in envelopes
    )
    assert envelopes[0]["cycle"] == identity.cycle.to_payload()
    assert envelopes[0]["relevant_at"] == RECEIPT_RECORDED_AT.isoformat()
    assert envelopes[0]["available_at"] == RECEIPT_RECORDED_AT.isoformat()
    assert envelopes[0]["data_regime"] == identity.data_regime
    assert envelopes[2]["cycle"] == MarketSession(date(2026, 8, 21)).to_payload()
    assert envelopes[3]["cycle"] == crypto_cycle.to_payload()
    assert envelopes[3]["payload_discriminator"] == "unsupported_cycle_advance_receipt"
    assert receipts[3].cycle == crypto_cycle


def test_advance_receipt_parser_rejects_crypto_as_success_even_when_resealed() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = AdvanceReceipt.advanced(
        identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
    ).to_payload()
    crypto_cycle = CryptoDecisionWindow(
        datetime(2026, 8, 22, tzinfo=UTC),
        datetime(2026, 8, 23, tzinfo=UTC),
    ).to_payload()
    envelope["cycle"] = crypto_cycle
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["cycle"] = crypto_cycle
    _reseal_receipt_with_pinned_identity(envelope)

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_round_trips_the_universe_owned_data_regime_grammar() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = AdvanceReceipt.advanced(
        identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
    ).to_payload()
    envelope["data_regime"] = "alpaca:iex"
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["data_regime"] = "alpaca:iex"
    _reseal_receipt_with_pinned_identity(envelope)

    parsed = parse_advance_receipt(envelope)

    assert parsed is not None
    assert parsed.pinned_run_identity is not None
    assert parsed.pinned_run_identity.data_regime == "alpaca:iex"


def test_advance_receipt_rejects_a_completion_time_before_its_evidence_cutoff() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)

    with pytest.raises(ValueError, match="advanced receipt requires completed recovery facts"):
        AdvanceReceipt.advanced(
            identity,
            snapshot,
            AdvanceRecovery.FRESH,
            identity.evidence_cutoff - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("envelope_schema_version",), True),
        (("payload_discriminator",), "unknown_receipt"),
        (("relevant_at",), "2026-08-21T22:00:00"),
        (("content_hash",), "f" * SHA256_HEX_LENGTH),
        (("material_fingerprints", "instrument_snapshot"), True),
        (("payload", "disposition"), "unknown"),
        (("payload", "universe_snapshot_id"), "not-a-hash"),
    ],
)
def test_advance_receipt_parser_rejects_hostile_or_changed_envelopes(
    path: tuple[str, ...],
    value: object,
) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    target = envelope
    for field in path[:-1]:
        nested = target[field]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    assert parse_advance_receipt(envelope) is None


@pytest.mark.parametrize(
    "case",
    [
        "non_mapping",
        "non_string_root_key",
        "wrong_root_fields",
        "missing_root_field",
        "non_mapping_payload",
        "wrong_payload_fields",
        "missing_payload_field",
        "non_mapping_fingerprints",
        "non_string_fingerprint_key",
        "partial_fingerprints",
        "invalid_cycle",
        "invalid_checkpoint",
        "invalid_pinned_identity_fields",
        "invalid_pinned_identity_fact",
        "invalid_pinned_identity_type",
        "invalid_pinned_cycle",
        "non_string_disposition",
        "invalid_failure_reason",
        "non_string_failure_reason",
        "invalid_recovery",
        "non_string_recovery",
        "non_string_relevant_at",
        "equivalent_relevant_at_subclass",
        "malformed_available_at",
        "noncanonical_available_at",
        "equivalent_available_at_subclass",
        "non_string_data_regime",
        "invalid_data_regime",
        "equivalent_data_regime_subclass",
        "inconsistent_receipt_shape",
        "null_spoofed_failure_reason",
    ],
)
def test_advance_receipt_parser_rejects_hostile_shapes(  # noqa: PLR0912, PLR0915 - mutate every hostile envelope seam explicitly.
    case: str,
) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    value: object = deepcopy(
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    if case == "non_mapping":
        value = []
    else:
        assert isinstance(value, dict)
        if case == "non_string_root_key":
            value[1] = "hostile"
        elif case == "wrong_root_fields":
            value["extra"] = "hostile"
        elif case == "missing_root_field":
            value.pop("content_hash")
        elif case == "non_mapping_payload":
            value["payload"] = []
        elif case == "non_mapping_fingerprints":
            value["material_fingerprints"] = []
        elif case == "non_string_relevant_at":
            value["relevant_at"] = True
        elif case == "equivalent_relevant_at_subclass":
            value["relevant_at"] = _StringSubclass(exact_text(value["relevant_at"]))
        elif case == "malformed_available_at":
            value["available_at"] = "not-a-time"
        elif case == "noncanonical_available_at":
            value["available_at"] = exact_text(value["available_at"]).replace("+00:00", "Z")
        elif case == "equivalent_available_at_subclass":
            value["available_at"] = _StringSubclass(exact_text(value["available_at"]))
        elif case == "non_string_data_regime":
            value["data_regime"] = True
        elif case == "invalid_data_regime":
            value["data_regime"] = "Invalid Regime"
        elif case == "equivalent_data_regime_subclass":
            value["data_regime"] = _StringSubclass(exact_text(value["data_regime"]))
        elif case == "invalid_cycle":
            value["cycle"] = {}
        else:
            receipt_payload = value["payload"]
            fingerprints = value["material_fingerprints"]
            assert isinstance(receipt_payload, dict)
            assert isinstance(fingerprints, dict)
            if case == "wrong_payload_fields":
                receipt_payload["extra"] = "hostile"
            elif case == "missing_payload_field":
                receipt_payload.pop("disposition")
            elif case == "non_string_fingerprint_key":
                fingerprints[1] = "a" * SHA256_HEX_LENGTH
            elif case == "partial_fingerprints":
                fingerprints.pop("configuration")
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["data_regime"] = True
            elif case == "invalid_checkpoint":
                receipt_payload["completed_checkpoint"] = {}
            elif case == "invalid_pinned_identity_fields":
                receipt_payload["pinned_run_identity"] = {}
            elif case == "invalid_pinned_identity_fact":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["run_id"] = "f" * SHA256_HEX_LENGTH
            elif case == "invalid_pinned_identity_type":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["configuration_version"] = True
            elif case == "invalid_pinned_cycle":
                pinned = receipt_payload["pinned_run_identity"]
                assert isinstance(pinned, dict)
                pinned["cycle"] = {}
            elif case == "non_string_disposition":
                receipt_payload["disposition"] = True
            elif case == "invalid_failure_reason":
                receipt_payload["failure_reason"] = "unknown"
            elif case == "non_string_failure_reason":
                receipt_payload["failure_reason"] = True
            elif case == "invalid_recovery":
                receipt_payload["recovery"] = "unknown"
            elif case == "non_string_recovery":
                receipt_payload["recovery"] = True
            elif case == "null_spoofed_failure_reason":
                receipt_payload["failure_reason"] = _NoneSpoof()
            else:
                assert case == "inconsistent_receipt_shape"
                receipt_payload["failure_reason"] = AdvanceFailureReason.INVALID_SESSION.value

    assert parse_advance_receipt(value) is None


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "configuration_hash",
        "data_regime",
        "evidence_cutoff",
        "instrument_snapshot_hash",
        "position_snapshot_hash",
        "eligibility_policy_hash",
    ],
)
def test_advance_receipt_parser_rejects_equivalent_pinned_text_subclasses(field: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned[field] = _StringSubclass(exact_text(pinned[field]))

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_parser_rejects_an_equivalent_configuration_version_subclass() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    pinned["configuration_version"] = _IntSubclass(1)

    assert parse_advance_receipt(envelope) is None


def test_advance_receipt_parser_rejects_a_self_consistent_naive_cutoff() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    envelope = deepcopy(
        AdvanceReceipt.advanced(
            identity, snapshot, AdvanceRecovery.FRESH, RECEIPT_RECORDED_AT
        ).to_payload()
    )
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    pinned = payload["pinned_run_identity"]
    assert isinstance(pinned, dict)
    naive_cutoff = identity.evidence_cutoff.replace(tzinfo=None).isoformat()
    pinned["evidence_cutoff"] = naive_cutoff
    envelope["relevant_at"] = naive_cutoff
    envelope["available_at"] = naive_cutoff
    _reseal_receipt_with_pinned_identity(envelope)

    assert parse_advance_receipt(envelope) is None


@pytest.mark.parametrize("completion_point", ["start", "reconcile"])
def test_advance_returns_a_concurrent_checkpoint_receipt(completion_point: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    receipt = AdvanceReceipt(
        AdvanceDisposition.ADVANCED,
        completed_phase=LifecycleCheckpoint.equity(LifecyclePhase.SNAPSHOT_UNIVERSE),
        pinned_run_identity=identity,
        failure_reason=None,
        recovery=AdvanceRecovery.PREVIOUSLY_COMPLETED,
        universe_snapshot_id=snapshot.snapshot_id,
        recorded_at=RECEIPT_RECORDED_AT,
    )
    capability = _advance(ConcurrentCompletionLedger(completion_point, receipt))

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed.disposition is receipt.disposition
    assert observed.completed_phase is receipt.completed_phase
    assert observed.pinned_run_identity is receipt.pinned_run_identity
    assert observed.recovery is AdvanceRecovery.PREVIOUSLY_COMPLETED


@pytest.mark.parametrize("failure_point", ["reconcile_failure", "pin_failure"])
def test_advance_returns_a_durable_checkpoint_failure(failure_point: str) -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            failure_point,
            AdvanceReceipt.advanced(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.failed_closed(AdvanceFailureReason.INVALID_DURABLE_STATE)


def test_advance_reports_a_checkpoint_completed_during_pinning() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            "pin_observed",
            AdvanceReceipt.advanced(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    observed = capability(
        cycle=_cycle(),
        mode="champion",
        idempotency_key="concurrent-request",
    )

    assert observed == AdvanceReceipt.advanced(
        identity,
        snapshot,
        AdvanceRecovery.PREVIOUSLY_COMPLETED,
        RECEIPT_RECORDED_AT,
    )


def test_advance_rejects_an_incomplete_checkpoint_result() -> None:
    identity = pinned_run_identity(_request())
    snapshot = universe_snapshot(identity)
    capability = _advance(
        ConcurrentCompletionLedger(
            "incomplete_reconcile",
            AdvanceReceipt.advanced(
                identity,
                snapshot,
                AdvanceRecovery.PREVIOUSLY_COMPLETED,
                RECEIPT_RECORDED_AT,
            ),
        )
    )

    with pytest.raises(
        InvalidLifecycleStateError,
        match="lifecycle ledger returned an incomplete checkpoint result",
    ):
        capability(
            cycle=_cycle(),
            mode="champion",
            idempotency_key="concurrent-request",
        )
