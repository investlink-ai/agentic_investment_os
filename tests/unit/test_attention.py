from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentic_investment_os.domain.attention import (
    AdapterQuotaDisposition,
    AttentionArtifact,
    AttentionFeature,
    AttentionInputs,
    AttentionPolicy,
    AttentionState,
    AttentionSubjectInput,
    AttentionTransitionExitReason,
    CandidateCard,
    CandidateDisposition,
    CandidateReason,
    DossierRequest,
    DossierSelectionKind,
    HoldingRefresh,
    HoldingRefreshDisposition,
    InvalidAttentionError,
    attention_history_fingerprint,
    parse_attention_artifact,
    select_attention,
)
from agentic_investment_os.domain.identity import (
    EquityInstrumentIdentity,
    InstrumentAlias,
    MarketSession,
)
from agentic_investment_os.domain.temporal import UtcInstant
from agentic_investment_os.domain.universe import (
    PositionDisposition,
    UniverseExclusionReason,
    UniverseSubject,
)

_RUN_ID = "1" * 64
_SNAPSHOT_ID = "2" * 64
_EVIDENCE_POLICY_ID = "3" * 64
_MARKET_ARTIFACT_ID = "4" * 64
_NEWS_ARTIFACT_ID = "5" * 64
_CUTOFF = UtcInstant.parse("2026-08-24T20:00:00.000000+00:00")
_MAXIMUM_CANDIDATE_CARDS = 20
_MAXIMUM_NEW_DOSSIERS = 5
_EXPECTED_HOLDING_REFRESHES = 2
_EXPECTED_PRIORITY_DOSSIERS = 4


def _set_path(root: dict[str, object], path: tuple[object, ...], value: object) -> None:
    current: object = root
    for part in path[:-1]:
        if isinstance(part, int):
            assert isinstance(current, list)
            current = current[part]
        else:
            assert isinstance(part, str)
            assert isinstance(current, dict)
            current = current[part]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(current, list)
        current[final] = value
    else:
        assert isinstance(final, str)
        assert isinstance(current, dict)
        current[final] = value


def _policy() -> AttentionPolicy:
    parsed = AttentionPolicy.parse(
        {
            "schema_version": 1,
            "policy_type": "v0_attention",
            "candidate_card_limit": 20,
            "new_dossier_limit": 5,
            "weekly_dossier_budget": 10,
            "weekly_exploration_budget": 1,
            "exploration_seed": "baseline-attention-v1",
        }
    )
    assert isinstance(parsed, AttentionPolicy)
    return parsed


def _subject(index: int, *, holding: bool = False) -> AttentionSubjectInput:
    catalog_id = f"equity-{index:03d}"
    identity = EquityInstrumentIdentity("alpaca-paper", catalog_id, "NASDAQ")
    universe_subject = UniverseSubject(
        identity=identity,
        aliases=(InstrumentAlias("alpaca", f"S{index:03d}"),),
        is_position=holding,
        eligible_for_new_entry=not holding,
        position_disposition=(
            PositionDisposition.REFRESH_REQUIRED if holding else PositionDisposition.NOT_APPLICABLE
        ),
        exclusion_reasons=(),
    )
    observed = (
        (AttentionFeature.CURRENT_HOLDING,)
        if holding
        else (
            AttentionFeature.FRESHNESS,
            AttentionFeature.NEWS_ARRIVAL,
        )
    )
    evidence_ids = (_NEWS_ARTIFACT_ID,) if not holding else ()
    return AttentionSubjectInput(
        subject=universe_subject,
        observed_features=observed,
        missing_features=(
            AttentionFeature.PRICE_CHANGE,
            AttentionFeature.LIQUIDITY_CHANGE,
            AttentionFeature.GAP,
            AttentionFeature.UNUSUAL_VOLUME,
            AttentionFeature.KNOWN_EVENT_PROXIMITY,
            AttentionFeature.EXISTING_BELIEF,
            AttentionFeature.THESIS_EXPIRY,
        ),
        evidence_artifact_ids=evidence_ids,
    )


