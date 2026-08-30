"""Adapt private HMAC key material to the typed DecisionPacket signing boundary."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from agentic_investment_os.portfolio.publication import PacketSignature

__all__ = ("HmacSha256DecisionPacketSigner",)

_INVALID_SIGNING_INPUT = "DecisionPacket signing material must be non-empty bytes"


@dataclass(frozen=True, slots=True)
class HmacSha256DecisionPacketSigner:
    """Sign and verify canonical packet bytes without exposing the private secret."""

    _secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self._secret) is not bytes or not self._secret:
            raise ValueError(_INVALID_SIGNING_INPUT)

    def sign(self, material: bytes) -> PacketSignature:
        if type(material) is not bytes or not material:
            raise ValueError(_INVALID_SIGNING_INPUT)
        return PacketSignature(
            "hmac-sha256-v1",
            hashlib.sha256(self._secret).hexdigest(),
            hmac.new(self._secret, material, hashlib.sha256).hexdigest(),
        )

    def verify(self, material: bytes, signature: PacketSignature) -> bool:
        if type(material) is not bytes or type(signature) is not PacketSignature:
            return False
        expected = self.sign(material)
        return hmac.compare_digest(expected.key_id, signature.key_id) and hmac.compare_digest(
            expected.value,
            signature.value,
        )
