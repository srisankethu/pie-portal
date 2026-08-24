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

The second hazard is the mirror of the first. ``quote_documents`` is derived
state: every column is rewritten from the payload on every sync, so a loss
reason stored there would survive exactly until the next pull. Human facts live
on ``quote_outcomes``, which no sync opens — asserted here by running two full
syncs over a recorded loss and comparing every column including ``updated_at``,
and by parsing the ingestion package for any mention of that table at all.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.commercial import quote_service
from app.domain import models
from app.domain.enums import QuoteLossReason, QuoteOutcomeStatus
from app.ingestion.sync import SyncService

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
    def list_quotes(self): return list(self._quotes)


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
    ``quote_documents`` for a reason to be put in the first place.

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


def test_rebuilding_quote_documents_from_nothing_keeps_every_link(session):
    """``DELETE FROM quote_documents`` plus a full re-sync leaves every human
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

    session.execute(text("DELETE FROM quote_documents"))
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


def test_a_platform_quote_that_became_an_erp_quote_is_one_row_not_two(session):
    """The link that stops the same quote being counted twice.

    A quote this platform priced and pushed to Zoho exists as both a
    ``quote_outcomes`` row and, after the next pull, a ``quote_documents`` row.
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
    assert (run.notes or {}).get("quotes") == {"read": 4, "undated_decisions": 1}
