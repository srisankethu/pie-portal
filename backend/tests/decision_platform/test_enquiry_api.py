"""The corpus door, and the one rule that dies at a door.

``enquiry/capture.py`` was complete and tested from the day it landed. Nothing
called it, so ``inbound_lines`` held zero rows and every text technique in
``14-machine-learning.md`` §5.17–§5.21 was blocked on a table with no way in.
This suite covers the way in.

**Why it exists separately from ``test_inbound_line_capture``.** That suite pins
the no-normalisation rule at the function, and it cannot see the layer that
actually breaks it. ``constr(strip_whitespace=True)`` on the request body,
a ``@field_validator`` that trims, a ``.strip()`` before the call — each reads
as hygiene, each passes every function-level test, and each destroys the corpus.
So the horrible string below goes over the wire, and comes back out of an export
over the wire, and is compared byte for byte at both ends.

The second half is the Quote Builder hook, which is what makes the corpus fill
from work that already happens rather than from a habit somebody has to keep.
It captures a *subset* — asks that reached the Quote Builder, so asks somebody
chose to work — and the tests say so, because ``InboundLine``'s docstring makes
a stronger claim (a denominator that does not condition on success) that this
door cannot honour and a later adapter will have to.
"""
from __future__ import annotations

from app.config import settings
from app.domain import enums, models
from app.seed import SEED_PASSWORD

ORG = settings.DEFAULT_ORG_ID
SALES = "r.nair@pie.example"
MANAGER = "m.rao@pie.example"

#: The same string ``test_inbound_line_capture`` uses, on purpose: leading and
#: trailing whitespace, a tab, CRLF, doubled spaces, mixed case, a non-ASCII
#: dimension sign, a zero-width space, and a decomposed accent that unicode
#: normalisation would silently recombine. If the two suites ever disagree about
#: what survives, the layer between them is doing something.
MESSY = (
    "  pls quote\tCNMG 120408\r\n"
    "  2 nos  ⌀12 mm  end mill​\n"
    "for Reńe's job — URGENT!!  "
)


