"""Detector outcome reporting — the false-alarm rate the signal layer never had.

The acceptance criterion that matters most is the negative one: an unworked
queue must report its dismissal rate as *unknown*, never as zero. A detector
nobody has judged is not a detector with no false alarms, and §1 of the working
agreement forbids the benign default in exactly this shape.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.clock import now as utc_now
from app.config import settings
from app.db import get_session
from app.decisions.outcomes import dismissal_band, report, summarize
from app.domain import models
from app.repositories import DecisionRepository, SignalRepository
from app.routers import internal, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
DECLINE = "CUSTOMER_DECLINE"
MARGIN = "MARGIN_DETERIORATION"


def _decisions(dtype: str, **by_status: int) -> list[models.Decision]:
    """Decision rows in the given statuses.

    ``decision_key`` is unique per row because the table says it must be. The
    counter is deliberate rather than a uuid: these tests assert on equality of
    whole reports, so the fixture has to be reproducible.
    """
    out: list[models.Decision] = []
    for status, count in by_status.items():
        for i in range(count):
            out.append(models.Decision(
                organization_id=ORG, decision_type=dtype,
                decision_key=f"dk_{dtype}_{status}_{i}",
                subject_entity_type="CUSTOMER", subject_entity_id="c1",
                assigned_role="SALESPERSON", status=status))
    return out


def _signals(stype: str, count: int) -> list[models.Signal]:
    return [models.Signal(
        organization_id=ORG, signal_type=stype, subject_entity_type="CUSTOMER",
        subject_entity_id="c1", detector_version="v1", threshold_config_version="th_x",
        window={}, metrics={}, severity_base=50) for _ in range(count)]


# ── the invariant: absence of judgement is not a pass ────────────────────────
def test_an_unworked_queue_reports_unknown_not_zero():
    out = summarize(_decisions(DECLINE, OPEN=40), _signals(DECLINE, 40), days=30)
    row = out["by_type"][DECLINE]

    assert row["judged"] == 0
    assert row["dismissal_rate"] is None, "0.0 would read as a perfect detector"
    assert row["band"] == "NOT_REVIEWED"
    assert out["dismissal_rate"] is None
    assert out["band"] == "NOT_REVIEWED"


def test_viewed_is_not_judged():
    """Opening a card and leaving it is not a verdict on the detector."""
    out = summarize(_decisions(DECLINE, VIEWED=30), _signals(DECLINE, 30), days=30)
    row = out["by_type"][DECLINE]
    assert row["judged"] == 0
    assert row["dismissal_rate"] is None
    assert row["band"] == "NOT_REVIEWED"


def test_a_rate_over_too_few_judged_is_not_a_finding():
    out = summarize(_decisions(DECLINE, DISMISSED=3), _signals(DECLINE, 3), days=30)
    row = out["by_type"][DECLINE]
    # The rate is reported — it is a real measurement over 3 cards — but it is
    # explicitly not banded as a problem.
    assert row["dismissal_rate"] == 1.0
    assert row["band"] == "INSUFFICIENT_DATA"


# ── the two-sided band ───────────────────────────────────────────────────────
def test_a_noisy_detector_bands_high():
    out = summarize(_decisions(MARGIN, DISMISSED=18, ACTIONED=2),
                    _signals(MARGIN, 20), days=30)
    row = out["by_type"][MARGIN]
    assert row["dismissal_rate"] == 0.9
    assert row["band"] == "HIGH"


def test_a_detector_nothing_is_ever_dismissed_from_is_also_a_finding():
    out = summarize(_decisions(MARGIN, ACTIONED=20), _signals(MARGIN, 20), days=30)
    row = out["by_type"][MARGIN]
    assert row["dismissal_rate"] == 0.0     # measured zero, over a real sample
    assert row["band"] == "SUSPICIOUSLY_LOW"


def test_an_ordinary_rate_bands_ok():
    out = summarize(_decisions(DECLINE, DISMISSED=4, ACTIONED=16),
                    _signals(DECLINE, 20), days=30)
    assert out["by_type"][DECLINE]["dismissal_rate"] == 0.2
    assert out["by_type"][DECLINE]["band"] == "OK"


def test_measured_zero_and_no_measurement_do_not_render_alike():
    """The distinction the whole module exists to preserve."""
    unworked = summarize(_decisions(DECLINE, OPEN=20), (), days=30)["by_type"][DECLINE]
    worked = summarize(_decisions(DECLINE, ACTIONED=20), (), days=30)["by_type"][DECLINE]

    assert unworked["dismissal_rate"] is None and unworked["band"] == "NOT_REVIEWED"
    assert worked["dismissal_rate"] == 0.0 and worked["band"] == "SUSPICIOUSLY_LOW"


def test_escalated_counts_as_judged_and_not_as_a_rejection():
    """Handing a card upward asserts it is real; it is not a dismissal."""
    out = summarize(_decisions(MARGIN, ESCALATED=10, DISMISSED=10),
                    _signals(MARGIN, 20), days=30)
    row = out["by_type"][MARGIN]
    assert row["judged"] == 20
    assert row["dismissed"] == 10
    assert row["dismissal_rate"] == 0.5


def test_band_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_OUTCOME_MIN_SAMPLE", 2)
    monkeypatch.setattr(settings, "SIGNAL_DISMISSAL_RATE_MAX", 0.10)
    band, _ = dismissal_band(0.5, judged=4)
    assert band == "HIGH"


# ── emission vs. amplification ───────────────────────────────────────────────
def test_repeated_emission_behind_one_card_is_visible():
    """Nightly re-emission is not readable from either count on its own."""
    out = summarize(_decisions(MARGIN, OPEN=1), _signals(MARGIN, 7), days=7)
    row = out["by_type"][MARGIN]
    assert row["signals_emitted"] == 7
    assert row["decisions_raised"] == 1
    assert row["signals_per_decision"] == 7.0


def test_a_type_that_emitted_but_opened_no_card_is_still_reported():
    out = summarize((), _signals(MARGIN, 5), days=7)
    row = out["by_type"][MARGIN]
    assert row["signals_emitted"] == 5
    assert row["decisions_raised"] == 0
    assert row["signals_per_decision"] is None      # not 0, and not a crash
    assert row["band"] == "NOT_REVIEWED"


def test_empty_book_reports_nothing_rather_than_failing():
    out = summarize((), (), days=30)
    assert out["by_type"] == {}
    assert out["dismissal_rate"] is None
    assert out["band"] == "NOT_REVIEWED"


# ── windows, determinism, persistence ────────────────────────────────────────
def test_windows_are_bounded_by_created_at(session):
    ref = utc_now()
    repo_d = DecisionRepository(session, ORG)
    fresh = _decisions(DECLINE, DISMISSED=1)[0]
    old = _decisions(DECLINE, DISMISSED=1)[0]
    old.decision_key = "dk_old"
    repo_d.add(fresh)
    repo_d.add(old)
    session.flush()
    fresh.created_at = ref - timedelta(days=2)
    old.created_at = ref - timedelta(days=20)
    session.commit()

    out = report(repo_d, SignalRepository(session, ORG), windows=(7, 30), now=ref)
    assert out["windows"]["7d"]["decisions_raised"] == 1
    assert out["windows"]["30d"]["decisions_raised"] == 2


def test_the_report_is_a_pure_function_of_the_rows(session):
    repo_d, repo_s = DecisionRepository(session, ORG), SignalRepository(session, ORG)
    for d in _decisions(DECLINE, DISMISSED=2, ACTIONED=3):
        repo_d.add(d)
    session.commit()

    ref = utc_now()
    first = report(repo_d, repo_s, now=ref)
    second = report(repo_d, repo_s, now=ref)
    assert first == second


# ── the endpoint ─────────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _token(client, email):
    return client.post("/api/v1/auth/login",
                       json={"email": email, "password": SEED_PASSWORD}).json()["token"]


def test_endpoint_is_owner_only(client):
    r = client.get("/api/v1/internal/detector-outcomes",
                   headers={"Authorization": f"Bearer {_token(client, 'r.nair@pie.example')}"})
    assert r.status_code == 403


def test_endpoint_returns_both_windows_for_an_owner(client):
    r = client.get("/api/v1/internal/detector-outcomes",
                   headers={"Authorization": f"Bearer {_token(client, 's.menon@pie.example')}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["windows"]) == {"7d", "30d"}
    # An empty book must answer "unknown", not "healthy".
    assert body["windows"]["30d"]["dismissal_rate"] is None
    assert body["windows"]["30d"]["band"] == "NOT_REVIEWED"
