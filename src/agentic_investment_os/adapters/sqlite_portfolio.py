"""Persist exact deterministic portfolio-construction artifacts append-only."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from agentic_investment_os.domain.lifecycle import (
    InvalidLifecycleStateError,
    LifecyclePersistenceError,
    PortfolioCheckpoint,
    PortfolioCheckpointReference,
    PortfolioCheckpointRefusalReason,
    is_sha256,
)
from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.portfolio.construction import (
    PortfolioConstructionResult,
    parse_portfolio_construction_result,
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

    def record(
        self,
        run_id: str,
        result: PortfolioConstructionResult,
        recorded_at: UtcInstant,
    ) -> PortfolioCheckpoint:
        """Append one result, or replay only byte-identical run material."""
        if not is_sha256(run_id) or type(recorded_at) is not UtcInstant:
            raise LifecyclePersistenceError(_CHECKPOINT_FAILED)
        payload = result.to_payload()
        encoded = _canonical_json(payload)
        checkpoint = _checkpoint(result)
        try:
            with closing(sqlite3.connect(self.database, timeout=5.0)) as connection, connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                existing = connection.execute(
                    """
                    SELECT result_id, result_json, recorded_at
                    FROM portfolio_constructions WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing[0] != checkpoint.result_id
                        or existing[1] != encoded
                        or _recorded_at(existing[2]) is None
                    ):
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    return checkpoint
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
                available: list[PortfolioCheckpointReference] = []
                for row in rows:
                    run_id = row[0]
                    if not is_sha256(run_id):
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    decoded: object = json.loads(row[7])
                    result = parse_portfolio_construction_result(decoded)
                    rebuilt = None if result is None else _checkpoint(result)
                    parsed_recorded_at = _recorded_at(row[8])
                    if (
                        rebuilt is None
                        or result is None
                        or row[1] != rebuilt.result_id
                        or row[2] != rebuilt.house_view_id
                        or row[3] != rebuilt.policy_id
                        or row[4] != rebuilt.input_id
                        or row[5] != _canonical_json(list(rebuilt.target_band_ids))
                        or row[6]
                        != (
                            None if rebuilt.refusal_reason is None else rebuilt.refusal_reason.value
                        )
                        or row[7] != _canonical_json(result.to_payload())
                        or parsed_recorded_at is None
                        or (result.house_view is not None and result.house_view.run_id != run_id)
                    ):
                        raise InvalidLifecycleStateError(_INVALID_HISTORY)
                    available.append(
                        PortfolioCheckpointReference(run_id, rebuilt, parsed_recorded_at)
                    )
                for reference in references:
                    try:
                        available.remove(reference)
                    except ValueError as error:
                        raise InvalidLifecycleStateError(_INVALID_HISTORY) from error
        except InvalidLifecycleStateError:
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as error:
            raise InvalidLifecycleStateError(_INVALID_HISTORY) from error


def _checkpoint(result: PortfolioConstructionResult) -> PortfolioCheckpoint:
    refusal = (
        None if result.refusal is None else PortfolioCheckpointRefusalReason(result.refusal.value)
    )
    return PortfolioCheckpoint(
        result_id=result.content_hash,
        house_view_id=(None if result.house_view is None else result.house_view.house_view_id),
        policy_id=result.policy_id,
        input_id=result.input_id,
        target_band_ids=tuple(sorted(band.target_band_id for band in result.target_bands)),
        refusal_reason=refusal,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _recorded_at(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except InvalidUtcInstantError:
        return None
