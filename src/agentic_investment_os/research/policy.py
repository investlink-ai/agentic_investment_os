"""Define the complete versioned policy for production research roles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeGuard

from agentic_investment_os.research.model import (
    ResearchRole,
    parse_model_configuration_contract_payload,
    parse_prompt_contract_payload,
    parse_tool_contract_payloads,
)

__all__ = (
    "ModelConfiguration",
    "ProductionResearchPolicy",
    "PromptArtifact",
    "ResearchRoleContract",
    "ToolContract",
)

_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "policy_type",
        "maximum_belief_events",
        "maximum_evidence_artifacts",
        "role_contracts",
    }
)
_ROLE_CONTRACT_FIELDS = frozenset({"role", "prompt", "model_configuration", "tools"})
_MAXIMUM_GRAPH_NODES = 100
_ROLE_ORDER = (
    ResearchRole.EVIDENCE_COLLECTOR,
    ResearchRole.THESIS_BUILDER,
    ResearchRole.INDEPENDENT_SKEPTIC,
    ResearchRole.SCENARIO_FORECASTER,
    ResearchRole.CIO,
)


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    """Pin one exact role prompt and its declared content identity."""

    prompt_id: str
    content: str
    content_hash: str
    fingerprint: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "prompt_id": self.prompt_id,
            "content": self.content,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """Pin the exposed model identity and bounded reasoning resources."""

    model_identity: str
    effort: str
    maximum_output_tokens: int
    maximum_turns: int
    content_hash: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "model_identity": self.model_identity,
            "reasoning": {
                "effort": self.effort,
                "maximum_output_tokens": self.maximum_output_tokens,
                "maximum_turns": self.maximum_turns,
            },
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Pin an inert tool schema without granting a callable capability."""

    name: str
    schema_json: str
    schema_hash: str
    fingerprint: str

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "schema_json": self.schema_json,
            "schema_hash": self.schema_hash,
        }


@dataclass(frozen=True, slots=True)
class ResearchRoleContract:
    """Bind one role to its exact prompt, model, reasoning, and tool contract."""

    role: ResearchRole
    prompt: PromptArtifact
    model_configuration: ModelConfiguration
    tools: tuple[ToolContract, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "prompt": self.prompt.to_payload(),
            "model_configuration": self.model_configuration.to_payload(),
            "tools": [tool.to_payload() for tool in self.tools],
        }


@dataclass(frozen=True, slots=True)
class ProductionResearchPolicy:
    """Carry the complete production role and bounded-memory policy."""

    maximum_belief_events: int
    maximum_evidence_artifacts: int
    role_contracts: tuple[ResearchRoleContract, ...]
    fingerprint: str

    @classmethod
    def parse(cls, value: object) -> ProductionResearchPolicy | None:
        """Validate hostile configuration before exposing production model authority."""
        root = _exact_mapping(value, _POLICY_FIELDS)
        if (
            root is None
            or root["schema_version"] != 1
            or root["policy_type"] != "v0_production_research"
        ):
            return None
        maximum_belief_events = root["maximum_belief_events"]
        maximum_evidence_artifacts = root["maximum_evidence_artifacts"]
        if (
            type(maximum_belief_events) is not int
            or not 1 <= maximum_belief_events <= _MAXIMUM_GRAPH_NODES
            or type(maximum_evidence_artifacts) is not int
            or not 1 <= maximum_evidence_artifacts <= _MAXIMUM_GRAPH_NODES
        ):
            return None
        role_values = root["role_contracts"]
        if type(role_values) is not list:
            return None
        contracts = tuple(_parse_role_contract(item) for item in role_values)
        if any(contract is None for contract in contracts):
            return None
        narrowed = tuple(contract for contract in contracts if contract is not None)
        if tuple(contract.role for contract in narrowed) != _ROLE_ORDER:
            return None
        canonical = {
            "schema_version": 1,
            "policy_type": "v0_production_research",
            "maximum_belief_events": maximum_belief_events,
            "maximum_evidence_artifacts": maximum_evidence_artifacts,
            "role_contracts": [contract.to_payload() for contract in narrowed],
        }
        return cls(
            maximum_belief_events,
            maximum_evidence_artifacts,
            narrowed,
            _content_hash(canonical),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "policy_type": "v0_production_research",
            "maximum_belief_events": self.maximum_belief_events,
            "maximum_evidence_artifacts": self.maximum_evidence_artifacts,
            "role_contracts": [contract.to_payload() for contract in self.role_contracts],
        }

    def contract_for(self, role: ResearchRole) -> ResearchRoleContract:
        """Return the one contract fixed for a required logical role."""
        return self.role_contracts[_ROLE_ORDER.index(role)]


def _parse_role_contract(value: object) -> ResearchRoleContract | None:
    root = _exact_mapping(value, _ROLE_CONTRACT_FIELDS)
    if root is None or type(root["role"]) is not str:
        return None
    try:
        role = ResearchRole(root["role"])
    except ValueError:
        return None
    prompt = parse_prompt_contract_payload(root["prompt"])
    model = parse_model_configuration_contract_payload(root["model_configuration"])
    tools = parse_tool_contract_payloads(root["tools"])
    if prompt is None or model is None or tools is None:
        return None
    return ResearchRoleContract(
        role,
        PromptArtifact(prompt[0], prompt[1], prompt[2], prompt[3]),
        ModelConfiguration(*model),
        tuple(
            ToolContract(name, schema_json, schema_hash, fingerprint)
            for name, schema_json, schema_hash, fingerprint in tools
        ),
    )


def _exact_mapping(
    value: object,
    fields: frozenset[str],
) -> dict[str, object] | None:
    if not _is_string_mapping(value) or set(value) != fields:
        return None
    return value


def _is_string_mapping(value: object) -> TypeGuard[dict[str, object]]:
    return type(value) is dict and all(type(key) is str for key in value)


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
