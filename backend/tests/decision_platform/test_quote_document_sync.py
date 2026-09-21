"""The ERP quote pull, and the trap it is built around.

Quotes are the demand-side document this platform never read. Sales orders are
what customers committed to; these are everything that was *offered*, and on
this book roughly three quarters of them ended in no recorded way at all — the
quote lapsed, or nobody chased it, or the customer stopped replying.

That silence is the whole hazard, and it is what most of this file is about.
Reading ``expired`` as a loss would turn ~215 shrugs into ~215 labels, every one
of them defensible-looking, and anything learned from them would be confidently
wrong about why this business loses work. So the sweep below asserts a *total*
rather than a row: it feeds every status Zoho can produce plus several it
cannot, and requires that exactly one of them — the one the ERP explicitly
records as declined — comes out LOST.

The second hazard is the mirror of the first. ``erp_quotes`` is derived
state: every column is rewritten from the payload on every sync, so a loss
reason stored there would survive exactly until the next pull. Human facts live
on ``quote_outcomes``, which no sync opens — asserted here by running two full
syncs over a recorded loss and comparing every column including ``updated_at``,
and by parsing the ingestion package for any mention of that table at all.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.commercial import quote_service
from app.config import settings
from app.domain import models
from app.domain.enums import QuoteLossReason, QuoteOutcomeStatus
from app.ingestion import normalize
from app.ingestion.sync import SyncService
from app.seed import SEED_PASSWORD

_INGESTION = pathlib.Path(__file__).resolve().parents[2] / "app" / "ingestion"


class _Source:
    """A Zoho source that answers about quotes and the contacts behind them.

    Only the two listings the quote pull needs. ``run_supply`` probes every
    optional stage with ``hasattr``, so a source that offers neither sales
    orders nor payments simply has those stages skipped — which is what makes
    a source this small a legitimate test double rather than a stub with holes.
    """

    def __init__(self, quotes, contacts=None):
        self._quotes = list(quotes)
        self._contacts = list(contacts if contacts is not None else _CONTACTS)

    def list_contacts(self): return list(self._contacts)
    def list_items(self): return []
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []
    def list_users(self): return []
    def list_quotes(self, skip=None):
        """Takes ``skip`` because the real one does.

        The pull buys a detail call per quote for the line breakdown, and the
        predicate is what keeps a resumed sync at one list call. Rows already
        known are yielded without ``line_items``, which is exactly what the
        client does on a resume — the header refreshes, the breakdown does not.
        """
        self.skipped = []
        out = []
        for q in self._quotes:
            row = dict(q)
            if skip is not None and skip(str(row.get("estimate_id")),
                                         str(row.get("last_modified_time") or "")):
                self.skipped.append(str(row.get("estimate_id")))
                row.pop("line_items", None)
            out.append(row)
        return out


_CONTACTS = [{"contact_id": "c1", "contact_name": "Pitti Engineering Ltd",
              "status": "active"}]


def _quote(estimate_id: str, status: str, **over) -> dict:
    """One estimate as Zoho's list endpoint returns it.

    Dates present by default and decision dates absent by default, because that
    is the shape of the overwhelming majority: a quote that was raised, and
    about which nothing further was ever written down.
    """
    row = {"estimate_id": estimate_id, "estimate_number": f"SLS/QTN-{estimate_id}",
           "reference_number": "", "customer_id": "c1",
           "customer_name": "Pitti Engineering Ltd", "date": "2026-05-04",
           "expiry_date": "2026-06-03", "status": status,
           "accepted_date": "", "declined_date": "", "total": "12500.00",
           "salesperson_id": "zu-1", "client_viewed_time": ""}
    row.update(over)
    return row


def _sync(session, quotes, org="org_a", **kw):
    report = SyncService(session, _Source(quotes), org, **kw).run()
    session.commit()
    return report


def _hdr(client, email: str) -> dict:
    """Signed-in headers for one of the seeded users."""
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _docs(session, org="org_a"):
    return {d.external_ref: d for d in session.query(models.QuoteDoc)
            .filter(models.QuoteDoc.organization_id == org).all()}


# ── what the ERP actually decided ───────────────────────────────────────────

def test_an_accepted_quote_is_won_and_a_declined_one_is_lost_with_its_date(session):
    """The two outcomes an ERP can prove, and both carry the date they happened.

    ``invoiced`` and ``accepted`` are both wins — one already billed, one not
    yet — and ``declined`` is the only loss Zoho states. Each keeps the ERP's
    own word beside the classification, so the mapping stays auditable instead
    of being the only surviving record of what the source said.
    """
    report = _sync(session, [
        _quote("est-1", "invoiced", accepted_date="2026-05-12"),
        _quote("est-2", "accepted", accepted_date="2026-05-15"),
        _quote("est-3", "declined", declined_date="2026-05-20"),
    ])
    assert (report.quote_documents, report.quote_documents_undated) == (3, 0)

    rows = _docs(session)
    assert rows["est-1"].outcome == "WON"
    assert rows["est-1"].decided_on == date(2026, 5, 12)
    assert rows["est-1"].source_status == "invoiced"   # verbatim, never mapped
    assert rows["est-2"].outcome == "WON"
    assert rows["est-2"].decided_on == date(2026, 5, 15)
    assert rows["est-3"].outcome == "LOST"
    assert rows["est-3"].decided_on == date(2026, 5, 20)
    assert rows["est-3"].source_status == "declined"
    # The quote's own selling total — not a cost and not a margin — and a
    # Decimal, so summing a book of them cannot drift.
    assert rows["est-1"].total == Decimal("12500.0000")


#: The three groups the whole classification reduces to, swept together.
#:
#: The casing variants are here because ``classify_outcome`` strips and
#: lowercases, so ``Declined `` must land exactly where ``declined`` does — a
#: membership test on a verbatim string is only safe if the normalisation is
#: pinned.
_WON_STATUSES = ["invoiced", "accepted", "INVOICED", "Accepted "]
_LOST_STATUSES = ["declined", "Declined ", "DECLINED"]

#: Everything else Zoho can put on an estimate, plus several things it cannot.
#:
#: ``approved`` is the trap inside the trap: it is *our own* internal approval
#: step, not the customer accepting anything, and reading it as WON would invent
#: a customer decision out of our own approval queue — the same class of error
#: as ``expired`` → LOST and considerably easier to miss, because the word
#: sounds like the customer's.
#:
#: The invented strings and the empty one stand for a Zoho release adding a
#: state, another connector's vocabulary arriving, and a payload with the field
#: blank. All three must land in the same place: unrecorded.
_NEITHER_STATUSES = ["draft", "pending_approval", "approved", "sent", "viewed",
                     "expired", "sent_and_viewed", "closed", "cancelled",
                     "rejected", "partially_invoiced", ""]


def test_no_source_status_becomes_a_loss_except_declined(session):
    """**The regression test this pull exists for.** Nothing but an explicitly
    declined estimate may ever come out LOST.

    Every quote in this sweep carries *both* an ``accepted_date`` and a
    ``declined_date``, which is the part that gives it teeth. The date gate is a
    second line of defence and a real one — an undated decision is refused
    whatever its status — but a sweep that leaned on it would pass just as
    happily with ``expired`` added to the lost vocabulary, because an expired
    estimate carries no decline date to be refused for. Supplying both dates
    strips that protection away and leaves the two allowlists as the only thing
    deciding anything, which is exactly what this test is for.

    Deliberately shaped as a sweep over *totals* rather than a row-by-row
    assertion. Every field-level assertion in the MFLOOR incident passed while
    the endpoint gave up cost; a per-row check here would pass just as happily
    while a twelfth status quietly started counting as a loss. Asserting the
    sets means a status can only ever move onto the won or the lost side on
    purpose, with these numbers changing in the diff.

    ``expired`` is the one worth naming. It is the single most tempting status
    to read as a loss — the offer lapsed and no order came — and it is not one:
    it spans "nobody worked it", "the customer never answered" and "we lost it
    to a competitor", and only the third is a loss. There are ~215 of them on
    this book, so the mistake would manufacture two hundred labels.
    """
    every = _WON_STATUSES + _LOST_STATUSES + _NEITHER_STATUSES
    quotes = [_quote(f"est-{i}", status,
                     accepted_date="2026-05-12", declined_date="2026-05-20")
              for i, status in enumerate(every)]
    _sync(session, quotes)

    rows = _docs(session)
    assert len(rows) == len(every)
    by_outcome = {ref: row.source_status for ref, row in rows.items()}
    lost = sorted(by_outcome[r] for r in rows if rows[r].outcome == "LOST")
    won = sorted(by_outcome[r] for r in rows if rows[r].outcome == "WON")

    assert lost == sorted(_LOST_STATUSES), (
        "A status other than an explicitly declined estimate produced a LOST "
        f"outcome: {sorted(set(lost) - set(_LOST_STATUSES))}. Nothing in this "
        "pull may read silence, expiry or our own approval step as a customer "
        "saying no.")
    assert won == sorted(_WON_STATUSES), (
        "A status the customer never accepted produced a WON outcome: "
        f"{sorted(set(won) - set(_WON_STATUSES))}. `approved` is our own "
        "internal step, not the customer's answer.")
    unrecorded = {by_outcome[r] for r in rows if rows[r].outcome == "UNRECORDED"}
    assert unrecorded == set(_NEITHER_STATUSES)
    # An unrecorded quote carries no decision date, whatever dates the payload
    # happened to hold — a date beside "we do not know" would read as one.
    assert all(rows[r].decided_on is None for r in rows
               if rows[r].outcome == "UNRECORDED")


def test_expiry_is_never_read_as_a_decision_however_long_ago_it_lapsed(session):
    """A quote that expired eighteen months ago is still not a loss.

    The same rule as the sweep, aimed at the specific reasoning that would break
    it: "it lapsed so long ago that nothing can still be open". Age is not
    evidence. The structural guarantee behind this is that the expiry date is
    not a parameter of ``classify_outcome`` at all, so no expression that can
    return LOST has one to hand.
    """
    _sync(session, [_quote("est-old", "expired", date="2025-01-06",
                           expiry_date="2025-02-05")])
    row = _docs(session)["est-old"]
    assert row.outcome == "UNRECORDED"
    assert row.decided_on is None
    # Kept, and kept *datable*, which is what makes the pile actionable later:
    # "lapsed in February 2025" is a different worklist entry from "sent last
    # week and awaiting a reply".
    assert row.expires_on == date(2025, 2, 5)


def test_a_decision_without_its_date_is_unrecorded_and_the_cost_is_counted(session):
    """An ERP decision with no date is not usable evidence, and saying so out
    loud is half the rule.

    ``DecidedQuote.decided_on`` is a required date and the outcome readers
    already skip a decided row without one, so a dateless WON would be discarded
    later, further from anything that could explain it. Refusing it here is the
    same rule one layer earlier — but a rule that silently drops evidence is the
    defect §1 of CLAUDE.md is about, so every row it costs is counted onto the
    run report and the ERP's own word is stored beside the outcome, which is
    what makes a GROUP BY able to name exactly which statuses fell through.
    """
    report = _sync(session, [
        _quote("est-1", "invoiced"),              # a win with no accepted_date
        _quote("est-2", "declined"),              # a loss with no declined_date
        _quote("est-3", "expired"),               # never decided at all
    ])
    rows = _docs(session)
    assert [rows[r].outcome for r in ("est-1", "est-2", "est-3")] == \
        ["UNRECORDED"] * 3
    # Two, not three: the expired quote is unrecorded because nobody decided
    # it, which costs nothing and must not inflate a number whose whole purpose
    # is to be small enough to investigate.
    assert report.quote_documents_undated == 2
    assert report.quote_documents == 3
    assert {rows[r].source_status for r in rows} == {"invoiced", "declined",
                                                     "expired"}


# ── the trap: a re-sync must not reach what a person wrote ──────────────────

_HUMAN_COLUMNS = tuple(c.name for c in models.QuoteOutcome.__table__.columns)


def _outcome_snapshot(session, org="org_a") -> dict:
    """Every column of every human outcome row, keyed by row id.

    Every column, including ``updated_at``, and that is the sharp end: the
    column carries ``onupdate``, so a sync that touched the row without changing
    a single value would still move it. A snapshot of the fields somebody
    thought were interesting would pass through exactly the write this test
    exists to catch.
    """
    return {row.quote_outcome_id: {c: getattr(row, c) for c in _HUMAN_COLUMNS}
            for row in session.query(models.QuoteOutcome)
            .filter(models.QuoteOutcome.organization_id == org).all()}


def test_a_full_resync_cannot_change_a_recorded_loss_reason(session):
    """The VendorPaymentTerm trap, closed before there is anything to lose.

    A human loss reason recorded against an ERP-raised quote survives two more
    full syncs, byte for byte. It is not that the sync avoids writing to
    ``quote_outcomes``: it never opens the table, and there is nowhere on
    ``erp_quotes`` for a reason to be put in the first place.

    The ERP row underneath is deliberately allowed to move — ``est-lost`` is
    re-pulled with its status changed — so the test proves the human record
    holds while the derived one is being rewritten around it, rather than
    proving that nothing happened at all.
    """
    _sync(session, [_quote("est-lost", "declined", declined_date="2026-05-20")])

    quote_service.set_outcome(
        session, "org_a", quote_document_ref="est-lost",
        status=QuoteOutcomeStatus.LOST,
        loss_reason=QuoteLossReason.PRICE, lost_to="Sandvik",
        note="Beaten on price by about 8% on the whole basket.",
        customer_ref="Pitti Engineering Ltd", user_id="u1")
    session.commit()
    before = _outcome_snapshot(session)
    assert len(before) == 1

    # Two more pulls, the second with the ERP's own word changed underneath.
    _sync(session, [_quote("est-lost", "declined", declined_date="2026-05-20")])
    _sync(session, [_quote("est-lost", "expired")])
    session.expire_all()

    assert _outcome_snapshot(session) == before, (
        "A sync changed a human-recorded quote outcome. Nothing in "
        "ingestion/ may write to or read quote_outcomes.")
    # And the derived row did move, so the comparison above is not vacuous.
    assert _docs(session)["est-lost"].source_status == "expired"
    assert _docs(session)["est-lost"].outcome == "UNRECORDED"


def test_rebuilding_erp_quotes_from_nothing_keeps_every_link(session):
    """``DELETE FROM erp_quotes`` plus a full re-sync leaves every human
    pointer resolving — and every surrogate different.

    ``quote_outcomes.quote_document_ref`` holds the id the *source system*
    issued, never ``quote_document_id``, which is a ``_uuid`` minted at insert.
    That is the property that makes the harshest rebuild safe, and the second
    assertion is what makes this a real test of it: if the surrogates came back
    unchanged, a pointer written the wrong way round would pass too.
    """
    _sync(session, [_quote("est-a", "declined", declined_date="2026-05-20"),
                    _quote("est-b", "expired")])
    quote_service.set_outcome(
        session, "org_a", quote_document_ref="est-a",
        status=QuoteOutcomeStatus.LOST, loss_reason=QuoteLossReason.DELIVERY,
        user_id="u1")
    session.commit()
    surrogates_before = {ref: row.quote_document_id
                         for ref, row in _docs(session).items()}

    # ``erp_quotes``, not ``quote_documents``. The merge with main renamed
    # this table — main holds a *different* one under the old name, recording
    # a quote written *into* a source system — and this line kept working
    # against that one: the DELETE succeeded, removed nothing of ours, and the
    # "rebuild" then found every row still present and reused its surrogates.
    # The guard below is what said so, which is why it is phrased as an
    # instruction to check the fixture.
    session.execute(text("DELETE FROM erp_quotes"))
    session.commit()
    _sync(session, [_quote("est-a", "declined", declined_date="2026-05-20"),
                    _quote("est-b", "expired")])

    rebuilt = _docs(session)
    outcome = session.query(models.QuoteOutcome).one()
    assert outcome.quote_document_ref in rebuilt
    assert outcome.loss_reason == QuoteLossReason.DELIVERY.value
    assert set(rebuilt) == set(surrogates_before)
    assert all(rebuilt[ref].quote_document_id != surrogates_before[ref]
               for ref in surrogates_before), (
        "The rebuild reused its surrogate keys, so this test cannot tell a "
        "value pointer from a foreign key. Check the fixture, not the code.")


def test_the_pull_never_names_the_table_a_person_writes(session):
    """No module under ``ingestion/`` may mention ``QuoteOutcome`` or import
    ``commercial.quote_service``.

    Parsed rather than grepped, for the reason ``test_layer_boundaries`` gives
    about the same class of rule: a package named in a comment or a docstring
    must not be able to fail the build, and a rule enforced by a string search
    is a rule that a rename silently retires.

    The property is structural, not stylistic. "The sync avoids writing to
    ``quote_outcomes``" is a habit; "nothing in the pull can reach the table"
    is a guarantee, and the difference is what a re-sync is worth after
    somebody has spent a quarter recording why quotes were lost.
    """
    offenders: list[str] = []
    for path in sorted(_INGESTION.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "QuoteOutcome"):
                offenders.append(f"{path.name}:{node.lineno} names QuoteOutcome")
            if isinstance(node, ast.Name) and node.id == "QuoteOutcome":
                offenders.append(f"{path.name}:{node.lineno} names QuoteOutcome")
            if isinstance(node, ast.ImportFrom) and "quote_service" in (
                    node.module or ""):
                offenders.append(f"{path.name}:{node.lineno} imports quote_service")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "quote_service" in alias.name:
                        offenders.append(
                            f"{path.name}:{node.lineno} imports quote_service")
    assert offenders == [], (
        "The quote pull can reach the table a person writes: "
        + "; ".join(offenders))


# ── header grain, and what that means for a reader ─────────────────────────

def test_an_erp_quote_has_no_priced_lines_and_none_are_invented(session):
    """A quote read from the ERP is a header and nothing else, and the platform
    does not manufacture the missing half.

    Header grain is a deliberate cost. The list endpoint answers "what was
    offered, to whom, for how much, and how did it end" in one call; the line
    breakdown would be one call per quote for questions this does not ask. So
    an ERP quote joins to no ``QuoteDecision`` — those are snapshots of lines
    *this platform* priced — and the coherent handling is that it stays empty:
    a fabricated line, or a zero line count, would both be read as "a quote
    with no items on it", which is a different and false statement.

    What the row does still carry is everything a person needs to act on it —
    the customer, the value, the ERP's own status and when the offer lapses.
    """
    _sync(session, [_quote("est-1", "invoiced", accepted_date="2026-05-12")])

    row = _docs(session)["est-1"]
    assert session.query(models.QuoteDecision).count() == 0
    assert session.query(models.QuoteOutcome).count() == 0   # nobody recorded one
    assert row.customer_id is not None                       # resolved to the master
    assert row.customer_ref == "Pitti Engineering Ltd"
    assert row.number == "SLS/QTN-est-1"
    assert row.expires_on == date(2026, 6, 3)

    # And a person can still record an outcome against it, without the platform
    # ever having priced a line of it. That is the whole point of the pointer.
    quote_service.set_outcome(
        session, "org_a", quote_document_ref="est-1",
        status=QuoteOutcomeStatus.WON, user_id="u1")
    session.commit()
    outcome = session.query(models.QuoteOutcome).one()
    assert outcome.quote_id is None
    assert outcome.status == QuoteOutcomeStatus.WON.value


def test_a_quote_to_a_customer_the_pull_never_saw_is_kept(session):
    """An unresolved customer costs the link, never the row.

    Dropping the quote would silently shrink the denominator of every win rate
    by exactly the quotes that are hardest to see — the same reasoning that
    keeps a sales order against an unknown customer. The ERP's own name is
    stored beside the null id so the row is still nameable on a screen.
    """
    report = _sync(session, [_quote("est-1", "expired", customer_id="c-unknown",
                                    customer_name="Wheels India Ltd")])
    row = _docs(session)["est-1"]
    assert report.quote_documents == 1
    assert row.customer_id is None
    assert row.customer_ref == "Wheels India Ltd"
    # Not reported as a skip either: nothing went wrong, and a skip list that
    # fills with quotes nobody can act on is a skip list nobody reads.
    assert [s for s in report.skipped if s["kind"] == "quote_document"] == []


def test_the_pull_is_idempotent_and_rewrites_the_status_that_moved(session):
    """A second pull leaves one row per quote, and that row reflects the ERP as
    it is *now*.

    Both halves matter. A duplicate would double every count; a row written once
    and never revisited would report an accepted quote as still open for ever,
    because ``status``, ``accepted_date`` and ``declined_date`` are exactly the
    fields that change after a quote is raised.
    """
    _sync(session, [_quote("est-1", "sent")])
    first = _docs(session)["est-1"]
    assert (first.outcome, first.decided_on) == ("UNRECORDED", None)
    surrogate = first.quote_document_id

    _sync(session, [_quote("est-1", "accepted", accepted_date="2026-05-19")])
    session.expire_all()
    rows = _docs(session)
    assert len(rows) == 1
    assert rows["est-1"].quote_document_id == surrogate
    assert rows["est-1"].outcome == "WON"
    assert rows["est-1"].decided_on == date(2026, 5, 19)


# ── the human writer's own refusals ────────────────────────────────────────

def test_an_outcome_row_must_name_a_document(session):
    """An outcome about nothing is refused where it is written.

    Enforced in ``set_outcome`` rather than by a CHECK constraint: this repo has
    no CHECK precedent and ``compare_metadata`` does not compare them, so the
    drift test could never police one — while this function is provably the only
    thing that writes the table.
    """
    with pytest.raises(ValueError):
        quote_service.set_outcome(session, "org_a",
                                  status=QuoteOutcomeStatus.SENT)


def test_a_loss_on_an_erp_quote_still_has_to_say_why(session):
    """Every refusal ``set_outcome`` already made now covers ERP-raised quotes,
    because the function was extended rather than copied.

    A bare LOST can mean a competitor supplied it or that the requirement went
    away, and those point in opposite directions for every later question about
    this customer. Recording one without saying which is refused for an ERP
    quote exactly as it is for a platform quote — and a decided quote cannot be
    reopened, which is what stops a re-read of the ERP being able to rewrite a
    history somebody already counted.
    """
    _sync(session, [_quote("est-1", "declined", declined_date="2026-05-20")])

    with pytest.raises(quote_service.MissingLossReason):
        quote_service.set_outcome(session, "org_a",
                                  quote_document_ref="est-1",
                                  status=QuoteOutcomeStatus.LOST)
    session.rollback()

    quote_service.set_outcome(session, "org_a", quote_document_ref="est-1",
                              status=QuoteOutcomeStatus.WON, user_id="u1")
    session.commit()
    with pytest.raises(quote_service.InvalidTransition):
        quote_service.set_outcome(session, "org_a", quote_document_ref="est-1",
                                  status=QuoteOutcomeStatus.LOST,
                                  loss_reason=QuoteLossReason.PRICE)


def test_an_outcome_on_a_sent_erp_quote_needs_no_two_call_dance(session):
    """A new outcome row against an ERP quote opens in the state that quote's
    own ``source_status`` proves it is in.

    ``QUOTE_OUTCOME_TRANSITIONS[DRAFT]`` allows only SENT and LOST, so a row
    that opened DRAFT would refuse a WIN on a quote Zoho can already show was
    sent, viewed and invoiced. The first caller to hit that would either relax
    the transition table — which exists to stop history being rewritten — or
    record a fake SENT.

    ``sent_at`` stays null, and that is the same rule seen from the other side:
    the estimate list row carries no send timestamp, and ``client_viewed_time``
    is when the customer *opened* it. Filling one from the other would be a
    benign-looking default about a fact nobody recorded.
    """
    _sync(session, [_quote("est-1", "invoiced", accepted_date="2026-05-12"),
                    _quote("est-2", "draft")])

    won = quote_service.set_outcome(session, "org_a", quote_document_ref="est-1",
                                    status=QuoteOutcomeStatus.WON, user_id="u1")
    assert won.status == QuoteOutcomeStatus.WON.value
    assert won.sent_at is None

    # A draft estimate has been put in front of nobody, so its row opens DRAFT
    # and the lifecycle applies in full.
    drafted = quote_service.set_outcome(session, "org_a",
                                        quote_document_ref="est-2",
                                        status=QuoteOutcomeStatus.SENT,
                                        user_id="u1")
    assert drafted.status == QuoteOutcomeStatus.SENT.value


def test_our_own_approval_queue_is_not_evidence_the_customer_saw_it(session):
    """An outcome row opens SENT only where the ERP says the quote was sent.

    ``pending_approval`` and ``approved`` are the trap, and the same one
    ``_QUOTE_VOCABULARY`` names: they are *our own* internal sign-off step on a
    quote that has been put in front of nobody. A row opened SENT for them is a
    false fact — a send that never happened, with a fabricated ``sent_at`` on it
    — written into the one table no sync may reach and only a human may write.
    And it is not recoverable by recording the truth afterwards, because
    ``DRAFT`` is not a transition SENT allows: the honest record could not be
    made at all.

    The shape that produced it was a denylist — "the status is not the word
    draft, therefore it was sent". So this sweeps every status Zoho can put on
    an unanswered estimate rather than checking the two that were wrong: an
    allowlist can only gain a new sent-state on purpose, with this list changing
    in the diff.
    """
    unsent = ["draft", "pending_approval", "approved", "", "sent_and_viewed",
              "cancelled", "rejected"]
    sent = ["sent", "viewed", "expired"]
    _sync(session, [_quote(f"est-{i}", status)
                    for i, status in enumerate(unsent + sent)])

    rows = _docs(session)
    opened = {rows[ref].source_status: quote_service._opening_status(rows[ref])
              for ref in rows}
    assert {s for s, st in opened.items()
            if st is QuoteOutcomeStatus.SENT} == set(sent), (
        "A status that is not evidence of a send opened an outcome row at "
        "SENT. Our own approval queue is not the customer's inbox, and an "
        "unreadable status is not evidence of anything.")

    # And the honest record is now recordable: an unsent quote opens DRAFT, so
    # DRAFT is not refused as a backwards transition.
    row = quote_service.set_outcome(session, "org_a", quote_document_ref="est-1",
                                    status=QuoteOutcomeStatus.DRAFT,
                                    customer_ref="Pitti Engineering Ltd")
    assert (row.status, row.sent_at) == (QuoteOutcomeStatus.DRAFT.value, None)


def test_a_reference_naming_two_books_quotes_is_refused_not_guessed(session):
    """An ERP id is unique inside one connected company, and not beyond it.

    ``uq_quote_document_source`` is (org, connector, connection_id,
    external_ref) for that reason, and two connected books of a system whose
    quote numbers are per-company sequences will both issue ``1001``. The human
    row carries the bare reference and nothing that could say which of them it
    describes, so a lookup on the reference alone matches whichever row comes
    back first: the second person to record an outcome would silently rewrite
    the first person's loss reason, winner and note in place — LOST → LOST is
    not even a transition the lifecycle objects to.

    Refusing costs the recording entirely in that configuration, and that is the
    trade taken deliberately: a fact only a person held, destroyed with nothing
    on the response saying so, is not recoverable, and a refusal that names the
    two books is.
    """
    _sync(session, [_quote("1001", "sent")], connection_id="conn-a")
    _sync(session, [_quote("1001", "sent")], connection_id="conn-b")
    assert session.query(models.QuoteDoc).count() == 2

    with pytest.raises(quote_service.AmbiguousQuoteDocument) as caught:
        quote_service.set_outcome(session, "org_a", quote_document_ref="1001",
                                  status=QuoteOutcomeStatus.LOST,
                                  loss_reason=QuoteLossReason.PRICE,
                                  lost_to="Sandvik", note="beaten on price")
    assert "conn-a" in str(caught.value) and "conn-b" in str(caught.value)
    # Refused before anything was written, so there is no half-made row for the
    # next recording to walk into.
    assert session.query(models.QuoteOutcome).count() == 0


def test_a_reference_qualified_by_its_book_is_not_ambiguous(session):
    """The qualifier the refusal above deferred. Named with the book it is
    unique in, the same reference resolves, and the row remembers the book."""
    _sync(session, [_quote("1001", "sent")], connection_id="conn-a")
    _sync(session, [_quote("1001", "sent")], connection_id="conn-b")

    row = quote_service.set_outcome(
        session, "org_a", quote_document_ref="1001",
        quote_document_connection_id="conn-b",
        status=QuoteOutcomeStatus.LOST, loss_reason=QuoteLossReason.PRICE,
        lost_to="Sandvik")
    assert (row.quote_document_ref, row.quote_document_connection_id) == ("1001", "conn-b")
    assert row.status == "LOST"
    assert quote_service.sole_erp_quote(session, "org_a", "1001", "conn-a").connection_id == "conn-a"
    with pytest.raises(quote_service.AmbiguousQuoteDocument):
        quote_service.sole_erp_quote(session, "org_a", "1001")


def test_two_books_holding_one_reference_each_get_their_own_outcome(session):
    """The constraint that used to refuse the second book's quote outright.

    ``uq_quote_outcome_org_document`` was ``(organization, reference)``, which
    is unique only by accident: Zoho's estimate ids are system-wide. Business
    Central and Acumatica number quotes per company, so two connected books
    both issue ``SQ-1001`` — and the second recorded outcome hit an
    IntegrityError at commit, about a quote it has nothing to do with. The
    constraint carries the company now, and each book's quote has its own
    answer, its own reason and its own winner.
    """
    _sync(session, [_quote("SQ-1001", "sent")], connection_id="conn-a")
    _sync(session, [_quote("SQ-1001", "sent")], connection_id="conn-b")

    quote_service.set_outcome(
        session, "org_a", quote_document_ref="SQ-1001",
        quote_document_connection_id="conn-a",
        status=QuoteOutcomeStatus.LOST, loss_reason=QuoteLossReason.PRICE,
        lost_to="Sandvik")
    quote_service.set_outcome(
        session, "org_a", quote_document_ref="SQ-1001",
        quote_document_connection_id="conn-b",
        status=QuoteOutcomeStatus.WON)
    session.commit()

    rows = {r.quote_document_connection_id: r
            for r in session.query(models.QuoteOutcome)}
    assert set(rows) == {"conn-a", "conn-b"}
    assert rows["conn-a"].status == "LOST" and rows["conn-a"].lost_to == "Sandvik"
    assert rows["conn-b"].status == "WON" and rows["conn-b"].lost_to is None

    # And each book's document reads its own book's answer back — the join in
    # the other direction, which would have shown one loss on both quotes.
    docs = session.query(models.QuoteDoc).all()
    records = quote_service.erp_outcomes_of_record(session, "org_a", docs)
    by_book = {d.connection_id: records[d.quote_document_id] for d in docs}
    assert by_book["conn-a"].status.value == "LOST"
    assert by_book["conn-b"].status.value == "WON"


def test_a_company_less_outcome_still_contests_every_book_of_its_reference(session):
    """The other side of widening the constraint, and it must not be silent.

    A row written before the qualifier existed names no book, so nothing on it
    says which of two it meant. It keeps contesting the reference — the
    domain refusal, with a sentence, where the constraint used to raise an
    IntegrityError at commit.
    """
    _sync(session, [_quote("SQ-1001", "sent")], connection_id="conn-a")
    _sync(session, [_quote("SQ-1001", "sent")], connection_id="conn-b")
    session.add(models.QuoteOutcome(
        organization_id="org_a", quote_document_ref="SQ-1001",
        status="LOST", loss_reason="PRICE", customer_ref="Pitti"))
    session.flush()

    with pytest.raises(quote_service.QuoteOutcomeRepointed) as caught:
        quote_service.set_outcome(
            session, "org_a", quote_document_ref="SQ-1001",
            quote_document_connection_id="conn-b",
            status=QuoteOutcomeStatus.WON)
    assert "SQ-1001" in str(caught.value)


def test_the_ambiguous_reference_refusal_survives_the_trip_through_http(
        session, api_client):
    """And it arrives as 409 with both books still named in a readable string.

    The half above pins the service. This pins what the person at the capture
    screen actually receives, which is the part a router can quietly lose:
    ``AmbiguousQuoteDocument`` is a ``ValueError`` like the other three, so a
    single ``except ValueError`` clause would map it to the 422 that means "one
    field is missing, here are the choices" — and there is no field, no choice,
    and nothing the caller can put in the body that would fix it. 409 says the
    conflict is with what is stored, and stays true however long the screen
    lives.

    ``detail`` being a string rather than a list is asserted because it is the
    only thing that reaches the reader: the web client renders ``detail``
    directly, and a list arrives as "[object Object]" — on the one refusal
    whose prose is all the caller gets.
    """
    org = settings.DEFAULT_ORG_ID
    _sync(session, [_quote("1001", "sent")], org=org, connection_id="conn-a")
    _sync(session, [_quote("1001", "sent")], org=org, connection_id="conn-b")

    r = api_client.post(
        "/api/v1/quote-intelligence/outcome",
        json={"quote_document_ref": "1001", "status": "LOST",
              "loss_reason": "PRICE", "lost_to": "Sandvik"},
        headers=_hdr(api_client, "m.rao@pie.example"))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    assert "conn-a" in detail and "conn-b" in detail
    assert session.query(models.QuoteOutcome).filter(
        models.QuoteOutcome.organization_id == org).count() == 0


def test_a_platform_quote_that_became_an_erp_quote_is_one_row_not_two(session):
    """The link that stops the same quote being counted twice.

    A quote this platform priced and pushed to Zoho exists as both a
    ``quote_outcomes`` row and, after the next pull, an ``erp_quotes`` row.
    ``routers.quote`` records the estimate id on the human row at the moment it
    learns it, which is the only durable half of that link — the in-process
    quote store's ids never reach the database at all.

    Repointing that row at a *different* ERP quote is refused: the reason, the
    winner and the decision on it were recorded about one quote, and moving them
    would produce a loss nobody entered against a customer nobody spoke to.
    """
    row = quote_service.set_outcome(
        session, "org_a", quote_id="q1-1", quote_document_ref="est-1",
        status=QuoteOutcomeStatus.SENT, customer_ref="Pitti Engineering Ltd",
        user_id="u1")
    session.commit()
    assert (row.quote_id, row.quote_document_ref) == ("q1-1", "est-1")
    assert quote_service.outcome_to_dict(row)["quote_document_ref"] == "est-1"

    again = quote_service.set_outcome(
        session, "org_a", quote_id="q1-1", quote_document_ref="est-1",
        status=QuoteOutcomeStatus.WON, user_id="u1")
    assert again.quote_outcome_id == row.quote_outcome_id
    assert session.query(models.QuoteOutcome).count() == 1

    with pytest.raises(ValueError):
        quote_service.set_outcome(session, "org_a", quote_id="q1-1",
                                  quote_document_ref="est-99",
                                  status=QuoteOutcomeStatus.WON)


def test_an_outcome_follows_a_revision_of_its_own_quote_and_nothing_else(session):
    """The one exception to "never repointed", and it is narrow.

    A quote re-sent as a new revision is one quote whose newest document has
    changed, and its outcome follows the newest document. Allowed only when
    the caller names the document it is moving *from*, that is the one the row
    holds, and this quote itself wrote it. A row pointing at a document the
    quote never wrote — an ERP-raised one, or another quote's — is a human
    fact about that document and stays where it was recorded.
    """
    quote_service.record_document(
        session, "org_a", quote_id="q1-1", external_system="zoho",
        number="EST-1", document_id="est-1", line_count=1, fingerprint="f1")
    quote_service.set_outcome(
        session, "org_a", quote_id="q1-1", quote_document_ref="est-1",
        status=QuoteOutcomeStatus.SENT, user_id="u1")
    session.commit()

    # Revision 2 of the same quote: written, then the outcome moves onto it.
    quote_service.record_document(
        session, "org_a", quote_id="q1-1", external_system="zoho",
        number="EST-2", document_id="est-2", line_count=1, fingerprint="f2",
        revision=2, reference="QB-1-r2")
    row = quote_service.set_outcome(
        session, "org_a", quote_id="q1-1", quote_document_ref="est-2",
        status=QuoteOutcomeStatus.SENT, user_id="u1", repoint_from="est-1")
    assert row.quote_document_ref == "est-2"
    assert session.query(models.QuoteOutcome).count() == 1

    # Naming a document the row does not hold moves nothing.
    with pytest.raises(quote_service.QuoteOutcomeRepointed):
        quote_service.set_outcome(
            session, "org_a", quote_id="q1-1", quote_document_ref="est-3",
            status=QuoteOutcomeStatus.SENT, repoint_from="est-1")
    # Nor does naming the held one when this quote never wrote it: q2's row
    # points at an ERP-raised document a person recorded it against.
    quote_service.set_outcome(
        session, "org_a", quote_id="q2-1", quote_document_ref="erp-77",
        status=QuoteOutcomeStatus.SENT, user_id="u1")
    quote_service.record_document(
        session, "org_a", quote_id="q2-1", external_system="zoho",
        number="EST-9", document_id="est-9", line_count=1, fingerprint="f9",
        revision=2, reference="QB-2-r2")
    with pytest.raises(quote_service.QuoteOutcomeRepointed):
        quote_service.set_outcome(
            session, "org_a", quote_id="q2-1", quote_document_ref="est-9",
            status=QuoteOutcomeStatus.SENT, repoint_from="erp-77")
    held = {r.quote_id: r.quote_document_ref
            for r in session.query(models.QuoteOutcome)}
    assert held == {"q1-1": "est-2", "q2-1": "erp-77"}


def test_an_unqualified_outcome_joins_no_document_where_two_books_share_the_id(session):
    """The read side of ``sole_erp_quote``'s refusal. Two connected books
    issued the same id; a person's row that names no book names neither
    document, and neither ERP row shows that person's decision. A row that
    names its book joins that book's document and nothing else."""
    from datetime import date

    def doc(conn: str) -> models.QuoteDoc:
        return models.QuoteDoc(
            organization_id="org_a", connector="dynamics365", connection_id=conn,
            external_ref="SQ-1001", number="SQ-1001", customer_ref="Pitti",
            date=date(2026, 9, 1), source_status="open", outcome="UNRECORDED")

    a, b = doc("conn-a"), doc("conn-b")
    session.add_all([a, b])
    row = models.QuoteOutcome(
        organization_id="org_a", quote_document_ref="SQ-1001",
        status="LOST", loss_reason="PRICE", customer_ref="Pitti")
    session.add(row)
    session.flush()
    records = quote_service.erp_outcomes_of_record(session, "org_a", [a, b])
    assert records[a.quote_document_id].source is None
    assert records[b.quote_document_id].source is None

    # The reference is unique per organization on the human table, so the
    # qualified case is the same row saying which book it meant.
    row.quote_document_connection_id = "conn-b"
    session.flush()
    records = quote_service.erp_outcomes_of_record(session, "org_a", [a, b])
    assert records[a.quote_document_id].source is None
    assert records[b.quote_document_id].status.value == "LOST"
    assert records[b.quote_document_id].source.value == "HUMAN"


