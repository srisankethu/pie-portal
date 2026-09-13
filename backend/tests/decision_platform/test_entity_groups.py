"""Groups: the version, the narrowing, and the two ways it could leak.

Four properties this feature stands on, each tested at the level it can actually
be broken at rather than at the level it is easiest to assert:

**The version moves when the definition does.** Asserted against the roster,
because that is the half a reader would not expect to be in a content hash and
the half that silently changes every number computed under the group.

**A group narrows and never widens.** Over HTTP, as a salesperson, because the
function it rests on (``groups.narrow``) is trivially correct in isolation and
the failure mode is an endpoint that forgets to call it — which only a request
can see.

**A group-scoped figure is recomputed, not trimmed.** The bounded snapshot is
compared against the same lines filtered afterwards, the ``test_bounded_loads``
pattern: a bound whose result differs from the unbounded computation over the
same rows is a wrong number nobody would ever look twice at.

**A restricted group is absent, not refused.** 404 rather than 403, because a
403 confirms the group exists and the existence is part of what is withheld.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import groups, threshold_registry
from app.db import get_session
from app.domain import models
from app.routers import accounts, groups as groups_router, insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals.aggregates import load_snapshot

ORG = "org_pie"
SALES = "r.nair@pie.example"
MANAGER = "m.rao@pie.example"

#: Which seeded user owns which account, so the salesperson's book is a known
#: subset rather than whatever the seed happened to assign.
SALES_USER = "usr_sales"


@pytest.fixture()
def app_and_maker():
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(accounts.router)
    app.include_router(groups_router.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), maker


def _hdr(client, email):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_customers(maker, *, owned_by: dict[str, str] | None = None) -> list[str]:
    """Three accounts. ``owned_by`` maps customer id to the user who covers it."""
    owned_by = owned_by or {}
    s = maker()
    ids = []
    for i in range(3):
        cid = f"cust-{i}"
        s.add(models.Customer(customer_id=cid, organization_id=ORG,
                              external_id=f"zx-{i}", name=f"Account {i}",
                              assigned_user_id=owned_by.get(cid)))
        ids.append(cid)
    s.commit()
    s.close()
    return ids


def _make_group(maker, slug="aerospace", kind="CUSTOMER", members=(), **kw):
    s = maker()
    g = groups.create(s, ORG, entity_kind=kind, name=kw.pop("name", "Aerospace"),
                      slug=slug, **kw)
    if members:
        groups.add_members(s, g, list(members))
    s.commit()
    version = g.version
    s.close()
    return version


# ── the version ──────────────────────────────────────────────────────────────
def test_a_groups_version_moves_when_its_roster_does(session):
    """The property the whole stamp exists for.

    A version that held still while members were added would be worse than no
    version at all: a reader comparing two figures would take the matching stamp
    as evidence they were computed over the same set.
    """
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="x1", name="One"))
    session.flush()

    group = groups.create(session, ORG, entity_kind="CUSTOMER", name="A",
                          slug="a")
    empty = group.version
    assert empty.startswith("gr_")

    groups.add_members(session, group, ["c1"])
    assert group.version != empty

    with_one = group.version
    groups.remove_members(session, group, ["c1"])
    assert group.version != with_one
    # And back: the hash is over content, so the same roster is the same stamp.
    groups.add_members(session, group, ["c1"])
    assert group.version == with_one


def test_renaming_a_group_does_not_move_its_version(session):
    """A name is not part of a definition, and a stamp that moved on a rename
    would stop meaning "the answer may have moved" — the only thing it is for."""
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.flush()
    group = groups.create(session, ORG, entity_kind="VENDOR", name="Old",
                          slug="v")
    before = group.version

    groups.rename(session, group, name="New name entirely", description="and a note")
    assert group.version == before

    groups.set_visibility(session, group, groups.RESTRICTED)
    groups.set_archived(session, group, True)
    assert group.version == before


def test_every_version_a_group_has_had_stays_resolvable(session):
    """The registry holds the pre-image of each roster, not only the current one.

    This is what a group version buys over simply reading the group row back: the
    roster is superseded in place, so the membership that produced last quarter's
    figure exists nowhere else once somebody has edited it.
    """
    session.add(models.Organization(organization_id=ORG, name="T"))
    for i in range(2):
        session.add(models.Customer(customer_id=f"c{i}", organization_id=ORG,
                                    external_id=f"x{i}", name=f"C{i}"))
    session.flush()

    group = groups.create(session, ORG, entity_kind="CUSTOMER", name="A", slug="a")
    seen = [group.version]
    groups.add_members(session, group, ["c0"])
    seen.append(group.version)
    groups.add_members(session, group, ["c1"])
    seen.append(group.version)
    session.commit()

    assert len(set(seen)) == 3
    for version in seen:
        assert threshold_registry.resolve(session, ORG, version).kind == "group"

    # And each one still describes the roster it was taken over rather than
    # today's, which is the whole difference between this and reading the group
    # row back. ``values`` is the safe surface — see ``ResolvedThresholds``.
    rosters = [threshold_registry.resolve(session, ORG, v).values["members"]
               for v in seen]
    assert rosters == [[], ["c0"], ["c0", "c1"]]


def test_a_group_version_is_a_group_stamp_to_the_registry():
    assert threshold_registry.kind_of("gr_0123456789") == "group"
    assert threshold_registry.kind_of("ci_0123456789") == "commercial"


# ── membership writes ────────────────────────────────────────────────────────
def test_an_id_from_outside_the_workspace_refuses_the_whole_call(session):
    """Not a partial success. A roster that differs from what somebody asked for
    is a definition nobody chose, and it is silently in every number after."""
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Customer(customer_id="mine", organization_id=ORG,
                                external_id="x", name="Mine"))
    session.add(models.Customer(customer_id="theirs", organization_id="other-org",
                                external_id="y", name="Theirs"))
    session.flush()
    group = groups.create(session, ORG, entity_kind="CUSTOMER", name="A", slug="a")

    with pytest.raises(groups.GroupError):
        groups.add_members(session, group, ["mine", "theirs"])
    assert groups.member_ids(session, group) == []


def test_a_customer_id_cannot_be_added_to_an_item_group(session):
    """The check the polymorphic ``entity_id`` column cannot make itself."""
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="x", name="One"))
    session.flush()
    items = groups.create(session, ORG, entity_kind="PRODUCT", name="Line",
                          slug="line")

    with pytest.raises(groups.GroupError):
        groups.add_members(session, items, ["c1"])


# ── narrowing ────────────────────────────────────────────────────────────────
def test_a_group_narrows_a_salespersons_book_and_never_widens_it(app_and_maker):
    """The rule that keeps a labelling feature from becoming a permission one.

    The group holds all three accounts; the salesperson owns one. Asking for the
    group must return the one, not the three — and not the two they do not own
    merely because a group named them.
    """
    client, maker = app_and_maker
    _seed_customers(maker, owned_by={"cust-0": SALES_USER})
    _make_group(maker, members=["cust-0", "cust-1", "cust-2"])

    sales = _hdr(client, SALES)
    everything = client.get("/api/v1/accounts", headers=sales)
    assert {r["customer_id"] for r in everything.json()} == {"cust-0"}

    scoped = client.get("/api/v1/accounts?group=aerospace", headers=sales)
    assert scoped.status_code == 200, scoped.text
    assert {r["customer_id"] for r in scoped.json()} == {"cust-0"}

    # And a manager, who owns nothing in particular, sees the whole group.
    manager = _hdr(client, MANAGER)
    wide = client.get("/api/v1/accounts?group=aerospace", headers=manager)
    assert {r["customer_id"] for r in wide.json()} == {"cust-0", "cust-1", "cust-2"}


def test_a_group_holding_only_other_peoples_accounts_returns_nothing(app_and_maker):
    """The sharper form of the same rule: the intersection is empty, and an
    empty answer is correct. A group is not a key to somebody else's book."""
    client, maker = app_and_maker
    _seed_customers(maker, owned_by={"cust-0": SALES_USER})
    _make_group(maker, slug="theirs", members=["cust-1", "cust-2"])

    r = client.get("/api/v1/accounts?group=theirs", headers=_hdr(client, SALES))
    assert r.status_code == 200
    assert r.json() == []


