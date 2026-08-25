"""Record validated belief transitions through the public memory capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_investment_os.domain.temporal import InvalidUtcInstantError, UtcInstant
from agentic_investment_os.memory.admission import RecordRefusalCode, parse_belief_event
from agentic_investment_os.memory.beliefs import (
    BeliefEvidenceResolver,
    BeliefGraph,
    BeliefGraphRefusal,
    BeliefGraphRefusalCode,
    BeliefLedger,
    BeliefPersistenceError,
    RecordReceipt,
    parse_belief_graph_query,
)

if TYPE_CHECKING:
    from agentic_investment_os.application.lifecycle import Clock

__all__ = ("Record",)

_CLOCK_INVALID = "Record clock must return a timezone-aware instant representable in UTC"


@dataclass(frozen=True, slots=True)
class Record:
    """Append or replay one validated evidence-bound Belief Event."""

    ledger: BeliefLedger
    evidence_resolver: BeliefEvidenceResolver
    clock: Clock

    def __call__(self, event: object) -> RecordReceipt:
        parsed = parse_belief_event(event)
        if parsed is None:
            return RecordReceipt.refused(RecordRefusalCode.INVALID_EVENT)
        try:
            recorded_at = UtcInstant.from_datetime(self.clock.now())
        except InvalidUtcInstantError as error:
            raise BeliefPersistenceError(_CLOCK_INVALID) from error
        return self.ledger.record(parsed, self.evidence_resolver, recorded_at)

    def graph(self, query: object) -> BeliefGraph | BeliefGraphRefusal:
        """Rebuild one bounded as-of graph without trusting a prior projection."""
        parsed = parse_belief_graph_query(query)
        if parsed is None:
            return BeliefGraphRefusal(BeliefGraphRefusalCode.INVALID_QUERY)
        return self.ledger.rebuild_graph(parsed, self.evidence_resolver)