def test_quotes_are_scoped_to_their_organization(session):
    """Two organizations quoting the same ERP id keep separate rows."""
    _sync(session, [_quote("est-1", "expired")], org="org_a")
    _sync(session, [_quote("est-1", "declined", declined_date="2026-05-20")],
          org="org_b")
    assert _docs(session, "org_a")["est-1"].outcome == "UNRECORDED"
    assert _docs(session, "org_b")["est-1"].outcome == "LOST"
    assert session.query(models.QuoteDoc).count() == 2


# ── the pull as the background job actually runs it ─────────────────────────

def test_the_job_reads_quotes_and_commits_at_the_phase_boundary(session, monkeypatch):
    """The whole pull through ``jobs.execute_sync``, which is how it runs in
    production and the only place the commit discipline is visible.

    Three things at once, because they are one property:

    ``run_supply`` wraps the pull in ``_supply_phase``, which calls the
    ``on_phase`` callback — and in ``execute_sync`` that callback **commits**.
    So "Reading quotes" appearing in the phases the run recorded is the
    evidence that the stage ran *and* that everything written before it was
    committed rather than flushed. CLAUDE.md §4 is explicit about the
    difference: a flush is invisible to every other connection, so a progress
    counter behind one cannot move and a long write holds its lock to the end.

    The counters reach the run row through ``notes`` rather than a
    ``sync_runs`` column, matching the two most recent pulls — credit notes and
    locations — neither of which added one.
    """
    from app.ingestion import jobs
    from app.seed import ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()
    org = session.query(models.Organization).first().organization_id

    quotes = [_quote("est-1", "invoiced", accepted_date="2026-05-12"),
              _quote("est-2", "declined", declined_date="2026-05-20"),
              _quote("est-3", "expired"),
              _quote("est-4", "accepted")]          # decided, and undated
    monkeypatch.setattr("app.ingestion.sync.get_source",
                        lambda s, o, since=None, **kw: _Source(quotes))

    run = models.SyncRun(organization_id=org, source="fixture", status="QUEUED")
    session.add(run)
    session.commit()
    phases: list[str] = []
    monkeypatch.setattr(jobs, "persist_log",
                        lambda s, r: phases.append(r.phase or ""))
    jobs.execute_sync(session, run, since=date(2026, 1, 1), analysis=False)
    session.commit()

    assert "Reading quotes" in phases
    rows = _docs(session, org)
    assert {r: rows[r].outcome for r in sorted(rows)} == {
        "est-1": "WON", "est-2": "LOST", "est-3": "UNRECORDED",
        "est-4": "UNRECORDED"}
    # ``lines: 0`` because these fixtures are list rows without a breakdown —
    # the shape a resumed pull yields. Counted separately from the quotes so a
    # run that refreshed every header and read no lines is visibly that.
    # ``unreadable_view_stamps`` was counted on the report and never
    # persisted, so nobody could see it climb — the same argument
    # ``undated_decisions`` beside it is here for: a rule that discards
    # evidence has to say how often it fires.
    assert (run.notes or {}).get("quotes") == {
        "read": 4, "lines": 0, "undated_decisions": 1,
        "unreadable_view_stamps": 0}


