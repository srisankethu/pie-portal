"""An owner edits the margin policy; an owner runs several Zoho companies.

Two restrictions removed. The tests that matter are the ones that stop the
removal from breaking something: a policy that contradicts itself, a second
company that silently replaces the first, or a threshold edit that changes
numbers already computed without saying so.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial import policy
from app.commercial.config import load_commercial_thresholds
from app.db import get_session
from app.domain import models
from app.ingestion import connections as conn
from app.routers import admin, connections as connections_router, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
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
    for r in (platform_auth.router, admin.router, connections_router.router):
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


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _patch(c, email, body):
    return c.patch("/api/v1/admin/margin-policy", json=body, headers=_hdr(c, email))


# ── the margin policy is now the owner's ────────────────────────────────────
def test_an_owner_can_change_the_target_margin(client):
    r = _patch(client, OWNER, {"target_margin_default": 0.30})
    assert r.status_code == 200, r.text
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["target_margin_default"]["value"] == 0.30
    assert fields["target_margin_default"]["overridden"] is True


def test_only_an_owner_may_change_it(client):
    assert _patch(client, MANAGER, {"target_margin_default": 0.30}).status_code == 403
    assert _patch(client, SALES, {"target_margin_default": 0.30}).status_code == 403


def test_a_manager_can_read_it_without_being_able_to_change_it(client):
    r = client.get("/api/v1/admin/policy", headers=_hdr(client, MANAGER))
    assert r.status_code == 200
    assert r.json()["can_manage"] is False
    assert r.json()["margin_policy"]["fields"]


def test_the_edit_actually_reaches_the_engine(client):
    """A settings screen that saves a number the analysis never reads is worse
    than one that refuses to edit."""
    _patch(client, OWNER, {"min_margin": 0.20, "margin_floor": 0.22,
                           "target_margin_default": 0.30})
    s = client.Maker()
    th = policy.load_for_org(s, ORG)
    s.close()
    assert th.min_margin == 0.20 and th.margin_floor == 0.22


def test_editing_changes_the_version_so_old_numbers_stay_attributable(client):
    """The reason this can be editable at all: every metric row and quote
    snapshot records the threshold version that produced it."""
    before = client.get("/api/v1/admin/policy",
                        headers=_hdr(client, OWNER)).json()["margin_policy"]["version"]
    _patch(client, OWNER, {"min_margin": 0.10})
    after = client.get("/api/v1/admin/policy",
                       headers=_hdr(client, OWNER)).json()["margin_policy"]["version"]
    assert before != after
    assert after.startswith("ci_")


def test_the_floor_ladder_cannot_be_inverted(client):
    """Approval floor above review floor means a line is flagged for review and
    cleared for sending at the same time.

    Asserted on the half of the message only this rule produces. It used to
    check for "approval floor", which the *family target* rule also says — "…is
    below the approval floor…" — and that rule fires on the same edit, because
    raising `min_margin` to 30% also puts it above every default family target.
    So the rung this test is named for could be deleted outright and the test
    stayed green. A test that a second rule can satisfy is not testing the first.
    """
    r = _patch(client, OWNER, {"min_margin": 0.30, "margin_floor": 0.15})
    assert r.status_code == 400
    assert "cannot sit above the review floor" in r.json()["detail"]


def test_a_review_floor_above_the_target_is_refused(client):
    r = _patch(client, OWNER, {"margin_floor": 0.40, "target_margin_default": 0.24})
    assert r.status_code == 400
    assert "cannot sit above the target margin" in r.json()["detail"]


def test_a_family_target_below_the_approval_floor_is_refused(client):
    """Otherwise every line in that family needs approval by construction."""
    r = _patch(client, OWNER, {"target_margin_by_family": {"reamer": 0.05}})
    assert r.status_code == 400
    assert "reamer" in r.json()["detail"]


@pytest.mark.requires_pie
def test_a_family_the_pack_does_not_declare_is_refused_naming_the_vocabulary(client):
    """The keys are the PIE pack's family vocabulary and nothing else. A key
    the pack does not declare never matches a parsed line, so accepting it
    would leave the map looking set while every line priced at the blended
    default — and the 400 names the valid vocabulary, because "invalid" on its
    own is a puzzle, not an error an owner can act on."""
    r = _patch(client, OWNER, {"target_margin_by_family": {"widgets": 0.30}})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "widgets" in detail
    assert "milling_insert" in detail, "the valid vocabulary is named"


@pytest.mark.requires_pie
def test_a_family_the_pack_does_declare_is_accepted(client):
    r = _patch(client, OWNER, {"target_margin_by_family": {"milling_insert": 0.32}})
    assert r.status_code == 200, r.text
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["target_margin_by_family"]["value"] == {"milling_insert": 0.32}


def test_without_a_readable_pack_a_family_edit_is_refused_not_waved_through(
        client, monkeypatch):
    """No pack means no vocabulary to check names against, and "could not look"
    must not read as "looked and found nothing wrong" (CLAUDE.md §1). The rest
    of the policy stays editable — only the vocabulary-bound map is held."""
    import app.pie_service as pie_service
    monkeypatch.setattr(pie_service, "pack_families", lambda: None)

    r = _patch(client, OWNER, {"target_margin_by_family": {"reamer": 0.30}})
    assert r.status_code == 400
    assert "vocabulary" in r.json()["detail"]
    assert _patch(client, OWNER, {"target_margin_default": 0.30}).status_code == 200


def test_a_margin_given_as_a_percentage_is_refused(client):
    """24 instead of 0.24 would make every quote wildly profitable on paper."""
    assert _patch(client, OWNER, {"target_margin_default": 24}).status_code == 400


def test_validation_is_of_the_result_not_the_edit(client):
    """One edit that is fine alone can invert the ladder against what is
    already saved, and only the combination is what quotes are judged against."""
    assert _patch(client, OWNER, {"min_margin": 0.10,
                                  "margin_floor": 0.12}).status_code == 200
    # 0.30 is a fine number on its own; against the saved 0.12 review floor it
    # is not. Named rule, not just a 400 — see the note on the ladder test above.
    r = _patch(client, OWNER, {"min_margin": 0.30})
    assert r.status_code == 400
    assert "cannot sit above the review floor" in r.json()["detail"]


def test_analysis_internals_are_not_editable(client):
    """Window lengths are not preferences — moving them silently changes what
    "eroding" means."""
    r = _patch(client, OWNER, {"recent_days": 30})
    assert r.status_code == 422 or r.status_code == 400


def test_clearing_an_override_returns_the_default(client):
    base = load_commercial_thresholds().target_margin_default
    _patch(client, OWNER, {"target_margin_default": 0.31})
    r = _patch(client, OWNER, {"clear": ["target_margin_default"]})
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["target_margin_default"]["value"] == base
    assert fields["target_margin_default"]["overridden"] is False


def test_quantity_bands_are_editable_and_normalised(client):
    r = _patch(client, OWNER, {"quantity_band_edges": [200, 5, 5, 50]})
    assert r.status_code == 200
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["quantity_band_edges"]["value"] == [5, 50, 200], "sorted, deduped"


def test_every_editable_field_carries_a_label_and_an_explanation(client):
    """A settings screen full of raw field names is a screen nobody touches.

    The kind is checked against ``admin._PATCH_TYPE`` rather than a list written
    out here. This test used to carry its own copy of the seven kinds, which
    made it the third place the set was written down — and the copy that goes
    stale is never the one anybody looks at. Keyed off the registry, a new kind
    fails this test only when it has no wire type, which is the actual defect.
    """
    from app.routers.admin import _PATCH_TYPE

    r = client.get("/api/v1/admin/policy", headers=_hdr(client, OWNER)).json()
    for f in r["margin_policy"]["fields"]:
        assert f["label"] and f["help"], f["field"]
        assert f["kind"] in _PATCH_TYPE, f["field"]


def test_a_retained_profit_figure_round_trips_through_the_patch_body(client):
    """End to end, because the PATCH body is generated from ``EDITABLE`` and a
    field with no wire type is dropped silently — the exact failure
    ``_update_margin_policy_model`` was written to make impossible."""
    r = _patch(client, OWNER, {"retained_pat": [
        ["cx-sls", "FY2024-25", "1,25,00,000"], ["cx-4u", "FY2024-25", "-400000"]]})
    assert r.status_code == 200, r.text

    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    # Sorted, grouping separators gone, and a loss preserved as a loss.
    assert fields["retained_pat"]["value"] == [
        ["cx-4u", "FY2024-25", "-400000"], ["cx-sls", "FY2024-25", "12500000"]]
    assert fields["retained_pat"]["overridden"] is True


def test_nothing_is_confirmed_by_default(client):
    """The field exists on the settings screen and is empty — which is what the
    self-funding reading reads as "nobody has answered yet"."""
    r = client.get("/api/v1/admin/policy", headers=_hdr(client, OWNER)).json()
    fields = {f["field"]: f for f in r["margin_policy"]["fields"]}

    assert fields["retained_pat"]["value"] == []
    assert fields["retained_pat"]["overridden"] is False


def test_a_retained_profit_figure_is_refused_when_it_names_no_year(client):
    r = _patch(client, OWNER,
               {"retained_pat": [["cx-sls", "last year", "900000"]]})

    assert r.status_code == 400, r.text
    assert "financial year" in r.text


def test_a_flag_round_trips_as_a_boolean(client):
    """The screen sends `true`, not `1`. Before the flag kind existed this field
    was rendered as a percent box and arrived as the string "0", which
    ``bool("0")`` would have read as True."""
    r = _patch(client, OWNER, {"carrying_rate_is_published": True})
    assert r.status_code == 200, r.text
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["carrying_rate_is_published"]["value"] is True
    assert fields["carrying_rate_is_published"]["kind"] == "flag"

    back = _patch(client, OWNER, {"carrying_rate_is_published": False})
    fields = {f["field"]: f for f in back.json()["margin_policy"]["fields"]}
    assert fields["carrying_rate_is_published"]["value"] is False


def test_a_day_count_round_trips_as_a_whole_number(client):
    """Not a ratio: the screen must not scale it, and the server must not store
    a fraction of a day."""
    r = _patch(client, OWNER, {"dead_stock_days": 400, "slow_stock_days": 150})
    assert r.status_code == 200, r.text
    fields = {f["field"]: f for f in r.json()["margin_policy"]["fields"]}
    assert fields["dead_stock_days"]["value"] == 400
    assert fields["slow_stock_days"]["value"] == 150
    assert fields["dead_stock_days"]["kind"] == "days"
    # A whole number, not 400.0 — the settings box renders the value verbatim.
    assert isinstance(fields["dead_stock_days"]["value"], int)


def test_every_editable_field_can_actually_be_saved(client):
    """The PATCH body used to be a hand-written list of nine fields while
    EDITABLE had fourteen. Pydantic drops unknown keys silently, so the other
    five rendered on the settings screen, accepted an edit, returned 200 and
    changed nothing — the worst shape of bug, because it looks like success.

    Asserts the schema, not one field: a new EDITABLE entry that the body does
    not accept fails here rather than in six months on a customer's screen."""
    from app.routers.admin import UpdateMarginPolicy

    accepted = set(UpdateMarginPolicy.model_fields) - {"clear"}
    assert accepted == set(policy.EDITABLE)