def test_an_empty_group_answers_empty_rather_than_the_whole_book(app_and_maker):
    """``load_snapshot`` reads ``[]`` as a real bound for exactly this reason.

    Reading an empty group as "no filter" is the benign default CLAUDE.md §1
    names: a question about a set nobody is in, answered with everybody.
    """
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="nobody", members=[])

    manager = _hdr(client, MANAGER)
    assert len(client.get("/api/v1/accounts", headers=manager).json()) == 3
    assert client.get("/api/v1/accounts?group=nobody", headers=manager).json() == []


def test_an_unknown_group_is_a_404_rather_than_a_silent_whole_book(app_and_maker):
    client, maker = app_and_maker
    _seed_customers(maker)
    r = client.get("/api/v1/accounts?group=no-such-thing",
                   headers=_hdr(client, MANAGER))
    assert r.status_code == 404


def test_a_group_of_the_wrong_kind_does_not_resolve(app_and_maker):
    """``?group=`` on the account directory names a group of customers. A vendor
    group with the same slug is a different object and must not be found."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="shared-name", kind="VENDOR")

    r = client.get("/api/v1/accounts?group=shared-name",
                   headers=_hdr(client, MANAGER))
    assert r.status_code == 404


# ── visibility ───────────────────────────────────────────────────────────────
def test_a_restricted_group_is_absent_rather_than_refused(app_and_maker):
    """404, never 403. A 403 confirms the group exists, and for a restricted
    group its existence is part of what is being withheld."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="watchlist", members=["cust-0"],
                visibility=groups.RESTRICTED)

    sales = _hdr(client, SALES)
    listed = client.get("/api/v1/groups", headers=sales).json()
    assert [g["slug"] for g in listed["groups"]] == []
    assert client.get("/api/v1/groups/CUSTOMER/watchlist",
                      headers=sales).status_code == 404
    assert client.get("/api/v1/accounts?group=watchlist",
                      headers=sales).status_code == 404

    manager = _hdr(client, MANAGER)
    assert [g["slug"] for g in
            client.get("/api/v1/groups", headers=manager).json()["groups"]] \
        == ["watchlist"]