def _inputs(
    cycle: date,
    subjects: tuple[AttentionSubjectInput, ...],
) -> AttentionInputs:
    return AttentionInputs(
        run_id=_RUN_ID,
        cycle=MarketSession(cycle),
        universe_snapshot_id=_SNAPSHOT_ID,
        cutoff=_CUTOFF,
        data_regime="alpaca-basic-iex-v1",
        evidence_policy_id=_EVIDENCE_POLICY_ID,
        evidence_artifact_ids=(_MARKET_ARTIFACT_ID, _NEWS_ARTIFACT_ID),
        subjects=subjects,
    )


def test_attention_policy_rejects_caps_or_exploration_outside_the_contract() -> None:
    valid = _policy().to_payload()

    for invalid in (
        {**valid, "candidate_card_limit": 21},
        {**valid, "new_dossier_limit": 6},
        {**valid, "weekly_exploration_budget": 0},
        {**valid, "weekly_exploration_budget": 3},
        {**valid, "schema_version": True},
        {**valid, "policy_type": "unknown"},
        {**valid, "exploration_seed": "invalid seed"},
        {**valid, "unexpected": True},
    ):
        assert AttentionPolicy.parse(invalid) is None

    with pytest.raises(InvalidAttentionError, match="invalid attention policy"):
        AttentionPolicy(21, 5, 10, 1, "baseline-attention-v1")


def test_attention_value_objects_reject_invalid_boundary_states() -> None:
    subject = _subject(1)
    inputs = _inputs(date(2026, 8, 24), (subject,))
    artifact = select_attention(_policy(), inputs, (), available_at=_CUTOFF)

    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        replace(
            subject,
            observed_features=(*subject.observed_features, AttentionFeature.FRESHNESS),
        )
    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        replace(subject, observed_features=(AttentionFeature.CURRENT_HOLDING,))
    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        replace(subject, evidence_artifact_ids=("bad",))
    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        replace(inputs, run_id="bad")
    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        replace(inputs, subjects=(subject, subject))
    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        DossierRequest("bad", "bad", subject.subject.identity, DossierSelectionKind.PRIORITY)
    holding = _subject(100, holding=True)
    refresh = HoldingRefresh.create(_RUN_ID, holding)
    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        replace(
            refresh,
            missing_features=(AttentionFeature.CURRENT_HOLDING,),
        )
    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        replace(artifact.resource_accounting, model_tokens=1)


def test_cards_validate_later_research_and_every_terminal_exit_shape() -> None:
    subject = _subject(1)
    feature_reasons = (
        CandidateReason.FRESH_EVIDENCE,
        CandidateReason.NEWS_ARRIVAL,
    )
    already_researching = CandidateCard.create(
        run_id=_RUN_ID,
        identity=subject.subject.identity,
        previous_state=AttentionState.DOSSIER,
        next_state=AttentionState.DOSSIER,
        disposition=CandidateDisposition.ALREADY_IN_RESEARCH,
        observed_features=subject.observed_features,
        reasons=(*feature_reasons, CandidateReason.ALREADY_IN_RESEARCH),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
    )
    assert already_researching.next_state is AttentionState.DOSSIER

    exits = {
        AttentionState.REJECTED: AttentionTransitionExitReason.INELIGIBLE,
        AttentionState.REFUTED: AttentionTransitionExitReason.REFUTED_BY_EVIDENCE,
        AttentionState.DORMANT: AttentionTransitionExitReason.DORMANT_WITHOUT_NEW_EVIDENCE,
        AttentionState.ARCHIVED: AttentionTransitionExitReason.ARCHIVED_AS_SUPERSEDED,
    }
    for state, exit_reason in exits.items():
        card = CandidateCard.create(
            run_id=_RUN_ID,
            identity=subject.subject.identity,
            previous_state=AttentionState.CANDIDATE,
            next_state=state,
            disposition=CandidateDisposition.EXITED_ACTIVE_PATH,
            observed_features=subject.observed_features,
            reasons=(*feature_reasons, CandidateReason.TERMINAL_STATE),
            evidence_artifact_ids=subject.evidence_artifact_ids,
            missing_features=subject.missing_features,
            exit_reason=exit_reason,
        )
        assert card.exit_reason is exit_reason