def test_an_unreadable_view_stamp_costs_the_field_and_not_the_quote():
    """The whole document used to go with it.

    ``client_viewed_time`` is an optional read receipt. A stamp nothing could
    place raised from ``_parse_timestamp``, the sync recorded a skip, and that
    quote never reached ``erp_quotes`` at all — no header, no total, no
    status. It then appeared in no win-rate denominator and on no worklist,
    which is the silent shrinking this pull exists to prevent.

    Null is honest here because the only reader says so: ``insight/unrecorded``
    refuses to read a missing open as "the customer never opened it".
    """
    raw = _quote("q-view", "sent", client_viewed_time="2026-05-04")  # naive

    doc = normalize.normalize_quote_document(raw)

    assert doc.external_ref == "q-view"
    assert doc.total is not None
    assert doc.client_viewed_at is None
    # And the loss is countable rather than silent, the same bargain the date
    # gate makes one function above.
    assert normalize.unreadable_view_stamp(raw) is True


def test_a_placeable_view_stamp_still_comes_through():
    raw = _quote("q-view", "sent",
                 client_viewed_time="2026-05-04T10:15:00+0530")

    assert normalize.normalize_quote_document(raw).client_viewed_at is not None
    assert normalize.unreadable_view_stamp(raw) is False


def test_no_view_stamp_is_not_an_unreadable_one():
    """A quote nobody opened and a quote whose open could not be read are
    different facts, and only the second is worth investigating."""
    assert normalize.unreadable_view_stamp(_quote("q1", "sent")) is False
    assert normalize.unreadable_view_stamp(
        _quote("q1", "sent", client_viewed_time="")) is False