def test_a_restricted_groups_name_does_not_reach_a_row_chip(app_and_maker):
    """The ``filterCounts.MFLOOR`` shape: the guard on one surface and the value
    on the next. Withholding a group from the picker while printing its name
    down the directory would disclose exactly what the picker withheld."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="watchlist", name="Below floor",
                members=["cust-0"], visibility=groups.RESTRICTED)

    rows = client.get("/api/v1/accounts", headers=_hdr(client, SALES)).json()
    assert all(row["groups"] == [] for row in rows)

    mgr_rows = client.get("/api/v1/accounts", headers=_hdr(client, MANAGER)).json()
    named = {r["customer_id"]: [g["name"] for g in r["groups"]] for r in mgr_rows}
    assert named["cust-0"] == ["Below floor"]


def test_a_description_can_be_cleared_and_not_only_set(app_and_maker):
    """A PATCH of ``{"description": null}`` means clear it.

    Read off the value rather than off which fields the caller sent, "not
    mentioned" and "explicitly null" are the same thing — so a description could
    be set and never unset, which is the sort of gap nobody finds until they
    have typed something into the wrong group.
    """
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, members=["cust-0"])
    manager = _hdr(client, MANAGER)

    client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=manager,
                 json={"description": "the PSU book"})
    assert client.get("/api/v1/groups/CUSTOMER/aerospace",
                      headers=manager).json()["description"] == "the PSU book"

    client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=manager,
                 json={"description": None})
    assert client.get("/api/v1/groups/CUSTOMER/aerospace",
                      headers=manager).json()["description"] is None

    # And a PATCH that does not mention the field leaves it alone.
    client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=manager,
                 json={"description": "back again"})
    client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=manager,
                 json={"name": "Renamed"})
    body = client.get("/api/v1/groups/CUSTOMER/aerospace", headers=manager).json()
    assert body["description"] == "back again"
    assert body["name"] == "Renamed"


def test_an_edited_group_still_says_who_drew_it(app_and_maker):
    """``created_by: null`` reads as "nobody drew this", not as "not looked up",
    and it is the one field whose point is that a judgement has a name on it."""
    client, maker = app_and_maker
    _seed_customers(maker)
    manager = _hdr(client, MANAGER)
    client.post("/api/v1/groups", headers=manager,
                json={"entity_kind": "CUSTOMER", "name": "Aerospace"})

    patched = client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=manager,
                           json={"name": "Aero"}).json()
    assert patched["created_by"] == "M. Rao"


def test_a_salesperson_cannot_draw_or_edit_a_group(app_and_maker):
    """Reading is everyone's; drawing is policy, the same gate item categories
    sit behind."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, members=["cust-0"])
    sales = _hdr(client, SALES)

    assert client.post("/api/v1/groups", headers=sales, json={
        "entity_kind": "CUSTOMER", "name": "Mine"}).status_code == 403
    assert client.patch("/api/v1/groups/CUSTOMER/aerospace", headers=sales,
                        json={"name": "Renamed"}).status_code == 403
    assert client.post("/api/v1/groups/CUSTOMER/aerospace/members",
                       headers=sales,
                       json={"entity_ids": ["cust-1"]}).status_code == 403
    # And reading still works, which is the whole point of the split.
    assert client.get("/api/v1/groups/CUSTOMER/aerospace",
                      headers=sales).status_code == 200