def test_selection_is_bounded_order_independent_and_refreshes_every_holding() -> None:
    subjects = (
        *tuple(_subject(index) for index in range(30)),
        _subject(100, holding=True),
        _subject(101, holding=True),
    )

    first = select_attention(
        _policy(), _inputs(date(2026, 8, 24), subjects), (), available_at=_CUTOFF
    )
    reordered = select_attention(
        _policy(),
        _inputs(date(2026, 8, 24), tuple(reversed(subjects))),
        (),
        available_at=_CUTOFF,
    )

    assert first == reordered
    assert len(first.candidate_cards) == _MAXIMUM_CANDIDATE_CARDS
    assert not first.dossier_requests
    assert len(first.holding_refreshes) == _EXPECTED_HOLDING_REFRESHES
    assert all(
        refresh.disposition is HoldingRefreshDisposition.REQUIRED
        for refresh in first.holding_refreshes
    )
    assert all(card.previous_state is AttentionState.OBSERVED for card in first.candidate_cards)
    assert all(card.next_state is AttentionState.WATCH for card in first.candidate_cards)
    assert first.resource_accounting.model_tokens == 0
    assert first.resource_accounting.model_turns == 0
    assert (
        first.resource_accounting.adapter_quota_disposition is AdapterQuotaDisposition.NOT_CONSULTED
    )


@given(
    eligible_count=st.integers(min_value=5, max_value=50),
    holding_count=st.integers(min_value=0, max_value=15),
)
def test_generated_universes_preserve_caps_holding_refresh_and_exploration(
    eligible_count: int,
    holding_count: int,
) -> None:
    policy = _policy()
    subjects = (
        *tuple(_subject(index) for index in range(eligible_count)),
        *tuple(_subject(100 + index, holding=True) for index in range(holding_count)),
    )

    monday = select_attention(
        policy, _inputs(date(2026, 8, 24), subjects), (), available_at=_CUTOFF
    )
    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), subjects),
        (monday,),
        available_at=_CUTOFF,
    )
    wednesday = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
        available_at=_CUTOFF,
    )

    assert len(monday.candidate_cards) <= _MAXIMUM_CANDIDATE_CARDS
    assert len(monday.holding_refreshes) == holding_count
    assert len(wednesday.dossier_requests) <= _MAXIMUM_NEW_DOSSIERS
    assert (
        sum(
            request.selection_kind is DossierSelectionKind.EXPLORATION
            for request in wednesday.dossier_requests
        )
        == 1
    )


def test_subjects_advance_one_state_before_regular_and_exploration_dossiers() -> None:
    policy = _policy()
    subjects = tuple(_subject(index) for index in range(8))
    monday = select_attention(
        policy, _inputs(date(2026, 8, 24), subjects), (), available_at=_CUTOFF
    )
    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), subjects),
        (monday,),
        available_at=_CUTOFF,
    )
    wednesday = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
        available_at=_CUTOFF,
    )

    assert all(card.next_state is AttentionState.WATCH for card in monday.candidate_cards)
    assert all(card.next_state is AttentionState.CANDIDATE for card in tuesday.candidate_cards)
    assert len(wednesday.dossier_requests) == _MAXIMUM_NEW_DOSSIERS
    assert (
        sum(
            request.selection_kind is DossierSelectionKind.EXPLORATION
            for request in wednesday.dossier_requests
        )
        == 1
    )
    assert (
        sum(
            request.selection_kind is DossierSelectionKind.PRIORITY
            for request in wednesday.dossier_requests
        )
        == _EXPECTED_PRIORITY_DOSSIERS
    )
    requested_cards = {request.candidate_card_id for request in wednesday.dossier_requests}
    assert all(
        card.next_state is AttentionState.DOSSIER
        for card in wednesday.candidate_cards
        if card.card_id in requested_cards
    )
    assert all(
        card.disposition is CandidateDisposition.NEW_DOSSIER_REQUESTED
        for card in wednesday.candidate_cards
        if card.card_id in requested_cards
    )
    assert wednesday.resource_accounting.weekly_exploration_after == 1
    assert wednesday.resource_accounting.weekly_dossiers_after == _MAXIMUM_NEW_DOSSIERS

    thursday = select_attention(
        policy,
        _inputs(date(2026, 8, 27), subjects),
        (monday, tuesday, wednesday),
        available_at=_CUTOFF,
    )
    dossier_identities = {request.identity for request in wednesday.dossier_requests}
    assert dossier_identities.isdisjoint(card.identity for card in thursday.candidate_cards)
    assert all(card.next_state is not AttentionState.THESIS for card in thursday.candidate_cards)


