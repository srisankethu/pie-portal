"""Realistic demo dataset for the Decision Platform UI.

Builds a multi-account read model whose histories deterministically trigger the
five signal families, then runs the real pipeline (detectors → decision
generation) so the UI renders genuine, role-gated decisions end to end. This is
demo/seed data only — it inserts into the read model exactly as a Zoho sync
would, then lets the deterministic engine and AI layer do their normal work.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import memberships
from .commercial import source_concepts
from .config import settings
from .domain import models
from .domain.enums import Role
from .seed import ensure_org_and_users
from .signals.engine import run_detectors

# Reference "today" for the demo histories.
AS_OF = date(2026, 7, 22)

# Demo customers/products get fixed, human-readable primary keys — never the
# random uuid4 a real Zoho sync assigns. That is what makes them unambiguously
# identifiable later: no real synced row can ever collide with one of these
# ids, no matter what a real customer or product happens to be named.
_DEMO_CUSTOMERS = [
    ("cst_rane", "Rane Madras", "usr_sales"),
    ("cst_ace", "ACE Designers", "usr_sales"),
    ("cst_pitti", "Pitti Engineering Ltd", "usr_sales"),
    ("cst_brakes", "Brakes India", "usr_sales"),
    ("cst_tvs", "TVS Sundram Fasteners", "usr_sales"),
]
# Every demo item states a unit. Without one ``evidence.normalize`` excludes
# the row as ``NO_UNIT`` — a quantity in no unit has no meaning, and the engine
# never guesses one — so an item master with a blank ``uom`` makes every
# transaction for it unusable as evidence. "Nos" is how these books spell a
# discrete count; ``evidence.canonical_unit`` folds it to ``each``.
_DEMO_PRODUCTS = [
    ("prd_cnmg", "CNMG 120408-MP insert", "Nos"),
    ("prd_dnmg", "DNMG 150608-MP insert", "Nos"),
    ("prd_holder", "25mm shank turning holder", "Nos"),
    ("prd_ream", "8.0mm HSS-Co machine reamer", "Nos"),
]
# One supplier, with both readings of its payment term: what Zoho's dropdown
# could express and what was actually agreed. ``VendorPaymentTerm`` exists for
# exactly that gap, and the quote diagnosis's working-capital reading refuses
# outright when neither is on record.
_DEMO_VENDORS = [("vnd_kennametal", "Kennametal India Limited", 30)]
_DEMO_VENDOR_ID = _DEMO_VENDORS[0][0]

#: The system the demo documents claim to have come from. Every demo row
#: already says ``{"system": "zoho"}`` in its ``source_ref``; the quote below
#: needs it as a column because ``source_concepts.in_force`` matches a
#: declaration to a record by connector and refuses to guess one.
_DEMO_CONNECTOR = "zoho"

#: The one quote the ERP "issued" in this dataset. Fixed and human-readable for
#: the reason the customer and product ids are.
_DEMO_QUOTE_REF = "est-demo-ace-01"
#: The connected company every demo document belongs to. An ERP reference is
#: unique only inside one book, and every reader is qualified by it now; a
#: demo whose documents named no company was a demo of the unqualified path.
_DEMO_CONNECTION_ID = "cx_demo"
#: The estimate the demo's *platform* quote became in that company's book —
#: sent from the Quote Builder, then accepted there, so the one join the
#: outcome of record turns on can be seen without a live ERP.
_DEMO_SENT_QUOTE_REF = "est-demo-pitti-01"

#: The field this organization is declared to record a quote's intent in, and
#: what it wrote on that quote. A source key, named here and nowhere
#: downstream — the whole point of ``commercial/source_concepts``.
_DEMO_QUOTE_INTENT_KEY = "cf_quote_type"

#: Stamped on the declaration so a purge can find exactly this row and no
#: administrator's. ``source_ref`` is the column's own purpose: where a
#: declaration came from, so a wrong reading can be traced to whoever made it.
_DEMO_DECLARATION_SOURCE = "PIE demo seed"

DEMO_CUSTOMER_IDS = frozenset(c[0] for c in _DEMO_CUSTOMERS)
DEMO_PRODUCT_IDS = frozenset(p[0] for p in _DEMO_PRODUCTS)
DEMO_VENDOR_IDS = frozenset(v[0] for v in _DEMO_VENDORS)
DEMO_QUOTE_REFS = frozenset({_DEMO_QUOTE_REF, _DEMO_SENT_QUOTE_REF})
DEMO_CONNECTION_IDS = frozenset({_DEMO_CONNECTION_ID})

#: How long after the commercial date the source system recorded the document.
#: Not a rounding of "the same day": ``SalesTxn.source_recorded_at`` and
#: ``CostRecord.source_recorded_at`` both record the measurement these come
#: from — an invoice is authored in the ERP and 99.5% of them carry no lag at
#: all, while a bill is transcribed from a supplier's document that arrived
#: later and runs a three-day median. One number for both would make the sell
#: side look slower than it is and the buy side faster.
_INVOICE_ENTRY_LAG_DAYS = 0
_BILL_ENTRY_LAG_DAYS = 3

#: The time of day a document is stamped with — 10:00 in the book's own zone.
#: Arbitrary but fixed: the demo may not read a clock, and the diagnosis
#: engine's cut-off is the end of a day, so any working hour is inside it.
_ENTRY_TIME = time(4, 30)


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _stamp(when: date, *, lag_days: int = 0) -> datetime:
    """When the source system recorded a document dated ``when``.

    **Timezone-aware, and that is the whole of it.** This is the value
    ``quote_diagnosis.evidence.is_knowable`` compares against a quote's
    ``knowable_by`` to decide whether a row was visible yet, and a stamp with no
    offset cannot be placed on the UTC line at all — which is why
    ``clock.utc_stamp`` returns ``None`` for one rather than assuming a zone.
    A row with no stamp is excluded as ``NO_RECORDED_AT``, so a demo history
    without these is history no diagnosis may cite.
    """
    return datetime.combine(when + timedelta(days=lag_days), _ENTRY_TIME,
                            tzinfo=timezone.utc)


def _sale(org, cust, prod, when, qty, price, inv, line="l1"):
    return models.SalesTxn(
        organization_id=org, external_ref=f"{inv}:{line}", customer_id=cust,
        product_id=prod, date=when, qty=Decimal(str(qty)), unit_price=Decimal(str(price)),
        line_revenue=Decimal(str(qty)) * Decimal(str(price)),
        source_recorded_at=_stamp(when, lag_days=_INVOICE_ENTRY_LAG_DAYS),
        source_ref={"system": "zoho", "record_type": "invoice", "record_id": inv, "line_id": line})


def _cost(org, prod, when, qty, unit_cost, bill, line="l1",
          vendor=_DEMO_VENDOR_ID):
    # Demo bills carry no discount, so rate == the effective cost and the
    # discount is 0% — the same shape a real, undiscounted bill line produces.
    #
    # ``vendor_id`` is set because the diagnosis reads the supplier off the
    # purchases its cost baseline actually cited: a bill naming nobody funds
    # nothing, and the working-capital reading then has no term to work from.
    cost = Decimal(str(unit_cost))
    return models.CostRecord(
        organization_id=org, external_ref=f"{bill}:{line}", product_id=prod, date=when,
        vendor_id=vendor,
        qty=Decimal(str(qty)), unit_cost=cost, rate=cost, discount_percent=Decimal("0"),
        source_recorded_at=_stamp(when, lag_days=_BILL_ENTRY_LAG_DAYS),
        source_ref={"system": "zoho", "record_type": "bill", "record_id": bill, "line_id": line})


def provision_demo_org(session: Session, organization_id: str,
                       owner_email: str, *, name: str = "PIE Demo") -> models.User:
    """The organization and the one account the public demo door signs in as.

    Separate from ``seed.ensure_org_and_users`` because the two are different
    jobs: that one builds *the* deployment's tenant from settings, this one
    builds a named throwaway beside it. Pointing the public demo at the default
    organization instead would hand a stranger read access to whatever real book
    a single-tenant install keeps there, which is the one outcome this whole
    feature must not have.

    The account is created **with no password hash**, and that is the control
    rather than an omission: ``verify_password`` refuses a blank hash, so
    ``POST /auth/login`` cannot be talked into this account by any password.
    The only way in is the demo door, which mints a session without a
    credential and which `authz` then refuses every write.

    OWNER, so a visitor sees the screens worth seeing. That is safe only
    because the write refusal is a seam rather than a role check — see
    ``current_principal``.
    """
    org = session.get(models.Organization, organization_id)
    if org is None:
        session.add(models.Organization(
            organization_id=organization_id, name=name,
            currency=settings.DEFAULT_CURRENCY, country="IN", config={}))
        session.flush()

    email = owner_email.strip().lower()
    user = session.scalar(select(models.User).where(models.User.email == email))
    if user is None:
        user = models.User(
            user_id=f"usr_demo_{organization_id}"[:64],
            organization_id=organization_id, email=email,
            name="Demo Visitor", role=Role.OWNER.value,
            password_hash=None, active=True)
        session.add(user)
        session.flush()
    # The membership is what `load_principal` resolves the role from, so
    # without this the demo visitor signs in and is refused everything.
    # `ensure_member`, because this function is re-run whenever the demo
    # workspace is reseeded.
    memberships.ensure_member(session, organization_id=organization_id,
                              user_id=user.user_id, role=Role.OWNER)
    session.flush()
    return user


def seed_demo(session: Session, *, organization_id: Optional[str] = None) -> dict:
    """Idempotent-ish demo seed. Returns the generate summary.

    ``organization_id`` defaults to the deployment's own organization, which is
    what ``/internal/demo-seed`` has always done. Pass one to build the public
    demonstration tenant beside a real book instead.

    The demo ids are fixed and human-readable by design — no real synced row can
    collide with ``cst_rane`` — and ``Customer`` is keyed on that id alone, so
    **one database holds the demo data in exactly one organization.** Seeding a
    second returns "already seeded" rather than duplicating it.
    """
    org = organization_id or settings.DEFAULT_ORG_ID
    if org == settings.DEFAULT_ORG_ID:
        ensure_org_and_users(session)

    # Skip if already seeded — anywhere. See the note above on fixed ids.
    if session.get(models.Customer, "cst_rane"):
        return {"note": "demo already seeded"}

    for cid, name, uid in _DEMO_CUSTOMERS:
        session.add(models.Customer(customer_id=cid, organization_id=org, external_id=cid,
                                    name=name, assigned_user_id=uid, status="ACTIVE"))
    for pid, name, uom in _DEMO_PRODUCTS:
        session.add(models.Product(product_id=pid, organization_id=org, external_id=pid,
                                   name=name, uom=uom))
    for vid, name, zoho_days in _DEMO_VENDORS:
        session.add(models.Vendor(vendor_id=vid, organization_id=org,
                                  connector=_DEMO_CONNECTOR, external_id=vid,
                                  name=name, payment_terms_days=zoho_days,
                                  status="ACTIVE",
                                  source_ref={"system": "zoho",
                                              "record_type": "contact",
                                              "record_id": vid}))
    session.flush()

    rows: list = []

    # ── Rane Madras: CUSTOMER_DECLINE (high baseline, low recent) ────────────
    for i, da in enumerate([150, 140]):   # history
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 100, 103, f"inv-rane-h{i}"))
    for i, da in enumerate([120, 100, 95]):  # prior window, strong
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 100, 103, f"inv-rane-b{i}"))
    for i, da in enumerate([50, 30, 20]):     # recent window, down ~41%
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 59, 103, f"inv-rane-r{i}"))

    # ── ACE Designers: CUSTOMER_DORMANCY (regular then silent) ───────────────
    for i, da in enumerate([250, 216, 182, 148, 130, 96]):   # ~34d cadence, last 96d ago
        rows.append(_sale(org, "cst_ace", "prd_holder", _d(da), 12, 1800, f"inv-ace-{i}"))

    # ── Pitti: MARGIN_DETERIORATION on prd_dnmg (price fell, cost stable) ─────
    rows.append(_cost(org, "prd_dnmg", _d(200), 100, 372, "bill-dnmg-1"))
    for i, da in enumerate([120, 100, 95]):
        rows.append(_sale(org, "cst_pitti", "prd_dnmg", _d(da), 20, 506, f"inv-pitti-b{i}"))
    for i, da in enumerate([50, 30, 20]):
        rows.append(_sale(org, "cst_pitti", "prd_dnmg", _d(da), 20, 430, f"inv-pitti-r{i}"))

    # ── Brakes India: COST_PASS_THROUGH on prd_cnmg (cost jumped, price flat) ─
    rows.append(_cost(org, "prd_cnmg", _d(160), 100, 320, "bill-cnmg-1"))
    rows.append(_cost(org, "prd_cnmg", _d(8), 100, 349, "bill-cnmg-2"))   # +9%
    for i, da in enumerate([120, 100, 90]):
        rows.append(_sale(org, "cst_brakes", "prd_cnmg", _d(da), 30, 412, f"inv-brakes-b{i}"))
    for i, da in enumerate([5, 3, 1]):
        rows.append(_sale(org, "cst_brakes", "prd_cnmg", _d(da), 30, 414, f"inv-brakes-r{i}"))

    # ── what the demo supplier is actually bought on ─────────────────────────
    #
    # Purchases for the holder, so the diagnosis below has a cost baseline with
    # something to trim rather than a single point. Five of the six sit in a
    # narrow band; the sixth is a purchase well under it, which the trim removes
    # and reports as ``POSSIBLE_COST_DRIVEN`` — context with its row attached,
    # never an assertion about why it was cheap, because nothing in the ledger
    # records that. The newest of them predates the quote, so it is knowable.
    for i, (da, qty, unit_cost) in enumerate([
            (300, 10, 1180), (240, 10, 1195), (180, 15, 1160),
            (120, 10, 1190), (95, 5, 890), (40, 10, 1205)]):
        rows.append(_cost(org, "prd_holder", _d(da), qty, unit_cost, f"bill-holder-{i}"))

    for r in rows:
        session.add(r)
    session.flush()

    _seed_supplier_terms(session, org)
    _seed_connection(session, org)
    _seed_erp_quote(session, org)
    _seed_sent_quote(session, org)
    session.flush()

    run_detectors(session, org, as_of=AS_OF)
    from .decisions.service import DecisionService
    summary = DecisionService(session, org).generate()
    session.flush()
    return summary


def _seed_supplier_terms(session: Session, org: str) -> None:
    """What we actually agreed to pay the demo supplier in.

    Beside ``Vendor.payment_terms_days``, never over it: Zoho's dropdown can
    say 30 and the agreement is 45, and the difference between the two is the
    thing worth seeing — it is also the difference between the cash this
    business plans around and the cash it actually has.

    **The stamps are historical and set here rather than left to default.**
    ``working_capital`` puts ``updated_at`` through the engine's own
    ``is_knowable``: a term typed after a quote went out is not a term that
    quote was priced against, and a row stamped with the moment the demo was
    seeded would be excluded from every diagnosis of every demo quote — which
    is the same defect as the missing ``source_recorded_at`` this seed used to
    have, arriving through a different column.
    """
    agreed_on = _stamp(_d(200))
    session.add(models.VendorPaymentTerm(
        vendor_payment_term_id="vpt_demo_kennametal", organization_id=org,
        vendor_id=_DEMO_VENDOR_ID, days=45, basis="NET",
        note="Agreed at the annual distributor review; Zoho's dropdown can only "
             "say 30.",
        created_at=agreed_on, updated_at=agreed_on))


def _seed_erp_quote(session: Session, org: str) -> None:
    """One quote the ERP issued, worth opening.

    Two lines, and each is a different finding rather than two of the same one:

    * The **holder** is quoted at 1,650 against six invoices at 1,800 to this
      same account at this same quantity — below the range that account's own
      history supports, by enough per unit and in total to clear every gate in
      ``rules._surfaces``. It earns money at the price quoted; what it gives up
      is the difference.
    * The **insert** is quoted at 312 against purchases at 320 and 349, so it
      **loses money on every piece**. There is no history of this customer
      buying it, so the price side honestly answers ``INSUFFICIENT_EVIDENCE``
      and no card is raised — and the quote roll-up names the line anyway,
      because ``rollup.loss_lines`` is deliberately independent of the gate that
      decides what interrupts somebody.

    ``source_attributes`` carries the field this business fills in, under the
    key its own system wrote. Nothing reads that key except the declaration in
    ``_declare_quote_intent``; every rule downstream reads the concept.
    """
    raised_on = _d(3)
    # (product, qty, unit price, description). The line NUMBER is not written
    # here: it comes from ``enumerate`` below, because ``normalize`` builds it
    # that way (``for position, item in enumerate(...)``, no ``start=``) and a
    # seeded row that numbered itself differently from a synced one would be a
    # demo that disagrees with production about what line 1 is. It is
    # zero-based on the wire, and ``ErpQuoteScreen`` adds the 1 a reader sees.
    lines = [
        ("prd_holder", Decimal("12"), Decimal("1650"),
         "25mm shank turning holder"),
        ("prd_cnmg", Decimal("30"), Decimal("312"),
         "CNMG 120408-MP insert"),
    ]
    session.add(models.QuoteDoc(
        quote_document_id="qdoc_demo_ace", organization_id=org,
        connector=_DEMO_CONNECTOR, connection_id=_DEMO_CONNECTION_ID,
        external_ref=_DEMO_QUOTE_REF,
        number="QT-DEMO-0001", source_reference="ACE/RFQ/2026-114",
        customer_id="cst_ace", customer_ref="ACE Designers",
        date=raised_on, expires_on=raised_on + timedelta(days=30),
        source_status="sent", outcome="UNRECORDED",
        total=sum((qty * price for _, qty, price, _ in lines), Decimal("0")),
        source_attributes={_DEMO_QUOTE_INTENT_KEY: "Repeat order"},
        source_recorded_at=_stamp(raised_on),
        source_ref={"system": "zoho", "record_type": "estimate",
                    "record_id": _DEMO_QUOTE_REF}))
    for number, (pid, qty, price, description) in enumerate(lines):
        session.add(models.ErpQuoteLine(
            erp_quote_line_id=f"eqln_demo_ace_{number}", organization_id=org,
            connector=_DEMO_CONNECTOR, connection_id=_DEMO_CONNECTION_ID,
            external_ref=f"{_DEMO_QUOTE_REF}:{number}",
            quote_ref=_DEMO_QUOTE_REF, line_number=number, product_id=pid,
            item_code=pid, description=description, qty=qty, unit="Nos",
            rate=price, amount=qty * price, discount_percent=Decimal("0"),
            source_ref={"system": "zoho", "record_type": "estimate",
                        "record_id": _DEMO_QUOTE_REF, "line_id": str(number)}))
    _declare_quote_intent(session, org)


def _seed_connection(session: Session, org: str) -> None:
    """The one connected company the demo's documents belong to.

    **Disabled, and that is load-bearing.** It has no credential and nothing
    to pull, and three things read "the organization's first enabled Zoho
    company": ``connections.get_zoho_credentials`` with no connection named,
    ``catalog.seed_company_catalogues`` on every boot, and the sync's target
    list. Enabled, this row would have been that company on a fresh clone —
    the boot would have bound the shipped catalogue to it and the first live
    pull would have tried to decrypt a secret it does not hold. Disabled it
    is a name: ``origin.Companies`` labels by it (no enabled filter, on
    purpose), every demo document names it, and nothing tries to pull from
    it. A real sync purges it anyway.
    """
    session.add(models.ZohoConnection(
        connection_id=_DEMO_CONNECTION_ID, organization_id=org,
        connector=_DEMO_CONNECTOR, label="SLS Engineers (demo)",
        zoho_organization_id="demo-book", enabled=False))


def _seed_sent_quote(session: Session, org: str) -> None:
    """A quote this platform priced and sent, which the ERP then accepted.

    The other half of the lifecycle the ERP quote above shows. It goes through
    the real paths — minted by ``quote_workspace.create``, recorded by
    ``quote_service.record_document``, moved to SENT by ``set_outcome`` — so
    what the demo shows is what the product does, and a change to any of
    them changes the demo with it. The ERP's own row for the estimate says
    ``accepted``, so the workspace, Won & lost and the ERP tab all read it as
    WON with the ERP as its source: the one rule (``quote_service.decide``),
    seen from both sides, with no live book behind it.

    Stamped historically, like everything else here: a quote sent "now" would
    be the one row in the demo that moves relative to ``AS_OF``.
    """
    from . import quote_workspace
    from .commercial import policy as policy_service
    from .commercial import quote_service
    from .domain.enums import QuoteOutcomeStatus
    from .store import Line, store

    sent_on = _d(2)
    th = policy_service.load_for_org(session, org)
    quote = quote_workspace.create(
        session, org, user_id="usr_sales", customer="Pitti Engineering Ltd",
        customer_id="cst_pitti", connection_id=_DEMO_CONNECTION_ID)

    def clear_of_the_floor(cost: float) -> float:
        # Five points above the organization's margin floor, whatever the
        # policy says today: a seeded send below the floor would be a quote
        # the gate refuses, recorded as though it had passed.
        return float(round(cost / (1 - float(th.margin_floor) - 0.05), 0))

    lines = [
        ("prd_dnmg", "DNMG 150608-MP insert", 20, 506.0, clear_of_the_floor(372.0), 372.0),
        ("prd_cnmg", "CNMG 120408-MP insert", 30, 452.0, clear_of_the_floor(349.0), 349.0),
    ]
    for n, (pid, desc, qty, list_price, quoted, cost) in enumerate(lines):
        quote.lines.append(Line(
            id=f"ln_demo_pitti_{n}", raw=f"{pid} x {qty}", reqCode=pid,
            reqDesc=desc, reqQty=qty, rel="EXACT", supplyCode=pid,
            supplyDesc=desc, candidates=[], outcome="OK", semantics="EXACT",
            inBooks=True, itemId=pid, listPrice=list_price, quoted=quoted,
            priceSource="USER", cost=cost, costSource="BOOKS"))
    quote_workspace.save(session, quote, "usr_sales")
    draft = session.get(models.QuoteDraft, quote.id)
    draft.created_at = draft.updated_at = _stamp(sent_on)
    doc = quote_service.record_document(
        session, org, quote_id=quote.id, external_system=_DEMO_CONNECTOR,
        connection_id=_DEMO_CONNECTION_ID, number="QT-DEMO-0002",
        document_id=_DEMO_SENT_QUOTE_REF, line_count=len(lines),
        fingerprint=store.priced_fingerprint(quote), reference=quote.reference,
        thresholds_version=th.version)
    doc.written_at = _stamp(sent_on)
    outcome = quote_service.set_outcome(
        session, org, quote_id=quote.id, status=QuoteOutcomeStatus.SENT,
        quote_document_ref=_DEMO_SENT_QUOTE_REF,
        quote_document_connection_id=_DEMO_CONNECTION_ID,
        customer_ref="Pitti Engineering Ltd", customer_id="cst_pitti",
        user_id="usr_sales")
    outcome.sent_at = outcome.created_at = outcome.updated_at = _stamp(sent_on)

    # What the ERP holds for it, as the next pull would read it back.
    total = sum(Decimal(str(qty * quoted)) for _, _, qty, _, quoted, _ in lines)
    session.add(models.QuoteDoc(
        quote_document_id="qdoc_demo_pitti", organization_id=org,
        connector=_DEMO_CONNECTOR, connection_id=_DEMO_CONNECTION_ID,
        external_ref=_DEMO_SENT_QUOTE_REF, number="QT-DEMO-0002",
        source_reference=quote.reference,
        customer_id="cst_pitti", customer_ref="Pitti Engineering Ltd",
        date=sent_on, expires_on=sent_on + timedelta(days=30),
        source_status="accepted", outcome="WON", decided_on=_d(1),
        total=total, source_attributes={_DEMO_QUOTE_INTENT_KEY: "Repeat order"},
        source_recorded_at=_stamp(sent_on),
        source_ref={"system": "zoho", "record_type": "estimate",
                    "record_id": _DEMO_SENT_QUOTE_REF}))
    for n, (pid, desc, qty, _, quoted, _) in enumerate(lines):
        session.add(models.ErpQuoteLine(
            erp_quote_line_id=f"eqln_demo_pitti_{n}", organization_id=org,
            connector=_DEMO_CONNECTOR, connection_id=_DEMO_CONNECTION_ID,
            external_ref=f"{_DEMO_SENT_QUOTE_REF}:{n}",
            quote_ref=_DEMO_SENT_QUOTE_REF, line_number=n, product_id=pid,
            item_code=pid, description=desc, qty=Decimal(qty), unit="Nos",
            rate=Decimal(str(quoted)), amount=Decimal(str(qty * quoted)),
            discount_percent=Decimal("0"),
            source_ref={"system": "zoho", "record_type": "estimate",
                        "record_id": _DEMO_SENT_QUOTE_REF, "line_id": str(n)}))


def _declare_quote_intent(session: Session, org: str) -> None:
    """Say what this organization's own quote field means.

    Through ``source_concepts.declare`` rather than by writing the row, for the
    reason CLAUDE.md §2 gives: that function owns the closed vocabularies, the
    supersede rule and the effective-from asymmetry, and a seed that built the
    row itself would be a second answer to all three — one that could put a
    value outside the vocabulary into a table whose whole purpose is that it
    cannot hold one.

    A first declaration is effective from the beginning of time, which is
    ``declare``'s own default and the only cut that is not arbitrary: it applies
    to the quote above, which was raised before this database existed.

    **The one value in this seed that is not derived from ``AS_OF``** is the
    ``recorded_at`` stamp ``declare`` writes — the moment the declaration was
    typed, which is what that column means and is the same class of value as the
    ``created_at`` every other demo row carries. It is not what a diagnosis
    reads: the point-in-time predicate is ``effective_from <= at <
    superseded_at``, and ``recorded_at`` only tie-breaks two declarations made in
    the same instant, of which this seed writes one.
    """
    # ``intent.ENTITY`` rather than the literal "quote": it is the record kind
    # the diagnosis actually asks for, so a declaration filed under anything
    # else would be in force and never read.
    from .commercial.quote_diagnosis import intent

    source_concepts.declare(
        session, org, connector=_DEMO_CONNECTOR,
        entity=intent.ENTITY, source_key=_DEMO_QUOTE_INTENT_KEY,
        concept=source_concepts.QUOTE_INTENT,
        value_map={"Repeat order": "REPEAT_ORDER", "Tender": "TENDER",
                   "Budgetary": "BUDGETARY", "New enquiry": "FIRM_ENQUIRY",
                   "Sample": "SAMPLE"},
        source_ref=_DEMO_DECLARATION_SOURCE)


def purge_demo_seed(session: Session, organization_id: str) -> dict[str, int]:
    """Remove the demo dataset for one org, if present.

    Called whenever a real Zoho sync runs, so a fabricated customer or product
    never sits alongside real data — a fresh clone shows demo decisions before
    anyone connects Zoho, and once a real account is linked those decisions
    would otherwise linger forever, indistinguishable from the real ones they
    were only ever a stand-in for.

    Every delete is scoped to the fixed demo ids, so it can never touch a real
    Zoho customer or product, and it is a no-op (all counts zero) once nothing
    demo-seeded remains — safe to call on every sync, not just the first.

    **Ordered against the foreign keys**, which is why this is a literal dict
    rather than a loop over tables: cost lines name a vendor, a payment term
    names a vendor, and a quote names a customer, so each of those has to go
    before the row it points at. The one delete that is *not* keyed on a fixed
    id is the source-attribute declaration — that table is keyed on what a
    field means rather than on a row this module minted — so it is narrowed by
    the marker the seed writes into ``source_ref``, and an administrator's own
    declaration of the same field is left alone.
    """
    subject_ids = list(DEMO_CUSTOMER_IDS | DEMO_PRODUCT_IDS)
    # The platform quote the seed minted is found by the document the seed
    # recorded for it — a fixed ERP id no live send ever produces — not by
    # its customer: a person can start a quote for a demo customer from the
    # builder before the first real sync, and that is their work, kept. Empty,
    # the `in_` below matches nothing, which is the idempotent second purge.
    demo_quote_ids = list(session.scalars(
        select(models.QuoteDocument.quote_id).where(
            models.QuoteDocument.organization_id == organization_id,
            models.QuoteDocument.external_document_id.in_(list(DEMO_QUOTE_REFS))))) or ["-"]
    removed = {
        # Outcomes first: they point at documents and at quotes.
        "quote_outcomes": session.query(models.QuoteOutcome).filter(
            models.QuoteOutcome.organization_id == organization_id,
            or_(models.QuoteOutcome.quote_id.in_(demo_quote_ids),
                models.QuoteOutcome.quote_document_ref.in_(list(DEMO_QUOTE_REFS))),
        ).delete(synchronize_session=False),
        "quote_documents": session.query(models.QuoteDocument).filter(
            models.QuoteDocument.organization_id == organization_id,
            models.QuoteDocument.quote_id.in_(demo_quote_ids),
        ).delete(synchronize_session=False),
        "quote_decisions": session.query(models.QuoteDecision).filter(
            models.QuoteDecision.organization_id == organization_id,
            models.QuoteDecision.quote_id.in_(demo_quote_ids),
        ).delete(synchronize_session=False),
        "quote_drafts": session.query(models.QuoteDraft).filter(
            models.QuoteDraft.organization_id == organization_id,
            models.QuoteDraft.quote_id.in_(demo_quote_ids),
        ).delete(synchronize_session=False),
        "decisions": session.query(models.Decision).filter(
            models.Decision.organization_id == organization_id,
            models.Decision.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        "signals": session.query(models.Signal).filter(
            models.Signal.organization_id == organization_id,
            models.Signal.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        # Telemetry from the AI calls the demo decisions triggered — not
        # customer-visible, but left in place it would permanently skew the
        # owner-facing AI cost/health metrics with demo-run numbers.
        "ai_call_logs": session.query(models.AiCallLog).filter(
            models.AiCallLog.organization_id == organization_id,
            models.AiCallLog.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        "sales_txns": session.query(models.SalesTxn).filter(
            models.SalesTxn.organization_id == organization_id,
            models.SalesTxn.customer_id.in_(list(DEMO_CUSTOMER_IDS)),
        ).delete(synchronize_session=False),
        "cost_records": session.query(models.CostRecord).filter(
            models.CostRecord.organization_id == organization_id,
            models.CostRecord.product_id.in_(list(DEMO_PRODUCT_IDS)),
        ).delete(synchronize_session=False),
        "erp_quote_lines": session.query(models.ErpQuoteLine).filter(
            models.ErpQuoteLine.organization_id == organization_id,
            models.ErpQuoteLine.quote_ref.in_(list(DEMO_QUOTE_REFS)),
        ).delete(synchronize_session=False),
        "erp_quotes": session.query(models.QuoteDoc).filter(
            models.QuoteDoc.organization_id == organization_id,
            models.QuoteDoc.external_ref.in_(list(DEMO_QUOTE_REFS)),
        ).delete(synchronize_session=False),
        # The agreed term before the vendor it hangs off, and both after the
        # cost lines that name it.
        "vendor_payment_terms": session.query(models.VendorPaymentTerm).filter(
            models.VendorPaymentTerm.organization_id == organization_id,
            models.VendorPaymentTerm.vendor_id.in_(list(DEMO_VENDOR_IDS)),
        ).delete(synchronize_session=False),
        "vendors": session.query(models.Vendor).filter(
            models.Vendor.organization_id == organization_id,
            models.Vendor.vendor_id.in_(list(DEMO_VENDOR_IDS)),
        ).delete(synchronize_session=False),
        "source_attribute_mappings": session.query(
            models.SourceAttributeMapping).filter(
            models.SourceAttributeMapping.organization_id == organization_id,
            models.SourceAttributeMapping.connector == _DEMO_CONNECTOR,
            models.SourceAttributeMapping.source_key == _DEMO_QUOTE_INTENT_KEY,
            models.SourceAttributeMapping.source_ref == _DEMO_DECLARATION_SOURCE,
        ).delete(synchronize_session=False),
        # The company last: every row above named it.
        "zoho_connections": session.query(models.ZohoConnection).filter(
            models.ZohoConnection.organization_id == organization_id,
            models.ZohoConnection.connection_id.in_(list(DEMO_CONNECTION_IDS)),
        ).delete(synchronize_session=False),
        "customers": session.query(models.Customer).filter(
            models.Customer.organization_id == organization_id,
            models.Customer.customer_id.in_(list(DEMO_CUSTOMER_IDS)),
        ).delete(synchronize_session=False),
        "products": session.query(models.Product).filter(
            models.Product.organization_id == organization_id,
            models.Product.product_id.in_(list(DEMO_PRODUCT_IDS)),
        ).delete(synchronize_session=False),
    }
    session.flush()
    return removed


def main() -> None:
    """Seed the demo data, and optionally build a tenant to hold it.

        # into this deployment's own organization, as it always did
        python -m app.demo

        # a public demonstration tenant beside a real book
        python -m app.demo --org org_demo --email visitor@demo.example

    The second form is what ``PUBLIC_DEMO_ORG_ID`` and ``PUBLIC_DEMO_EMAIL``
    should then be pointed at. Both settings must name what this created, and
    ``authz`` treats only that organization as the demo one.
    """
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", help="organization to seed into. Defaults to "
                                      "this deployment's own.")
    parser.add_argument("--email", help="the account the public demo door signs "
                                        "in as. Required with --org.")
    args = parser.parse_args()
    if args.org and not args.email:
        parser.error("--org needs --email: the demo door signs in as a named "
                     "account, and inferring one is how it ends up being the "
                     "wrong account.")

    from .db import SessionLocal
    s = SessionLocal()
    try:
        if args.org:
            user = provision_demo_org(s, args.org, args.email)
            print(f"Demo tenant: {args.org} / {user.email}")
        out = seed_demo(s, organization_id=args.org)
        s.commit()
        print("Demo seed:", out)
        if args.org:
            print(f"\nSet PUBLIC_DEMO_ORG_ID={args.org} and "
                  f"PUBLIC_DEMO_EMAIL={args.email} to open the door.")
    finally:
        s.close()


if __name__ == "__main__":
    main()
