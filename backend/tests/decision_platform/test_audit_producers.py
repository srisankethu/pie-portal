"""The producers — because a collector with no writers is the defect, not the fix.

This branch has already removed two of those: ``WorkloadTracker`` shipped
twenty-five metrics with no callers, and ``trust/access.record_use`` is a
complete break-glass log with no production call site to this day. A chain with
a verifier and no appenders would be the third, and it would be the most
convincing one — every test in ``test_audit_chain.py`` would pass over an empty
table forever.

So these tests drive the real endpoints and the real service functions and
assert an entry landed. They are written against *behaviour visible to a
caller*, not against the append call, so moving the append inside a producer
does not break them but removing it does.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.routers import platform_auth, trust as trust_router
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.trust import audit

ORG = "org_pie"
OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"


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
    for r in (platform_auth.router, trust_router.router):
        app.include_router(r)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _entries(client, action=None):
    s = client.Maker()
    try:
        return audit.entries_for(s, ORG, limit=500, action=action)
    finally:
        s.close()


def _hdr(client, email=OWNER, password=SEED_PASSWORD):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── authentication ──────────────────────────────────────────────────────────
def test_a_successful_sign_in_is_on_the_chain(client):
    _hdr(client)
    rows = _entries(client, audit.LOGIN_SUCCEEDED)
    assert len(rows) == 1
    assert rows[0].actor_label == OWNER
    assert rows[0].actor_role == "OWNER"
    assert rows[0].detail["session_id"]
    # The credential itself is never in the log — the session id names the row,
    # the token would *be* the row.
    assert "token" not in str(rows[0].detail)


def test_a_failed_sign_in_survives_the_raise_that_follows_it(client):
    """``platform_auth`` documents why the failure counter had to be committed
    rather than flushed: ``get_session`` rolls back on any exception, so a flush
    would be undone by the 401. The audit entry rides the same commit, and this
    is what proves it does — a failed sign-in leaving no row is the case an
    audit log is most often opened to look at."""
    r = client.post("/api/v1/auth/login",
                    json={"email": OWNER, "password": "wrong"})
    assert r.status_code == 401

    rows = _entries(client, audit.LOGIN_FAILED)
    assert len(rows) == 1, "the 401 rolled the entry back"
    assert rows[0].actor_label == OWNER
    assert rows[0].detail["consecutive_failures"] == 1


def test_repeated_failures_accumulate_as_separate_entries(client):
    for _ in range(3):
        client.post("/api/v1/auth/login",
                    json={"email": OWNER, "password": "wrong"})
    rows = _entries(client, audit.LOGIN_FAILED)
    assert len(rows) == 3
    assert sorted(r.detail["consecutive_failures"] for r in rows) == [1, 2, 3]


def test_a_failed_sign_in_for_an_unknown_address_is_not_recorded(client):
    """Deliberate. There is no organization to file it under, and a register of
    which addresses somebody probed — in a table the tenant can export — is a
    worse artefact than the application log line that already exists."""
    r = client.post("/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401
    assert _entries(client, audit.LOGIN_FAILED) == []


def test_signing_out_is_on_the_chain(client):
    headers = _hdr(client)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    rows = _entries(client, audit.SESSION_ENDED)
    assert len(rows) == 1
    assert rows[0].detail["scope"] == "THIS_SESSION"


def test_ending_every_session_is_on_the_chain(client):
    headers = _hdr(client)
    assert client.post("/api/v1/auth/logout-all", headers=headers).status_code == 200
    rows = _entries(client, audit.SESSIONS_ENDED_ALL)
    assert len(rows) == 1
    assert rows[0].detail["ended"] >= 1


def test_the_auth_chain_verifies_after_a_realistic_session(client):
    client.post("/api/v1/auth/login", json={"email": OWNER, "password": "no"})
    headers = _hdr(client)
    client.post("/api/v1/auth/logout", headers=headers)

    s = client.Maker()
    try:
        report = audit.verify(s, ORG)
        assert report["ok"] is True
        assert report["entries"] == 3
        assert audit.actions_seen(audit.chain(s, ORG)) == {
            audit.LOGIN_FAILED: 1, audit.LOGIN_SUCCEEDED: 1, audit.SESSION_ENDED: 1}
    finally:
        s.close()


# ── the read surface ────────────────────────────────────────────────────────
def test_the_audit_surface_is_owner_only(client):
    """§1 sets the gate: this chain carries a margin-policy transition, which is
    the tightest classification of anything it records."""
    for path in ("/api/v1/trust/audit", "/api/v1/trust/audit/verify",
                 "/api/v1/trust/audit/export"):
        assert client.get(path, headers=_hdr(client, SALES)).status_code == 403, path
        assert client.get(path, headers=_hdr(client)).status_code == 200, path


def test_the_verdict_covers_the_whole_chain_not_the_page(client):
    """Answering "has anything been altered" from the page somebody asked for
    would report a clean bill the moment the tamper scrolled out of view."""
    headers = _hdr(client)
    for _ in range(3):
        client.post("/api/v1/auth/login", json={"email": SALES, "password": "no"})

    s = client.Maker()
    try:
        victim = audit.chain(s, ORG)[0]
        victim.detail = {"rewritten": True}
        s.commit()
    finally:
        s.close()

    body = client.get("/api/v1/trust/audit?limit=1", headers=headers).json()
    assert len(body["entries"]) == 1
    assert body["verification"]["ok"] is False
    assert body["verification"]["first_break"]["seq"] == 1


def test_the_export_is_offered_as_csv_as_well(client):
    _hdr(client)
    r = client.get("/api/v1/trust/audit/export?fmt=csv", headers=_hdr(client))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "entry_hash" in r.text.splitlines()[0]


def test_the_chain_travels_with_the_tenant_export(client):
    """EXPORTED, not EXCLUDED — and with the hashes, so it stays re-verifiable
    in the recipient's hands rather than merely readable."""
    _hdr(client)
    body = client.get("/api/v1/trust/export", headers=_hdr(client)).json()
    assert "audit_entries" in body["data"]
    assert body["data"]["audit_entries"], "the chain is exported empty"
    assert {"prev_hash", "entry_hash", "seq"} <= set(body["data"]["audit_entries"][0])