def test_portfolio_mismatch_is_retained_as_an_explicit_holding_refresh() -> None:
    identity = EquityInstrumentIdentity("alpaca-paper", "equity-disabled", "NYSE")
    mismatch = AttentionSubjectInput(
        subject=UniverseSubject(
            identity=identity,
            aliases=(InstrumentAlias("alpaca", "DISABLED"),),
            is_position=True,
            eligible_for_new_entry=False,
            position_disposition=PositionDisposition.PORTFOLIO_MISMATCH,
            exclusion_reasons=(UniverseExclusionReason.UNSUPPORTED_ASSET_CLASS,),
        ),
        observed_features=(AttentionFeature.CURRENT_HOLDING,),
        missing_features=tuple(
            feature
            for feature in AttentionFeature
            if feature is not AttentionFeature.CURRENT_HOLDING
        ),
        evidence_artifact_ids=(),
    )

    artifact = select_attention(
        _policy(),
        _inputs(date(2026, 8, 24), (mismatch,)),
        (),
        available_at=_CUTOFF,
    )

    assert not artifact.candidate_cards
    assert not artifact.dossier_requests
    assert artifact.holding_refreshes[0].disposition is HoldingRefreshDisposition.PORTFOLIO_MISMATCH
    assert artifact.holding_refreshes[0].exclusion_reasons == (
        UniverseExclusionReason.UNSUPPORTED_ASSET_CLASS,
    )


def test_candidate_cards_reject_skipped_promotions_and_preserve_terminal_exit_reasons() -> None:
    subject = _subject(1)

    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        CandidateCard.create(
            run_id=_RUN_ID,
            identity=subject.subject.identity,
            previous_state=AttentionState.OBSERVED,
            next_state=AttentionState.THESIS,
            disposition=CandidateDisposition.ADVANCED_ONE_STATE,
            observed_features=subject.observed_features,
            reasons=(
                CandidateReason.FRESH_EVIDENCE,
                CandidateReason.FUNNEL_PROGRESSION,
                CandidateReason.NEWS_ARRIVAL,
            ),
            evidence_artifact_ids=subject.evidence_artifact_ids,
            missing_features=subject.missing_features,
        )

    rejected = CandidateCard.create(
        run_id=_RUN_ID,
        identity=subject.subject.identity,
        previous_state=AttentionState.CANDIDATE,
        next_state=AttentionState.REJECTED,
        disposition=CandidateDisposition.EXITED_ACTIVE_PATH,
        observed_features=subject.observed_features,
        reasons=(
            CandidateReason.FRESH_EVIDENCE,
            CandidateReason.NEWS_ARRIVAL,
            CandidateReason.TERMINAL_STATE,
        ),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
        exit_reason=AttentionTransitionExitReason.INELIGIBLE,
    )

    assert rejected.next_state is AttentionState.REJECTED
    assert rejected.exit_reason is AttentionTransitionExitReason.INELIGIBLE


def test_candidate_card_reasons_must_match_the_observed_features() -> None:
    subject = _subject(1)

    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        CandidateCard.create(
            run_id=_RUN_ID,
            identity=subject.subject.identity,
            previous_state=AttentionState.OBSERVED,
            next_state=AttentionState.WATCH,
            disposition=CandidateDisposition.ADVANCED_ONE_STATE,
            observed_features=subject.observed_features,
            reasons=(
                CandidateReason.FILING_ARRIVAL,
                CandidateReason.FUNNEL_PROGRESSION,
            ),
            evidence_artifact_ids=subject.evidence_artifact_ids,
            missing_features=subject.missing_features,
        )