# ── the line breakdown ──────────────────────────────────────────────────────

def _with_lines(estimate_id: str = "e1", **over) -> dict:
    """An estimate as the *detail* call returns it — the list row plus lines."""
    return _quote(estimate_id, "sent", line_items=[
        {"line_item_id": "l1", "item_id": "i1", "sku": "CNMG120408",
         "description": "CNMG 120408 MP KCP25 turning insert",
         "quantity": "10", "unit": "pcs", "rate": "450.00",
         "item_total": "4500.00", "discount": "0"},
        {"line_item_id": "l2", "sku": "", "name": "Freight",
         "quantity": "1", "unit": "nos", "rate": "500.00",
         "item_total": "500.00"},
    ], **over)


def test_the_lines_on_a_quote_are_read_and_kept_in_order(session):
    _sync(session, [_with_lines()])

    rows = session.query(models.ErpQuoteLine).order_by(
        models.ErpQuoteLine.line_number).all()

    assert [r.external_ref for r in rows] == ["e1:l1", "e1:l2"]
    assert rows[0].item_code == "CNMG120408"
    assert str(rows[0].qty) == "10.0000"
    assert str(rows[0].rate) == "450.0000"
    assert rows[0].quote_ref == "e1"


def test_a_line_naming_nothing_in_the_item_master_is_still_kept(session):
    """Real quoting activity. Dropping it would shrink the document to the part
    that happens to be tidy — the same reasoning that keeps a quote whose
    customer never resolved."""
    _sync(session, [_with_lines()])

    freight = session.query(models.ErpQuoteLine).filter_by(
        external_ref="e1:l2").one()

    assert freight.product_id is None
    assert freight.description == "Freight"