# ── policy transitions ──────────────────────────────────────────────────────
def _session():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    return s


def test_a_policy_edit_records_the_version_it_replaced():
    """The highest-value entry in the log.

    ``CommercialPolicy.overrides`` is overwritten in place, so the ``ci_``
    version that judged every previously computed row is gone the moment
    somebody edits the margin policy. This entry is the only thing that survives
    it, which is why it has to carry the version on *both* sides.
    """
    from app.commercial import policy

    s = _session()
    try:
        before = policy.load_for_org(s, ORG)
        after = policy.save_for_org(s, ORG, {"target_margin_default": 0.31},
                                    user_id="u_owner")
        s.commit()

        assert after.version != before.version, "the stamp did not move"

        rows = audit.entries_for(s, ORG, action=audit.POLICY_CHANGED)
        assert len(rows) == 1
        entry = rows[0]
        assert entry.thresholds_version == after.version
        assert entry.detail["from_version"] == before.version
        assert entry.detail["to_version"] == after.version
        # §1: ``ci_`` and ``th_`` are different stamps and must not be conflated.
        assert entry.detail["version_kind"] == "ci"
        assert entry.thresholds_version.startswith("ci_")
        assert entry.detail["fields"] == ["target_margin_default"]
        assert entry.detail["changes"]["target_margin_default"]["to"] == 0.31
        assert entry.actor_user_id == "u_owner"
        assert audit.verify(s, ORG)["ok"]
    finally:
        s.close()


def test_a_rejected_policy_edit_leaves_no_entry():
    """An attempt that raised did not change anything, and a log saying it did
    would be a false record. ``append`` not committing is what makes this hold —
    the entry, if one had been written, dies with the transaction."""
    from app.commercial import policy

    s = _session()
    try:
        with pytest.raises(policy.PolicyError):
            policy.save_for_org(s, ORG, {"not_a_field": 1}, user_id="u_owner")
        s.rollback()
        assert audit.entries_for(s, ORG, action=audit.POLICY_CHANGED) == []
    finally:
        s.close()