def test_the_carrying_rate_edit_actually_reaches_the_engine(client):
    """Same check as the margin ladder's: a setting the analysis never reads is
    worse than one that refuses to edit."""
    _patch(client, OWNER, {"carrying_cost_annual_pct": 0.15,
                           "dead_stock_days": 400})
    s = client.Maker()
    th = policy.load_for_org(s, ORG)
    s.close()
    assert th.carrying_cost_annual_pct == 0.15
    assert th.dead_stock_days == 400


# ── many connections ────────────────────────────────────────────────────────
def _add(c, email, zoho_org, label="", **kw):
    body = {"zoho_organization_id": zoho_org, "label": label,
            "client_id": "1000.APP", "client_secret": "s", "refresh_token": "r"}
    body.update(kw)
    return c.post("/api/v1/connections", json=body, headers=_hdr(c, email))


def test_an_owner_can_add_several_companies(client):
    assert _add(client, OWNER, "111", "SLS Engineers").status_code == 201
    assert _add(client, OWNER, "222", "4U Precision").status_code == 201
    assert _add(client, OWNER, "333", "UPS").status_code == 201

    rows = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    assert [c["zoho_organization_id"] for c in rows["connections"]] == ["111", "222", "333"]
    assert [c["label"] for c in rows["connections"]] == ["SLS Engineers", "4U Precision", "UPS"]