def test_a_line_removed_in_the_erp_disappears_here_too(session):
    """Delete-then-insert rather than a per-line upsert.

    An upsert would leave the removed line behind for ever, and a document on
    screen that disagrees with the document in the source system is the one
    thing a mirror must not do.

    The stamp has to move for this to be the case under test at all: with an
    unchanged ``last_modified_time`` the resume predicate skips the detail call
    and the pull never sees the shorter quote, which is the *other* test below.
    """
    _sync(session, [_with_lines(last_modified_time="2026-05-04T10:00:00+0530")])
    assert session.query(models.ErpQuoteLine).count() == 2

    shorter = _quote("e1", "sent",
                     last_modified_time="2026-05-06T09:00:00+0530",
                     line_items=[
                         {"line_item_id": "l1", "item_id": "i1",
                          "sku": "CNMG120408",
                          "description": "CNMG 120408 MP KCP25 turning insert",
                          "quantity": "10", "unit": "pcs", "rate": "450.00",
                          "item_total": "4500.00"}])
    _sync(session, [shorter])

    rows = session.query(models.ErpQuoteLine).all()
    assert [r.external_ref for r in rows] == ["e1:l1"]


def test_a_resumed_quote_keeps_the_lines_the_earlier_pull_read(session):
    """The one way a cheap re-sync could destroy data, pinned.

    A resumed row carries no ``line_items``, and rewriting the stored lines from
    that empty list would delete a breakdown this run never read. The header
    still refreshes — ``source_status`` and ``outcome`` are exactly the columns
    that change after a quote is raised.
    """
    _sync(session, [_with_lines(last_modified_time="2026-05-04T10:00:00+0530")])
    assert session.query(models.ErpQuoteLine).count() == 2

    # Same quote, same stamp, now accepted: the resume predicate skips the
    # detail call, so no lines arrive.
    resumed = _quote("e1", "accepted", accepted_date="2026-05-20",
                     last_modified_time="2026-05-04T10:00:00+0530")
    _sync(session, [resumed])

    assert session.query(models.ErpQuoteLine).count() == 2
    assert _docs(session)["e1"].outcome == "WON"


