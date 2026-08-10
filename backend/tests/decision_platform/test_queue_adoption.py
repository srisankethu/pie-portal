"""Does anybody act on the queue — the adoption half of `decisions/outcomes`.

The failures worth pinning here are all the same shape: a number that looks
like an answer when it is really an absence. A category nobody has ruled on
must not report 0% acceptance, a small sample must not report a band, and a
window spanning the capture cut-over must say so rather than averaging two
definitions of `ACTIONED` into one figure.

The arithmetic itself is addition and division and is not worth a test each.
"""
from __future__ import annotations

from itertools import count
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.decisions import outcomes as adoption
from app.domain import models
from app.repositories import DecisionRepository
from app.config import settings
from app.domain.enums import DecisionStatus, HumanAction

ORG = "org_test"
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
#: `id(object())` is not unique — CPython reuses the address of a temporary
#: as soon as it is collected, so two helper calls can mint the same key and
#: the unique index rejects the second. A counter cannot collide.
_KEYS = count()


def _trail(*entries: tuple[str, datetime]) -> dict:
    rows = [{"action": a, "acted_at": t.isoformat(), "actor_user_id": "u1"}
            for a, t in entries]
    return {**rows[-1], "trail": rows}


def _d(status: str, *, dtype: str = "CUSTOMER_DECLINE", user: str = "u1",
       role: str = "SALESPERSON", detected: datetime = NOW,
       trail: dict | None = None, ai: str = "OK", origin: str = "SIGNAL",
       financial: str | None = None) -> models.Decision:
    return models.Decision(
        organization_id=ORG, decision_key=f"dk_{next(_KEYS)}", decision_type=dtype,
        subject_entity_type="CUSTOMER", subject_entity_id="c1",
        assigned_user_id=user, assigned_role=role, detected_at=detected,
        status=status, origin=origin, human_action=trail,
        ai={"status": ai}, impact={"financial": financial} if financial else {})


# ── absence is not a rate ────────────────────────────────────────────────────
def test_a_category_nobody_ruled_on_has_no_acceptance_rate():
    """Not 0.0 — that reads as "everybody rejects it", which is the opposite."""
    out = adoption._tally([_d(DecisionStatus.OPEN.value) for _ in range(5)])

    assert out["ruled"] == 0
    assert out["acceptance_rate"] is None
    assert out["band"] == "INSUFFICIENT_DATA"
    assert out["untouched"] == 5


def test_a_small_sample_draws_no_inference_in_either_direction():
    rows = [_d(DecisionStatus.ACTIONED.value) for _ in range(settings.SIGNAL_OUTCOME_MIN_SAMPLE - 1)]

    band, note = adoption.acceptance_band(1.0, len(rows))

    assert band == "INSUFFICIENT_DATA"
    assert str(settings.SIGNAL_OUTCOME_MIN_SAMPLE) in note


def test_the_band_is_two_sided():
    """A queue accepted almost every time is a finding, not a success."""
    assert adoption.acceptance_band(0.05, 100)[0] == "LOW"
    assert adoption.acceptance_band(0.50, 100)[0] == "OK"
    assert adoption.acceptance_band(0.99, 100)[0] == "SUSPICIOUSLY_HIGH"


def test_a_detector_driven_close_is_not_counted_as_a_human_ruling():
    """EXPIRED and SUPERSEDED are the passage of time, not a judgement."""
    rows = [_d(DecisionStatus.ACTIONED.value),
            _d(DecisionStatus.EXPIRED.value),
            _d(DecisionStatus.SUPERSEDED.value)]

    out = adoption._tally(rows)

    assert out["ruled"] == 1
    assert out["acceptance_rate"] == 1.0
    assert out["closed_without_a_human"] == 2


# ── the two denominators are different questions ─────────────────────────────
def test_untouched_decisions_leave_acceptance_alone_but_sink_engagement():
    """A queue nobody opens and a detector nobody agrees with are not the same
    failure, and one number cannot distinguish them."""
    rows = ([_d(DecisionStatus.ACTIONED.value) for _ in range(2)]
            + [_d(DecisionStatus.OPEN.value) for _ in range(8)])

    out = adoption._tally(rows)

    assert out["acceptance_rate"] == 1.0     # of those ruled on, all accepted
    assert out["engagement_rate"] == 0.2     # of those raised, most ignored


# ── the cut-over is derived from evidence, not assumed away ──────────────────
def test_the_cutover_is_unknown_rather_than_absent_when_nothing_proves_it():
    rows = [_d(DecisionStatus.ACTIONED.value,
               trail=_trail((HumanAction.ACT.value, NOW)))]

    assert adoption.semantics_cutover(rows) is None