def test_ineligible_watched_subject_gets_one_terminal_exit_and_cannot_reenter() -> None:
    policy = _policy()
    subject = _subject(1)
    monday = select_attention(
        policy,
        _inputs(date(2026, 8, 24), (subject,)),
        (),
        available_at=_CUTOFF,
    )
    ineligible = replace(
        subject,
        subject=replace(
            subject.subject,
            eligible_for_new_entry=False,
            exclusion_reasons=(UniverseExclusionReason.NOT_TRADABLE,),
        ),
    )

    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), (ineligible,)),
        (monday,),
        available_at=_CUTOFF,
    )
    wednesday = select_attention(
        policy,
        _inputs(date(2026, 8, 26), (subject,)),
        (monday, tuesday),
        available_at=_CUTOFF,
    )

    assert len(tuesday.candidate_cards) == 1
    exit_card = tuesday.candidate_cards[0]
    assert exit_card.previous_state is AttentionState.WATCH
    assert exit_card.next_state is AttentionState.REJECTED
    assert exit_card.exit_reason is AttentionTransitionExitReason.INELIGIBLE
    assert not wednesday.candidate_cards


def test_contradictory_transition_history_is_rejected_and_history_is_identity_material() -> None:
    policy = _policy()
    subject = _subject(1)
    monday_inputs = _inputs(date(2026, 8, 24), (subject,))
    tuesday_inputs = _inputs(date(2026, 8, 25), (subject,))
    monday = select_attention(policy, monday_inputs, (), available_at=_CUTOFF)
    tuesday = select_attention(
        policy,
        tuesday_inputs,
        (monday,),
        available_at=_CUTOFF,
    )
    contradictory_card = CandidateCard.create(
        run_id=_RUN_ID,
        identity=subject.subject.identity,
        previous_state=AttentionState.OBSERVED,
        next_state=AttentionState.WATCH,
        disposition=CandidateDisposition.ADVANCED_ONE_STATE,
        observed_features=subject.observed_features,
        reasons=(
            CandidateReason.FRESH_EVIDENCE,
            CandidateReason.FUNNEL_PROGRESSION,
            CandidateReason.NEWS_ARRIVAL,
        ),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
    )
    contradictory = AttentionArtifact.create(
        inputs=tuesday_inputs,
        policy=policy,
        available_at=_CUTOFF,
        history_fingerprint=attention_history_fingerprint((monday,)),
        candidate_cards=(contradictory_card,),
        dossier_requests=(),
        holding_refreshes=(),
        resource_accounting=tuesday.resource_accounting,
    )

    assert tuesday.history_fingerprint == attention_history_fingerprint((monday,))
    with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
        select_attention(
            policy,
            _inputs(date(2026, 8, 26), (subject,)),
            (monday, contradictory),
            available_at=_CUTOFF,
        )


def test_artifact_creation_enforces_the_policy_preimage_and_truthful_availability() -> None:
    subjects = (_subject(1), _subject(2))
    inputs = _inputs(date(2026, 8, 24), subjects)
    artifact = select_attention(_policy(), inputs, (), available_at=_CUTOFF)
    smaller_policy = AttentionPolicy.parse(
        {
            **_policy().to_payload(),
            "candidate_card_limit": 1,
            "new_dossier_limit": 1,
        }
    )
    assert smaller_policy is not None

    assert artifact.available_at == _CUTOFF
    assert artifact.attention_policy == _policy()
    assert not artifact.matches_inputs(inputs, smaller_policy)
    with pytest.raises(InvalidAttentionError, match="invalid attention artifact"):
        AttentionArtifact.create(
            inputs=inputs,
            policy=smaller_policy,
            available_at=_CUTOFF,
            history_fingerprint=attention_history_fingerprint(()),
            candidate_cards=artifact.candidate_cards,
            dossier_requests=artifact.dossier_requests,
            holding_refreshes=artifact.holding_refreshes,
            resource_accounting=artifact.resource_accounting,
        )