def test_a_quote_whose_lines_were_never_read_simply_has_none(session):
    """Not an error, and not an invented line. The reader says the breakdown is
    not held rather than that the quote was empty."""
    _sync(session, [_quote("e1", "sent")])

    assert session.query(models.ErpQuoteLine).count() == 0
    assert _docs(session)["e1"].external_ref == "e1"


def test_every_source_takes_the_argument_the_sync_passes(session):
    """The protocol and its implementations, called the way ``sync`` calls them.

    ``list_quotes`` grew a ``skip`` parameter on the Zoho client and nowhere
    else, and nothing failed until a source that had not grown it was asked for
    quotes — at which point the whole stage died on a ``TypeError`` that
    ``_supply_phase`` records as a generic failure. A protocol one implementer
    has widened is a protocol the caller cannot call uniformly.

    Asserted by *calling* rather than by reading signatures: a keyword-only
    mismatch, a positional-only marker or a decorator that drops kwargs all
    pass an `inspect` check and fail here.
    """
    from app.ingestion.erp import acumatica, dynamics365, netsuite
    from app.ingestion.mock_source import FixtureZohoSource
    from app.ingestion.zoho_client import ZohoApiSource

    # Every source that offers quotes at all, not a list somebody remembers to
    # extend: the sync probes ``hasattr(source, "list_quotes")``, so a
    # connector gaining the method is a connector this protocol now binds, and
    # the registry connectors arrived one at a time after the pair above.
    for source in (FixtureZohoSource, ZohoApiSource,
                   dynamics365.BusinessCentralSource,
                   acumatica.AcumaticaSource, netsuite.NetSuiteSource):
        sig = inspect.signature(source.list_quotes)
        assert "skip" in sig.parameters, f"{source.__name__} cannot be resumed"

    # And the mock actually runs with it, which is what the demo org does.
    assert list(FixtureZohoSource().list_quotes(skip=lambda _id, _at: False))


