from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from agentic_investment_os.domain.governance import ACTIVE_CONSTITUTION
from agentic_investment_os.domain.identity import EquityInstrumentIdentity
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.beliefs import BeliefGraph, BeliefGraphQuery

SUBJECT = EquityInstrumentIdentity("alpaca-paper", "equity-aapl", "NASDAQ")
CUTOFF = UtcInstant.from_datetime(datetime(2026, 8, 24, 18, tzinfo=UTC))
AVAILABLE_AT = UtcInstant.from_datetime(datetime(2026, 8, 24, 17, tzinfo=UTC))
ARTIFACT_ID = "a" * 64
PORTFOLIO_FINGERPRINT = "b" * 64
TOOL_SCHEMA_JSON = '{"additionalProperties":false,"type":"object"}'
TOOL_SCHEMA_HASH = hashlib.sha256(TOOL_SCHEMA_JSON.encode()).hexdigest()
SOURCE_HISTORY_HASH = "d" * 64
EVIDENCE_CONTENT = "Reported revenue increased year over year while unit volumes weakened."
EVIDENCE_CONTENT_HASH = hashlib.sha256(EVIDENCE_CONTENT.encode()).hexdigest()
PROMPT_CONTENT = "Build the exact Evidence Collector Dossier schema from only supplied evidence."
PROMPT_HASH = hashlib.sha256(PROMPT_CONTENT.encode()).hexdigest()


def content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def model_configuration() -> dict[str, object]:
    material: dict[str, object] = {
        "schema_version": 1,
        "model_identity": "codex-subscription/test-model",
        "reasoning": {
            "effort": "medium",
            "maximum_output_tokens": 4_000,
            "maximum_turns": 1,
        },
    }
    return {**material, "content_hash": content_hash(material)}


def belief_graph() -> BeliefGraph:
    return BeliefGraph.create(
        query=BeliefGraphQuery(CUTOFF, (SUBJECT,), 10, 10),
        source_history_hash=SOURCE_HISTORY_HASH,
        belief_nodes=(),
        evidence_nodes=(),
        edges=(),
        omitted_belief_events=0,
        omitted_evidence_artifacts=0,
    )


def replay_request(
    *,
    request_id: str = "replay-aapl-2026-08-24",
    namespace: str = "lab.synthetic.aapl",
    available_at: UtcInstant = AVAILABLE_AT,
    prompt_content: str = PROMPT_CONTENT,
) -> dict[str, object]:
    prompt_hash = hashlib.sha256(prompt_content.encode()).hexdigest()
    model = model_configuration()
    model_hash = model["content_hash"]
    assert isinstance(model_hash, str)
    graph = belief_graph()
    material_hashes = sorted(
        {
            EVIDENCE_CONTENT_HASH,
            ACTIVE_CONSTITUTION.content_hash,
            graph.content_hash,
            PORTFOLIO_FINGERPRINT,
            prompt_hash,
            model_hash,
            TOOL_SCHEMA_HASH,
        }
    )
    return {
        "schema_version": 1,
        "record_kind": "lab_replay_request",
        "request_id": request_id,
        "namespace": namespace,
        "input_kind": "synthetic",
        "subject": SUBJECT.to_payload(),
        "evidence": [
            {
                "artifact_id": ARTIFACT_ID,
                "content_hash": EVIDENCE_CONTENT_HASH,
                "available_at": available_at.isoformat(),
                "subject": SUBJECT.to_payload(),
                "content": EVIDENCE_CONTENT,
            }
        ],
        "evidence_cutoff": CUTOFF.isoformat(),
        "data_regime": "test-regime-v1",
        "constitution": ACTIVE_CONSTITUTION,
        "belief_graph": graph,
        "portfolio_context_fingerprint": PORTFOLIO_FINGERPRINT,
        "prompt": {
            "schema_version": 1,
            "prompt_id": "evidence-collector-v1",
            "content": prompt_content,
            "content_hash": prompt_hash,
        },
        "model_configuration": model,
        "tools": [
            {
                "name": "structured_output",
                "schema_json": TOOL_SCHEMA_JSON,
                "schema_hash": TOOL_SCHEMA_HASH,
            }
        ],
        "material_input_hashes": material_hashes,
    }


def dossier_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "evidence_collector_dossier",
        "authority_scope": "research_lab_non_production",
        "non_production": True,
        "subject": SUBJECT.to_payload(),
        "facts": [
            {
                "assertion_id": "reported-revenue",
                "statement_kind": "fact",
                "statement": "Reported revenue increased year over year.",
                "citation_artifact_ids": [ARTIFACT_ID],
                "relevant_at": AVAILABLE_AT.isoformat(),
                "uncertainty": "observed",
            }
        ],
        "interpretations": [
            {
                "assertion_id": "resilient-demand",
                "statement_kind": "interpretation",
                "statement": "The filing may indicate resilient demand.",
                "citation_artifact_ids": [ARTIFACT_ID],
                "relevant_at": AVAILABLE_AT.isoformat(),
                "uncertainty": "inferred",
            }
        ],
        "contradicting_evidence": [
            {
                "artifact_id": ARTIFACT_ID,
                "explanation": "The same artifact reports weaker unit volumes.",
            }
        ],
        "missing_evidence": ["Allowed-source consensus evidence is unavailable."],
        "lenses": [
            {"lens": lens, "disposition": "addressed", "rationale": "Covered by cited evidence."}
            for lens in (
                "information_and_sentiment",
                "growth_and_expectations",
                "quality_and_resilience",
                "valuation_and_embedded_expectations",
                "market_behavior_and_liquidity",
                "catalyst_timing_and_downside",
            )
        ],
    }


def dossier_bytes(payload: object | None = None) -> bytes:
    value = dossier_payload() if payload is None else payload
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