def _hdr(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _capture(client, email: str = SALES, **over):
    body = {"raw_text": MESSY, "channel": "WHATSAPP"}
    body.update(over)
    return client.post("/api/v1/enquiries", json=body, headers=_hdr(client, email))


# ── the rule ────────────────────────────────────────────────────────────────
def test_the_wire_does_not_tidy_the_customers_words(api_client, session):
    """The whole reason this file exists. Compared against the stored bytes."""
    created = _capture(api_client)
    assert created.status_code == 201, created.text

    row = session.get(models.InboundLine, created.json()["inbound_line_id"])
    assert row.raw_text == MESSY, (
        "something between the request body and the column changed the "
        "customer's text — a benchmark over it now measures the tidier")
    # And the way back out, which is the half an exporter breaks separately.
    assert created.json()["raw_text"] == MESSY


def test_the_export_hands_back_the_same_bytes(api_client):
    """A corpus that survives capture and dies on the way out is still lost."""
    _capture(api_client)
    body = api_client.get("/api/v1/enquiries",
                          headers=_hdr(api_client, MANAGER)).json()

    assert body["count"] == 1
    assert body["lines"][0]["raw_text"] == MESSY


def test_the_same_ask_twice_is_two_rows(api_client):
    """No content key, and none is possible: the same customer asking for the
    same part twice in a week is two enquiries, and a door that merged them
    would under-report exactly the repeat demand this table measures."""
    _capture(api_client)
    _capture(api_client)
    body = api_client.get("/api/v1/enquiries",
                          headers=_hdr(api_client, MANAGER)).json()

    assert body["count"] == 2


# ── the refusals, mapped ────────────────────────────────────────────────────
def test_a_channel_outside_the_closed_set_is_refused_and_names_it(api_client):
    """400 rather than 422: the body parsed and a domain rule said no. The
    message is the useful part, and the closed set is in it."""
    r = _capture(api_client, channel="CARRIER_PIGEON")

    assert r.status_code == 400
    assert "WHATSAPP" in r.json()["detail"]


def test_an_empty_ask_is_refused_rather_than_stored(api_client):
    """A captured line with no text inflates the coverage denominator with an
    ask nobody made — the §1 mistake, in the table built to avoid it."""
    r = _capture(api_client, raw_text="   \n\t ")

    assert r.status_code == 400
    assert "denominator" in r.json()["detail"]


def test_the_channels_endpoint_publishes_both_closed_sets(api_client):
    """So no client keeps its own copy and quietly offers last year's list."""
    body = api_client.get("/api/v1/enquiries/channels",
                          headers=_hdr(api_client, SALES)).json()

    assert set(body["channels"]) == {c.value for c in enums.InboundChannel}
    assert set(body["dispositions"]) == {d.value for d in enums.LineDisposition}


# ── dispositions ────────────────────────────────────────────────────────────
def test_an_undecided_line_reads_as_undecided_not_as_failed(api_client):
    """``None`` is UNKNOWN. "Answered with nothing" is a row saying ABSTAINED
    or NO_STOCK, and the two must never render the same."""
    line_id = _capture(api_client).json()["inbound_line_id"]
    body = api_client.get(f"/api/v1/enquiries/{line_id}",
                          headers=_hdr(api_client, SALES)).json()

    assert body["disposition"] is None
    assert body["disposition_history"] == []


def test_a_correction_supersedes_and_both_answers_stay_readable(api_client):
    """A coverage number that changed with no record of having changed cannot
    be defended, which is why the second table exists at all."""
    line_id = _capture(api_client).json()["inbound_line_id"]
    hdr = _hdr(api_client, SALES)
    first = api_client.post(f"/api/v1/enquiries/{line_id}/disposition",
                            json={"disposition": "NO_STOCK"}, headers=hdr)
    second = api_client.post(f"/api/v1/enquiries/{line_id}/disposition",
                             json={"disposition": "QUOTED"}, headers=hdr)

    assert first.json()["written"] is True
    assert second.json()["written"] is True
    body = api_client.get(f"/api/v1/enquiries/{line_id}", headers=hdr).json()
    assert body["disposition"] == "QUOTED"
    assert [d["disposition"] for d in body["disposition_history"]] == [
        "NO_STOCK", "QUOTED"]
    assert body["disposition_history"][0]["superseded_at"] is not None
    assert body["disposition_history"][1]["superseded_at"] is None


def test_re_asserting_the_same_verdict_writes_nothing(api_client):
    """``written=False`` is the guard working. A nightly job that could not see
    the difference would log a write every night for ever."""
    line_id = _capture(api_client).json()["inbound_line_id"]
    hdr = _hdr(api_client, SALES)
    api_client.post(f"/api/v1/enquiries/{line_id}/disposition",
                    json={"disposition": "QUOTED"}, headers=hdr)
    again = api_client.post(f"/api/v1/enquiries/{line_id}/disposition",
                            json={"disposition": "QUOTED"}, headers=hdr)

    assert again.json()["written"] is False


# ── scope ───────────────────────────────────────────────────────────────────
def test_a_salesperson_may_record_but_not_export_the_whole_corpus(api_client):
    """Recording is the work of whoever takes the enquiry. The bulk export is
    every word this tenant's customers have written, in one response."""
    assert _capture(api_client, email=SALES).status_code == 201
    assert api_client.get("/api/v1/enquiries",
                          headers=_hdr(api_client, SALES)).status_code == 403
    assert api_client.get("/api/v1/enquiries",
                          headers=_hdr(api_client, MANAGER)).status_code == 200


def test_a_line_this_tenant_does_not_hold_answers_as_a_missing_one(
        api_client, session):
    """One answer for two failures. A distinct refusal for the second confirms
    the row exists, which is most of what an enumeration is after."""
    other = models.InboundLine(
        organization_id="org_someone_else", raw_text="theirs",
        channel="EMAIL")
    session.add(other)
    session.commit()

    r = api_client.get(f"/api/v1/enquiries/{other.inbound_line_id}",
                       headers=_hdr(api_client, SALES))
    missing = api_client.get("/api/v1/enquiries/no-such-id",
                             headers=_hdr(api_client, SALES))

    assert r.status_code == missing.status_code == 404
    assert r.json()["detail"] == missing.json()["detail"]


# ── nothing here is economics ───────────────────────────────────────────────
def test_no_response_from_this_router_carries_a_number_from_the_books(
        api_client):
    """§1, at the surface most likely to leave the building. A disposition says
    what happened to a line; what the line was worth belongs to the quote it
    became."""
    line_id = _capture(api_client).json()["inbound_line_id"]
    hdr = _hdr(api_client, SALES)
    api_client.post(f"/api/v1/enquiries/{line_id}/disposition",
                    json={"disposition": "QUOTED"}, headers=hdr)

    one = api_client.get(f"/api/v1/enquiries/{line_id}", headers=hdr).text
    everything = api_client.get("/api/v1/enquiries",
                                headers=_hdr(api_client, MANAGER)).text
    blob = f"{one}{everything}".lower()
    for word in ("cost", "margin", "price", "revenue", "gross_profit"):
        assert word not in blob, f"{word!r} reached the enquiry surface"


def test_the_export_ceiling_refuses_before_it_loads_the_corpus(
        api_client, monkeypatch):
    """A ceiling checked after the load is a ceiling that has already paid.

    ``enquiry.export`` reads every line and every disposition row and builds a
    dataclass per line — raw customer text, so the rows are not small. Comparing
    ``len(lines)`` afterwards meant the refusal fired only once the process had
    done exactly the work the ceiling exists to prevent. It is a ``count(*)``
    now, and the message names the real number.
    """
    from app.routers import enquiries

    _capture(api_client)
    _capture(api_client)
    monkeypatch.setattr(enquiries, "_EXPORT_CEILING", 1)

    r = api_client.get("/api/v1/enquiries", headers=_hdr(api_client, MANAGER))

    assert r.status_code == 413, r.text
    assert "2 lines is past" in r.json()["detail"]