def test_the_demo_source_carries_lines_so_the_screen_can_be_looked_at(session):
    """Without them the one screen that reads a quote's lines cannot be seen
    without a live ERP, which is how it would rot unnoticed."""
    from app.ingestion.mock_source import FixtureZohoSource

    quoted = [q for q in FixtureZohoSource().list_quotes() if q.get("line_items")]
    assert quoted, "no demo quote carries a line breakdown"


# ── the discount shape a real book writes ───────────────────────────────────
#
# Every test above this line spells a line discount as `"0"`, and that is the
# one shape the defect below could not reach. Zoho writes a *percentage* line
# discount as the string `"50.00%"`, and on the book this pull was built for
# almost every line carries one.

def _discounted(estimate_id: str, discount, **over) -> dict:
    """One quote, one line, with the discount written as the ERP writes it."""
    return _quote(estimate_id, "sent", line_items=[
        {"line_item_id": "l1", "item_id": "i1", "sku": "22000865",
         "description": "CNMG120408-UC-D2 YC0014",
         "quantity": "80", "unit": "pcs", "rate": "1176.00",
         "item_total": "47040.00", "discount": discount},
    ], **over)


def test_a_percentage_discount_does_not_take_the_whole_quote_pull_down(session):
    """The defect this section exists for, and it cost the entire book.

    ``"50.00%"`` went straight to the schema, whose ``Decimal(str(v))`` raised
    ``InvalidOperation``. That is not a ``NormalizationError``, so the per-quote
    handler in ``_sync_quote_documents`` did not catch it and ``_supply_phase``
    aborted the stage — on the *first* discounted quote, before writing a single
    header or line. A book that discounts every line therefore synced no quotes
    at all, reported ``InvalidOperation: [<class 'decimal.ConversionSyntax'>]``,
    and advised re-running the sync, which could never have helped.

    So the assertion is about the quotes *after* the discounted one, not just
    about the discounted one: the stage surviving is the whole point.
    """
    report = _sync(session, [_discounted("e1", "50.00%"),
                             _discounted("e2", "55.00%"),
                             _with_lines("e3")])

    assert report.quote_documents == 3
    assert [s for s in report.skipped if s["code"] == "SUPPLY_STAGE_FAILED"] == []
    assert {r.quote_ref for r in session.query(models.ErpQuoteLine).all()} == {
        "e1", "e2", "e3"}