def test_the_earliest_view_dates_the_capture_fix():
    """No client sent VIEW before the fix, so the first one is a lower bound."""
    early, late = NOW - timedelta(days=3), NOW
    rows = [
        _d(DecisionStatus.ACTIONED.value, trail=_trail((HumanAction.ACT.value, NOW))),
        _d(DecisionStatus.VIEWED.value, trail=_trail((HumanAction.VIEW.value, late))),
        _d(DecisionStatus.OVERRIDDEN.value,
           trail=_trail((HumanAction.VIEW.value, early),
                        (HumanAction.OVERRIDE.value, late))),
    ]

    assert adoption.semantics_cutover(rows) == early.isoformat()


# ── modify distance keeps its sign ───────────────────────────────────────────
def _q(price: str | None, target: str | None, *, overridden: bool = False,
       gated: bool = True) -> models.QuoteDecision:
    refs = [{"code": adoption.TARGET_MARGIN_PRICE, "value": float(target)}] if target else []
    return models.QuoteDecision(
        organization_id=ORG, quote_id="q1", quote_line_id="l1", quantity=Decimal(1),
        quoted_unit_price=Decimal(price) if price else None, references=refs,
        requires_approval=gated, overridden=overridden, as_of=NOW.date())


def test_a_line_the_policy_could_not_price_is_excluded_rather_than_read_as_agreement():
    out = adoption.override_distance([_q("100", None), _q("90", "100")])

    d = out["distance_from_target_margin_price"]
    assert d["lines_measured"] == 1
    assert d["lines_without_a_target_reference"] == 1


def test_pricing_under_and_over_the_target_are_told_apart():
    """A mean absolute distance reports discounting hard and holding firm as the
    same finding."""
    out = adoption.override_distance([_q("90", "100"), _q("80", "100"), _q("110", "100")])

    d = out["distance_from_target_margin_price"]
    assert d["median"] < 0
    assert d["share_below_target"] == round(2 / 3, 4)
    assert d["share_above_target"] == round(1 / 3, 4)


def test_the_override_rate_is_over_the_lines_a_rule_actually_fired_on():
    out = adoption.override_distance(
        [_q("90", "100", overridden=True, gated=True), _q("90", "100", gated=False)])

    assert out["lines_priced"] == 2
    assert out["lines_a_rule_fired_on"] == 1
    assert out["override_rate"] == 1.0


# ── number 3 ─────────────────────────────────────────────────────────────────
def test_acceptance_is_reported_against_the_depth_the_queue_was_at():
    """The shape is the finding: a rate that falls as depth rises is the ceiling."""
    shallow = [_d(DecisionStatus.ACTIONED.value, detected=NOW - timedelta(days=10),
                  trail=_trail((HumanAction.ACT.value, NOW - timedelta(days=9))))]
    # Twenty open decisions detected before a ruling put the queue deep.
    deep_bg = [_d(DecisionStatus.OPEN.value, detected=NOW - timedelta(days=2))
               for _ in range(20)]
    deep = [_d(DecisionStatus.DISMISSED.value, detected=NOW - timedelta(days=2),
               trail=_trail((HumanAction.DISMISS.value, NOW)))]

    out = adoption.acceptance_by_volume(shallow + deep_bg + deep)

    assert out["buckets"]["1-5"]["acceptance_rate"] == 1.0
    assert out["buckets"]["16-40"]["acceptance_rate"] == 0.0
    assert "approximate" in out["note"]


def test_a_ruling_with_no_usable_timestamp_is_refused_rather_than_placed():
    out = adoption.acceptance_by_volume([_d(DecisionStatus.ACTIONED.value)])

    assert out["buckets"] == {}
    assert "no ruling" in out["note"].lower()


# ── value at risk ────────────────────────────────────────────────────────────
def test_dismissed_exposure_is_summed_from_what_the_decision_was_worth():
    rows = [_d(DecisionStatus.DISMISSED.value, financial="400000", origin="STATE"),
            _d(DecisionStatus.ACTIONED.value, financial="100000", origin="STATE"),
            _d(DecisionStatus.OPEN.value)]  # a signal decision carries no figure

    out = adoption._value(rows)

    assert out["decisions_carrying_a_figure"] == 2
    assert out["dismissed"] == "400000"
    assert out["raised"] == "500000"


def test_an_unparseable_impact_figure_is_dropped_rather_than_coerced():
    out = adoption._value([_d(DecisionStatus.DISMISSED.value, financial="not a number")])

    assert out["decisions_carrying_a_figure"] == 0
    assert out["dismissed"] == "0"


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_queue_metrics_is_owner_only(api_client):
    """It reads across every role's queue and carries cost-derived figures."""
    def token(email: str) -> str:
        return api_client.post("/api/v1/auth/login", json={
            "email": email, "password": "change-me-now"}).json()["token"]

    for email, expected in (("r.nair@sanketh.in", 403),
                            ("m.rao@sanketh.in", 403),
                            ("s.menon@sanketh.in", 200)):
        r = api_client.get("/api/v1/internal/queue-adoption",
                           headers={"Authorization": f"Bearer {token(email)}"})
        assert r.status_code == expected, f"{email}: {r.status_code}"


