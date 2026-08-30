"""Persist exact deterministic portfolio-construction artifacts append-only."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from agentic_investment_os.adapters.recorded_portfolio import (
    parse_recorded_portfolio_shadow_account,
)
from agentic_investment_os.domain.lifecycle import (
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    PortfolioCheckpoint,
    PortfolioCheckpointReference,
    PortfolioCheckpointRefusalReason,
    PortfolioShadowReference,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.portfolio.construction import (
    parse_portfolio_construction_result,
)
from agentic_investment_os.portfolio.shadows import (
    PortfolioCycleResult,
    PortfolioShadowAccount,
)

__all__ = ("SQLitePortfolioLedger",)

if TYPE_CHECKING:
    from pathlib import Path

_CHECKPOINT_FAILED = "SQLite portfolio checkpoint failed"
_INVALID_HISTORY = "durable portfolio construction is invalid"


@dataclass(frozen=True, slots=True)
class SQLitePortfolioLedger:
    """Append exact construction results and validate lifecycle-owned references."""

    database: Path

    @classmethod
    def open_existing(cls, database: Path) -> Self:
        """Open the already startup-validated runtime database."""
        return cls(database)

    def record_cycle(
        self,
        run_id: str,
        result: PortfolioCycleResult,
        recorded_at: UtcInstant,
    ) -> PortfolioCheckpoint:
        """Append Balanced and every required shadow, or replay exact cycle material."""
        if not is_sha256(run_id) or type(recorded_at) is not UtcInstant:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED)
        balanced = result.balanced
        if balanced.house_view is not None and balanced.house_view.run_id != run_id:
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        if any(item.run_id != run_id for item in result.shadows):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        payload = balanced.to_payload()
        encoded = _canonical_json(payload)
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection, connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT result_id, result_json, recorded_at
                    FROM portfolio_constructions WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    stored_recorded_at = _recorded_at(existing[2])
                    if stored_recorded_at is None:
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    checkpoint = _checkpoint(result, stored_recorded_at)
                    if (
                        existing[0] != checkpoint.result_id
                        or existing[1] != encoded
                        or _stored_shadows(connection, run_id, stored_recorded_at) != result.shadows
                    ):
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    return checkpoint
                checkpoint = _checkpoint(result, recorded_at)
                connection.execute(
                    """
                    INSERT INTO portfolio_constructions (
                        run_id, result_id, house_view_id, policy_id, input_id,
                        target_band_ids, refusal_reason, result_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        checkpoint.result_id,
                        checkpoint.house_view_id,
                        checkpoint.policy_id,
                        checkpoint.input_id,
                        _canonical_json(list(checkpoint.target_band_ids)),
                        (
                            None
                            if checkpoint.refusal_reason is None
                            else checkpoint.refusal_reason.value
                        ),
                        encoded,
                        recorded_at.isoformat(),
                    ),
                )
                for shadow in result.shadows:
                    connection.execute(
                        """
                        INSERT INTO portfolio_shadow_accounts (
                            run_id, account_kind, account_id, house_view_id, policy_id,
                            input_id, result_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            shadow.account_kind.value,
                            shadow.account_id,
                            shadow.house_view_id,
                            shadow.policy_id,
                            shadow.input_id,
                            _canonical_json(shadow.to_payload()),
                            recorded_at.isoformat(),
                        ),
                    )
        except InvalidLifecycleStateError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED) from error
        return checkpoint

    def validate_history(self, references: tuple[PortfolioCheckpointReference, ...]) -> None:
        """Reparse every referenced artifact without trusting stored columns or JSON."""
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                rows = connection.execute(
                    """
                    SELECT run_id, result_id, house_view_id, policy_id, input_id,
                           target_band_ids, refusal_reason, result_json, recorded_at
                    FROM portfolio_constructions ORDER BY run_id
                    """
                ).fetchall()
                available = [_validated_reference(connection, row) for row in rows]
                shadow_run_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT run_id FROM portfolio_shadow_accounts"
                    ).fetchall()
                }
                if shadow_run_ids - {row[0] for row in rows}:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
                for reference in references:
                    try:
                        available.remove(reference)
                    except ValueError as error:
                        raise InvalidLifecycleStateError(_INVALID_HISTORY) from error
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def validate_reference(self, reference: PortfolioCheckpointReference) -> None:
        """Validate only the portfolio history owned by one requested lifecycle run."""
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                row = connection.execute(
                    """
                    SELECT run_id, result_id, house_view_id, policy_id, input_id,
                           target_band_ids, refusal_reason, result_json, recorded_at
                    FROM portfolio_constructions WHERE run_id = ?
                    """,
                    (reference.run_id,),
                ).fetchone()
                if row is None or _validated_reference(connection, row) != reference:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def load_cycle(self, reference: PortfolioCheckpointReference) -> PortfolioCycleResult:
        """Load one exact revalidated Balanced-plus-shadow cycle by lifecycle reference."""
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                row = connection.execute(
                    """
                    SELECT run_id, result_id, house_view_id, policy_id, input_id,
                           target_band_ids, refusal_reason, result_json, recorded_at
                    FROM portfolio_constructions WHERE run_id = ?
                    """,
                    (reference.run_id,),
                ).fetchone()
                if row is None or _validated_reference(connection, row) != reference:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
                return _load_cycle_for_row(connection, row)
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error

    def load_cycle_with_reference_for_run(
        self,
        run_id: str,
    ) -> tuple[PortfolioCycleResult, PortfolioCheckpointReference]:
        """Load one exact cycle together with its revalidated durable reference."""
        if not is_sha256(run_id):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection:
                row = connection.execute(
                    """
                    SELECT run_id, result_id, house_view_id, policy_id, input_id,
                           target_band_ids, refusal_reason, result_json, recorded_at
                    FROM portfolio_constructions WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise InvalidLifecycleStateError(_INVALID_HISTORY)
                reference = _validated_reference(connection, row)
                return _load_cycle_for_row(connection, row), reference
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error


def _validated_reference(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
) -> PortfolioCheckpointReference:
    run_id = row[0]
    encoded = row[7]
    if type(run_id) is not str or not is_sha256(run_id) or type(encoded) is not str:
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    decoded: object = json.loads(encoded)
    result = parse_portfolio_construction_result(decoded)
    parsed_recorded_at = _recorded_at(row[8])
    shadows = _stored_shadows(connection, run_id, parsed_recorded_at)
    cycle_result = None if result is None else PortfolioCycleResult(result, shadows)
    rebuilt = (
        None
        if cycle_result is None or parsed_recorded_at is None
        else _checkpoint(cycle_result, parsed_recorded_at)
    )
    if (
        rebuilt is None
        or result is None
        or row[1] != rebuilt.result_id
        or row[2] != rebuilt.house_view_id
        or row[3] != rebuilt.policy_id
        or row[4] != rebuilt.input_id
        or row[5] != _canonical_json(list(rebuilt.target_band_ids))
        or row[6] != (None if rebuilt.refusal_reason is None else rebuilt.refusal_reason.value)
        or encoded != _canonical_json(result.to_payload())
        or parsed_recorded_at is None
        or (result.house_view is not None and result.house_view.run_id != run_id)
        or any(item.run_id != run_id for item in shadows)
    ):
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return PortfolioCheckpointReference(run_id, rebuilt, parsed_recorded_at)


def _checkpoint(
    result: PortfolioCycleResult,
    recorded_at: UtcInstant,
) -> PortfolioCheckpoint:
    balanced = result.balanced
    refusal = (
        None
        if balanced.refusal is None
        else PortfolioCheckpointRefusalReason(balanced.refusal.value)
    )
    return PortfolioCheckpoint(
        result_id=balanced.content_hash,
        house_view_id=(None if balanced.house_view is None else balanced.house_view.house_view_id),
        policy_id=balanced.policy_id,
        input_id=balanced.input_id,
        target_band_ids=tuple(sorted(band.target_band_id for band in balanced.target_bands)),
        refusal_reason=refusal,
        recorded_at=recorded_at,
        shadow_accounts=tuple(
            PortfolioShadowReference(item.account_kind, item.account_id) for item in result.shadows
        ),
    )


def _load_cycle_for_row(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
) -> PortfolioCycleResult:
    run_id = _text_value(row[0])
    decoded: object = json.loads(_text_value(row[7]))
    balanced = parse_portfolio_construction_result(decoded)
    recorded_at = _recorded_at(row[8])
    if balanced is None or recorded_at is None:
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return PortfolioCycleResult(
        balanced,
        _stored_shadows(connection, run_id, recorded_at),
    )


def _stored_shadows(
    connection: sqlite3.Connection,
    run_id: str,
    expected_recorded_at: UtcInstant | None,
) -> tuple[PortfolioShadowAccount, ...]:
    rows = connection.execute(
        """
        SELECT account_kind, account_id, house_view_id, policy_id, input_id,
               result_json, recorded_at
        FROM portfolio_shadow_accounts WHERE run_id = ? ORDER BY account_kind
        """,
        (run_id,),
    ).fetchall()
    parsed: list[PortfolioShadowAccount] = []
    for row in rows:
        decoded: object = json.loads(row[5])
        account = parse_recorded_portfolio_shadow_account(decoded)
        recorded_at = _recorded_at(row[6])
        if (
            account is None
            or recorded_at is None
            or recorded_at != expected_recorded_at
            or row[0] != account.account_kind.value
            or row[1] != account.account_id
            or row[2] != account.house_view_id
            or row[3] != account.policy_id
            or row[4] != account.input_id
            or row[5] != _canonical_json(account.to_payload())
        ):
            raise InvalidLifecycleStateError(_INVALID_HISTORY)
        parsed.append(account)
    order = {"conservative": 0, "growth": 1, "equal_weight": 2}
    return tuple(sorted(parsed, key=lambda item: order[item.account_kind.value]))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _recorded_at(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None


def _text_value(value: object) -> str:
    if type(value) is not str:
        raise InvalidLifecycleStateError(_INVALID_HISTORY)
    return value