def test_the_second_company_reuses_the_grant_without_re_entering_it(client):
    _add(client, OWNER, "111")
    listing = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    credential_id = listing["credentials"][0]["credential_id"]

    r = client.post("/api/v1/connections", headers=_hdr(client, OWNER),
                    json={"zoho_organization_id": "222", "label": "Second",
                          "credential_id": credential_id})
    assert r.status_code == 201
    s = client.Maker()
    assert s.query(models.ZohoCredential).count() == 1, "one grant, two companies"
    s.close()


def test_the_grant_is_named_after_itself_not_after_the_first_company(client):
    """One sign-in commonly serves three entities, so naming it after the first
    company it happened to connect makes the connections list read as a lie:
    every row would say its sign-in is "SLS Engineers"."""
    _add(client, OWNER, "111", "SLS Engineers")
    _add(client, OWNER, "222", "4U Precision",
         credential_id=None, client_id="1000.APP", client_secret="s", refresh_token="r")

    listing = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    assert len(listing["credentials"]) == 1, "identical secrets are one grant"
    grant = listing["credentials"][0]["label"]
    assert "SLS" not in grant and "4U" not in grant, grant
    assert "1000.APP" in grant

    # And every connection reports that same grant, which is what makes one
    # rotation cover all of them.
    assert {c["credential_label"] for c in listing["connections"]} == {grant}


