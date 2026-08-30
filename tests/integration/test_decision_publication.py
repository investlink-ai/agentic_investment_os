from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.adapters.decision_signing import HmacSha256DecisionPacketSigner
from agentic_investment_os.adapters.decision_window import PreOpenDecisionPacketWindowSource
from agentic_investment_os.adapters.sqlite_decision import SQLiteDecisionPublicationLedger
from agentic_investment_os.adapters.sqlite_lifecycle import (
    PreparedRuntimeDatabase,
    SQLiteLifecycleLedger,
    prepare_runtime_database,
)
from agentic_investment_os.adapters.sqlite_portfolio import SQLitePortfolioLedger
from agentic_investment_os.domain.identity import MarketSession
from agentic_investment_os.domain.lifecycle import (
    DecisionCheckpoint,
    DecisionCheckpointReference,
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.portfolio.publication import (
    DecisionPacketAccountScope,
    DecisionPacketValidityWindow,
    DecisionPublicationRefusalReason,
    DecisionPublicationResult,
    PacketSignature,
    construct_decision_publication,
)
from tests._decision import TEST_DECISION_ACCOUNT_SCOPE, TEST_PACKET_CRYPTOGRAPHY
from tests._portfolio import (
    SYNTHETIC_AAPL,
    SYNTHETIC_SPY,
)
from tests._portfolio import (
    synthetic_portfolio_cycle as _synthetic_portfolio_cycle,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentic_investment_os.portfolio.shadows import PortfolioCycleResult

_SESSION = MarketSession(date(2026, 8, 24))
_PORTFOLIO_RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 24, 13, 10, tzinfo=UTC))
_PUBLISHED_AT = UtcInstant.from_datetime(datetime(2026, 8, 24, 13, 15, tzinfo=UTC))


def synthetic_portfolio_cycle(
    *,
    run_id: str = "1" * 64,
    with_authorized_adjustments: bool = True,
    with_authorized_decrease: bool = False,
    model_fingerprint: str = "6" * 64,
) -> PortfolioCycleResult:
    return _synthetic_portfolio_cycle(
        run_id=run_id,
        with_authorized_adjustments=with_authorized_adjustments,
        with_authorized_decrease=with_authorized_decrease,
        model_fingerprint=model_fingerprint,
        cycle=_SESSION,
    )


def _ledgers(
    tmp_path: Path,
    cycle_result: PortfolioCycleResult,
    *,
    portfolio_recorded_at: UtcInstant = _PORTFOLIO_RECORDED_AT,
) -> tuple[Path, SQLitePortfolioLedger, SQLiteDecisionPublicationLedger]:
    prepared = prepare_runtime_database(tmp_path / "runtime")
    assert isinstance(prepared, PreparedRuntimeDatabase)
    SQLiteLifecycleLedger(prepared.path)
    portfolio = SQLitePortfolioLedger(prepared.path)
    portfolio.record_cycle(
        cycle_result.balanced.require_house_view().run_id,
        cycle_result,
        portfolio_recorded_at,
    )
    decisions = SQLiteDecisionPublicationLedger(
        prepared.path,
        TEST_PACKET_CRYPTOGRAPHY,
        portfolio,
        SYNTHETIC_SPY,
        PreOpenDecisionPacketWindowSource(),
    )
    return prepared.path, portfolio, decisions


def _publication(
    cycle_result: PortfolioCycleResult,
    *,
    published_at: UtcInstant = _PUBLISHED_AT,
    account_scope: DecisionPacketAccountScope = TEST_DECISION_ACCOUNT_SCOPE,
    packet_expected: bool = True,
) -> DecisionPublicationResult:
    house_view = cycle_result.balanced.require_house_view()
    window = PreOpenDecisionPacketWindowSource().window_for(house_view.cycle, published_at)
    validity_window = window if isinstance(window, DecisionPacketValidityWindow) else None
    result = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=account_scope,
        validity_window=validity_window,
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(result, DecisionPublicationResult)
    assert (result.packet is not None) is packet_expected
    return result


def _reference(
    cycle_result: PortfolioCycleResult,
    checkpoint: DecisionCheckpoint,
) -> DecisionCheckpointReference:
    house_view = cycle_result.balanced.require_house_view()
    return DecisionCheckpointReference(house_view.run_id, house_view.cycle, checkpoint)


