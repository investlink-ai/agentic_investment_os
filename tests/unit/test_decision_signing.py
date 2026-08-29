from __future__ import annotations

import pytest

from agentic_investment_os.adapters.decision_signing import HmacSha256DecisionPacketSigner
from agentic_investment_os.portfolio.publication import PacketSignature


def test_synthetic_signer_is_deterministic_private_and_verifiable() -> None:
    signer = HmacSha256DecisionPacketSigner(b"synthetic-test-secret")
    signature = signer.sign(b"canonical packet material")

    assert signature == signer.sign(b"canonical packet material")
    assert signer.verify(b"canonical packet material", signature)
    assert not signer.verify(b"changed packet material", signature)
    assert "synthetic-test-secret" not in repr(signer)


@pytest.mark.parametrize("secret", [b"", "not-bytes"])
def test_signer_rejects_missing_or_non_byte_key_material(secret: object) -> None:
    with pytest.raises(ValueError, match="signing material must be non-empty bytes"):
        HmacSha256DecisionPacketSigner(secret)  # type: ignore[arg-type]  # Hostile composition input.


@pytest.mark.parametrize("material", [b"", "not-bytes"])
def test_signer_rejects_missing_or_non_byte_packet_material(material: object) -> None:
    signer = HmacSha256DecisionPacketSigner(b"synthetic-test-secret")

    with pytest.raises(ValueError, match="signing material must be non-empty bytes"):
        signer.sign(material)  # type: ignore[arg-type]  # Hostile runtime boundary input.


def test_verifier_rejects_wrong_types_and_key_identity() -> None:
    signer = HmacSha256DecisionPacketSigner(b"synthetic-test-secret")
    signature = signer.sign(b"canonical packet material")
    wrong_key = PacketSignature(signature.scheme, "0" * 64, signature.value)

    assert not signer.verify("not-bytes", signature)  # type: ignore[arg-type]  # Hostile input.
    assert not signer.verify(b"canonical packet material", object())  # type: ignore[arg-type]  # Hostile input.
    assert not signer.verify(b"canonical packet material", wrong_key)