def test_adding_the_same_company_twice_does_not_duplicate_it(client):
    """Two rows for one Zoho company would sync it twice and double everything."""
    _add(client, OWNER, "111", "First")
    _add(client, OWNER, "111", "Renamed")
    rows = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    assert len(rows["connections"]) == 1
    assert rows["connections"][0]["label"] == "Renamed"


def test_a_connection_can_be_disabled_without_losing_its_credentials(client):
    r = _add(client, OWNER, "111")
    cid = r.json()["connection_id"]
    assert client.patch(f"/api/v1/connections/{cid}", json={"enabled": False},
                        headers=_hdr(client, OWNER)).json()["enabled"] is False

    s = client.Maker()
    assert conn.list_connections(s, ORG, enabled_only=True) == []
    assert len(conn.list_connections(s, ORG)) == 1, "kept, not deleted"
    s.close()


def test_a_connection_can_be_deleted(client):
    cid = _add(client, OWNER, "111").json()["connection_id"]
    r = client.delete(f"/api/v1/connections/{cid}", headers=_hdr(client, OWNER))
    assert r.status_code == 200 and r.json()["removed"] is True
    assert client.get("/api/v1/connections",
                      headers=_hdr(client, OWNER)).json()["connections"] == []


def test_deleting_a_connection_leaves_the_data_it_pulled(client):
    """Disconnecting is an administrative act about credentials, not a decision
    to forget what was invoiced."""
    cid = _add(client, OWNER, "111").json()["connection_id"]
    s = client.Maker()
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme"))
    s.commit()
    s.close()

    client.delete(f"/api/v1/connections/{cid}", headers=_hdr(client, OWNER))
    s = client.Maker()
    assert s.query(models.Customer).count() == 1
    s.close()


def test_only_an_owner_may_add_or_delete_a_connection(client):
    assert _add(client, MANAGER, "111").status_code == 403
    cid = _add(client, OWNER, "111").json()["connection_id"]
    assert client.delete(f"/api/v1/connections/{cid}",
                         headers=_hdr(client, MANAGER)).status_code == 403


def test_a_manager_may_see_the_connections(client):
    _add(client, OWNER, "111", "SLS")
    r = client.get("/api/v1/connections", headers=_hdr(client, MANAGER))
    assert r.status_code == 200 and r.json()["can_manage"] is False


def test_a_salesperson_may_not_see_them_at_all(client):
    assert client.get("/api/v1/connections",
                      headers=_hdr(client, SALES)).status_code == 403


def test_the_required_scopes_are_published_with_what_each_one_buys(client):
    """A half-granted scope authenticates and then returns nothing — the token
    works, the endpoint 401s, and the sync reports zero rows.

    Published on the catalog entry for Zoho rather than beside the connection
    list, because the screen that names the access requirements is the screen
    that picks the system. Read the connectors test below for what that buys.
    """
    r = client.get("/api/v1/connections/catalog", headers=_hdr(client, OWNER)).json()
    zoho = next(c for c in r["connectors"] if c["key"] == "zoho")
    scopes = {p["name"]: p for p in zoho["permissions"]}
    assert "ZohoBooks.bills.READ" in scopes
    assert "margin" in scopes["ZohoBooks.bills.READ"]["why"]
    assert scopes["ZohoBooks.users.READ"]["required"] is False
    assert "ZohoBooks.bills.READ" in zoho["permission_string"]


