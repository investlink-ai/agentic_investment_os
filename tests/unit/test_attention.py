from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentic_investment_os.domain.attention import (
    AdapterQuotaDisposition,
    AttentionFeature,
    AttentionInputs,
    AttentionPolicy,
    AttentionState,
    AttentionSubjectInput,
    AttentionTransitionExitReason,
    CandidateCard,
    CandidateDisposition,
    CandidateReason,
    DossierSelectionKind,
    HoldingRefreshDisposition,
    InvalidAttentionError,
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
        {**valid, "unexpected": True},
    ):
        assert AttentionPolicy.parse(invalid) is None


def test_selection_is_bounded_order_independent_and_refreshes_every_holding() -> None:
    subjects = (
        *tuple(_subject(index) for index in range(30)),
        _subject(100, holding=True),
        _subject(101, holding=True),
    )

    first = select_attention(_policy(), _inputs(date(2026, 8, 24), subjects), ())
    reordered = select_attention(
        _policy(),
        _inputs(date(2026, 8, 24), tuple(reversed(subjects))),
        (),
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

    monday = select_attention(policy, _inputs(date(2026, 8, 24), subjects), ())
    tuesday = select_attention(policy, _inputs(date(2026, 8, 25), subjects), (monday,))
    wednesday = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
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
    monday = select_attention(policy, _inputs(date(2026, 8, 24), subjects), ())
    tuesday = select_attention(
        policy,
        _inputs(date(2026, 8, 25), subjects),
        (monday,),
    )
    wednesday = select_attention(
        policy,
        _inputs(date(2026, 8, 26), subjects),
        (monday, tuesday),
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
            reasons=(CandidateReason.FUNNEL_PROGRESSION,),
            evidence_artifact_ids=subject.evidence_artifact_ids,
            missing_features=subject.missing_features,
        )

    rejected = CandidateCard.create(
        run_id=_RUN_ID,
        identity=subject.subject.identity,
        previous_state=AttentionState.CANDIDATE,
        next_state=AttentionState.REJECTED,
        disposition=CandidateDisposition.EXITED_ACTIVE_PATH,
        reasons=(CandidateReason.TERMINAL_STATE,),
        evidence_artifact_ids=subject.evidence_artifact_ids,
        missing_features=subject.missing_features,
        exit_reason=AttentionTransitionExitReason.INELIGIBLE,
    )

    assert rejected.next_state is AttentionState.REJECTED
    assert rejected.exit_reason is AttentionTransitionExitReason.INELIGIBLE


def test_selection_rejects_duplicate_or_future_history_before_publishing() -> None:
    subjects = tuple(_subject(index) for index in range(5))
    monday = select_attention(_policy(), _inputs(date(2026, 8, 24), subjects), ())
    future = select_attention(
        _policy(),
        _inputs(date(2026, 8, 27), subjects),
        (monday,),
    )

    for history in ((monday, monday), (monday, future)):
        with pytest.raises(InvalidAttentionError, match="invalid attention inputs"):
            select_attention(
                _policy(),
                _inputs(date(2026, 8, 26), subjects),
                history,
            )


def test_attention_artifact_round_trips_and_rejects_prohibited_or_changed_content() -> None:
    subjects = tuple(_subject(index) for index in range(4))
    first = select_attention(_policy(), _inputs(date(2026, 8, 24), subjects), ())
    second = select_attention(
        _policy(),
        _inputs(date(2026, 8, 25), subjects),
        (first,),
    )
    artifact = select_attention(
        _policy(),
        _inputs(date(2026, 8, 26), subjects),
        (first, second),
    )

    payload = artifact.to_payload()
    assert parse_attention_artifact(payload) == artifact
    serialized = str(payload).lower()
    assert "weight" not in serialized
    assert "order" not in serialized
    assert "execution" not in serialized

    changed = {**payload, "content_hash": "f" * 64}
    assert parse_attention_artifact(changed) is None