def test_publication_time_changes_content_but_not_selection_identity() -> None:
    inputs = _inputs(date(2026, 8, 24), (_subject(1), _subject(2)))
    first = select_attention(_policy(), inputs, (), available_at=_CUTOFF)
    later = select_attention(
        _policy(),
        inputs,
        (),
        available_at=UtcInstant(_CUTOFF.value + timedelta(seconds=1)),
    )

    assert later.artifact_id == first.artifact_id
    assert later.content_hash != first.content_hash
    assert later.available_at != first.available_at

    next_inputs = _inputs(date(2026, 8, 25), inputs.subjects)
    after_first = select_attention(_policy(), next_inputs, (first,), available_at=_CUTOFF)
    after_later = select_attention(_policy(), next_inputs, (later,), available_at=_CUTOFF)

    assert after_later.history_fingerprint == after_first.history_fingerprint
    assert after_later.artifact_id == after_first.artifact_id
    assert after_later.candidate_cards == after_first.candidate_cards
    assert after_later.resource_accounting == after_first.resource_accounting


def test_selection_rejects_duplicate_or_future_history_before_publishing() -> None:
    subjects = tuple(_subject(index) for index in range(5))
    monday = select_attention(
        _policy(), _inputs(date(2026, 8, 24), subjects), (), available_at=_CUTOFF
    )
    future = select_attention(
        _policy(),
        _inputs(date(2026, 8, 27), subjects),
        (monday,),
        available_at=_CUTOFF,
    )

    for history in ((monday, monday), (monday, future)):
        with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
            select_attention(
                _policy(),
                _inputs(date(2026, 8, 26), subjects),
                history,
                available_at=_CUTOFF,
            )


def test_attention_artifact_round_trips_and_rejects_prohibited_or_changed_content() -> None:
    subjects = tuple(_subject(index) for index in range(4))
    first = select_attention(
        _policy(), _inputs(date(2026, 8, 24), subjects), (), available_at=_CUTOFF
    )
    second = select_attention(
        _policy(),
        _inputs(date(2026, 8, 25), subjects),
        (first,),
        available_at=_CUTOFF,
    )
    artifact = select_attention(
        _policy(),
        _inputs(date(2026, 8, 26), subjects),
        (first, second),
        available_at=_CUTOFF,
    )

    payload = artifact.to_payload()
    assert parse_attention_artifact(payload) == artifact
    serialized = str(payload).lower()
    assert "weight" not in serialized
    assert "order" not in serialized
    assert "execution" not in serialized

    changed = {**payload, "content_hash": "f" * 64}
    assert parse_attention_artifact(changed) is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("envelope_schema_version",), True),
        (("payload_schema_version",), 2),
        (("record_kind",), "unknown"),
        (("payload_discriminator",), "unknown"),
        (("authority_scope",), "portfolio"),
        (("cycle",), {}),
        (("relevant_at",), "not-an-instant"),
        (("available_at",), "2026-08-24T19:59:59.000000+00:00"),
        (("data_regime",), "invalid regime"),
        (("artifact_id",), "not-a-hash"),
        (("content_hash",), "not-a-hash"),
        (("material_fingerprints", "universe_snapshot"), "not-a-hash"),
        (("material_fingerprints", "attention_history"), "not-a-hash"),
        (("payload", "run_id"), "not-a-hash"),
        (("payload", "attention_policy"), {}),
        (("payload", "evidence_artifact_ids"), ["not-a-hash"]),
        (("payload", "week"), True),
        (("payload", "disposition"), "unknown"),
        (("payload", "no_action_reason"), "unknown"),
        (("payload", "candidate_cards"), {}),
        (("payload", "candidate_cards", 0), {}),
        (("payload", "candidate_cards", 0, "identity"), {}),
        (("payload", "candidate_cards", 0, "previous_state"), "Unknown"),
        (("payload", "candidate_cards", 0, "next_state"), "Unknown"),
        (("payload", "candidate_cards", 0, "disposition"), "unknown"),
        (("payload", "candidate_cards", 0, "observed_features"), ["unknown"]),
        (("payload", "candidate_cards", 0, "observed_features"), {}),
        (
            ("payload", "candidate_cards", 0, "observed_features"),
            ["freshness", "freshness"],
        ),
        (("payload", "candidate_cards", 0, "reasons"), ["unknown"]),
        (("payload", "candidate_cards", 0, "evidence_artifact_ids"), ["bad"]),
        (("payload", "candidate_cards", 0, "missing_features"), ["unknown"]),
        (("payload", "candidate_cards", 0, "exit_reason"), "unknown"),
        (("payload", "candidate_cards", 0, "card_id"), "f" * 64),
        (("payload", "dossier_requests"), {}),
        (("payload", "dossier_requests", 0), {}),
        (("payload", "dossier_requests", 0, "candidate_card_id"), True),
        (("payload", "dossier_requests", 0, "identity"), {}),
        (("payload", "dossier_requests", 0, "selection_kind"), "unknown"),
        (("payload", "dossier_requests", 0, "request_id"), "f" * 64),
        (("payload", "holding_refreshes"), {}),
        (("payload", "holding_refreshes", 0), {}),
        (("payload", "holding_refreshes", 0, "identity"), {}),
        (("payload", "holding_refreshes", 0, "disposition"), "unknown"),
        (("payload", "holding_refreshes", 0, "exclusion_reasons"), ["unknown"]),
        (("payload", "holding_refreshes", 0, "evidence_artifact_ids"), ["bad"]),
        (("payload", "holding_refreshes", 0, "missing_features"), ["unknown"]),
        (("payload", "holding_refreshes", 0, "refresh_id"), "f" * 64),
        (("payload", "resource_accounting"), {}),
        (("payload", "resource_accounting", "candidate_card_count"), True),
        (("payload", "resource_accounting", "candidate_card_count"), -1),
        (("payload", "resource_accounting", "adapter_quota_disposition"), "consulted"),
        (("payload", "resource_accounting", "adapter_quota_disposition"), True),
    ],
)
def test_attention_artifact_parser_rejects_each_hostile_boundary_shape(
    path: tuple[object, ...],
    value: object,
) -> None:
    policy = _policy()
    subjects = (*tuple(_subject(index) for index in range(8)), _subject(100, holding=True))
    monday = select_attention(
        policy,
        _inputs(date(2026, 8, 24), subjects),
        (),
        available_at=_CUTOFF,
    )
    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), subjects),
        (monday,),
        available_at=_CUTOFF,
    )
    artifact = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
        available_at=_CUTOFF,
    )
    payload = deepcopy(artifact.to_payload())
    _set_path(payload, path, value)

    assert parse_attention_artifact(payload) is None


