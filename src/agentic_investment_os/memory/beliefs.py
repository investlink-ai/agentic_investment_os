"""Define append-only belief history and deterministic projection contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeGuard

from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    canonical_instrument_bytes,
    parse_instrument_identity,
)
from agentic_investment_os.domain.lifecycle import is_sha256
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.memory.admission import (
    BeliefClaimKind,
    BeliefEvent,
    BeliefEvidenceArtifact,
    BeliefEvidenceReference,
    BeliefEvidenceRelationship,
    BeliefStatus,
    RecordRefusalCode,
    validate_belief_evidence,
)

__all__ = (
    "AppendBeliefRecord",
    "BeliefEvidenceResolver",
    "BeliefGraph",
    "BeliefGraphBeliefNode",
    "BeliefGraphEdge",
    "BeliefGraphEdgeKind",
    "BeliefGraphEvidenceNode",
    "BeliefGraphQuery",
    "BeliefGraphRefusal",
    "BeliefGraphRefusalCode",
    "BeliefHistory",
    "BeliefHistoryValidator",
    "BeliefLedger",
    "BeliefLedgerEntry",
    "BeliefLifecycleReference",
    "BeliefPersistenceError",
    "RecordDisposition",
    "RecordReceipt",
    "belief_graph_evidence_references",
    "parse_belief_graph_query",
    "project_belief_graph",
)

_MAXIMUM_GRAPH_SUBJECTS = 32
_MAXIMUM_GRAPH_NODES = 100
_INVALID_HISTORY = "invalid belief history"
_INVALID_RECEIPT = "invalid belief record receipt"
_INVALID_GRAPH = "invalid belief graph"
_INVALID_GRAPH_QUERY = "invalid belief graph query"
_INVALID_GRAPH_REFUSAL = "invalid belief graph refusal"
_GRAPH_QUERY_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "cutoff",
        "subjects",
        "maximum_belief_events",
        "maximum_evidence_artifacts",
    }
)


class RecordDisposition(StrEnum):
    """Distinguish a new append, replay, or bounded refusal."""

    APPENDED = "appended"
    REPLAYED = "replayed"
    REFUSED = "refused"


class BeliefPersistenceError(RuntimeError):
    """Report that authoritative belief history cannot be trusted or appended."""


class BeliefEvidenceResolver(Protocol):
    """Resolve immutable Vault facts without making memory another evidence source."""

    def resolve_belief_evidence(
        self,
        references: tuple[BeliefEvidenceReference, ...],
    ) -> tuple[BeliefEvidenceArtifact, ...] | RecordRefusalCode: ...


class BeliefLedger(Protocol):
    """Append or replay a belief event against validated authoritative storage."""

    def record(
        self,
        event: BeliefEvent,
        evidence_resolver: BeliefEvidenceResolver,
        recorded_at: UtcInstant,
    ) -> RecordReceipt: ...

    def rebuild_graph(
        self,
        query: BeliefGraphQuery,
        evidence_resolver: BeliefEvidenceResolver,
    ) -> BeliefGraph | BeliefGraphRefusal: ...

    def validate_history(
        self,
        event_ids: tuple[str, ...],
        evidence_resolver: BeliefEvidenceResolver,
    ) -> None: ...

    def validate_lifecycle_references(
        self,
        references: tuple[BeliefLifecycleReference, ...],
        evidence_resolver: BeliefEvidenceResolver,
    ) -> None: ...


class BeliefHistoryValidator(Protocol):
    """Validate authoritative Belief history and lifecycle event references."""

    def validate_history(self, event_ids: tuple[str, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class BeliefLifecycleReference:
    """Bind expected belief material to the run and CIO result that produced it."""

    run_id: str
    cio_resolution_id: str
    event_id: str
    belief_id: str
    subject: EquityInstrumentIdentity
    claim: str
    valid_at: UtcInstant
    evidence_cutoff: UtcInstant
    confidence: str
    evidence: tuple[BeliefEvidenceReference, ...]
    falsifiers: tuple[str, ...]
    lifecycle_recorded_at: UtcInstant

    def __post_init__(self) -> None:
        expected_event_id = _hash_payload(
            {
                "cio_resolution_id": self.cio_resolution_id,
                "belief_id": self.belief_id,
            }
        )
        try:
            probe = BeliefEvent.create(
                event_id=self.event_id,
                belief_id=self.belief_id,
                subject=self.subject,
                claim_kind=BeliefClaimKind.EXPECTATION,
                claim=self.claim,
                valid_at=self.valid_at,
                transaction_at=self.valid_at,
                evidence_cutoff=self.evidence_cutoff,
                confidence=self.confidence,
                evidence=self.evidence,
                falsifiers=self.falsifiers,
                status=BeliefStatus.ACTIVE,
                transition_from_event_id=None,
                supersedes_event_id=None,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(_INVALID_HISTORY) from error
        if (
            not is_sha256(self.run_id)
            or not is_sha256(self.cio_resolution_id)
            or self.event_id != expected_event_id
            or type(self.lifecycle_recorded_at) is not UtcInstant
            or self.valid_at.value > self.lifecycle_recorded_at.value
            or probe.event_id != self.event_id
        ):
            raise ValueError(_INVALID_HISTORY)


class BeliefGraphEdgeKind(StrEnum):
    """Name one provenance or belief-history relationship in a projection."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    TRANSITION_FROM = "transition_from"
    SUPERSEDES = "supersedes"