def test_successive_policy_edits_form_a_readable_history():
    """What the mutable row cannot do: say what the policy was three edits ago."""
    from app.commercial import policy

    s = _session()
    try:
        for target in (0.28, 0.31, 0.34):
            policy.save_for_org(s, ORG, {"target_margin_default": target}, user_id="u_owner")
            s.commit()

        rows = audit.chain(s, ORG)
        versions = [(r.detail["from_version"], r.detail["to_version"]) for r in rows]
        # Each edit's "to" is the next edit's "from" — an unbroken account of
        # which policy was in force between any two moments.
        for earlier, later in zip(versions, versions[1:]):
            assert earlier[1] == later[0]
        assert audit.verify(s, ORG)["ok"]
    finally:
        s.close()


# ── AI calls ────────────────────────────────────────────────────────────────
def test_the_decision_sweep_does_not_grow_the_chain():
    """`AiTelemetryRepository.record` deliberately appends nothing.

    It first did, and three reviewers objected for three different reasons. It
    runs on the proactive sweep — four times a day, once per signal, unbounded
    by customer count — and on cache hits as well as real calls, so chain length
    became a function of background volume rather than of auditable events, and
    every `verify` HMACs all of it. `DecisionService.generate` also holds one
    transaction across every provider round trip in a sweep, so the append sat
    on the tenant's chain head and blocked every other appender behind it. And
    with no principal in scope it recorded SYSTEM.

    `AiCallLog` still records provider, model, cost and tokens. The chain is for
    acts of authority that cannot be reconstructed; a cached interpretation is
    neither.
    """
    from app.ai.telemetry import CallTelemetry
    from app.repositories import AiTelemetryRepository

    s = _session()
    try:
        before = len(audit.entries_for(s, ORG))
        tel = CallTelemetry(decision_type="X", ai_status="OK", provider="p",
                            model="m", provider_called=True)
        AiTelemetryRepository(s, ORG).record(tel)
        s.commit()
        assert len(audit.entries_for(s, ORG)) == before, (
            "the sweep must not append to the chain")
    finally:
        s.close()


def test_a_reading_that_failed_after_the_round_trip_still_counts_as_called():
    """`provider_called`, not `used_ai`, is what the RFQ audit gate reads.

    The gate was success, so the three paths where the customer's enquiry was
    sent and the answer was unusable — a provider exception, unparseable JSON, a
    clean parse yielding nothing — recorded that their text reached a model
    nowhere at all. Those are exactly the calls somebody asks about later.
    """
    from app.ai import reading

    class Exploding:
        name, model = "p", "m"

        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("upstream refused")

    class Garbage:
        name, model = "p", "m"

        def complete(self, system: str, user: str) -> str:
            return "not json at all"

    class Empty:
        name, model = "p", "m"

        def complete(self, system: str, user: str) -> str:
            return '{"lines": []}'

    for provider in (Exploding(), Garbage(), Empty()):
        result = reading.read("2 x CNMG 120408", provider)
        assert result.used_ai is False, "these are all fallbacks"
        assert result.provider_called is True, (
            f"{type(provider).__name__}: the enquiry was sent and must be "
            f"auditable even though the answer was not usable")

    # And the two that never reach a provider must stay False, or the entry
    # would claim a call that never happened.
    assert reading.read("", None).provider_called is False
    assert reading.read("2 x CNMG 120408", None).provider_called is False


def test_no_producer_writes_a_price_a_cost_or_a_margin_field():
    """§1, as a check rather than a promise.

    The audit log carries values a salesperson may never see, and OWNER-only
    reads are what keep that safe. This is the other half: the log must not
    become a *new* home for economics, so no entry any producer writes may carry
    a line's cost, price or margin. ``filterCounts.MFLOOR`` is the precedent —
    a below-floor count, computed two lines below the guard that correctly
    withheld the floor itself.
    """
    from app.ai.telemetry import CallTelemetry
    from app.commercial import policy
    from app.repositories import AiTelemetryRepository

    s = _session()
    try:
        policy.save_for_org(s, ORG, {"target_margin_default": 0.29}, user_id="u_owner")
        AiTelemetryRepository(s, ORG).record(
            CallTelemetry(decision_type="X", ai_status="OK", provider="p", model="m"))
        s.commit()

        forbidden = ("unit_cost", "cost", "purchase_price", "unit_price",
                     "proposed_price", "gross_profit", "margin_pct",
                     "recommended_price", "floor_price")
        for row in audit.chain(s, ORG):
            keys = _keys(row.detail)
            # ``target_margin`` and the other policy field names are allowed:
            # they are the policy, they are what changed, and OWNER can already
            # read every one of them from /commercial/policy.
            if row.action == audit.POLICY_CHANGED:
                continue
            assert not (keys & set(forbidden)), (row.action, sorted(keys))
    finally:
        s.close()