def test_attention_artifact_parser_rejects_non_mappings_extra_fields_and_unsorted_outputs() -> None:
    subjects = tuple(_subject(index) for index in range(8))
    policy = _policy()
    monday = select_attention(
        policy,
        _inputs(date(2026, 8, 24), subjects),
        (),
        available_at=_CUTOFF,
    )
    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), subjects),
        (monday,),
        available_at=_CUTOFF,
    )
    artifact = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
        available_at=_CUTOFF,
    )
    payload = artifact.to_payload()
    extra_root = {**payload, "unexpected": True}
    extra_fingerprints = deepcopy(payload)
    fingerprints = extra_fingerprints["material_fingerprints"]
    assert isinstance(fingerprints, dict)
    fingerprints["unexpected"] = "f" * 64
    reversed_cards = deepcopy(payload)
    card_payload = reversed_cards["payload"]
    assert isinstance(card_payload, dict)
    cards = card_payload["candidate_cards"]
    requests = card_payload["dossier_requests"]
    assert isinstance(cards, list)
    assert isinstance(requests, list)
    cards.reverse()
    requests.reverse()
    reversed_requests = deepcopy(payload)
    request_payload = reversed_requests["payload"]
    assert isinstance(request_payload, dict)
    request_items = request_payload["dossier_requests"]
    assert isinstance(request_items, list)
    request_items.reverse()
    duplicate_evidence = deepcopy(payload)
    evidence_payload = duplicate_evidence["payload"]
    assert isinstance(evidence_payload, dict)
    evidence_ids = evidence_payload["evidence_artifact_ids"]
    assert isinstance(evidence_ids, list)
    evidence_ids.append(evidence_ids[0])

    assert parse_attention_artifact([]) is None
    assert parse_attention_artifact(extra_root) is None
    assert parse_attention_artifact(extra_fingerprints) is None
    assert parse_attention_artifact(reversed_cards) is None
    assert parse_attention_artifact(reversed_requests) is None
    assert parse_attention_artifact(duplicate_evidence) is None