def _checkpoint(value: DecisionCheckpoint | DecisionPublicationRefusalReason) -> DecisionCheckpoint:
    assert isinstance(value, DecisionCheckpoint)
    return value


class _AlwaysAllowingDecisionWindowSource:
    """Simulate a timing adapter that violates the portfolio-owned port contract."""

    def window_for(
        self,
        cycle: MarketSession,
        recorded_at: UtcInstant,
    ) -> DecisionPacketValidityWindow:
        return DecisionPacketValidityWindow(
            cycle,
            recorded_at,
            UtcInstant(recorded_at.value + timedelta(minutes=5)),
        )

    def allows_publication(
        self,
        window: DecisionPacketValidityWindow,
        recorded_at: UtcInstant,
    ) -> bool:
        _ = (window, recorded_at)
        return True


def test_publication_is_atomic_and_replays_exactly_after_reopen(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER interrupt_decision_publication
            BEFORE INSERT ON decision_publications
            BEGIN SELECT RAISE(ABORT, 'injected publication interruption'); END
            """
        )

    with pytest.raises(
        LifecyclePersistenceError,
        match="SQLite decision publication checkpoint failed",
    ):
        decisions.record_publication(run_id, publication, _PUBLISHED_AT)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)
        connection.execute("DROP TRIGGER interrupt_decision_publication")

    checkpoint = _checkpoint(decisions.record_publication(run_id, publication, _PUBLISHED_AT))
    reopened = SQLiteDecisionPublicationLedger.open_existing(
        database,
        verifier=TEST_PACKET_CRYPTOGRAPHY,
        portfolio_ledger=SQLitePortfolioLedger.open_existing(database),
        benchmark_identity=SYNTHETIC_SPY,
        decision_window_source=PreOpenDecisionPacketWindowSource(),
    )

    assert reopened.replay_publication(run_id, publication.decision_record.cycle) == checkpoint
    reopened.validate_reference(_reference(cycle_result, checkpoint))
    reopened.validate_history((_reference(cycle_result, checkpoint),))
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT decision_record_id, packet_id, cycle_identity, decision_record_json, "
            "packet_json, no_action_reason "
            "FROM decision_publications"
        ).fetchone()
    assert row is not None
    assert row[0] == checkpoint.decision_record_id
    assert row[1] == checkpoint.packet_id
    assert row[2] == json.dumps(
        publication.decision_record.cycle.to_payload(), sort_keys=True, separators=(",", ":")
    )
    assert row[3] == json.dumps(
        publication.decision_record.to_payload(), sort_keys=True, separators=(",", ":")
    )
    assert publication.packet is not None
    assert row[4] == json.dumps(
        publication.packet.to_payload(), sort_keys=True, separators=(",", ":")
    )
    assert row[5] is None


def test_official_packet_window_is_revalidated_at_the_durable_boundary(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    regular_open = UtcInstant.from_datetime(datetime(2026, 8, 24, 13, 30, tzinfo=UTC))

    assert (
        decisions.record_publication(
            publication.decision_record.run_id,
            publication,
            regular_open,
        )
        is DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (
            datetime(2026, 8, 24, 13, 14, 59, 999999, tzinfo=UTC),
            datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 24, 13, 15, tzinfo=UTC),
            datetime(2026, 8, 24, 14, 0, 0, 1, tzinfo=UTC),
        ),
    ],
)
def test_coherently_signed_nonofficial_packet_window_never_becomes_visible(
    tmp_path: Path,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    publication = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            UtcInstant.from_datetime(issued_at),
            UtcInstant.from_datetime(expires_at),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(publication, DecisionPublicationResult)
    assert publication.packet is not None

    assert (
        decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT)
        is DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_publication_at_the_exact_evidence_cutoff_is_valid_history(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    publication = _publication(cycle_result, packet_expected=False)
    cutoff = publication.decision_record.evidence_cutoff
    _, _, decisions = _ledgers(
        tmp_path,
        cycle_result,
        portfolio_recorded_at=cutoff,
    )

    checkpoint = _checkpoint(
        decisions.record_publication(publication.decision_record.run_id, publication, cutoff)
    )

    assert checkpoint.recorded_at == cutoff
    assert (
        decisions.replay_publication(
            publication.decision_record.run_id,
            publication.decision_record.cycle,
        )
        == checkpoint
    )


def test_concurrent_changed_publication_times_return_one_winning_packet(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    later = UtcInstant.from_datetime(_PUBLISHED_AT.value + timedelta(seconds=1))
    candidates = (_publication(cycle_result), _publication(cycle_result, published_at=later))

    with ThreadPoolExecutor(max_workers=2) as executor:
        checkpoints = tuple(
            executor.map(
                lambda item: decisions.record_publication(run_id, item[0], item[1]),
                zip(candidates, (_PUBLISHED_AT, later), strict=True),
            )
        )

    assert checkpoints[0] == checkpoints[1]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (1,)


def test_no_action_decision_redelivery_replays_without_a_packet(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    publication = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            _PUBLISHED_AT,
            UtcInstant.from_datetime(_PUBLISHED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(publication, DecisionPublicationResult)
    assert publication.packet is None

    first = _checkpoint(decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT))
    replay = decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT)

    assert replay == first
    assert first.packet_id is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT packet_id, packet_json, no_action_reason FROM decision_publications"
        ).fetchone() == (None, None, "no_authorized_adjustments")


def test_changed_decision_or_authorization_material_conflicts_without_replacement(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    original = _publication(cycle_result)
    checkpoint = _checkpoint(decisions.record_publication(run_id, original, _PUBLISHED_AT))
    changed_model = _publication(synthetic_portfolio_cycle(model_fingerprint="d" * 64))
    changed_scope = _publication(
        cycle_result,
        account_scope=DecisionPacketAccountScope("alpaca", "paper", "e" * 64),
    )

    for changed in (changed_model, changed_scope):
        with pytest.raises(
            InvalidLifecycleStateError, match="durable decision publication is invalid"
        ):
            decisions.record_publication(run_id, changed, _PUBLISHED_AT)

    assert decisions.replay_publication(run_id, original.decision_record.cycle) == checkpoint
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (1,)


def test_redelivery_conflicts_on_each_decision_envelope_field(tmp_path: Path) -> None:
    no_action_cycle = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    no_action_root = tmp_path / "no-action"
    no_action_root.mkdir()
    _, _, no_action_decisions = _ledgers(no_action_root, no_action_cycle)
    no_action = _publication(no_action_cycle, packet_expected=False)
    run_id = no_action.decision_record.run_id
    _checkpoint(no_action_decisions.record_publication(run_id, no_action, _PUBLISHED_AT))
    changed_reason = _publication(no_action_cycle, packet_expected=False)
    object.__setattr__(changed_reason, "no_action_reason", None)

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        no_action_decisions.record_publication(run_id, changed_reason, _PUBLISHED_AT)

    cycle_result = synthetic_portfolio_cycle()
    decision_root = tmp_path / "decision"
    decision_root.mkdir()
    _, _, decisions = _ledgers(decision_root, cycle_result)
    original = _publication(cycle_result)
    run_id = original.decision_record.run_id
    _checkpoint(decisions.record_publication(run_id, original, _PUBLISHED_AT))
    changed_record = _publication(synthetic_portfolio_cycle(model_fingerprint="d" * 64))
    hostile_candidate = _publication(cycle_result)
    object.__setattr__(hostile_candidate, "decision_record", changed_record.decision_record)

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication(run_id, hostile_candidate, _PUBLISHED_AT)

    changed_liquidity = _publication(cycle_result)
    assert changed_liquidity.packet is not None
    object.__setattr__(
        changed_liquidity.packet,
        "maximum_fraction_of_median_dollar_volume",
        Decimal("0.02"),
    )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication(run_id, changed_liquidity, _PUBLISHED_AT)


def test_invalid_signature_material_never_becomes_visible(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    assert publication.packet is not None
    object.__setattr__(
        publication.packet,
        "signature",
        PacketSignature("hmac-sha256-v1", "f" * 64, "0" * 64),
    )

    assert (
        decisions.record_publication(
            cycle_result.balanced.require_house_view().run_id,
            publication,
            _PUBLISHED_AT,
        )
        is DecisionPublicationRefusalReason.INVALID_PORTFOLIO
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_coherently_hashed_wrong_key_signature_never_becomes_visible(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, portfolio, _ = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    decisions = SQLiteDecisionPublicationLedger(
        database,
        HmacSha256DecisionPacketSigner(b"wrong-synthetic-signing-key"),
        portfolio,
        SYNTHETIC_SPY,
        PreOpenDecisionPacketWindowSource(),
    )

    assert (
        decisions.record_publication(
            cycle_result.balanced.require_house_view().run_id,
            publication,
            _PUBLISHED_AT,
        )
        is DecisionPublicationRefusalReason.SIGNING_FAILED
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_wrong_official_benchmark_never_becomes_visible(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    publication = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_AAPL,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            _PUBLISHED_AT,
            UtcInstant.from_datetime(_PUBLISHED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(publication, DecisionPublicationResult)
    assert publication.packet is None

    assert (
        decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT)
        is DecisionPublicationRefusalReason.INVALID_PORTFOLIO
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_resealed_no_action_benchmark_substitution_fails_reopen_validation(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    original = _publication(cycle_result, packet_expected=False)
    checkpoint = _checkpoint(
        decisions.record_publication(house_view.run_id, original, _PUBLISHED_AT)
    )
    substituted = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_AAPL,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            _PUBLISHED_AT,
            UtcInstant.from_datetime(_PUBLISHED_AT.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(substituted, DecisionPublicationResult)
    assert substituted.packet is None
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(
            "UPDATE decision_publications SET decision_record_id = ?, decision_record_json = ?",
            (
                substituted.decision_record.decision_record_id,
                json.dumps(
                    substituted.decision_record.to_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(house_view.run_id, house_view.cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(_reference(cycle_result, checkpoint))


def test_resealed_off_window_packet_fails_reopen_validation(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    original = _publication(cycle_result)
    checkpoint = _checkpoint(
        decisions.record_publication(house_view.run_id, original, _PUBLISHED_AT)
    )
    substituted = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=TEST_DECISION_ACCOUNT_SCOPE,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            UtcInstant.from_datetime(datetime(2026, 8, 24, 13, 14, 59, 999999, tzinfo=UTC)),
            UtcInstant.from_datetime(datetime(2026, 8, 24, 14, 0, tzinfo=UTC)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(substituted, DecisionPublicationResult)
    assert substituted.packet is not None
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(
            "UPDATE decision_publications "
            "SET packet_id = ?, packet_expires_at = ?, packet_json = ?",
            (
                substituted.packet.packet_id,
                substituted.packet.expires_at.isoformat(),
                json.dumps(
                    substituted.packet.to_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(house_view.run_id, house_view.cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(_reference(cycle_result, checkpoint))


def test_corrupt_authoritative_packet_fails_reopen_validation(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    checkpoint = _checkpoint(decisions.record_publication(run_id, publication, _PUBLISHED_AT))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute("UPDATE decision_publications SET packet_json = '{}' ")

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(_reference(cycle_result, checkpoint))


def test_corrupt_authoritative_decision_fails_reopen_validation(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    decisions.record_publication(run_id, publication, _PUBLISHED_AT)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute("UPDATE decision_publications SET decision_record_json = '{}'")

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(run_id, publication.decision_record.cycle)


def test_reopen_rejects_publication_recorded_before_its_signed_issue_time(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    decisions.record_publication(run_id, publication, _PUBLISHED_AT)
    before_issue = UtcInstant.from_datetime(_PUBLISHED_AT.value - timedelta(microseconds=1))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(
            "UPDATE decision_publications SET recorded_at = ?",
            (before_issue.isoformat(),),
        )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(run_id, publication.decision_record.cycle)


def test_checkpoint_rejects_visibility_before_issue_if_timing_adapter_misbehaves(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    before_issue = UtcInstant.from_datetime(_PUBLISHED_AT.value - timedelta(microseconds=1))
    invalid = replace(
        decisions,
        decision_window_source=_AlwaysAllowingDecisionWindowSource(),
    )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        invalid.record_publication(
            publication.decision_record.run_id,
            publication,
            before_issue,
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_reopen_rejects_no_action_publication_recorded_before_its_evidence_cutoff(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result, packet_expected=False)
    run_id = cycle_result.balanced.require_house_view().run_id
    checkpoint = _checkpoint(decisions.record_publication(run_id, publication, _PUBLISHED_AT))
    before_cutoff = UtcInstant.from_datetime(
        publication.decision_record.evidence_cutoff.value - timedelta(microseconds=1)
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(
            "UPDATE decision_publications SET recorded_at = ?",
            (before_cutoff.isoformat(),),
        )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(run_id, publication.decision_record.cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(_reference(cycle_result, checkpoint))


def test_publication_before_the_portfolio_checkpoint_never_becomes_valid_history(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle(with_authorized_adjustments=False)
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result, packet_expected=False)
    run_id = cycle_result.balanced.require_house_view().run_id
    before_portfolio = UtcInstant.from_datetime(
        _PORTFOLIO_RECORDED_AT.value - timedelta(microseconds=1)
    )

    assert (
        decisions.record_publication(run_id, publication, before_portfolio)
        is DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)

    checkpoint = _checkpoint(decisions.record_publication(run_id, publication, _PUBLISHED_AT))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(
            "UPDATE decision_publications SET recorded_at = ?",
            (before_portfolio.isoformat(),),
        )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(run_id, publication.decision_record.cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(_reference(cycle_result, checkpoint))


def test_expired_new_packet_and_mismatched_cycle_fail_closed(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    packet = publication.packet
    assert packet is not None
    run_id = cycle_result.balanced.require_house_view().run_id
    expired_at = packet.expires_at

    assert (
        decisions.record_publication(run_id, publication, expired_at)
        is DecisionPublicationRefusalReason.INVALID_VALIDITY_WINDOW
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)

    decisions.record_publication(run_id, publication, _PUBLISHED_AT)
    wrong_cycle = MarketSession(date(2026, 8, 22))
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(run_id, wrong_cycle)


def test_invalid_calls_and_missing_lifecycle_references_fail_closed(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    _, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    cycle = publication.decision_record.cycle
    missing_checkpoint = DecisionCheckpoint(
        "d" * 64,
        "e" * 64,
        UtcInstant.from_datetime(_PUBLISHED_AT.value + timedelta(minutes=5)),
        _PUBLISHED_AT,
        None,
    )
    missing_reference = DecisionCheckpointReference("f" * 64, cycle, missing_checkpoint)

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication("invalid", publication, _PUBLISHED_AT)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication("invalid", cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_history((missing_reference,))
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.validate_reference(missing_reference)


def test_replay_reports_no_publication_without_creating_state(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()

    assert decisions.replay_publication(house_view.run_id, house_view.cycle) is None
    decisions.validate_history(())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_missing_portfolio_history_is_a_typed_publication_refusal(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    missing_portfolio = SQLitePortfolioLedger(tmp_path / "missing-portfolio.sqlite3")
    invalid = replace(decisions, portfolio_ledger=missing_portfolio)

    assert (
        invalid.record_publication(
            cycle_result.balanced.require_house_view().run_id,
            publication,
            _PUBLISHED_AT,
        )
        is DecisionPublicationRefusalReason.INVALID_PORTFOLIO
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_unreadable_publication_storage_fails_closed_at_public_boundaries(
    tmp_path: Path,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    _, portfolio, _ = _ledgers(tmp_path, cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    unreadable = SQLiteDecisionPublicationLedger(
        tmp_path,
        TEST_PACKET_CRYPTOGRAPHY,
        portfolio,
        SYNTHETIC_SPY,
        PreOpenDecisionPacketWindowSource(),
    )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        unreadable.replay_publication(house_view.run_id, house_view.cycle)
    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        unreadable.validate_history(())


@pytest.mark.parametrize(
    ("update_sql", "corrupt_value"),
    [
        ("UPDATE decision_publications SET cycle_identity = ?", "{"),
        (
            "UPDATE decision_publications SET cycle_identity = ?",
            ' {"asset_class":"us_equity"}',
        ),
        ("UPDATE decision_publications SET run_id = ?", "z" * 64),
        ("UPDATE decision_publications SET recorded_at = ?", "not-an-instant"),
        ("UPDATE decision_publications SET decision_record_json = ?", ""),
    ],
)
def test_hostile_scalar_publication_rows_fail_replay_validation(
    tmp_path: Path,
    update_sql: str,
    corrupt_value: object,
) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    house_view = cycle_result.balanced.require_house_view()
    _checkpoint(decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER decision_publications_are_append_only_update")
        connection.execute(update_sql, (corrupt_value,))

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.replay_publication(house_view.run_id, house_view.cycle)