def _keys(value, out=None):
    out = set() if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(value, list):
        for v in value:
            _keys(v, out)
    return out


def test_the_policy_entry_names_no_customer_and_no_product():
    from app.commercial import policy

    s = _session()
    try:
        policy.save_for_org(s, ORG, {"target_margin_default": 0.29}, user_id="u_owner")
        s.commit()
        entry = audit.entries_for(s, ORG, action=audit.POLICY_CHANGED)[0]
        assert entry.subject_type == "COMMERCIAL_POLICY"
        assert entry.subject_id == ORG
        assert not (_keys(entry.detail) & {"customer_id", "product_id"})
    finally:
        s.close()


def test_the_model_payload_text_is_never_on_the_chain():
    """The prompt has one home — ``model_payloads``, encrypted under the tenant
    key — and the chain survives erasure by design. A second, plaintext copy
    here would quietly undo that encryption.

    Asserted against the surviving AI producers by reading what the router
    actually puts in ``detail``: the RFQ entry records how many characters the
    enquiry ran to, never the characters themselves.
    """
    import inspect

    from app.routers import ai_settings, quote

    for module in (quote, ai_settings):
        src = inspect.getsource(module)
        block = src[src.index("action=audit.AI_CALL"):][:1200]
        for forbidden in ("body.text,", "\"text\":", "prompt_payload",
                          "user=", "raw"):
            assert forbidden not in block, (
                f"{module.__name__}: the AI_CALL detail looks like it carries "
                f"payload text ({forbidden!r}) — the prompt belongs only in "
                f"model_payloads, encrypted")


# ── the schema knows about it ───────────────────────────────────────────────
def test_the_table_is_reachable_from_the_metadata():
    """§4: a model Alembic cannot see is a table that never exists in production."""
    from app.db import Base
    assert "audit_entries" in Base.metadata.tables


def test_every_policy_value_survives_the_round_trip_the_signature_covers():
    """A detail value that does not come back identically breaks verification.

    The chain's hash is taken over the *outgoing* structure and re-checked
    against what the JSON column gives back, so any type that does not
    round-trip through JSON would make an untampered entry read as forged — the
    worst failure this surface has, because it discredits the honest entries
    beside it. Margin ratios are ``float`` in ``CommercialThresholds`` and
    round-trip exactly; a ``Decimal`` would not serialise at all, which is what
    ``_readable`` renders as a string.

    Asserted by re-verifying rather than by inspecting a type, so the test
    covers whatever ``EDITABLE`` grows into rather than the two fields it holds
    today.
    """

    from app.commercial import policy
    from app.commercial.policy import _readable

    s = _session()
    try:
        policy.save_for_org(
            s, ORG,
            {"target_margin_default": 0.31, "quantity_band_edges": [1, 10, 100],
             "sales_discretion_band": 0.05},
            user_id="u")
        s.commit()
        assert audit.verify(s, ORG)["ok"] is True, (
            "a policy value did not survive the JSON round trip")
        entry = audit.entries_for(s, ORG, action=audit.POLICY_CHANGED)[0]
        assert entry.detail["changes"]["target_margin_default"]["to"] == 0.31
    finally:
        s.close()

    # And the type that cannot serialise is the one rendered as a string.
    assert _readable(Decimal("0.31")) == "0.31"
    assert _readable([Decimal("1.5")]) == ["1.5"]


# ── approval decisions ──────────────────────────────────────────────────────
#
# The request row is *mutated* on a decision and its ``thread`` is rewritten on
# every resubmit, so the sequence — raised, returned, re-raised, approved — was
# recoverable only from a JSON blob the same code path overwrites. These assert
# it is now a set of linked entries instead.

def _principal(user_id, role):
    from app.authz import Principal
    from app.domain.enums import Role
    return Principal(user_id=user_id, organization_id=ORG, role=Role(role),
                     name=user_id, email=f"{user_id}@pie.example")