# ── no economics anywhere on this surface ────────────────────────────────────
_FORBIDDEN = ("cost", "margin", "gross_profit", "unit_cost", "floor")


def _no_economics(payload) -> bool:
    """No key anywhere in the tree names cost or margin.

    Walks the whole structure rather than checking the top level: every
    field-level assertion in the quote suite passed while the endpoint gave up
    cost two levels down.
    """
    if isinstance(payload, dict):
        return all(not any(bad in str(k).lower() for bad in _FORBIDDEN)
                   and _no_economics(v) for k, v in payload.items())
    if isinstance(payload, list):
        return all(_no_economics(v) for v in payload)
    return True


def test_no_group_surface_carries_cost_or_margin_at_any_role(app_and_maker):
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, members=["cust-0", "cust-1"])

    for email in (SALES, MANAGER):
        headers = _hdr(client, email)
        for path in ("/api/v1/groups",
                     "/api/v1/groups/CUSTOMER/aerospace"):
            body = client.get(path, headers=headers).json()
            assert _no_economics(body), f"{path} as {email}: {body}"


# ── the bound recomputes rather than trims ───────────────────────────────────
def test_a_product_bounded_snapshot_equals_the_same_lines_filtered_after(session):
    """``sales_for_products`` is a filter on the query, so it has to agree with
    the same filter applied to the unbounded result — the ``test_bounded_loads``
    property. A bound that disagreed would be a wrong total with a plausible
    explanation attached, which is the class of defect this codebase keeps
    finding.
    """
    session.add(models.Organization(organization_id=ORG, name="T"))
    for i in range(3):
        session.add(models.Product(product_id=f"p{i}", organization_id=ORG,
                                   external_id=f"i{i}", name=f"Item {i}"))
    session.add(models.Customer(customer_id="c0", organization_id=ORG,
                                external_id="x", name="C"))
    for i in range(3):
        session.add(models.SalesTxn(
            organization_id=ORG, customer_id="c0", product_id=f"p{i}",
            external_ref=f"inv:{i}", date=date(2026, 1, 1 + i),
            qty=Decimal("2"), unit_price=Decimal("100"),
            line_revenue=Decimal("200")))
    session.flush()

    wanted = ["p0", "p2"]
    bounded = load_snapshot(session, ORG, sales_for_products=wanted)
    whole = load_snapshot(session, ORG)
    after = [r for r in whole.sales if r.product_id in set(wanted)]

    assert [(r.product_id, r.line_revenue) for r in bounded.sales] \
        == [(r.product_id, r.line_revenue) for r in after]
    assert sum(r.line_revenue for r in bounded.sales) == Decimal("400")


def test_an_empty_product_bound_is_a_bound_and_not_an_absent_filter(session):
    """The half that would make an empty group answer with the whole book."""
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Product(product_id="p0", organization_id=ORG,
                               external_id="i0", name="Item"))
    session.add(models.Customer(customer_id="c0", organization_id=ORG,
                                external_id="x", name="C"))
    session.add(models.SalesTxn(
        organization_id=ORG, customer_id="c0", product_id="p0",
        external_ref="inv:0", date=date(2026, 1, 1), qty=Decimal("1"),
        unit_price=Decimal("10"), line_revenue=Decimal("10")))
    session.flush()

    assert load_snapshot(session, ORG).sales
    assert load_snapshot(session, ORG, sales_for_products=[]).sales == []


