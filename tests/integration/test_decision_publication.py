from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agentic_investment_os.adapters.decision_signing import HmacSha256DecisionPacketSigner
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
    DecisionPublicationResult,
    PacketSignature,
    construct_decision_publication,
)
from tests._decision import TEST_DECISION_ACCOUNT_SCOPE, TEST_PACKET_CRYPTOGRAPHY
from tests._portfolio import (
    SYNTHETIC_SPY,
    synthetic_portfolio_cycle,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentic_investment_os.portfolio.shadows import PortfolioCycleResult

_PORTFOLIO_RECORDED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 10, tzinfo=UTC))
_PUBLISHED_AT = UtcInstant.from_datetime(datetime(2026, 8, 21, 20, 30, tzinfo=UTC))


def _ledgers(
    tmp_path: Path,
    cycle_result: PortfolioCycleResult,
) -> tuple[Path, SQLitePortfolioLedger, SQLiteDecisionPublicationLedger]:
    prepared = prepare_runtime_database(tmp_path / "runtime")
    assert isinstance(prepared, PreparedRuntimeDatabase)
    SQLiteLifecycleLedger(prepared.path)
    portfolio = SQLitePortfolioLedger(prepared.path)
    portfolio.record_cycle(
        cycle_result.balanced.require_house_view().run_id,
        cycle_result,
        _PORTFOLIO_RECORDED_AT,
    )
    decisions = SQLiteDecisionPublicationLedger(
        prepared.path,
        TEST_PACKET_CRYPTOGRAPHY,
        portfolio,
    )
    return prepared.path, portfolio, decisions


def _publication(
    cycle_result: PortfolioCycleResult,
    *,
    published_at: UtcInstant = _PUBLISHED_AT,
    account_scope: DecisionPacketAccountScope = TEST_DECISION_ACCOUNT_SCOPE,
) -> DecisionPublicationResult:
    house_view = cycle_result.balanced.require_house_view()
    result = construct_decision_publication(
        cycle_result,
        benchmark_identity=SYNTHETIC_SPY,
        account_scope=account_scope,
        validity_window=DecisionPacketValidityWindow(
            house_view.cycle,
            published_at,
            UtcInstant.from_datetime(published_at.value + timedelta(minutes=5)),
        ),
        signer=TEST_PACKET_CRYPTOGRAPHY,
    )
    assert isinstance(result, DecisionPublicationResult)
    assert result.packet is not None
    return result


def _reference(
    cycle_result: PortfolioCycleResult,
    checkpoint: DecisionCheckpoint,
) -> DecisionCheckpointReference:
    house_view = cycle_result.balanced.require_house_view()
    return DecisionCheckpointReference(house_view.run_id, house_view.cycle, checkpoint)


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

    checkpoint = decisions.record_publication(run_id, publication, _PUBLISHED_AT)
    reopened = SQLiteDecisionPublicationLedger.open_existing(
        database,
        verifier=TEST_PACKET_CRYPTOGRAPHY,
        portfolio_ledger=SQLitePortfolioLedger.open_existing(database),
    )

    assert reopened.replay_publication(run_id, publication.decision_record.cycle) == checkpoint
    reopened.validate_reference(_reference(cycle_result, checkpoint))
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT decision_record_id, packet_id, packet_json, no_action_reason "
            "FROM decision_publications"
        ).fetchone()
    assert row is not None
    assert row[0] == checkpoint.decision_record_id
    assert row[1] == checkpoint.packet_id
    assert row[2] is not None
    assert row[3] is None


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

    first = decisions.record_publication(house_view.run_id, publication, _PUBLISHED_AT)
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
    checkpoint = decisions.record_publication(run_id, original, _PUBLISHED_AT)
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

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication(
            cycle_result.balanced.require_house_view().run_id,
            publication,
            _PUBLISHED_AT,
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
    )

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication(
            cycle_result.balanced.require_house_view().run_id,
            publication,
            _PUBLISHED_AT,
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_publications").fetchone() == (0,)


def test_corrupt_authoritative_packet_fails_reopen_validation(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    run_id = cycle_result.balanced.require_house_view().run_id
    checkpoint = decisions.record_publication(run_id, publication, _PUBLISHED_AT)
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


def test_expired_new_packet_and_mismatched_cycle_fail_closed(tmp_path: Path) -> None:
    cycle_result = synthetic_portfolio_cycle()
    database, _, decisions = _ledgers(tmp_path, cycle_result)
    publication = _publication(cycle_result)
    packet = publication.packet
    assert packet is not None
    run_id = cycle_result.balanced.require_house_view().run_id
    expired_at = packet.expires_at

    with pytest.raises(InvalidLifecycleStateError, match="durable decision publication is invalid"):
        decisions.record_publication(run_id, publication, expired_at)
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