def _raise(s, requester):
    from app import approvals
    from app.domain.enums import ApprovalKind
    return approvals.raise_request(
        s, requester, kind=ApprovalKind.QUOTE_LINE_PRICE, subject_id="q1",
        subject={"line_id": "l1"}, title="A line below the floor",
        summary="needs a manager", subject_line_id="l1")


def test_an_approval_decision_is_on_the_chain():
    from app import approvals
    from app.domain.enums import ApprovalStatus

    s = _session()
    try:
        request = _raise(s, _principal("u_sales", "SALESPERSON"))
        manager = _principal("u_mgr", "SALES_MANAGER")
        approvals.decide(s, manager, request, ApprovalStatus.APPROVED, "agreed")
        s.commit()

        rows = audit.entries_for(s, ORG, action=audit.APPROVAL_DECIDED)
        assert len(rows) == 1
        entry = rows[0]
        assert entry.subject_type == "APPROVAL_REQUEST"
        assert entry.subject_id == request.approval_request_id
        assert entry.actor_user_id == "u_mgr"
        assert entry.actor_role == "SALES_MANAGER"
        assert entry.detail["outcome"] == "APPROVED"
        assert entry.detail["requested_by_user_id"] == "u_sales"
        assert entry.detail["rationale_given"] is True
        assert audit.verify(s, ORG)["ok"]
    finally:
        s.close()


def test_the_sequence_of_an_argument_survives_the_row_being_overwritten():
    """Returned, resubmitted, approved — three entries where the request row
    only ever holds the last of them."""
    from app import approvals
    from app.domain.enums import ApprovalStatus

    s = _session()
    try:
        sales = _principal("u_sales", "SALESPERSON")
        manager = _principal("u_mgr", "SALES_MANAGER")

        request = _raise(s, sales)
        approvals.decide(s, manager, request, ApprovalStatus.CHANGES_REQUESTED,
                         "try 150")
        s.commit()
        request = _raise(s, sales)              # resubmitted onto the same row
        approvals.decide(s, manager, request, ApprovalStatus.APPROVED, "fine")
        s.commit()

        outcomes = [r.detail["outcome"] for r in audit.chain(s, ORG)]
        assert outcomes == ["CHANGES_REQUESTED", "APPROVED"]
        assert request.status == "APPROVED", "the row holds only the last word"
        assert audit.verify(s, ORG)["ok"]
    finally:
        s.close()


def test_a_withdrawal_is_recorded_as_its_own_action():
    from app import approvals

    s = _session()
    try:
        sales = _principal("u_sales", "SALESPERSON")
        request = _raise(s, sales)
        approvals.withdraw(s, sales, request, "re-priced it")
        s.commit()

        rows = audit.entries_for(s, ORG, action=audit.APPROVAL_WITHDRAWN)
        assert len(rows) == 1
        assert rows[0].detail["outcome"] == "WITHDRAWN"
        assert rows[0].actor_user_id == "u_sales"
    finally:
        s.close()


def test_a_refused_decision_writes_nothing():
    """A salesperson cannot approve their own line, and an attempt that raised
    must leave no trace of having succeeded."""
    from app import approvals
    from app.domain.enums import ApprovalStatus

    s = _session()
    try:
        sales = _principal("u_sales", "SALESPERSON")
        request = _raise(s, sales)
        s.commit()
        with pytest.raises(approvals.NotAuthorized):
            approvals.decide(s, sales, request, ApprovalStatus.APPROVED, "me")
        s.rollback()
        assert audit.entries_for(s, ORG, action=audit.APPROVAL_DECIDED) == []
    finally:
        s.close()


def test_the_approval_entry_carries_no_price():
    """§1. What the entry adds is who signed and whether they gave the reason
    the rule required; the number that made the line below-cost stays on the
    request, behind the role gate that already guards it."""
    from app import approvals
    from app.domain.enums import ApprovalStatus

    s = _session()
    try:
        request = _raise(s, _principal("u_sales", "SALESPERSON"))
        approvals.decide(s, _principal("u_mgr", "SALES_MANAGER"), request,
                         ApprovalStatus.APPROVED, "agreed at 150 rupees")
        s.commit()

        entry = audit.entries_for(s, ORG, action=audit.APPROVAL_DECIDED)[0]
        # Not even the rationale text, which is free-form and can quote a price.
        assert "150" not in str(entry.detail)
        assert entry.detail["rationale_given"] is True
    finally:
        s.close()