def test_queue_metrics_reports_both_windows_on_an_empty_book(api_client):
    """Empty is a legitimate answer and must not be an error or a zero rate."""
    token = api_client.post("/api/v1/auth/login", json={
        "email": "s.menon@sanketh.in", "password": "change-me-now"}).json()["token"]

    body = api_client.get("/api/v1/internal/queue-adoption",
                          headers={"Authorization": f"Bearer {token}"}).json()

    assert set(body["windows"]) == {"7d", "30d"}
    week = body["windows"]["7d"]
    assert week["decisions_raised"] == 0
    assert week["overall"]["acceptance_rate"] is None
    assert week["overall"]["band"] == "INSUFFICIENT_DATA"
    assert week["semantics_cutover"] is None


def test_the_report_runs_against_stored_rows_not_just_constructed_ones(session):
    """The timestamps have to survive a round trip through the database.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column comes
    back *naive* while every timestamp parsed out of the action trail carries an
    offset. Comparing the two raises `TypeError`, and the whole report 500s on
    real data while passing against objects built in memory — which is how this
    module shipped its first bug. `clock.aware` is the fix and this is the test
    that would have caught it.
    """
    ruled = _d(DecisionStatus.ACTIONED.value,
               detected=NOW - timedelta(days=2),
               trail=_trail((HumanAction.VIEW.value, NOW - timedelta(days=1)),
                            (HumanAction.ACT.value, NOW)))
    session.add_all([ruled, _d(DecisionStatus.OPEN.value, detected=NOW - timedelta(days=3))])
    session.commit()

    out = adoption.adoption_report(session, DecisionRepository(session, ORG),
                                  windows=(30,), now=NOW + timedelta(hours=1))

    week = out["windows"]["30d"]
    assert week["decisions_raised"] == 2
    assert week["overall"]["accepted"] == 1
    # The depth reconstruction is the part that compares the two clocks.
    assert week["acceptance_by_queue_volume"]["buckets"]["1-5"]["ruled"] == 1
    assert week["semantics_cutover"] == (NOW - timedelta(days=1)).isoformat()


def test_the_tally_parts_sum_to_what_was_raised():
    """A denominator a reader cannot decompose is one they have to trust."""
    rows = [_d(DecisionStatus.ACTIONED.value), _d(DecisionStatus.OVERRIDDEN.value),
            _d(DecisionStatus.DISMISSED.value), _d(DecisionStatus.ESCALATED.value),
            _d(DecisionStatus.OPEN.value), _d(DecisionStatus.VIEWED.value),
            _d(DecisionStatus.EXPIRED.value)]

    out = adoption._tally(rows)

    parts = ("accepted", "modified", "dismissed", "escalated",
             "untouched", "viewed_not_ruled", "closed_without_a_human")
    assert sum(out[k] for k in parts) == out["raised"] == 7
    assert out["ruled"] == 3          # accepted + modified + dismissed

def test_every_rupee_raised_lands_in_exactly_one_outcome_bucket():
    """The parts must reconcile against the total, or the report drops money
    silently — which is how an escalated decision's exposure went missing."""
    rows = [_d(DecisionStatus.ACTIONED.value, financial="10"),
            _d(DecisionStatus.OVERRIDDEN.value, financial="20"),
            _d(DecisionStatus.DISMISSED.value, financial="30"),
            _d(DecisionStatus.ESCALATED.value, financial="40"),
            _d(DecisionStatus.OPEN.value, financial="50"),
            _d(DecisionStatus.EXPIRED.value, financial="60")]

    out = adoption._value(rows)

    parts = ("accepted", "modified", "dismissed", "escalated",
             "untouched", "closed_without_a_human")
    assert sum(Decimal(out[k]) for k in parts) == Decimal(out["raised"]) == Decimal(210)


def test_the_volume_buckets_use_the_same_denominator_as_the_headline_rate():
    """Two acceptance rates in one payload, over different denominators, is a
    payload that argues with itself. Escalation is a handoff, not a verdict —
    excluded from both, while still counting toward the depth somebody faced."""
    accepted = _d(DecisionStatus.ACTIONED.value,
                  trail=_trail((HumanAction.ACT.value, NOW)))
    escalated = _d(DecisionStatus.ESCALATED.value,
                   trail=_trail((HumanAction.ESCALATE.value, NOW)))

    out = adoption.acceptance_by_volume([accepted, escalated])
    bucket = next(iter(out["buckets"].values()))

    assert bucket["ruled"] == 1                      # not 2
    assert bucket["acceptance_rate"] == 1.0          # not 0.5
    assert adoption._tally([accepted, escalated])["ruled"] == 1