class BeliefGraphRefusalCode(StrEnum):
    """Bound graph-query or authoritative reconstruction failures."""

    INVALID_QUERY = "invalid_query"
    INVALID_AUTHORITATIVE_HISTORY = "invalid_authoritative_history"
    INVALID_EVIDENCE = "invalid_evidence"


@dataclass(frozen=True, slots=True)
class BeliefGraphRefusal:
    """Return a content-free graph refusal without a stale projection."""

    code: BeliefGraphRefusalCode

    def __post_init__(self) -> None:
        if type(self.code) is not BeliefGraphRefusalCode:
            raise ValueError(_INVALID_GRAPH_REFUSAL)


@dataclass(frozen=True, slots=True)
class BeliefGraphQuery:
    """Bound one as-of graph to explicit canonical subjects and node caps."""

    cutoff: UtcInstant
    subjects: tuple[EquityInstrumentIdentity, ...]
    maximum_belief_events: int
    maximum_evidence_artifacts: int

    def __post_init__(self) -> None:
        subject_keys = tuple(canonical_instrument_bytes(subject) for subject in self.subjects)
        if (
            type(self.cutoff) is not UtcInstant
            or type(self.subjects) is not tuple
            or not 1 <= len(self.subjects) <= _MAXIMUM_GRAPH_SUBJECTS
            or any(type(subject) is not EquityInstrumentIdentity for subject in self.subjects)
            or subject_keys != tuple(sorted(set(subject_keys)))
            or type(self.maximum_belief_events) is not int
            or not 1 <= self.maximum_belief_events <= _MAXIMUM_GRAPH_NODES
            or type(self.maximum_evidence_artifacts) is not int
            or not 1 <= self.maximum_evidence_artifacts <= _MAXIMUM_GRAPH_NODES
        ):
            raise ValueError(_INVALID_GRAPH_QUERY)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_kind": "belief_graph_query",
            "cutoff": self.cutoff.isoformat(),
            "subjects": [subject.to_payload() for subject in self.subjects],
            "maximum_belief_events": self.maximum_belief_events,
            "maximum_evidence_artifacts": self.maximum_evidence_artifacts,
        }


