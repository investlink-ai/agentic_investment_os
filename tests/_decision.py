"""Synthetic non-production DecisionPacket boundaries for deterministic tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from agentic_investment_os.adapters.decision_signing import (
    HmacSha256DecisionPacketSigner,
)
from agentic_investment_os.adapters.sqlite_decision import SQLiteDecisionPublicationLedger
from agentic_investment_os.application.lifecycle import Advance, Status
from agentic_investment_os.entrypoints.lifecycle import (
    configure_advance as _configure_advance,
)
from agentic_investment_os.entrypoints.lifecycle import (
    configure_status as _configure_status,
)
from agentic_investment_os.portfolio.publication import (
    DecisionPacketAccountScope,
    DecisionPacketValidityWindow,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentic_investment_os.domain.identity import MarketSession
    from agentic_investment_os.domain.temporal import UtcInstant
    from agentic_investment_os.entrypoints.configuration import ConfigurationRefusal

TEST_PACKET_CRYPTOGRAPHY = HmacSha256DecisionPacketSigner(bytes(range(1, 33)))
TEST_DECISION_ACCOUNT_SCOPE = DecisionPacketAccountScope(
    "alpaca",
    "paper",
    hashlib.sha256(b"synthetic-paper-account-scope").hexdigest(),
)

configure_production_advance = partial(
    _configure_advance,
    decision_signer=TEST_PACKET_CRYPTOGRAPHY,
    decision_verifier=TEST_PACKET_CRYPTOGRAPHY,
    decision_account_scope=TEST_DECISION_ACCOUNT_SCOPE,
)
configure_production_status = partial(
    _configure_status,
    decision_verifier=TEST_PACKET_CRYPTOGRAPHY,
)


class _RelativeDecisionWindowSource:
    """Keep non-timing tests independent from the production session calendar."""

    def window_for(
        self,
        cycle: MarketSession,
        recorded_at: UtcInstant,
    ) -> DecisionPacketValidityWindow:
        return DecisionPacketValidityWindow(
            cycle,
            recorded_at,
            type(recorded_at)(recorded_at.value + timedelta(minutes=5)),
        )

    def allows_publication(
        self,
        window: DecisionPacketValidityWindow,
        recorded_at: UtcInstant,
    ) -> bool:
        return window.issued_at.value <= recorded_at.value < window.expires_at.value


def _with_relative_window[**P](
    configure: Callable[P, Advance | ConfigurationRefusal],
) -> Callable[P, Advance | ConfigurationRefusal]:
    def configured(*args: P.args, **kwargs: P.kwargs) -> Advance | ConfigurationRefusal:
        capability = configure(*args, **kwargs)
        if isinstance(capability, Advance):
            window_source = _RelativeDecisionWindowSource()
            decision_ledger = capability.decision_ledger
            assert isinstance(decision_ledger, SQLiteDecisionPublicationLedger)
            return replace(
                capability,
                decision_ledger=replace(
                    decision_ledger,
                    decision_window_source=window_source,
                ),
                decision_window_source=window_source,
            )
        return capability

    return configured


def _with_relative_status_window[**P](
    configure: Callable[P, Status | ConfigurationRefusal],
) -> Callable[P, Status | ConfigurationRefusal]:
    def configured(*args: P.args, **kwargs: P.kwargs) -> Status | ConfigurationRefusal:
        capability = configure(*args, **kwargs)
        if isinstance(capability, Status):
            window_source = _RelativeDecisionWindowSource()
            decision_ledger = capability.decision_history_validator
            assert isinstance(decision_ledger, SQLiteDecisionPublicationLedger)
            return replace(
                capability,
                decision_history_validator=replace(
                    decision_ledger,
                    decision_window_source=window_source,
                ),
            )
        return capability

    return configured


configure_advance = _with_relative_window(configure_production_advance)
configure_status = _with_relative_status_window(configure_production_status)
