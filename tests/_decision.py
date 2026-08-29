"""Synthetic non-production DecisionPacket boundaries for deterministic tests."""

from __future__ import annotations

import hashlib
from functools import partial

from agentic_investment_os.adapters.decision_signing import (
    HmacSha256DecisionPacketSigner,
)
from agentic_investment_os.entrypoints.lifecycle import (
    configure_advance as _configure_advance,
)
from agentic_investment_os.entrypoints.lifecycle import (
    configure_status as _configure_status,
)
from agentic_investment_os.portfolio.publication import DecisionPacketAccountScope

TEST_PACKET_CRYPTOGRAPHY = HmacSha256DecisionPacketSigner(bytes(range(1, 33)))
TEST_DECISION_ACCOUNT_SCOPE = DecisionPacketAccountScope(
    "alpaca",
    "paper",
    hashlib.sha256(b"synthetic-paper-account-scope").hexdigest(),
)

configure_advance = partial(
    _configure_advance,
    decision_signer=TEST_PACKET_CRYPTOGRAPHY,
    decision_verifier=TEST_PACKET_CRYPTOGRAPHY,
    decision_account_scope=TEST_DECISION_ACCOUNT_SCOPE,
)
configure_status = partial(
    _configure_status,
    decision_verifier=TEST_PACKET_CRYPTOGRAPHY,
)