def test_every_system_publishes_its_own_access_requirements(client):
    """The defect this pins: the screen showed Zoho's ten scope strings under
    "Scopes this platform needs" whichever connector the tabs had selected, so
    an owner connecting NetSuite was told to grant ``ZohoBooks.*.READ`` — names
    that do not exist in NetSuite — and told nothing about the ones that do.

    So: every system in the catalog carries its own list, no two lists are the
    same, and no ERP's list mentions a Zoho scope."""
    r = client.get("/api/v1/connections/catalog", headers=_hdr(client, OWNER)).json()
    by_key = {c["key"]: c for c in r["connectors"]}
    assert "zoho" in by_key, "Zoho is one of the systems, not a separate panel"

    seen: dict[str, str] = {}
    for key, entry in by_key.items():
        names = [p["name"] for p in entry["permissions"]]
        assert names, f"{key} publishes no access requirements"
        assert entry["permission_note"], f"{key} does not say where to grant them"
        assert any(p["required"] for p in entry["permissions"]), key
        if key != "zoho":
            assert not [n for n in names if "ZohoBooks." in n], key
        fingerprint = "|".join(sorted(names))
        assert fingerprint not in seen, (
            f"{key} publishes the same list as {seen.get(fingerprint)}")
        seen[fingerprint] = key

    # Only Zoho takes its grants as one pasted string; the rest are clicked in
    # an admin console, and an empty box to copy would be worse than none.
    assert by_key["zoho"]["permission_string"]
    assert not by_key["netsuite"]["permission_string"]


def test_the_screen_says_that_connections_pool_into_one_analysis(client):
    """The consequence of many connections on one organization, stated rather
    than left to be discovered from a strange margin."""
    r = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    assert "roll up" in r["pooling_note"]


def test_adding_a_connection_with_no_credential_and_no_secrets_is_refused(client):
    r = client.post("/api/v1/connections", headers=_hdr(client, OWNER),
                    json={"zoho_organization_id": "111"})
    assert r.status_code == 400
    assert "client_id" in r.json()["detail"]


def test_a_credential_from_another_organization_cannot_be_borrowed(client):
    s = client.Maker()
    s.add(models.Organization(organization_id="org_other", name="Other"))
    other = conn.create_credential(s, "org_other", client_id="x", client_secret="y",
                                   refresh_token="z")
    s.commit()
    other_id = other.credential_id
    s.close()

    r = client.post("/api/v1/connections", headers=_hdr(client, OWNER),
                    json={"zoho_organization_id": "999", "credential_id": other_id})
    assert r.status_code == 403


# ── the window to read, per company ─────────────────────────────────────────
#
# One date box for every company either over-reads or under-reads at least one
# of them: an entity with four years of books and one with four months are not
# the same pull. The date is therefore carried per connection, and what a
# company was last read from is what is offered for it next time.
def _run(client, connection_id, since, *, status="OK", txns=0, started=None):
    s = client.Maker()
    s.add(models.SyncRun(
        organization_id=ORG, connection_id=connection_id, source="fixture",
        status=status, since=since, sales_txns=txns,
        started_at=started or datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)))
    s.commit()
    s.close()


def _conns(client):
    rows = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    return {c["label"]: c for c in rows["connections"]}


def test_a_company_never_pulled_is_offered_a_first_window_not_nothing(client):
    _add(client, OWNER, "111", "SLS Engineers")
    row = _conns(client)["SLS Engineers"]

    assert row["last_sync"] is None, "it has genuinely never been pulled"
    offered = date.fromisoformat(row["suggested_since"])
    months = (date.today().year - offered.year) * 12 + date.today().month - offered.month
    assert 17 <= months <= 19, (
        f"a first pull should offer about 18 months, got {months} — less than six "
        f"and the detectors refuse to call a decline at all")


def test_the_window_offered_is_the_one_this_company_was_last_read_from(client):
    _add(client, OWNER, "111", "SLS Engineers")
    _add(client, OWNER, "222", "4U Precision")
    rows = _conns(client)
    _run(client, rows["SLS Engineers"]["connection_id"], date(2021, 4, 1), txns=900)

    rows = _conns(client)
    assert rows["SLS Engineers"]["suggested_since"] == "2021-04-01"
    assert rows["SLS Engineers"]["last_sync"]["sales_txns"] == 900
    # And the other company keeps its own window rather than inheriting it.
    assert rows["4U Precision"]["suggested_since"] != "2021-04-01"
    assert rows["4U Precision"]["last_sync"] is None