@dataclass(frozen=True, slots=True)
class BeliefGraphBeliefNode:
    """Retain one selected belief event with its authoritative ledger position."""

    ledger_position: int
    recorded_at: UtcInstant
    event: BeliefEvent

    def to_payload(self) -> dict[str, object]:
        return {
            "ledger_position": self.ledger_position,
            "recorded_at": self.recorded_at.isoformat(),
            "event": self.event.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class BeliefGraphEvidenceNode:
    """Retain immutable Evidence Vault provenance without copying source content."""

    artifact_id: str
    content_hash: str
    available_at: UtcInstant

    def to_payload(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "available_at": self.available_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BeliefGraphEdge:
    """Connect selected nodes without granting the graph mutation authority."""

    kind: BeliefGraphEdgeKind
    source_id: str
    target_id: str

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True, slots=True)
class BeliefGraph:
    """Expose one bounded, hash-pinned, disposable as-of belief projection."""

    query: BeliefGraphQuery
    source_history_hash: str
    belief_nodes: tuple[BeliefGraphBeliefNode, ...]
    evidence_nodes: tuple[BeliefGraphEvidenceNode, ...]
    edges: tuple[BeliefGraphEdge, ...]
    omitted_belief_events: int
    omitted_evidence_artifacts: int
    content_hash: str

    def __post_init__(self) -> None:
        if (
            type(self.query) is not BeliefGraphQuery
            or not is_sha256(self.source_history_hash)
            or type(self.belief_nodes) is not tuple
            or type(self.evidence_nodes) is not tuple
            or type(self.edges) is not tuple
            or type(self.omitted_belief_events) is not int
            or self.omitted_belief_events < 0
            or type(self.omitted_evidence_artifacts) is not int
            or self.omitted_evidence_artifacts < 0
            or not is_sha256(self.content_hash)
            or _graph_content_hash(self) != self.content_hash
        ):
            raise ValueError(_INVALID_GRAPH)

    @classmethod
    def create(  # noqa: PLR0913 - the graph binds every projection dimension.
        cls,
        *,
        query: BeliefGraphQuery,
        source_history_hash: str,
        belief_nodes: tuple[BeliefGraphBeliefNode, ...],
        evidence_nodes: tuple[BeliefGraphEvidenceNode, ...],
        edges: tuple[BeliefGraphEdge, ...],
        omitted_belief_events: int,
        omitted_evidence_artifacts: int,
    ) -> BeliefGraph:
        """Construct a projection whose identity binds bounds, provenance, nodes, and omissions."""
        provisional = cls.__new__(cls)
        object.__setattr__(provisional, "query", query)
        object.__setattr__(provisional, "source_history_hash", source_history_hash)
        object.__setattr__(provisional, "belief_nodes", belief_nodes)
        object.__setattr__(provisional, "evidence_nodes", evidence_nodes)
        object.__setattr__(provisional, "edges", edges)
        object.__setattr__(provisional, "omitted_belief_events", omitted_belief_events)
        object.__setattr__(
            provisional,
            "omitted_evidence_artifacts",
            omitted_evidence_artifacts,
        )
        object.__setattr__(provisional, "content_hash", "0" * 64)
        return cls(
            query=query,
            source_history_hash=source_history_hash,
            belief_nodes=belief_nodes,
            evidence_nodes=evidence_nodes,
            edges=edges,
            omitted_belief_events=omitted_belief_events,
            omitted_evidence_artifacts=omitted_evidence_artifacts,
            content_hash=_graph_content_hash(provisional),
        )

    def to_payload(self) -> dict[str, object]:
        material = _graph_material(self)
        return {**material, "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class BeliefLedgerEntry:
    """Bind one validated event to its immutable ledger and projection positions."""

    ledger_position: int
    event: BeliefEvent
    recorded_at: UtcInstant
    projection_identity: str

    def __post_init__(self) -> None:
        if (
            type(self.ledger_position) is not int
            or self.ledger_position < 1
            or type(self.event) is not BeliefEvent
            or type(self.recorded_at) is not UtcInstant
            or self.event.transaction_at.value > self.recorded_at.value
            or not is_sha256(self.projection_identity)
        ):
            raise ValueError(_INVALID_HISTORY)


@dataclass(frozen=True, slots=True)
class BeliefHistory:
    """Carry one contiguous, append-only authoritative belief history."""

    entries: tuple[BeliefLedgerEntry, ...]

    def __post_init__(self) -> None:
        # Keep the history contract beside its value object while the reducer consumes that type.
        from agentic_investment_os.memory.reducer import (  # noqa: PLC0415
            validate_belief_history_integrity,
        )

        if type(self.entries) is not tuple:
            raise ValueError(_INVALID_HISTORY)
        if not validate_belief_history_integrity(self.entries):
            raise ValueError(_INVALID_HISTORY)


@dataclass(frozen=True, slots=True)
class RecordReceipt:
    """Expose only bounded append identity or refusal state to a Record caller."""

    disposition: RecordDisposition
    event_id: str | None
    ledger_position: int | None
    projection_identity: str | None
    refusal: RecordRefusalCode | None

    def __post_init__(self) -> None:
        accepted = self.disposition in (
            RecordDisposition.APPENDED,
            RecordDisposition.REPLAYED,
        )
        if (
            type(self.disposition) is not RecordDisposition
            or accepted
            != (
                self.event_id is not None
                and self.ledger_position is not None
                and self.projection_identity is not None
                and self.refusal is None
            )
            or (
                not accepted
                and not (
                    self.event_id is None
                    and self.ledger_position is None
                    and self.projection_identity is None
                    and type(self.refusal) is RecordRefusalCode
                )
            )
        ):
            raise ValueError(_INVALID_RECEIPT)

    @classmethod
    def refused(cls, code: RecordRefusalCode) -> RecordReceipt:
        """Return one content-free refusal."""
        return cls(RecordDisposition.REFUSED, None, None, None, code)


@dataclass(frozen=True, slots=True)
class AppendBeliefRecord:
    """Request one atomic append selected by the pure belief reducer."""

    entry: BeliefLedgerEntry
    receipt: RecordReceipt


type BeliefRecordDecision = AppendBeliefRecord | RecordReceipt


def parse_belief_graph_query(value: object) -> BeliefGraphQuery | None:
    """Validate one hostile graph query with explicit relevance and size bounds."""
    if type(value) is BeliefGraphQuery:
        value = value.to_payload()
    fields = _exact_mapping(value, _GRAPH_QUERY_FIELDS)
    if (
        fields is None
        or not _schema_version(fields["schema_version"], 1)
        or fields["record_kind"] != "belief_graph_query"
    ):
        return None
    cutoff = _instant(fields["cutoff"])
    subjects = _equity_subject_tuple(fields["subjects"])
    maximum_belief_events = _integer_value(fields["maximum_belief_events"])
    maximum_evidence_artifacts = _integer_value(fields["maximum_evidence_artifacts"])
    if (
        cutoff is None
        or subjects is None
        or maximum_belief_events is None
        or maximum_evidence_artifacts is None
    ):
        return None
    try:
        return BeliefGraphQuery(
            cutoff,
            subjects,
            maximum_belief_events,
            maximum_evidence_artifacts,
        )
    except ValueError:
        return None


def project_belief_graph(
    history: BeliefHistory,
    query: BeliefGraphQuery,
    artifacts: tuple[BeliefEvidenceArtifact, ...],
) -> BeliefGraph:
    """Build one deterministic bounded graph from validated authoritative history."""
    history.__post_init__()
    query.__post_init__()
    admitted = _admitted_belief_entries(history, query)
    evidence_refusal = validate_belief_evidence(tuple(entry.event for entry in admitted), artifacts)
    if evidence_refusal is not None:
        raise ValueError(_INVALID_HISTORY)
    ranked = tuple(
        sorted(
            admitted,
            key=lambda entry: (
                entry.event.transaction_at.value,
                entry.event.valid_at.value,
                entry.ledger_position,
            ),
            reverse=True,
        )
    )
    chosen_by_relevance = ranked[: query.maximum_belief_events]
    chosen = tuple(
        sorted(
            chosen_by_relevance,
            key=lambda entry: (
                canonical_instrument_bytes(entry.event.subject),
                entry.event.belief_id,
                entry.event.valid_at.value,
                entry.event.transaction_at.value,
                entry.ledger_position,
            ),
        )
    )
    belief_nodes = tuple(
        BeliefGraphBeliefNode(entry.ledger_position, entry.recorded_at, entry.event)
        for entry in chosen
    )
    referenced_artifact_ids = _ranked_artifact_ids(chosen_by_relevance)
    chosen_artifact_ids = referenced_artifact_ids[: query.maximum_evidence_artifacts]
    artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    evidence_nodes = tuple(
        BeliefGraphEvidenceNode(
            artifacts_by_id[artifact_id].artifact_id,
            artifacts_by_id[artifact_id].content_hash,
            artifacts_by_id[artifact_id].available_at,
        )
        for artifact_id in sorted(chosen_artifact_ids)
    )
    chosen_event_ids = frozenset(entry.event.event_id for entry in chosen)
    chosen_evidence_ids = frozenset(chosen_artifact_ids)
    edges = _graph_edges(chosen, chosen_event_ids, chosen_evidence_ids)
    source_history_hash = hashlib.sha256(
        _canonical_json(
            [
                {
                    "ledger_position": entry.ledger_position,
                    "recorded_at": entry.recorded_at.isoformat(),
                    "event_content_hash": entry.event.content_hash,
                }
                for entry in admitted
            ]
        )
    ).hexdigest()
    return BeliefGraph.create(
        query=query,
        source_history_hash=source_history_hash,
        belief_nodes=belief_nodes,
        evidence_nodes=evidence_nodes,
        edges=edges,
        omitted_belief_events=len(admitted) - len(chosen),
        omitted_evidence_artifacts=len(referenced_artifact_ids) - len(chosen_artifact_ids),
    )


def belief_graph_evidence_references(
    history: BeliefHistory,
    query: BeliefGraphQuery,
) -> tuple[BeliefEvidenceReference, ...]:
    """Return only evidence references knowable within one bounded as-of query."""
    history.__post_init__()
    query.__post_init__()
    references = {
        reference.artifact_id: reference
        for entry in _admitted_belief_entries(history, query)
        for reference in entry.event.evidence
    }
    return tuple(references[key] for key in sorted(references))


def _admitted_belief_entries(
    history: BeliefHistory,
    query: BeliefGraphQuery,
) -> tuple[BeliefLedgerEntry, ...]:
    subject_keys = frozenset(canonical_instrument_bytes(subject) for subject in query.subjects)
    return tuple(
        entry
        for entry in history.entries
        if canonical_instrument_bytes(entry.event.subject) in subject_keys
        and entry.event.valid_at.value <= query.cutoff.value
        and entry.event.transaction_at.value <= query.cutoff.value
        and entry.event.evidence_cutoff.value <= query.cutoff.value
        and entry.recorded_at.value <= query.cutoff.value
    )


def _ranked_artifact_ids(
    entries: tuple[BeliefLedgerEntry, ...],
) -> tuple[str, ...]:
    ranked: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for reference in entry.event.evidence:
            if reference.artifact_id not in seen:
                seen.add(reference.artifact_id)
                ranked.append(reference.artifact_id)
    return tuple(ranked)


def _graph_edges(
    entries: tuple[BeliefLedgerEntry, ...],
    event_ids: frozenset[str],
    evidence_ids: frozenset[str],
) -> tuple[BeliefGraphEdge, ...]:
    edges: list[BeliefGraphEdge] = []
    for entry in entries:
        event = entry.event
        edges.extend(
            BeliefGraphEdge(
                (
                    BeliefGraphEdgeKind.SUPPORTS
                    if reference.relationship is BeliefEvidenceRelationship.SUPPORTING
                    else BeliefGraphEdgeKind.CONTRADICTS
                ),
                reference.artifact_id,
                event.event_id,
            )
            for reference in event.evidence
            if reference.artifact_id in evidence_ids
        )
        if event.transition_from_event_id in event_ids:
            transition_id = event.transition_from_event_id
            if transition_id is not None:
                edges.append(
                    BeliefGraphEdge(
                        BeliefGraphEdgeKind.TRANSITION_FROM,
                        event.event_id,
                        transition_id,
                    )
                )
        if event.supersedes_event_id in event_ids:
            supersedes_id = event.supersedes_event_id
            if supersedes_id is not None:
                edges.append(
                    BeliefGraphEdge(
                        BeliefGraphEdgeKind.SUPERSEDES,
                        event.event_id,
                        supersedes_id,
                    )
                )
    return tuple(sorted(edges, key=lambda edge: (edge.kind.value, edge.source_id, edge.target_id)))


def _graph_material(graph: BeliefGraph) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "belief_graph",
        "query": graph.query.to_payload(),
        "source_history_hash": graph.source_history_hash,
        "belief_nodes": [node.to_payload() for node in graph.belief_nodes],
        "evidence_nodes": [node.to_payload() for node in graph.evidence_nodes],
        "edges": [edge.to_payload() for edge in graph.edges],
        "omissions": {
            "belief_events": graph.omitted_belief_events,
            "evidence_artifacts": graph.omitted_evidence_artifacts,
        },
    }


def _graph_content_hash(graph: BeliefGraph) -> str:
    return hashlib.sha256(_canonical_json(_graph_material(graph))).hexdigest()


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return (
        isinstance(value, dict) and type(value) is dict and all(type(key) is str for key in value)
    )


def _is_json_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list) and type(value) is list


def _exact_mapping(
    value: object,
    fields: frozenset[str],
) -> dict[str, object] | None:
    if not _is_json_object(value) or frozenset(value) != fields:
        return None
    return value


def _instant(value: object) -> UtcInstant | None:
    try:
        return UtcInstant.parse(value)
    except ValueError:
        return None


def _equity_subject_tuple(value: object) -> tuple[EquityInstrumentIdentity, ...] | None:
    if not _is_json_list(value):
        return None
    subjects: list[EquityInstrumentIdentity] = []
    for item in value:
        subject = parse_instrument_identity(item)
        if type(subject) is not EquityInstrumentIdentity:
            return None
        subjects.append(subject)
    return tuple(subjects)


def _integer_value(value: object) -> int | None:
    return value if type(value) is int else None


def _schema_version(value: object, expected: int) -> bool:
    return type(value) is int and value == expected
