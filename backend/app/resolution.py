"""The public resolution document — what an external caller is told, and why.

``POST /api/v1/resolve`` (``routers/resolve.py``) exists to make one claim
testable: that this is infrastructure an ERP or a CPQ can call, rather than an
application with a screen. Everything in this module is about the difference
between an *answer* and the absence of one.

**One rule shapes the whole document.** Every claim carries where it came from,
how sure the engine was, and — where the engine recorded one — the characters
it was read out of. A field that cannot say that does not go in. That is
pie-parser's own invariant ("do not add a field that cannot say where it came
from") crossing the network boundary intact, and it is the reason an integrator
can decide whether to trust a line rather than having to trust the endpoint.

**Absence of evidence is not a pass** (CLAUDE.md §1). Five different things can
stop a line resolving and they are five different answers, not one null:

* ``CATALOGUE_UNAVAILABLE`` — nobody asked the pack. This deployment has no
  engine loaded, so the response says nothing whatever about the item.
* ``ENGINE_ERROR`` — the pack was asked and the ask failed.
* ``NO_MATCH`` — the pack was asked, answered, and holds nothing like this.
  This one *is* evidence about the item.
* ``AMBIGUOUS`` — the pack holds several and the input does not choose between
  them. Also evidence: the alternatives are returned, ranked, to choose from.
* ``NEEDS_CONFIRMATION`` — the pack holds this exact code, but nobody has
  confirmed that *this customer's* code means it. Evidence, and the only
  abstention with an action attached: ``POST /api/v1/resolve/confirm``.

The first two are ``503`` and the rest are ``200``, because a caller that
cannot tell "we do not know" from "there is no such product" will eventually
write the second into their master data. ``pie_service.catalog_available``
exists to make exactly that distinction, which is why this module consults it
before the resolution rather than inferring emptiness afterwards.

Each of the three ``200`` reasons has a *different next move* — record the gap,
choose a record, confirm the mapping — which is the test for whether a reason
earns its own name rather than being folded into a neighbour.

**A key is a recipient like any other.** When the caller supplies a price, the
commercial half of the answer is produced by ``commercial.quote_service`` and
projected through ``project(intel, role)`` — the same function the Quote
Builder's own screen goes through. Nothing here computes a price, a margin or a
threshold, and nothing here decides what a role may see; both would be a second
implementation of a rule this codebase has already had to fix twice.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from .authz import Principal, can_view_customer
from .commercial.quote_service import (
    QuoteLineInput,
    assess_quote,
    project,
    resolve_customer,
)
from .pie_service import ATTRIBUTE_FIELDS, Bands, Candidate, Resolution, pie_service

log = logging.getLogger("pie_portal.resolution")

#: The contract version, echoed in every document. Bumped when a field changes
#: meaning or disappears — never when one is added, because an integrator that
#: breaks on a new key was already broken. The path carries the major version
#: (``/api/v1``); this is the one that tells a partner *which* v1 they are
#: talking to when something they were reading stops being there.
API_VERSION = "v1"

#: The abstention reasons that are not evidence about the item. Named as a set
#: because two places need the same answer — the HTTP status below, and the
#: documented advice that a caller must not cache these as "no such product".
NOT_EVIDENCE = frozenset({"CATALOGUE_UNAVAILABLE", "ENGINE_ERROR"})

#: **What counts as an answer is whether the engine selected a record**, not
#: how strong the relationship is. ``supplyCode`` set is the engine having
#: chosen; ``relationship`` then says how good that choice is, and a caller
#: reading ``POSSIBLE`` alongside ``equivalence_score`` has what it needs to
#: decide whether to quote it.
#:
#: This is deliberately the same line the Quote Builder draws — ``store``
#: prices whatever ``supplyCode`` names and shows the relationship beside it.
#: An API that abstained where the screen answered would mean two recipients
#: disagreeing about what the *engine* said, which is a worse defect than a
#: relationship a caller has to read: it would make "did PIE resolve this?"
#: depend on who asked.


def _span_of(needle: Optional[str], haystack: str) -> Optional[dict]:
    """Where ``needle`` sits in ``haystack``, or None if it is not exactly there.

    Case-insensitive and exact — never fuzzy. A span is a claim about which
    characters the caller typed produced this answer, and a *nearly* right span
    highlights the wrong word in somebody's UI while looking authoritative. If
    the identifier the engine matched on is not literally present in the text
    (it was reached through a confirmed mapping, or normalisation changed it),
    the honest answer is no span at all.
    """
    if not needle or not haystack:
        return None
    start = haystack.lower().find(str(needle).strip().lower())
    if start < 0:
        return None
    return {"start": start, "end": start + len(str(needle).strip()),
            "text_ref": "input.text"}


def _attributes(record: Optional[dict]) -> list[dict]:
    """The decoded slots of one catalogue row, each with its own provenance.

    Projected onto ``pie_service.ATTRIBUTE_FIELDS`` — the portal's statement of
    what a line may show — rather than onto whatever the pack emitted, so a
    pack change cannot silently widen what crosses this boundary. That tuple is
    imported rather than restated for the reason its own comment gives: the
    EXACT path and the suggestion path must describe a product the same way.

    ``span`` indexes into ``description``, **not** into the caller's input, and
    says so in every entry. They are different strings and a caller that
    conflated them would highlight arbitrary characters of the customer's own
    text. An absent span means the engine derived the value rather than reading
    it out of a substring — a lookup table, an explicit column — which is a
    real and different provenance, not a missing one.

    A **null provenance** is narrower still and means the pack recorded none
    for that slot. ``product_family`` is the live case: it is decided by the
    family router rather than by a grammar slot, so it reaches the emitted
    record as a top-level key with no ``field_meta`` entry behind it. Null
    rather than a stand-in label — inventing a provenance value here would be
    this module claiming an audit trail the engine never wrote.
    """
    if not record:
        return []
    meta = record.get("field_meta") or {}
    out: list[dict] = []
    for name in ATTRIBUTE_FIELDS:
        value = record.get(name)
        if value is None:
            continue
        fm = meta.get(name) or {}
        span = fm.get("span")
        out.append({
            "name": name,
            "value": value,
            "provenance": fm.get("provenance"),
            "confidence": fm.get("confidence"),
            "read_from": fm.get("raw"),
            "span": (None if not span else
                     {"start": span[0], "end": span[1],
                      "text_ref": "product.description"}),
        })
    return out


def _product(cand: Candidate, *, with_provenance: bool,
             connection_id: Optional[str] = None) -> dict:
    """One candidate as the wire sees it.

    Two confidence-shaped numbers, kept apart on purpose. ``equivalence_score``
    is how well this record answered the *request* and is null for an exact
    identity, where the question does not arise. ``record_confidence`` is how
    well the engine decoded this record's own description, and is a property of
    the catalogue rather than of this call. Fusing them into one "confidence"
    would produce a number that means neither, which is the kind of figure a
    downstream system rounds and then quotes back at somebody.
    """
    # The same company's catalogue that produced the candidate. Reading the
    # record back from anywhere else would attach one company's provenance to
    # another's answer, which is the one thing provenance must not do.
    record = (pie_service.lookup_record(cand.code, connection_id)
              if with_provenance else None)
    out: dict[str, Any] = {
        "record_id": cand.code,
        "description": cand.desc,
        "brand": cand.brand,
        "grade": cand.grade,
        "relationship": cand.rel,
        "equivalence_score": cand.score,
        # Without this the pair above is self-contradictory on the wire. A
        # candidate the engine could not fully compare is capped at POSSIBLE
        # however high it scored, so a caller told to "read relationship and
        # equivalence_score together" sees POSSIBLE beside 1.0 and has no way to
        # tell that from a genuine near-miss. The cap is the safe half; this is
        # the half that explains it.
        "comparison_complete": not cand.unverified,
        "explanation": cand.reason or None,
        # How this record came to be offered. ``ranking`` is the engine's own
        # scored pass over the catalogue; ``retrieval`` is nearest-by-
        # description, and ``confirmed_code`` is near a code this customer
        # confirmed means this product — both compared by the engine
        # afterwards but never ranked by it, so such a record is always
        # POSSIBLE and is never the answer. ``confirmed_code`` carries the code.
        "found_by": ("prior_choice" if cand.alias is not None and cand.alias_kind == "phrase"
                     else "confirmed_code" if cand.alias is not None
                     else "retrieval" if cand.retrieved else "ranking"),
        "confirmed_code": cand.alias if cand.alias_kind != "phrase" else None,
        # The words this customer had quoted as this record before, when that
        # is how it was found. A past choice: not an identity, not the engine's
        # reading, and honestly not always what they mean this time.
        "prior_phrase": cand.alias if cand.alias_kind == "phrase" else None,
        # Which of the company's catalogues — which manufacturer's — this
        # record is from. A company resolves against every catalogue it has
        # built, so the answer has to say which one answered; the key matches
        # an entry of ``engine.catalogues``.
        "catalogue": cand.catalogue,
    }
    if record is not None:
        out["record_confidence"] = record.get("row_confidence")
        out["attributes"] = _attributes(record)
    else:
        # The engine's own projection of the suggestion, which carries values
        # without the per-field meta. Emitted flat and *named* differently from
        # the provenanced list above, so a caller cannot mistake one for the
        # other and read absent provenance as absent evidence.
        out["record_confidence"] = None
        out["attributes_without_provenance"] = dict(cand.attributes or {})
    return out


def _abstention(reason: str, detail: str, questions: list[str]) -> dict:
    return {
        "reason": reason,
        "detail": detail,
        "questions": questions,
        # Stated in the payload rather than left to the docs. A caller writing
        # "unknown part" into their master data on a CATALOGUE_UNAVAILABLE is
        # the failure this whole distinction exists to prevent, and the flag is
        # readable by the code doing the writing.
        "is_evidence_about_the_input": reason not in NOT_EVIDENCE,
    }


def _catalogue_unavailable(text: str, connection_id: Optional[str] = None) -> dict:
    return _document(
        text, status="ABSTAINED", resolution=None, alternatives=[],
        abstention=_abstention(
            "CATALOGUE_UNAVAILABLE",
            "This company has no decoded catalogue, so nothing was asked about "
            "this text. This is not a statement that the product does not "
            "exist — build the company's catalogue on Setup → Decoded "
            "catalogue and ask again.",
            []),
        semantics="UNKNOWN", outcome="NOT_ASKED", notes=[],
        connection_id=connection_id)


def _document(text: str, *, status: str, resolution: Optional[dict],
              alternatives: list[dict], abstention: Optional[dict],
              semantics: str, outcome: str, notes: list[str],
              identity_proposal: Optional[dict] = None,
              commercial: Optional[dict] = None,
              connection_id: Optional[str] = None,
              retrieval: Optional[dict] = None) -> dict:
    """The one shape every answer takes, resolved or not.

    Same keys in every case, including the abstentions: a caller should be able
    to parse one document type rather than branch on the status before it can
    read anything. ``resolution`` is null exactly when ``status`` is
    ``ABSTAINED``, and that is the only structural difference.
    """
    return {
        "api_version": API_VERSION,
        "input": {"text": text, "length": len(text)},
        "status": status,
        "abstention": abstention,
        "resolution": resolution,
        "alternatives": alternatives,
        "identity_proposal": identity_proposal,
        "commercial": commercial,
        "engine": {
            # Of the company this line was resolved for. There is no
            # deployment-wide answer any more, which is the point: a stamp that
            # could not say *which* catalogue answered was never provenance.
            "catalogue_available": pie_service.catalog_available(connection_id),
            # The version of what this company resolves against: a hash over
            # each of its catalogues' run ids (input bytes plus ruleset), so it
            # moves when any is rebuilt from different files or through a
            # different pack. Kept under the field's original name; each
            # catalogue's own ruleset checksum is listed below.
            "ruleset_checksum": pie_service.catalog_version(connection_id) or None,
            "company": connection_id,
            # The catalogues behind that checksum: one per manufacturer this
            # company sells, each with its own pack and its own stamp. One
            # entry where the company has one catalogue, and the checksum
            # above is then that catalogue's own.
            "catalogues": pie_service.catalogues(connection_id),
            "input_semantics": semantics,
            "outcome": outcome,
            # The nearest-neighbour pass, when one ran: which model searched
            # how many records and offered how many. Null where it did not run
            # — an exact identity, an abstention before the engine, no index —
            # which reads as "not searched", never as "nothing near". An
            # alternative it offered says so itself, in ``found_by``.
            "retrieval": retrieval,
        },
        "notes": notes,
    }


def http_status(document: dict) -> int:
    """``200`` for an answer, ``503`` for "we could not ask".

    A distinct status for the two ``NOT_EVIDENCE`` reasons, on top of the flag
    in the body, because a caller that ignores the body still has to handle a
    5xx — and the one thing that must never happen is a transport-level
    failure being recorded as "no such product". Never ``500``: an abstention
    is a well-formed answer to a well-formed request, and every one of these
    carries the same document shape a resolution does.
    """
    reason = (document.get("abstention") or {}).get("reason")
    return 503 if reason in NOT_EVIDENCE else 200


def _identity_proposal(res: Resolution) -> Optional[dict]:
    """The confirmable proposal on this line, if the engine made one.

    Delegates to ``store._identity_candidate`` rather than re-reading
    ``outcome`` and ``candidates`` here. That predicate is one half of the
    confirmation gate (``tests/test_identity_confirmation_gate.py``), and a
    second reading of it in the API path is exactly how the API ends up
    offering a confirmation the UI would refuse.
    """
    from .store import _identity_candidate  # noqa: PLC0415 — avoids a cycle

    code = _identity_candidate(res)
    if not code:
        return None
    return {
        "record_id": code,
        "confirmable": True,
        "detail": "The catalogue holds this exact code, but nobody has "
                  "confirmed that this customer's code means this product. "
                  "POST it to /api/v1/resolve/confirm to record that it does.",
    }


def _commercial(session: Session, principal: Principal, *, customer_ref: str,
                product_ref: str, qty: Decimal,
                proposed_price: Decimal) -> dict:
    """The projected commercial answer for one resolved line.

    Every number comes from ``commercial/`` and every decision about what this
    recipient may see comes from ``project``. This function chooses neither —
    it assembles one ``QuoteLineInput`` and hands it over.

    **Three deliberate narrowings against the price walk**, which is the defect
    class CLAUDE.md §1 names twice. ``quote_intelligence/assess`` needs guards
    for an ``as_of`` window, a 200-line batch and repeated prices per product;
    this path has none of those to guard because it does not offer them: one
    line, today's date, one price. What is left is the per-key rate limit in
    ``api_keys`` and the standing residual ``project`` documents — two
    boundaries in three unknowns for a sales-role recipient, which does not
    yield cost.

    ``item_master_cost`` is deliberately absent. That field is the *fallback*
    cost used when the books hold none, and it is read from a server-held quote
    line; there is no quote here, so a line whose product has no cost record
    answers ``NO_COST_BASIS`` at every price rather than acquiring a boundary
    it never had. The reasoning is ``quote_intelligence._inputs``'.
    """
    assessment = assess_quote(
        session, principal.organization_id,
        customer_ref=customer_ref,
        lines=[QuoteLineInput(line_id="line", product_ref=product_ref,
                              qty=qty, proposed_price=proposed_price)])
    if not assessment.lines:
        return {
            "assessed": False,
            "detail": "The resolved product is not in this organization's "
                      "books, so there is nothing to price it against.",
            "thresholds_version": assessment.thresholds_version,
        }
    intel = assessment.lines[0]
    out = project(intel, principal.role, product_ref=product_ref,
                  unresolved=intel.line_id in assessment.unresolved)
    out["assessed"] = True
    out["customer"] = {
        "customer_id": assessment.customer_id,
        "label": assessment.customer_label,
        "resolved": assessment.customer_id is not None,
    }
    return out


def _visible_customer_ref(session: Session, principal: Principal,
                          ref: str) -> str:
    """``ref`` if this principal may see the account it names, else ``""``.

    ``authz.can_view_customer`` rather than a rule of this endpoint's own, and
    degraded to an empty reference rather than refused — both for the reasons
    ``quote_intelligence._visible_customer_ref`` gives at length: an
    out-of-scope account must be indistinguishable from one this book has never
    traded with, or the refusal itself answers "does this customer exist".

    An API key is scoped by the same rule. A key minted at ``SALESPERSON``
    holds no accounts at all, so every reference it supplies degrades to the
    unscoped answer — which is the correct behaviour for a machine credential
    nobody assigned a desk to, and the reason a key that genuinely needs
    customer history is minted at a role that has one.
    """
    if not ref:
        return ""
    customer = resolve_customer(session, principal.organization_id, ref)
    if customer is not None and not can_view_customer(principal, customer,
                                                      session):
        return ""
    return ref


# ── the org-scoped setup one engine call needs ──────────────────────────────
#
# Three facts, all read from the organization, all optional in the same way: a
# failure to load any of them degrades resolution to the packaged defaults
# rather than failing the request. They live here rather than in
# ``routers/quote.py`` — where they were written, privately, for the one caller
# that existed — because the public API needs the identical setup, and a second
# copy is how one caller ends up reading this organization's confirmed mappings
# while the other silently resolves against pie-parser's empty packaged store.


def bands_for(session: Session, organization_id: str) -> Optional[Bands]:
    """This organization's equivalence bands, or None for the packaged defaults.

    What counts as a technical equivalent is commercial policy, so it belongs to
    the org and moves with its threshold version — the same reason the pricing
    floors stopped being module constants.
    """
    from .commercial import policy as policy_service  # noqa: PLC0415

    try:
        t = policy_service.load_for_org(session, organization_id)
        return Bands(tech=t.equivalence_tech_band, compat=t.equivalence_compat_band)
    except Exception:  # noqa: BLE001 — policy is never a reason to fail intake
        log.exception("could not load equivalence bands for %s", organization_id)
        return None


def mapping_store_for(session: Session, organization_id: str) -> Optional[Any]:
    """This organization's confirmed code mappings, for the engine to read.

    None only when they could not be loaded: pie-parser then falls back to its
    packaged store, which is empty, so resolution degrades to the codes alone
    rather than failing the intake.
    """
    from .identity.mapping_store import OrgMappingStore  # noqa: PLC0415

    try:
        return OrgMappingStore(session, organization_id)
    except Exception:  # noqa: BLE001 — resolution proceeds without them
        log.exception("could not load confirmed mappings for %s", organization_id)
        return None


def customer_scope_for(session: Session, organization_id: str,
                       reference: str) -> Optional[str]:
    """The identity to resolve a line under, or None.

    Two ways to get None, and both mean "resolve on the codes alone": no
    customer matched, or a customer who has not been linked across connectors
    yet. That last one is the normal early state — linking is manual by design —
    so the fallback has to be the unscoped behaviour rather than a stand-in key.
    Substituting the connector's own id would mean every mapping confirmed today
    is filed under a name we intend to replace the moment somebody links the
    record.
    """
    from .identity import service as identity_service  # noqa: PLC0415

    try:
        customer = resolve_customer(session, organization_id, reference)
        return identity_service.identity_for_customer(session, organization_id,
                                                      customer)
    except Exception:  # noqa: BLE001 — scope is an optimisation, never a blocker
        log.exception("could not resolve an identity scope for %r", reference)
        return None


class CompanyNotNamed(ValueError):
    """The organization has several companies and the caller named none.

    Carries the valid ids so the caller is told what to pick rather than left
    to discover them. A refusal rather than a default: catalogues are per
    company now, so answering from one the caller did not choose would be a
    confidently provenanced answer about possibly the wrong company's product
    — the benign default §1 forbids, wearing a real stamp.
    """

    def __init__(self, companies: list[dict]):
        self.companies = companies
        super().__init__(
            "This organization reads more than one company's books, and each "
            "has its own product catalogue. Name the company this line is for "
            "(company_id): "
            + ", ".join(f"{c['connection_id']} ({c['label']})" if c["label"]
                        else c["connection_id"] for c in companies))


def company_for(session: Session, organization_id: str,
                connection_id: Optional[str] = None) -> Optional[str]:
    """Which company's catalogue answers, or a refusal naming the choices.

    One company: it answers, named or not — a picker with one option is a
    question with one answer, and every existing single-entity caller keeps
    working unchanged. Several: the caller must say, or this raises.

    A named company is checked against this organization's own, so an id from
    another tenant reads as "no such company" rather than resolving.
    """
    from .ingestion.connections import list_connections

    companies = [{"connection_id": c.connection_id, "label": c.label or ""}
                 for c in list_connections(session, organization_id,
                                           enabled_only=True)]
    if connection_id:
        if any(c["connection_id"] == connection_id for c in companies):
            return connection_id
        raise CompanyNotNamed(companies)
    if len(companies) == 1:
        return companies[0]["connection_id"]
    if not companies:
        # No company at all is not ambiguity — it is a deployment with nothing
        # connected, which resolves to no catalogue and abstains below.
        return None
    raise CompanyNotNamed(companies)


def resolve(session: Session, principal: Principal, *, text: str,
            customer_scope: Optional[str] = None,
            bands: Optional[Bands] = None,
            mapping_store: Any = None,
            customer_ref: str = "",
            connection_id: Optional[str] = None,
            quantity: Optional[Decimal] = None,
            proposed_price: Optional[Decimal] = None) -> dict:
    """Resolve one line of text into the public document.

    ``customer_scope`` is the customer's cross-connector identity, which lets
    the engine read this organization's confirmed mappings; ``customer_ref`` is
    the name the caller typed, which is what the commercial half is priced
    against. They are separate arguments because they are separate facts — a
    customer can be nameable and unlinked, which is the normal early state.
    """
    text = (text or "").strip()

    # Asked before resolving, not inferred from an empty result afterwards.
    # `resolve` degrades a missing engine to PIE_DOWN, which would arrive here
    # as ENGINE_ERROR — true but less useful than the specific fact that this
    # deployment never loaded a pack at all.
    company = company_for(session, principal.organization_id, connection_id)
    if not pie_service.catalog_available(company):
        return _catalogue_unavailable(text, company)

    res = pie_service.resolve(text, customer_scope, bands, mapping_store,
                              connection_id=company)

    if res.pie_offline:
        return _document(
            text, status="ABSTAINED", resolution=None, alternatives=[],
            abstention=_abstention(
                "ENGINE_ERROR",
                "The resolution engine failed on this input. This is not a "
                "statement about the product.", []),
            semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company)

    alternatives = [_product(c, with_provenance=False)
                    for c in res.candidates
                    if c.code != res.supplyCode]

    chosen = next((c for c in res.candidates if c.code == res.supplyCode),
                  None) if res.supplyCode else None

    if chosen is not None:
        resolution = _product(chosen, with_provenance=True,
                              connection_id=company)
        # Where in the caller's own text the answer was found — null unless the
        # code is literally there. See `_span_of`.
        resolution["input_span"] = _span_of(
            res.reqCode if res.rel == "EXACT" else None, text)
        commercial = None
        if proposed_price is not None:
            commercial = _commercial(
                session, principal,
                customer_ref=_visible_customer_ref(session, principal,
                                                   customer_ref),
                product_ref=res.supplyCode,
                qty=Decimal(quantity if quantity is not None else 1),
                proposed_price=Decimal(proposed_price))
        return _document(
            text, status="RESOLVED", resolution=resolution,
            alternatives=alternatives, abstention=None,
            semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company,
            identity_proposal=_identity_proposal(res), commercial=commercial)

    if res.supplyCode:
        # The engine named a record it did not describe. Not reachable through
        # ``pie_service._map`` as written — every branch that sets a supply
        # puts it in ``candidates`` — but the document promises ``resolution``
        # is null exactly when the status is ABSTAINED, and a "RESOLVED" answer
        # with a null resolution would break that for every caller at once.
        # An internal inconsistency is not evidence about the product, so it
        # abstains as one.
        log.error("the engine selected %r but described no such candidate for "
                  "%r; abstaining rather than answering with nothing",
                  res.supplyCode, text)
        return _document(
            text, status="ABSTAINED", resolution=None, alternatives=alternatives,
            abstention=_abstention(
                "ENGINE_ERROR",
                "The resolution engine returned an answer this server could "
                "not describe. This is not a statement about the product.", []),
            semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company)

    # No ``supplyCode``, so the engine itself abstained. Every candidate is an
    # alternative here — ``alternatives`` excluded the chosen record and there
    # was none — so the list above is already the whole set.
    proposal = _identity_proposal(res)

    if proposal is not None:
        # The narrowest abstention, and the only one with an action attached.
        # The catalogue holds this exact code; what is unconfirmed is that
        # *this customer's* code means it. Kept apart from AMBIGUOUS because
        # the caller's next move is different — POST it to /confirm, rather
        # than choose between records — and because "several records answer
        # this" would be a false description of one.
        return _document(
            text, status="ABSTAINED", resolution=None,
            alternatives=alternatives,
            abstention=_abstention(
                "NEEDS_CONFIRMATION",
                "The catalogue holds this exact code, but nobody has confirmed "
                "that this customer's code means that product. Confirm it at "
                "/api/v1/resolve/confirm, or pick a different record for this "
                "quote alone.",
                list(res.notes)),
            semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company,
            identity_proposal=proposal)

    if alternatives:
        return _document(
            text, status="ABSTAINED", resolution=None,
            alternatives=alternatives,
            abstention=_abstention(
                "AMBIGUOUS",
                "Several catalogue records answer this text and it does not "
                "choose between them. They are returned ranked; pick one.",
                list(res.notes)),
            semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company)

    return _document(
        text, status="ABSTAINED", resolution=None, alternatives=[],
        abstention=_abstention(
            "NO_MATCH",
            "The catalogue was searched and holds nothing matching this text.",
            list(res.notes)),
        semantics=res.semantics, outcome=res.outcome, notes=res.notes,
            retrieval=res.retrieval,
            connection_id=company)