# ── the group travels with the answer ────────────────────────────────────────
def test_a_scoped_answer_names_the_group_and_its_version(app_and_maker):
    """Beside ``thresholds_version``, and for the same reason: two readings
    taken either side of a membership edit are different questions."""
    client, maker = app_and_maker
    _seed_customers(maker)
    version = _make_group(maker, members=["cust-0", "cust-1"])

    body = client.get("/api/v1/insight/credit?group=aerospace",
                      headers=_hdr(client, MANAGER)).json()
    assert body["group"] == {"slug": "aerospace", "name": "Aerospace",
                             "entity_kind": "CUSTOMER",
                             "group_version": version, "members": 2}
    assert body["thresholds_version"]


def test_an_unscoped_answer_says_so_rather_than_naming_a_group(app_and_maker):
    """``None`` rather than a group that happens to hold everything — the same
    numbers, a different claim."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, members=["cust-0", "cust-1", "cust-2"])

    body = client.get("/api/v1/insight/credit",
                      headers=_hdr(client, MANAGER)).json()
    assert body["group"] is None


#: Every endpoint that takes a customer group, and the key its rows live under.
#: Parametrised rather than written out six times, because the property is the
#: same one at each: the scope is applied server-side and the answer names the
#: group it was computed over. A sixth endpoint added without a row here is an
#: endpoint whose scoping nothing checks.
CUSTOMER_SCOPED = [
    ("/api/v1/insight/credit", "accounts"),
    ("/api/v1/insight/cadence", "customers"),
    ("/api/v1/insight/journey", "series"),
    ("/api/v1/insight/lost-revenue", "causes"),
    ("/api/v1/insight/payments", "customers"),
    ("/api/v1/insight/composition", "series"),
]


@pytest.mark.parametrize("path,_rows_key", CUSTOMER_SCOPED)
def test_every_customer_scoped_screen_names_the_group_it_computed_over(
        app_and_maker, path, _rows_key):
    """The stamp travels with the answer, on all of them.

    Asserted per endpoint rather than once on a shared helper: the helper is
    trivially right and the failure mode is a screen that forgets to call it,
    which only a request can see.
    """
    client, maker = app_and_maker
    _seed_customers(maker)
    version = _make_group(maker, members=["cust-0"])
    manager = _hdr(client, MANAGER)

    scoped = client.get(f"{path}?group=aerospace", headers=manager)
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["group"] == {
        "slug": "aerospace", "name": "Aerospace", "entity_kind": "CUSTOMER",
        "group_version": version, "members": 1}

    # And unscoped says so, rather than naming a group that holds everything.
    assert client.get(path, headers=manager).json()["group"] is None


@pytest.mark.parametrize("path,_rows_key", CUSTOMER_SCOPED)
def test_every_customer_scoped_screen_refuses_an_unknown_group(
        app_and_maker, path, _rows_key):
    """404, not a silently unscoped whole book — the one failure that looks
    like a working screen."""
    client, maker = app_and_maker
    _seed_customers(maker)
    r = client.get(f"{path}?group=no-such-thing", headers=_hdr(client, MANAGER))
    assert r.status_code == 404, f"{path}: {r.status_code}"


@pytest.mark.parametrize("path,_rows_key", CUSTOMER_SCOPED)
def test_every_customer_scoped_screen_withholds_a_restricted_group(
        app_and_maker, path, _rows_key):
    """A salesperson naming a management-only group gets the same 404 a
    nonexistent one gets. A 403 would confirm it exists."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="watchlist", members=["cust-0"],
                visibility=groups.RESTRICTED)
    r = client.get(f"{path}?group=watchlist", headers=_hdr(client, SALES))
    assert r.status_code == 404, f"{path}: {r.status_code}"


def test_an_empty_group_explains_itself_rather_than_blaming_the_sync(app_and_maker):
    """"No customers have synced yet" would send somebody to re-run a sync that
    has already worked. The group is the reason, so the group is what it says."""
    client, maker = app_and_maker
    _seed_customers(maker)
    _make_group(maker, slug="nobody", name="Nobody yet", members=[])

    body = client.get("/api/v1/insight/credit?group=nobody",
                      headers=_hdr(client, MANAGER)).json()
    assert "Nobody yet" in body["empty_reason"]
    assert "sync" not in body["empty_reason"].lower()