def test_the_latest_pull_wins_not_the_first(client):
    _add(client, OWNER, "111", "SLS Engineers")
    cid = _conns(client)["SLS Engineers"]["connection_id"]
    _run(client, cid, date(2021, 4, 1),
         started=datetime(2026, 7, 1, tzinfo=timezone.utc))
    _run(client, cid, date(2025, 1, 1),
         started=datetime(2026, 8, 2, tzinfo=timezone.utc))

    assert _conns(client)["SLS Engineers"]["suggested_since"] == "2025-01-01"


def test_a_run_covering_every_company_is_not_credited_to_any_one_of_them(client):
    """Otherwise 'last pulled from 2019' would appear on a company whose own
    books were never read that far back — the date would be another
    company's."""
    _add(client, OWNER, "111", "SLS Engineers")
    _run(client, None, date(2019, 1, 1), txns=5000)

    row = _conns(client)["SLS Engineers"]
    assert row["last_sync"] is None
    assert row["suggested_since"] != "2019-01-01"


def test_a_failed_pull_is_still_the_last_pull_and_says_so(client):
    """A failure that vanishes from the card is a failure nobody fixes."""
    _add(client, OWNER, "111", "SLS Engineers")
    cid = _conns(client)["SLS Engineers"]["connection_id"]
    _run(client, cid, date(2025, 1, 1), status="FAILED")

    last = _conns(client)["SLS Engineers"]["last_sync"]
    assert last["status"] == "FAILED"
    # Still the window to offer: the intent was right, the pull was not.
    assert _conns(client)["SLS Engineers"]["suggested_since"] == "2025-01-01"


# ── how far back this company has actually been read ────────────────────────
#
# Distinct from `suggested_since`, which is what the *last run asked for*. The
# two diverge exactly where it is dangerous: a nightly pull can run for a year
# and still cover only the window the first run wanted, so "last pulled from
# 2025-01-01" says nothing about whether 2024 was ever read. Widening the
# window used to be a silent no-op, and this is the number that shows it.


def test_a_company_never_pulled_has_covered_nothing(client):
    _add(client, OWNER, "111", "SLS Engineers")
    assert _conns(client)["SLS Engineers"]["covered_from"] is None


def test_coverage_is_the_earliest_window_a_run_finished(client):
    """The floor of what has been listed — not the latest run's date, which is
    what `suggested_since` reports and is a different question."""
    _add(client, OWNER, "111", "SLS Engineers")
    cid = _conns(client)["SLS Engineers"]["connection_id"]
    _run(client, cid, date(2024, 1, 1),
         started=datetime(2026, 7, 1, tzinfo=timezone.utc))
    _run(client, cid, date(2025, 1, 1),
         started=datetime(2026, 8, 2, tzinfo=timezone.utc))

    row = _conns(client)["SLS Engineers"]
    assert row["covered_from"] == "2024-01-01"     # the deepest finished pull
    assert row["suggested_since"] == "2025-01-01"  # the most recent one


def test_a_failed_pull_covers_nothing_however_far_back_it_asked(client):
    """The conservative direction. A run that died in its third window of
    twenty covered three months, and crediting it the whole window would leave
    a hole no later pull ever fills — the incremental listing would skip
    exactly the months it claimed."""
    _add(client, OWNER, "111", "SLS Engineers")
    cid = _conns(client)["SLS Engineers"]["connection_id"]
    _run(client, cid, date(2020, 1, 1), status="FAILED")

    row = _conns(client)["SLS Engineers"]
    assert row["covered_from"] is None
    # Still the window to offer again: the intent was right, the pull was not.
    assert row["suggested_since"] == "2020-01-01"


def test_one_companys_coverage_is_not_anothers(client):
    _add(client, OWNER, "111", "SLS Engineers")
    _add(client, OWNER, "222", "4U Precision")
    sls = _conns(client)["SLS Engineers"]["connection_id"]
    _run(client, sls, date(2022, 1, 1))

    rows = _conns(client)
    assert rows["SLS Engineers"]["covered_from"] == "2022-01-01"
    assert rows["4U Precision"]["covered_from"] is None
