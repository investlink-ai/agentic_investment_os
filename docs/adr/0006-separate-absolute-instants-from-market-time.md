# ADR 0006: Separate absolute instants from market time

- Status: Accepted
- Date: 2026-08-23

## Context

US-equity decisions are organized by NYSE trading dates and exchange-relative deadlines, while
evidence, lifecycle events, model inputs, broker observations, and machine telemetry must share one
unambiguous timeline. Without a canonical contract, an adapter can accept any aware offset and
preserve its spelling. Equivalent instants could therefore acquire different durable text, ordering,
or hash inputs as timestamp-bearing capabilities expand.

Making Eastern Time universal would align some operator views with the market but would mix schedule
meaning with machine chronology, inherit daylight-saving ambiguity, and conflict with asset-class-owned
clocks. Making every temporal value UTC would instead erase the NYSE-date meaning of a `MarketSession`
and invite synthetic midnight instants.

## Decision

Represent an Absolute Instant inside deterministic and durable contracts as a domain-owned
`UtcInstant`. Normalize aware boundary datetimes to UTC before deterministic use. Serialize durable
instants as fixed-width ISO 8601 text with six fractional digits and the `+00:00` offset; this defines
microsecond precision. Durable reads accept only that canonical spelling and fail closed on naive,
malformed, over-precise, non-UTC, or otherwise noncanonical values. They never normalize or rewrite
existing authoritative rows. Under ADR 0004, incompatible pre-deployment runtime state remains
disposable rather than receiving a migration framework.

Keep calendar semantics with the capability that owns them. A `MarketSession` remains an NYSE trading
date. US-equity scheduling interprets local rules through the NYSE calendar and
`America/New_York`, then converts resolved deadlines and cutoffs to `UtcInstant`. Provider-original
timestamp text or offsets remain separate provenance only when they are material to reconstruction.

Machine logs, metrics, and traces use the same canonical UTC instant representation. Operator trading
views may derive Eastern Time and important alerts may show both, but display values and the host
timezone never become authoritative. Timeout and latency measurement uses monotonic elapsed time
rather than wall-clock arithmetic. The canonical instant and exchange-calendar rules are safety
invariants, not deployment-varying timezone configuration.

## Alternatives considered

- Store every temporal value in Eastern Time. Rejected because daylight-saving rules, non-equity
  clocks, provider chronology, and host-local operation would enter a supposedly shared timeline.
- Preserve any aware offset as durable text. Rejected because equivalent instants would remain
  representationally different in comparisons, hashes, diagnostics, and replay artifacts.
- Convert Market Sessions and source dates to UTC midnight. Rejected because a trading date is a
  calendar identity rather than an instant, and the conversion would manufacture precision the source
  does not provide.
- Add a project-wide timezone setting. Rejected because canonical chronology and asset-owned market
  calendars are fixed invariants rather than operator policy.

## Consequences

- Absolute instants have one comparison, ordering, hashing, and durable-reconstruction form across
  capabilities and process boundaries.
- Adapters may accept an aware provider offset but must construct the normalized value before handing
  it inward; raw source spelling remains separate when provenance requires it.
- Noncanonical current-schema lifecycle rows fail closed. Pre-deployment operators recreate disposable
  state rather than silently rewriting append-only history.
- Scheduler and presentation code must make conversion explicit and test daylight-saving transitions,
  holidays, early closes, and host-zone independence.
- This value contract is shared infrastructure, not a generic scheduling or time framework.
