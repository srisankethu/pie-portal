"""The database seam: rows in, a diagnosis out, and the row it is written to.

Every layer beneath this one is pure. This is the only module in the package
that holds a ``Session``, which is what lets the engine be tested without a
database and replayed without a request.

Three jobs, in order:

**Gather.** Load what the engine needs — this customer's and the market's
transactions, the item's purchases, the product's unit and family, the segment
roster, this customer's settled invoices, the supplier's payment term, the
quote's own source record with the declarations in force over it, and the
connection's migration cut-over — and shape them into ``EvidenceRow`` and
``CostObservation``.

**Run.** Push them through the same pipeline the tests exercise, in the same
order, with the same thresholds.

**Record.** Insert one append-only row. Never update: re-diagnosing writes a new
row, so a judgement made in June still says in December what it said in June.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ..config import CommercialThresholds
from ..insight import payments, settlements, terms
from ..quantity import band_for, bands
from .. import source_concepts
from . import baselines, comparables, evidence, intent, opportunity, rules

_ZERO = Decimal("0")

#: Prefix on every evidence hash. Versioned so that if what the hash *covers*
#: ever changes, an old hash is visibly of a different kind rather than merely
#: different — a silent change of definition would turn every stored diagnosis
#: into a false mismatch and teach everyone to ignore the check.
HASH_VERSION = "qdh1"


def evidence_hash(price_ids: Sequence[str], cost_ids: Sequence[str]) -> str:
    """A stable digest of the ordered evidence-row id set (§13).

    Order matters and is the caller's, not this function's: the pipeline already
    sorts on ``(event_date, evidence_id)``, a total order with the primary key as
    tie-break, so two runs over the same rows hash identically whatever order the
    database returned them in. Sorting again here would hide a caller that had
    stopped ordering.

    Only ids. Not the prices, not the band, not the verdict — the question this
    answers is "was the same evidence in front of it", and a hash over the
    conclusion could not distinguish a changed rule from changed data.
    """
    body = "|".join(("P:" + ",".join(price_ids), "C:" + ",".join(cost_ids)))
    return f"{HASH_VERSION}:{hashlib.sha256(body.encode()).hexdigest()}"


@dataclass(frozen=True)
class DiagnosisResult:
    """What one call produced, before anything is written."""

    owner: rules.OwnerDiagnosis
    opportunity: opportunity.Opportunity
    evidence_hash: str
    evidence_ids: tuple[str, ...]
    cost_ids: tuple[str, ...]


def diagnose_line(session: Session, org: str, *, quote_id: str, line_id: str,
                  customer_id: Optional[str], product_id: str, qty: Decimal,
                  quoted_unit_price: Optional[Decimal], as_of: date,
                  knowable_by: datetime, th: CommercialThresholds,
                  segment: Optional[frozenset[str]] = None,
                  backfill_before: Optional[date] = None) -> DiagnosisResult:
    """Diagnose one quote line as of a moment. Reads; writes nothing.

    ``knowable_by`` is separate from ``as_of`` and both are required. The first
    is the instant visibility is cut off at and the second is the commercial date
    the narrative speaks in; on a live quote they are the same day, and on a
    replay of a quote from March they are both March — which is the only reason a
    replay can reproduce anything.
    """
    product = session.get(models.Product, product_id)
    subject = comparables.Subject(
        customer_id=customer_id, product_id=product_id,
        family=_family_of(product), qty=qty, band=band_for(qty, th),
        unit=evidence.canonical_unit(product.uom if product else None),
        as_of=as_of)

    rows, costs = _load(session, org, subject=subject, as_of=as_of, th=th)

    kept, dropped = evidence.knowable_at(rows, knowable_by=knowable_by,
                                         backfill_before=backfill_before)
    kept, unnormalizable = evidence.normalize(kept, subject_unit=subject.unit)
    kept_costs, cost_dropped = evidence.costs_knowable_at(
        costs, knowable_by=knowable_by, backfill_before=backfill_before)
    excluded = dropped + unnormalizable + cost_dropped

    ladder = bands(th)
    axis = comparables.select_customer_axis(kept, subject=subject, bands=ladder,
                                            segment=segment)
    peer = comparables.select_peer_axis(kept, subject=subject, bands=ladder,
                                        segment=segment,
                                        recent_days=th.diagnosis_recent_days)

    own = [r for r in axis.rows if r.customer_id == customer_id]
    for_band, held = evidence.for_baseline(own)
    price = baselines.price_baseline(for_band, as_of=as_of, th=th, lost=own)
    cost = baselines.cost_baseline(kept_costs, as_of=as_of, th=th)

    evset = evidence.EvidenceSet(
        rows=kept, costs=kept_costs, excluded=excluded + held,
        backfill_cutover_unknown=(backfill_before is None))

    supplier = _supplier_credit(session, org, cost=cost, costs=kept_costs)
    record = _source_record(session, org, quote_id=quote_id)

    owner = rules.diagnose(
        line_id=line_id, subject_customer_id=customer_id, product_id=product_id,
        qty=qty, quantity_band=subject.band.label,
        quoted_unit_price=quoted_unit_price, as_of=as_of,
        knowable_by=knowable_by, axis=axis, peer=peer, price=price, cost=cost,
        evidence=evset, th=th,
        # The same cut-over the cost rows were filtered against. Threaded rather
        # than left to default, so the attribution's re-check of visibility asks
        # the question this load actually asked; a re-check with a different
        # boundary verifies nothing.
        backfill_before=backfill_before,
        # The working-capital reading's own evidence. Handed over whole and
        # unfiltered: the window and the visibility cut belong to the engine,
        # which applies this diagnosis's own lookback to them, and a filter here
        # would be a second window nobody could see.
        settlements=_settlements(session, org, customer_id),
        supplier_term=supplier.term,
        supplier_term_recorded_at=supplier.term_recorded_at,
        supplier_erp_days=supplier.erp_days,
        # The quote's own record and the declarations in force over it, as a
        # pair from one row so the system named on the first and the system the
        # second was loaded for cannot disagree. ``knowable_by`` and not
        # ``as_of``: a declaration is evidence like any other and one typed after
        # the quote was written is not a reading the quoter had, which is the
        # instant ``source_concepts.in_force`` asks for by name and refuses to
        # default.
        source_record=record,
        taxonomy=(source_concepts.in_force(
            session, org, connector=record.connector, entity=intent.ENTITY,
            at=knowable_by) if record.connector else None))
    opp = opportunity.compute(owner, th=th)

    # The cited set, not everything loaded: what the band was actually built
    # from is what a replay has to reproduce.
    price_ids = tuple(price.cited)
    cost_ids = tuple(cost.cited)
    return DiagnosisResult(owner=owner, opportunity=opp,
                           evidence_hash=evidence_hash(price_ids, cost_ids),
                           evidence_ids=price_ids, cost_ids=cost_ids)


# ── the quote's own record ───────────────────────────────────────────────────

def _source_record(session: Session, org: str, *,
                   quote_id: str) -> intent.SourceRecord:
    """The document the source system holds for this quote, if there is one.

    ``quote_id`` is an ``erp_quotes.external_ref`` on the path that diagnoses an
    issued quote, and the Quote Builder's own draft id on the path that diagnoses
    one being written. The second finds nothing, and that is the honest answer
    rather than a gap: a draft exists here and in no ERP, so it carries no source
    fields, and ``intent`` refuses with ``NO_SOURCE_RECORD`` and says so. It is
    deliberately not looked for anywhere else — this platform's own quote form is
    ``quote_field_definitions``, a different table about keys PIE mints, and
    reading one where the other is meant is the responsibility duplication
    CLAUDE.md §2 names.

    **Sorted in Python rather than in SQL.** ``external_ref`` is unique per
    (organization, connector, connection, ref) so two connections could in
    principle hold the same reference, and both keys are nullable — SQLite orders
    NULLs first and Postgres last, so an ``ORDER BY`` here would make the same
    book read differently on two engines. The platform already treats the
    reference as this organization's key for a quote (``insight/quote_book``
    builds its whole index on it), so this follows that rather than inventing a
    second answer; the sort only fixes which row wins if that assumption is ever
    wrong.

    One read per line, like the settlement load above — and unlike that one it is
    the *same* row every time, because every line of a request shares a quote.
    Kept here anyway: hoisting it would mean the router deciding which record the
    engine reads its concepts from, which is the shape §3 keeps out of routers,
    and ``replay.rediagnose`` calls this function directly and would need its own
    copy of the load. It is one indexed lookup on a column that already carries
    an index, against a per-line evidence query over a whole family's sales
    history.
    """
    rows = session.scalars(
        select(models.QuoteDoc).where(
            models.QuoteDoc.organization_id == org,
            models.QuoteDoc.external_ref == quote_id)).all()
    if not rows:
        return intent.NO_RECORD
    row = sorted(rows, key=lambda r: (r.connector or "", r.connection_id or ""))[0]
    return intent.SourceRecord(found=True, connector=row.connector,
                               attributes=row.source_attributes)


# ── the working-capital reading's own evidence ───────────────────────────────

def _settlements(session: Session, org: str, customer_id: Optional[str],
                 ) -> Sequence[payments.Settlement]:
    """This customer's settled invoices, or nothing when there is no customer.

    ``insight/settlements.load`` is the platform's one read of them — the same
    rows the payments screen and ``insight/financing`` compute over — and it is
    called rather than copied for the reason CLAUDE.md §2 gives: two loaders for
    one concept agree until somebody edits one, and the copy nobody reads is the
    one that drifts.

    **A line with no customer gets an empty sequence, not the book's.** Narrowing
    to one account is not a convenience here. ``payments.lag`` reads the party
    from its first row, so a mixed list would measure one account's habit and
    label it with another's name — ``working_capital.assess`` refuses one
    outright, and handing it the whole book would be that mistake with a
    ``ValueError`` at the end of it.

    The reading then refuses with ``NO_RECEIVABLE_DAYS``, which is the honest
    answer: a quote written against no account has no payment behaviour to read
    and no terms on record, and its refusal names the terms as what would finish
    it.
    """
    if customer_id is None:
        return ()
    # One read per line, like the evidence load above and for the same reason:
    # a line names its own customer and two lines on one quote need not name the
    # same one. It is the smallest of this function's per-line reads — one
    # account's settled invoices against a family's whole sales history — so it
    # does not change what a long quote costs; hoisting it would mean the router
    # deciding which rows the engine reasons over, which is the shape §3 keeps
    # out of routers.
    return settlements.load(session, org, customer_id)


@dataclass(frozen=True)
class _SupplierCredit:
    """How long this line's supplier lets us hold the money, from both records.

    Both, never one resolved into the other: ``terms.effective_days`` owns which
    of them wins and is the only place that decision is made. ``term_recorded_at``
    travels with the agreement because an agreement typed after the quote is not
    evidence the quoter had, and ``working_capital`` puts it through the engine's
    own ``is_knowable`` rather than comparing dates itself.
    """

    term: Optional[terms.Term]
    #: When the agreement last acquired the values above. ``updated_at`` rather
    #: than ``created_at``: ``vendor_payment_terms`` is upserted one row per
    #: vendor, so the creation stamp would vouch for a term that has since been
    #: retyped, and there is no earlier version kept to fall back on.
    term_recorded_at: Optional[datetime]
    #: ``Vendor.payment_terms_days`` — what Zoho's dropdown could express.
    erp_days: Optional[int]


_NO_SUPPLIER = _SupplierCredit(term=None, term_recorded_at=None, erp_days=None)


def _supplier_credit(session: Session, org: str, *, cost: baselines.CostBaseline,
                     costs: Sequence[evidence.CostObservation],
                     ) -> _SupplierCredit:
    """Whose credit this line is bought on, and on what terms.

    **The vendor comes from the purchases the cost baseline actually cited**,
    not from every purchase loaded and not from the item's whole history. The
    capital at risk is that baseline's ``expected_cost``, so the credit that
    funds it has to be the credit behind the same rows — reading a supplier off
    a purchase the baseline trimmed away would charge this line against a
    relationship its cost figure does not rest on.

    The **latest** of those rows wins, by the engine's own
    ``(event_date, evidence_id)`` order. An item bought from two suppliers has no
    single answer and this one is at least the current one; the alternative,
    blending two terms, would invent a supplier nobody trades with. Rows naming
    no vendor are passed over rather than treated as a supplier without terms.

    Nothing is filtered by visibility here. ``costs`` has already been through
    ``evidence.costs_knowable_at``, and the agreement's own stamp is checked by
    ``working_capital`` against the engine's one ``is_knowable`` — which is where
    the exclusion reason is reported from, so a second test here would be a
    second answer with nowhere to publish it.
    """
    cited = set(cost.cited)
    rows = sorted((c for c in costs
                   if c.evidence_id in cited and c.vendor_id),
                  key=lambda c: (c.event_date, c.evidence_id))
    if not rows:
        return _NO_SUPPLIER
    vendor_id = rows[-1].vendor_id

    agreed = session.scalar(
        select(models.VendorPaymentTerm).where(
            models.VendorPaymentTerm.organization_id == org,
            models.VendorPaymentTerm.vendor_id == vendor_id))
    vendor = session.get(models.Vendor, vendor_id)
    return _SupplierCredit(
        term=(terms.Term(days=agreed.days, basis=agreed.basis)
              if agreed is not None else None),
        term_recorded_at=agreed.updated_at if agreed is not None else None,
        erp_days=(vendor.payment_terms_days
                  if vendor is not None and vendor.organization_id == org
                  else None))


def _family_of(product: Optional[models.Product]) -> Optional[str]:
    """The family key for tiers 3 and 6.

    ``source_item_category`` — the operation a person in the business filed the
    item under, in their own words. Chosen over the ERP's own ``category``, which
    is empty on every item of the live master, and over ``manufacturer``, which
    says who makes a thing rather than what it is.

    It is evidence about an item, not a verified fact about it: the live master
    files an HSS reamer under Threading. That is why it ranks and explains here
    and gates nothing — the tiers it feeds are the loose ones, and a diagnosis
    never turns on a family alone.
    """
    if product is None:
        return None
    return product.source_item_category or product.category or None


def _load(session: Session, org: str, *, subject: comparables.Subject,
          as_of: date, th: CommercialThresholds,
          ) -> tuple[list[evidence.EvidenceRow], list[evidence.CostObservation]]:
    """Every row that could be comparable, bounded by the lookback window.

    Bounded on ``date`` rather than on visibility, because the visibility filter
    runs next and has to see the rows it is going to exclude — a query that
    pre-filtered them would leave the exclusion counts empty and a thin band
    would read as a quiet account rather than as a data gap.
    """
    since = as_of - timedelta(days=th.historical_lookback_days)
    product_ids = _family_product_ids(session, org, subject)

    sales = session.scalars(
        select(models.SalesTxn).where(
            models.SalesTxn.organization_id == org,
            models.SalesTxn.product_id.in_(product_ids),
            models.SalesTxn.date > since,
            models.SalesTxn.date <= as_of,
        )).all()

    units = _units_for(session, org, product_ids)
    families = _families_for(session, org, product_ids)

    rows = [
        evidence.EvidenceRow(
            evidence_id=t.sales_txn_id, source_table="sales_txns",
            customer_id=t.customer_id, product_id=t.product_id,
            event_date=t.date, recorded_at=t.source_recorded_at,
            # Every invoiced line is a price the customer accepted. Quoted
            # evidence has no producer yet — `erp_quotes` is header-grain — so
            # nothing here is ever QUOTED_WON or QUOTED_LOST, and the evidence
            # summary says so rather than letting a band of accepted prices
            # imply that all history is acceptance.
            evidence_class=evidence.REALIZED,
            qty=Decimal(t.qty), unit_price=Decimal(t.unit_price),
            unit=units.get(t.product_id),
            source_ref={**(t.source_ref or {}),
                        comparables.FAMILY_KEY: families.get(t.product_id)},
        )
        for t in sales
    ]

    # Cost is the item's own, never the family's: what a different insert cost
    # says nothing about what this one costs.
    purchases = session.scalars(
        select(models.CostRecord).where(
            models.CostRecord.organization_id == org,
            models.CostRecord.product_id == subject.product_id,
            models.CostRecord.date > since,
            models.CostRecord.date <= as_of,
        )).all()
    costs = [
        evidence.CostObservation(
            evidence_id=c.cost_record_id, source_table="cost_records",
            product_id=c.product_id, vendor_id=c.vendor_id, event_date=c.date,
            recorded_at=c.source_recorded_at, qty=Decimal(c.qty),
            unit_cost=Decimal(c.unit_cost), unit=units.get(c.product_id),
            source_ref=dict(c.source_ref or {}),
        )
        for c in purchases
    ]
    return rows, costs


def _family_product_ids(session: Session, org: str,
                        subject: comparables.Subject) -> list[str]:
    """The subject plus its family, or just the subject when it has none.

    An unclassified item is not in a family with every other unclassified item —
    15% of one live master carries no category at all, and treating a shared
    absence as a match would make one enormous comparable set out of the gaps.
    """
    if subject.family is None:
        return [subject.product_id]
    siblings = session.scalars(
        select(models.Product.product_id).where(
            models.Product.organization_id == org,
            models.Product.source_item_category == subject.family,
        )).all()
    return sorted({subject.product_id, *siblings})


def _units_for(session: Session, org: str, ids: Sequence[str]) -> dict[str, str]:
    rows = session.execute(
        select(models.Product.product_id, models.Product.uom).where(
            models.Product.organization_id == org,
            models.Product.product_id.in_(ids))).all()
    return {pid: evidence.canonical_unit(uom) for pid, uom in rows
            if evidence.canonical_unit(uom) is not None}


def _families_for(session: Session, org: str,
                  ids: Sequence[str]) -> dict[str, Optional[str]]:
    rows = session.execute(
        select(models.Product.product_id,
               models.Product.source_item_category,
               models.Product.category).where(
            models.Product.organization_id == org,
            models.Product.product_id.in_(ids))).all()
    return {pid: (cat or fallback or None) for pid, cat, fallback in rows}


# ── the connection's own settings ────────────────────────────────────────────

def backfill_cutover(session: Session, org: str,
                     connection_id: Optional[str] = None) -> Optional[date]:
    """This book's migration boundary, or ``None`` when nobody has stated one.

    ``None`` is read as "no migration" rather than as a date, and the engine
    reports ``backfill_cutover_unknown`` so a reader is told. Assuming an
    unstated cut-over would silently discard genuine history; assuming there is
    none builds bands out of a bulk load. Both are wrong and the only honest
    move is to say which one you are living with.

    Never falls back to a detected value. ``cutover.detect`` exists to put the
    evidence in front of a person, and a boundary inferred from row counts moves
    every time the counts do.
    """
    query = select(models.ZohoConnection.history_loaded_before).where(
        models.ZohoConnection.organization_id == org)
    if connection_id is not None:
        query = query.where(models.ZohoConnection.connection_id == connection_id)
    found = [d for (d,) in session.execute(query).all() if d is not None]
    # Several connections and no argument: the earliest wins, because a row is
    # only safe to trust if it is past *every* book's migration.
    return min(found) if found else None


def segment_roster(session: Session, org: str,
                   customer_id: Optional[str]) -> Optional[frozenset[str]]:
    """The customers this one is comparable to, from the groups it belongs to.

    An ``EntityGroup`` of customers is the segment — a named, versioned set
    somebody in the business drew, which is the only definition of "comparable
    account" this platform has and a better one than anything derivable.

    ``None`` when the customer is in no group, and the peer axis then degrades to
    every other customer and *says so*. A caller that read an empty frozenset as
    "no peers" would silence the account-level finding entirely.
    """
    if customer_id is None:
        return None
    group_ids = session.scalars(
        select(models.EntityGroupMember.group_id).where(
            models.EntityGroupMember.organization_id == org,
            models.EntityGroupMember.entity_id == customer_id)).all()
    if not group_ids:
        return None
    members = session.scalars(
        select(models.EntityGroupMember.entity_id).where(
            models.EntityGroupMember.organization_id == org,
            models.EntityGroupMember.group_id.in_(list(group_ids)))).all()
    return frozenset(members) or None


# ── writing ──────────────────────────────────────────────────────────────────

def record(session: Session, org: str, *, quote_id: str,
           result: DiagnosisResult) -> models.QuoteDiagnosis:
    """Insert one diagnosis. **Only ever an insert.**

    There is no update path in this module and there must not be one. Re-running
    a line writes a second row; which one was in force is a question of
    ``created_at``, and an edited row could not answer it at all.
    """
    owner = result.owner
    row = models.QuoteDiagnosis(
        organization_id=org, quote_id=quote_id, quote_line_id=owner.line_id,
        customer_id=owner.customer_id, product_id=owner.product_id,
        quantity=owner.qty, quantity_band=owner.quantity_band,
        quoted_unit_price=owner.quoted_unit_price,
        as_of=owner.as_of, knowable_by=owner.knowable_by,
        codes=list(owner.codes), context=list(owner.context),
        strength=owner.strength, surfaces=owner.surfaces,
        evidence_ids=list(result.evidence_ids),
        excluded=[e.to_dict() for e in owner.price.excluded],
        evidence_summary=dict(owner.evidence),
        evidence_hash=result.evidence_hash,
        price_band=owner.price.to_dict(),
        cost_baseline=owner.cost.to_dict(),
        peer_band=owner.peer.to_dict(),
        opportunity=result.opportunity.to_dict(),
        thresholds_version=owner.thresholds_version,
        engine_version=owner.engine_version,
    )
    session.add(row)
    # Flushed rather than left for the caller's commit, because the id is the
    # return value's only use: a dismissal points at this row, and handing back
    # an object whose primary key is still None is a trap that fails at the
    # *next* statement, where nothing names this function.
    session.flush()
    return row


def dismiss(session: Session, org: str, *, quote_diagnosis_id: str,
            reason_code: str, note: Optional[str] = None,
            user_id: Optional[str] = None) -> models.QuoteDiagnosisDismissal:
    """Record that somebody read a card and said it was wrong.

    Refuses a reason outside the vocabulary rather than storing it. A dismissal
    whose reason nobody can aggregate is a dismissal that tunes nothing, and the
    whole point of capturing them is to find out which rules are noise.

    Imported here rather than at module scope: ``render`` is the presentation
    layer and this is the seam, so the dependency runs one way at call time and
    a reader of either file is not sent to the other.
    """
    from .render import DISMISS_REASONS
    if reason_code not in DISMISS_REASONS:
        raise ValueError(
            f"{reason_code!r} is not a dismissal reason. One of: "
            f"{', '.join(sorted(DISMISS_REASONS))}")
    row = models.QuoteDiagnosisDismissal(
        organization_id=org, quote_diagnosis_id=quote_diagnosis_id,
        reason_code=reason_code, note=note, dismissed_by_user_id=user_id)
    session.add(row)
    return row


def latest(session: Session, org: str, *, quote_line_id: str,
           ) -> Optional[models.QuoteDiagnosis]:
    """The diagnosis in force for a line — the most recent row written for it."""
    return session.scalars(
        select(models.QuoteDiagnosis)
        .where(models.QuoteDiagnosis.organization_id == org,
               models.QuoteDiagnosis.quote_line_id == quote_line_id)
        # created_at then primary key: two rows written in the same instant
        # still have one answer, and it is the same answer on every run.
        .order_by(models.QuoteDiagnosis.created_at.desc(),
                  models.QuoteDiagnosis.quote_diagnosis_id.desc())
        .limit(1)).first()


def for_quote(session: Session, org: str, *, quote_id: str,
              ) -> list[models.QuoteDiagnosis]:
    rows = session.scalars(
        select(models.QuoteDiagnosis).where(
            models.QuoteDiagnosis.organization_id == org,
            models.QuoteDiagnosis.quote_id == quote_id)
        .order_by(models.QuoteDiagnosis.created_at.desc(),
                  models.QuoteDiagnosis.quote_diagnosis_id.desc())).all()
    seen: set[str] = set()
    out: list[models.QuoteDiagnosis] = []
    for row in rows:
        if row.quote_line_id in seen:
            continue
        seen.add(row.quote_line_id)
        out.append(row)
    return out


def cutover_observations(session: Session, org: str, *, limit: int = 5000,
                         ) -> list:
    """Creation stamps for ``cutover.detect``, newest document first.

    Both sides of the ledger, because a migration loads both and a boundary read
    off one of them alone would be a guess about the other.
    """
    from .cutover import Observation
    sales = session.execute(
        select(models.SalesTxn.date, models.SalesTxn.source_recorded_at)
        .where(models.SalesTxn.organization_id == org,
               models.SalesTxn.source_recorded_at.is_not(None))
        .limit(limit)).all()
    costs = session.execute(
        select(models.CostRecord.date, models.CostRecord.source_recorded_at)
        .where(models.CostRecord.organization_id == org,
               models.CostRecord.source_recorded_at.is_not(None))
        .limit(limit)).all()
    return [Observation(event_date=d, recorded_at=r)
            for d, r in list(sales) + list(costs)]


def _ids(rows: Iterable) -> tuple[str, ...]:
    return tuple(r.evidence_id for r in rows)