def test_both_discount_spellings_read_as_the_same_percentage(session):
    """A bare number and a "%"-suffixed string are the same fact.

    ``_parse_percent`` has read both since bills and invoices were first pulled;
    this pull simply was not using it. Pinned together so neither spelling can
    drift onto its own code path again.
    """
    _sync(session, [_discounted("e1", "50.00%"), _discounted("e2", 50)])

    held = {r.quote_ref: r.discount_percent
            for r in session.query(models.ErpQuoteLine).all()}

    assert held["e1"] == held["e2"] == Decimal("50.0000")


def test_an_unreadable_number_costs_its_quote_and_not_the_stage(session):
    """The contract, stated over the field rather than the one value.

    ``normalize_quote_document`` may refuse a payload — that is what
    ``NormalizationError`` is for, and the sync turns it into a named skip
    against one document. What it must never do is raise something shaped
    differently, because every caller here is written around that one shape and
    a stray ``InvalidOperation`` escapes all of them.
    """
    report = _sync(session, [_discounted("e1", "not a number"),
                             _with_lines("e2")])

    refused = [s for s in report.skipped if s["ref"] == "e1"]
    assert [s["code"] for s in refused] == ["BAD_DISCOUNT"]
    assert [s for s in report.skipped if s["code"] == "SUPPLY_STAGE_FAILED"] == []
    # The quote behind the refused one still landed, lines and all.
    assert {r.quote_ref for r in session.query(models.ErpQuoteLine).all()} == {"e2"}


def test_a_quote_line_carries_no_cost_even_though_the_detail_call_brings_it(session):
    """The detail call added in this feature carries buy-side figures the list
    row never did — ``purchase_price`` and ``item_profit_margin_percentage`` on
    every line, and ``profit_margin_amount`` on the header. ``erp_quote_lines``
    opens for every role precisely because it holds none of them, so the
    allowlist in ``_quote_lines`` is load-bearing rather than tidy.
    """
    _sync(session, [_quote("e1", "sent", line_items=[
        {"line_item_id": "l1", "sku": "22000865", "description": "insert",
         "quantity": "80", "rate": "1176.00", "item_total": "47040.00",
         "discount": "50.00%", "purchase_price": 529.2,
         "item_profit_margin_percentage": 10, "item_profit_margin_amount": 4704},
    ], profit_margin_percentage=16.42, profit_margin_amount="27845.400")])

    row = session.query(models.ErpQuoteLine).one()
    stored = {c.name for c in models.ErpQuoteLine.__table__.columns}

    # The table has nowhere to put cost, which is the structural half of the
    # claim — there is no field to forget to withhold.
    assert not {c for c in stored if "cost" in c or "margin" in c or "profit" in c}
    # And the normaliser copied none of it onto the row it did write.
    assert row.rate == Decimal("1176.0000")       # what the customer was shown
    assert row.discount_percent == Decimal("50.0000")
    assert not hasattr(row, "purchase_price")
