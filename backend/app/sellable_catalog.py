"""This organization's own book, as candidate records for the equivalence engine.

**The measured problem.** ``tools/resolve_rfq._build_sources`` builds a
candidate pool from the decoded *manufacturer* catalogue, and adds a second
source only when a ``--zoho-fixture`` path is given. ``PieService._make_args``
passes ``zoho_fixture=None``, so the only way a product this business actually
sells reached the ranking was through ``products.pie_record_id`` — set on about
9% of the master. The other ~91% could not be offered however well it matched,
because it was not in the pool at all.

Phase 1 made that fixable: ``product_attribute_values`` holds decoded facts per
product, org-scoped, with provenance. This module reads them back out and
presents them in the shape ``equivalence/catalog.py`` defines, so the engine
searches the book alongside the catalogue with **no pie-parser change** — that
file's own docstring says adding a source is "literally appending another
``CatalogSource``", and this is the portal taking it at its word.

**Per organization, never a process singleton** (decision 003). ``PieService``
loads the manufacturer catalogue once and shares it across every request, which
is right for a file every tenant may read and wrong for a pool of one tenant's
products: a source cached across organizations *is* a cross-tenant read, and the
worst defect available here is offering one customer another company's
catalogue. So every read below carries ``organization_id`` in its ``WHERE``
clause — row-level security binds on PostgreSQL only, and on SQLite these
filters are the whole of the tenant boundary — and the cache is keyed on the
organization plus a content version of its book.

**Sellability goes through the restriction, not around it.** A source built here
is appended to the list handed to ``resolve_rfq.run``, which computes
``_sellable_namespaces`` over the loaded pool and wraps every source in
``_NamespaceRestrictedSource``. With no ``--sellable-namespaces`` configured
every loaded namespace is sellable, so :data:`SELLABLE_PACK_ID` is sellable
because it is in the pool — and a deployment that later names its sellable
namespaces has to name this one too. That coupling is the point: a Zoho item is
sellable by definition, and the way to say so is to let it pass the same check
everything else passes.

**Non-transitivity is untouched.** A bigger pool is still scored with the
request as every comparison's left operand — ``query.find_equivalents`` scores
each pool record against ``input_spec`` and never against another candidate.
Nothing here compares two products, stores a relationship between two products,
or feeds a derived ``rel`` back in. These records also never enter identity
resolution: ``AuthoritativeIndex`` is built from the catalogue JSONL alone, so a
product from this pool can be a *suggestion* and can never be an asserted
identity that a later requirement is derived from.

**Nothing here is a number a screen renders.** No price, no cost, no margin —
there is no such column on ``product_attribute_values`` and no such value in
this module. The engine's own field vocabulary is the whole content.

**One physical product can now appear twice in one list, and does.**
``query._dedup`` keys on ``description_norm``, which a book record does not
carry, so it cannot collapse a catalogue entry against the book entry for the
same product — the list shows both, at the same score, spending two of six
slots on one answer. Measured: on 15 of 62 requirement lines a ``[book]`` entry
carried a description byte-identical to a ``[pie]`` entry already in the list.
Neither entry is wrong, so this is a cost rather than a safety failure, and it
is **unfixed**: collapsing across pools needs an exact key both sides can
compute, which is the identity problem and not a display one. It is written
here because the harness's shape-based count reported a confident ``0/62`` for
it while its own printed output showed the pairs three times over — a false
zero of exactly the kind CLAUDE.md §1 names, found in this module's own
measurement.

**Read this before assuming the book is in the pool in production.** The engine
side is wired — ``PieService.resolve`` takes a ``pool`` and composes it into the
source list — but the three call sites that would *build* one are in files this
change does not own, so today only the tests pass one. That is exactly the state
``test_attribute_decoration_run`` records about ``decorate_products``, which
"shipped with the store and nothing called it ... so in production the table was
empty", and it is written down here rather than left to a grep. The remaining
change is one argument at each of three call sites, alongside the mapping store
they already build:

* ``resolution.resolve`` — add a ``sellable_pool_for(session, org)`` beside
  ``mapping_store_for(session, org)`` and pass it through, the way
  ``bands_for`` and ``customer_scope_for`` already travel;
* ``store.build_lines`` / ``add_rfq`` — take the pool as a parameter and hand it
  to ``pie_service.resolve``. That store is deliberately database-free, so it
  must receive a built pool and never a session, exactly as it does with
  ``mapping_store``;
* ``routers.resolve.confirm`` — it re-resolves the line to check the engine's
  own proposal, and must resolve against the same pool the first call used or
  the two answers are about different questions.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import cache as cache_module
from .attributes import CATALOGUE_LINK, DECODED_NAME, unit_for
from .domain import models

log = logging.getLogger("pie_portal.sellable_catalog")

#: The catalog label every record from this source carries. It surfaces on a
#: suggestion as ``brand`` (``ScoredEquivalent.to_dict`` publishes
#: ``brand_label``, not the record's own maker), which is why it is a word a
#: reader can act on: this candidate came out of the book rather than out of the
#: manufacturer catalogue.
SELLABLE_LABEL = "book"

#: The namespace these records live in. One value for the whole pool rather than
#: one per organization: a ``pack_id`` reaches the engine's output and its
#: ``pool_brands``, and putting a tenant id there would publish which tenant
#: asked. Tenant separation is the ``organization_id`` filter on every query
#: below and the per-organization cache key — not this string.
#:
#: The cost, stated because it is real: ``crossref.evaluate``'s third path looks
#: an ISO 513 application group up by ``(pack_id, grade)``, and this namespace
#: has no such chart, so a book item's grade earns evidence only from an exact
#: grade match or a published cross-reference row. That is a bounded loss — the
#: grade component is an additive 0.15-weight boost and never a gate — and the
#: alternative, borrowing the manufacturer pack's id, would make a book record
#: indistinguishable from a catalogue record in exactly the field sellability
#: keys on.
SELLABLE_PACK_ID = "portal_sellable"

#: Which source kind wins when two of them claim the same field, strongest
#: first. **One rule, in one place.** The read below is an unordered scan, so
#: without this the field would hold whichever row the scan reached last —
#: "whichever the query returned last" is a precedence rule nobody wrote down,
#: nobody can reproduce, and the planner is free to change.
#:
#: The order is ``ProductAttributeValue.source_kind``'s own reasoning, restated
#: as a ranking rather than re-decided: HUMAN "outranks everything and is the
#: only kind that may contradict a decode without evidence"; CATALOGUE_LINK is
#: "a stronger claim because a catalogue row is the maker's own data";
#: DECODED_NAME is the parser's reading of an item's description, which is a
#: guess at what the maker's data would have said. SOURCE_FILE sits between the
#: two: an imported PIM export is somebody's data file rather than a reading of
#: a name, but it is not the catalogue record this item *is*.
#:
#: All four are listed although ``app/attributes/`` writes only two. The two
#: unwritten kinds are declared on the model, so a writer for either is a
#: change to that package and not to this one — and the failure mode of leaving
#: them out is silent and wrong: an unranked kind falls to the bottom below,
#: which would make a human correction lose to a name decode.
SOURCE_PRECEDENCE: Tuple[str, ...] = ("HUMAN", CATALOGUE_LINK, "SOURCE_FILE",
                                      DECODED_NAME)
_PRECEDENCE: Dict[str, int] = {kind: rank
                               for rank, kind in enumerate(SOURCE_PRECEDENCE)}
#: An unrecognised kind ranks below every known one and never displaces a value.
#: A source kind this module has not been taught about is not evidence that it
#: is a better one.
_UNRANKED = len(SOURCE_PRECEDENCE)

_WHITESPACE = re.compile(r"\s+")

#: One entry per organization, holding the pool and the version it was built
#: at. Bounded rather than a plain dict: an entry is the whole of one tenant's
#: book, so an unbounded map grows with the number of organizations a process
#: has ever served. Three legal entities today; the headroom is for a
#: deployment serving more, and the LRU is what keeps the memory finite.
#:
#: **No TTL.** A pool that expires on a clock is a pool that is briefly stale
#: for no reason and then rebuilt for no reason; a pool that is invalidated by
#: content is never either. Freshness is :func:`pool_version` — see it for what
#: moves and what does not.
_pool_cache = cache_module.register(cache_module.Cache(
    "sellable_pool", maxsize=8, ttl_seconds=0.0))


def _value_of(value_num: Optional[float], value_text: Optional[str]) -> Any:
    """One stored row's value, back in the type the engine compares it as.

    ``value_num`` is a Float column, so it cannot say whether the decoded value
    was ``4`` or ``4.0`` — and the engine's soft-signal comparison is
    ``str(v).strip().upper()``, under which a request's ``flute_count`` of 4
    *mismatches* a candidate's 4.0 and takes a 0.04 penalty for agreeing. The
    integer/float distinction survives in ``value_text``, which
    ``extract._text_and_number`` writes as ``str(value)``, so it is read back
    from there rather than guessed from the float.

    A row with no ``value_num`` is text — including the ``"true"`` / ``"false"``
    a boolean is deliberately stored as — and is returned as it was stored.
    """
    if value_num is None:
        return value_text
    try:
        return int(value_text)          # "4" -> 4; "0.8" and "4.0" raise
    except (TypeError, ValueError):
        return float(value_num)


def _usable(attribute_key: str, value_num: Optional[float]) -> bool:
    """Whether this row can be compared at all, by the rule its own key states.

    A field whose name carries a unit — ``corner_radius_mm``, ``point_angle_deg``
    — is a number, and ``distance.compare_geometry`` calls ``float()`` on both
    sides of every dimensional field. A non-numeric claim on such a key would
    raise inside the engine and take the whole line to PIE_DOWN, so it is
    dropped here instead: ``ZohoCatalogSource._normalize`` makes the same call
    for the same reason ("unparseable geometry dropped, never guessed").

    ``attributes.unit_for`` is the rule rather than a list of dimensional field
    names copied out of ``equivalence/distance.py``. That list would be a second
    statement of which fields are numeric, and the two would disagree the first
    time the pack emits a dimension nobody added here.
    """
    return unit_for(attribute_key) is None or value_num is not None


def _live_attributes(session: Session,
                     organization_id: str) -> Dict[str, Dict[str, Any]]:
    """``{product_id: {attribute_key: value}}`` for one organization.

    One query for the whole organization rather than ``writer.live_values`` per
    product: that function answers about one product and is right for a writer,
    and calling it in a loop over several thousand products is several thousand
    round trips on the quote screen's critical path. The filters are the same
    two it applies — the organization, and ``superseded_at IS NULL`` for the
    live row — because those are the store's contract and not this module's
    choice.

    :data:`SOURCE_PRECEDENCE` decides every disagreement, once, here.
    """
    pav = models.ProductAttributeValue
    by_product: Dict[str, Dict[str, Any]] = {}
    ranks: Dict[Tuple[str, str], int] = {}
    rows = session.execute(
        select(pav.product_id, pav.attribute_key, pav.source_kind,
               pav.value_num, pav.value_text)
        .where(pav.organization_id == organization_id,
               pav.superseded_at.is_(None)))
    for product_id, key, source_kind, value_num, value_text in rows:
        if not _usable(key, value_num):
            continue
        rank = _PRECEDENCE.get(source_kind, _UNRANKED)
        seat = (product_id, key)
        if seat in ranks and ranks[seat] <= rank:
            # A stronger claim already holds this field. Ties keep the first
            # row seen, which cannot happen for a well-formed store — the
            # partial unique index allows one live row per source kind — and is
            # only a rule so that a store with a duplicate is deterministic
            # rather than dependent on scan order.
            continue
        ranks[seat] = rank
        by_product.setdefault(product_id, {})[key] = _value_of(value_num,
                                                               value_text)
    return by_product


def _record(product_id: str, name: str, manufacturer: Optional[str],
            values: Dict[str, Any],
            family: Optional[str] = None) -> Dict[str, Any]:
    """One product as a canonical record, in the field names the engine reads.

    The attribute store keys on "the engine's own field name —
    ``corner_radius_mm``, ``flute_count``, ``iso_shape``", deliberately not
    remapped on the way in, so the values need no translation on the way out:
    the store's vocabulary and the engine's are the same vocabulary. That is
    what makes this a projection instead of a mapping table.

    Descriptive fields are written **after** the values, so a stored attribute
    can never rename the record or its description.

    ``record_id`` is the portal's own ``product_id``: unique within the
    organization, stable across a re-sync (``products`` is upserted on its
    source key), and the same shape for a product that arrived through Zoho and
    one that arrived through Acumatica. What it is *not* is a code the books can
    price — ``store._enrich_from_zoho`` looks a supply code up by SKU or name —
    so a candidate from this pool reaches a quote as an offerable candidate with
    no price attached until that lookup learns about portal product ids. That is
    a change in files this one does not own, and offering the product is the
    half that was missing.

    ``product_family`` comes from ``products.decoded_family`` — the route the
    parser placed the name in — and NOT from the attribute store, which refuses
    it (``attributes.ROUTE_FIELDS``) because the engine emits a route on every
    routed row and counting it would report Phase 1's coverage as ~100% with an
    office chair as a decorated product.

    **An earlier version of this docstring argued the field could be left out**,
    on the grounds that a gate only constrains what both sides specify, so its
    absence "widens the pool rather than emptying it — the safe direction". That
    is wrong, and it was wrong in the direction that puts a wrong part on a
    quote. ``product_family`` is the strongest of ``distance.HARD_GATE_FIELDS``,
    and a record without one matches **across families**: with it unstored, a
    real 11.1 mm drill in the book came back rank 0 for "endmill 11.1mm 4
    flute", scored 1.0, and marked verified rather than unverified — because a
    dimension genuinely was compared, so nothing downstream had grounds to
    doubt it. Widening a pool is only safe when the thing being widened past is
    noise; here it was the one gate standing between a drill and an endmill.

    A record whose product has no route carries ``None``, which the engine reads
    as unspecified — so this narrows the damage rather than ending it, and a
    NULL family is a reason to distrust a match rather than to trust it.
    ``iso_shape`` and ``insert_polarity``, the other two gates, come from the
    store and were always doing their work.
    """
    # Sorted, for ``attributes.decoded_facts``' reason: dict order is byte order
    # the moment anything serialises this, and two runs over one unchanged book
    # must produce the same bytes. The scan that filled ``values`` has no ORDER
    # BY — sorting four keys per product is free, and sorting 13,000 rows in
    # SQLite is not.
    rec: Dict[str, Any] = {key: values[key] for key in sorted(values)}
    clean = _WHITESPACE.sub(" ", (name or "").strip())
    rec["record_id"] = product_id
    rec["item_name"] = clean
    rec["description"] = clean
    rec["description_raw"] = clean
    # ``query._dedup_key`` ends on ``description_norm``. Left None on every
    # record, it would collapse two differently-named products that decoded to
    # the same facts into one suggestion — silently dropping one of two things
    # the business could actually ship.
    rec["description_norm"] = clean
    # After the values, like the descriptive fields above: a stored attribute
    # must not be able to overwrite the route the router actually chose.
    if family:
        rec["product_family"] = family
    if manufacturer:
        # Raw, as ``products.manufacturer`` stores it. Evidence for a human
        # reading the record — the engine scores on ``brand_label``, not this.
        rec["manufacturer"] = manufacturer
    return rec


class SellableCatalogSource:
    """One organization's sellable products, as a pie-parser ``CatalogSource``.

    Duck-typed rather than a subclass, for ``OrgMappingStore``'s reason: this
    module is imported wherever ``pie_service`` is, and a deployment built
    without the private pie-parser submodule must still import it. The ABC is
    ``equivalence.catalog.CatalogSource``, nothing in the engine does an
    ``isinstance`` check on it, and its whole contract is ``label`` plus
    ``load()``. ``CanonicalRecord`` *is* imported — from the engine, lazily —
    because re-declaring that dataclass here would be a second definition of the
    record shape the scoring layer reads.

    Snapshotted at construction, like ``OrgMappingStore`` and for the same
    reason: a quote's lines resolve in one pass, and a pool that changed halfway
    through would make one RFQ resolve two ways. It also keeps the engine, which
    knows nothing about sessions, from holding one open across a resolution.

    One difference from that store, and it is the one to preserve. ``OrgMappingStore``
    is built per request; this is **cached and shared across concurrent
    requests**, because the app serves on a thread pool. Nothing mutates it
    after ``__init__`` — the records are built once and ``load`` hands the same
    list back — and that immutability is the whole of why sharing it is safe.
    Adding a method that writes to ``_records`` would make two quotes one, the
    way the resolution cache's deep copy exists to prevent.
    """

    label = SELLABLE_LABEL

    def __init__(self, records: List[Dict[str, Any]], *, version: str,
                 organization_id: str, without_attributes: int = 0) -> None:
        from equivalence.catalog import CanonicalRecord  # noqa: PLC0415

        self.organization_id = organization_id
        #: Products in this organization holding no live attribute at all. Not
        #: in the pool, and counted rather than dropped in silence — see
        #: :func:`build_pool` for why they are excluded and what it costs.
        self.without_attributes = without_attributes
        self._version = version
        self._records = [CanonicalRecord(record=rec, brand_label=self.label,
                                         pack_id=SELLABLE_PACK_ID)
                         for rec in records]

    def load(self) -> List[Any]:
        """Every candidate record this organization offers.

        Built once and returned by reference, exactly as ``PieCatalogSource``
        does, because ``resolve_rfq.run`` loads every source at least twice per
        resolution — once to compute the sellable namespaces and once inside
        ``find_equivalents`` — and rebuilding the graph for the second read
        would double the cost of the thing this module exists to make cheap.
        """
        return self._records

    def fingerprint(self) -> str:
        """A value that changes when what this source offers changes.

        The same contract ``OrgMappingStore.fingerprint`` has, and read by the
        same caller: an engine result depends on the pool the engine could see,
        so ``PieService._cache_key`` has to carry it or one organization's
        cached answer is served to another.
        """
        return self._version

    def __len__(self) -> int:
        return len(self._records)


def pool_version(session: Session, organization_id: str) -> str:
    """A content version of this organization's book. Two aggregates, no rows.

    What has to move it: a product added, removed or deactivated; a product
    renamed (its name is the record's description, and its decode is rewritten);
    an attribute created, superseded, or superseded-with-a-replacement. Counting
    the live rows and taking the newest timestamp on each side catches all of
    them, because every one of those either changes a count or appends a row:

    * a retraction supersedes a live row with no replacement — the live count
      falls;
    * a correction supersedes one and appends another — the count holds and the
      newest ``created_at`` moves;
    * a deactivated or renamed product moves ``products.updated_at``, which
      ``onupdate`` stamps on every ORM write.

    It is deliberately not a hash of the pool. Reading six thousand products and
    thirty-four thousand attribute rows to decide whether to read them again is
    the cost this exists to avoid: measured, that read has a median of 154 ms
    and this has a median of 11 ms. Both aggregates filter on an indexed
    ``organization_id``, and the attribute half is the half that grows, because
    the live rows outnumber the products several times over.

    What it cannot see is an **in-place edit of a live row**, on either side.
    A product changed by raw SQL without touching ``updated_at``; an attribute
    row whose ``value`` is rewritten where it stands, which moves neither the
    live count nor the newest ``created_at``. Both leave a stale pool that
    believes itself current, and neither is ruled out by this function — it is
    ruled out by how the two writers behave: ``ingestion`` goes through the
    ORM, whose ``onupdate`` stamps ``products.updated_at``;
    ``attributes.writer`` supersedes and appends and never mutates a live
    value, which is the convention that makes a count and a timestamp
    sufficient here. A change to either writer is a change to this function's
    correctness, and a migration that edits rows in place is the place to say
    so.
    """
    product = models.Product
    pav = models.ProductAttributeValue
    products = session.execute(
        select(func.count(product.product_id), func.max(product.updated_at))
        .where(product.organization_id == organization_id,
               product.active.is_(True))).one()
    attributes = session.execute(
        select(func.count(pav.attribute_value_id), func.max(pav.created_at))
        .where(pav.organization_id == organization_id,
               pav.superseded_at.is_(None))).one()
    return cache_module.fingerprint("sellable_pool", organization_id,
                                    *products, *attributes)


def build_pool(session: Session,
               organization_id: str) -> Optional[SellableCatalogSource]:
    """This organization's pool, built from the database. ``None`` when empty.

    **A product is in the pool when this organization holds at least one live
    attribute for it.** A record carrying no comparable field is not gated out
    by anything — ``compare_geometry`` gates only on what both sides specify —
    so it reaches ``dimensional_score`` with nothing to score and takes the 1.0
    that means "no evidence against", at the top of the same scale a real match
    is measured on. That is "absence of evidence is not a pass" arriving in the
    ranking: the record scores best precisely because nothing about it could
    disagree.

    Such a record cannot be matched *on*, it consumes one of ``top_n`` slots a
    real option would have had, and it shares a ``rank_tier`` with genuinely
    partly-decoded records — so which one a person sees is decided by
    ``record_id``, sort order rather than evidence. ``query._dedup`` collapses
    several of them into one entry, since its key is the canonical slots and
    those are all ``None``, which limits how many slots they can take but not
    whether they lead. On the run recorded below, 70 of the 93 candidates the
    pool newly surfaced (75%) were dimensionally vacuous, and a vacuous new
    leader displaced the old one on 10 of 62 lines.

    The cap holds either way, and that is worth saying because it is what makes
    this a ranking decision rather than a safety one: ``PieService._unverified``
    reads the engine's ``dimensionally_vacuous`` on these exactly as it does on
    a catalogue record, so such a candidate is capped at POSSIBLE however it
    scored and is never auto-selected. Excluding them is about not spending a
    slot on a record that says nothing, not about stopping a bad TECH.

    So the pool grows as decoration coverage grows, which is the intended
    relationship between Phase 1 and this: ``without_attributes`` on the
    returned source is how many products are waiting on it, and it is counted
    rather than left to be inferred from a pool that came out smaller than
    somebody expected. On the harness's seeded master it was 147 of 6,717 —
    2.2% still unofferable — and the fix for that is more decoded product, not
    a looser pool. **That 2.2% is a ceiling artefact, not a forecast**: every
    item name in that master is a catalogue-format designation and decodes at
    97.6%, where ``DECODED_NAME`` reaches about 21% of the live master, whose
    names read ``SC DRILL SLOT ENDMILL``. The harness prints that caveat beside
    its own coverage line for the same reason it is repeated here.

    ``None`` for an organization with nothing to offer, so a deployment that has
    never been decorated resolves **exactly** as it does today — no source
    appended, nothing added to the cache key, no behaviour to regress.
    """
    # **Read before the rows, not after.** A version taken afterwards would
    # stamp this pool with a state newer than the rows it actually holds, so a
    # write landing mid-read would leave a pool that is stale and *says* it is
    # current — cached, and never rebuilt, because its fingerprint already
    # matches. Taken first, the same write leaves the pool stamped older than
    # its content: the next call sees the version move and rebuilds. One of
    # those two orders is wrong forever and the other is wrong for one request.
    version = pool_version(session, organization_id)
    values = _live_attributes(session, organization_id)
    product = models.Product
    rows = session.execute(
        select(product.product_id, product.name, product.manufacturer,
               product.decoded_family)
        .where(product.organization_id == organization_id,
               product.active.is_(True))
        # Ordered, because the engine's ranking breaks its last tie on
        # ``record_id`` and a pool in scan order would let two runs over one
        # unchanged database disagree about which of two identical products is
        # named first.
        .order_by(product.product_id))
    records: List[Dict[str, Any]] = []
    without = 0
    for product_id, name, manufacturer, family in rows:
        facts = values.get(product_id)
        if not facts:
            without += 1
            continue
        records.append(_record(product_id, name, manufacturer, facts, family))
    if not records:
        return None
    return SellableCatalogSource(
        records, version=version, organization_id=organization_id,
        without_attributes=without)


def sellable_pool_for(session: Session,
                      organization_id: str) -> Optional[SellableCatalogSource]:
    """This organization's candidate pool, cached until its book changes.

    The cache is the whole reason this function exists rather than callers using
    :func:`build_pool` directly, and the numbers are why. Every one below is
    printed by ``scripts/measure_sellable_pool.py`` — section 4 of its report —
    so a reader who doubts one can re-run it rather than trust this paragraph.
    An earlier draft of this table quoted a scratch session no committed script
    reproduced, and quoted its *best* per call while the median ran half again
    higher; the medians are what stands here, because a median is what a request
    actually waits.

    Measured on that harness's seeded master: 6,717 products, 33,915 live
    attribute rows, a 6,570-record pool, on SQLite, 15 repeats, on a machine
    doing other work. **Three runs**, and every figure is the range across them
    rather than a point — a second run already fell outside the first's numbers
    and a third outside the second's, so a point value here would be a
    precision this measurement does not have:

    * :func:`build_pool` — two queries and the object graph — median
      **154-164 ms** (p95 398-430);
    * :func:`pool_version` — the two aggregates this checks instead — median
      **11-12 ms** (p95 12-14), so a hit skips ~93% of the build;
    * ``PieService.resolve`` for one line, over 62 requirement lines: median
      **27-32 ms** against the manufacturer catalogue alone, **48-55 ms** with
      the pool — so the bigger pool costs the engine **+18 to +23 ms** on the
      median line, and much more in the tail: p95 **142-286 ms** becomes
      **422-459 ms**.

    The build and version figures are steady across runs; the resolve figures
    are not, and the tail least of all. Treat the two ends of each resolve
    range as the measurement and the middle as noise.

    Building per request would therefore have put another ~160 ms *on top of*
    the ~20 ms the pool already costs the engine — quadrupling the median
    resolution — and it would have paid it again for every line of an RFQ. A
    cache hit costs the ~12 ms version check, and it is paid once per request
    rather than once per line: the pool is built by the caller alongside the
    mapping store and passed down, the way ``OrgMappingStore`` already is.

    The ~20 ms the engine spends on the bigger pool is not cached away and is
    not meant to be — it is the cost of actually searching the book, and it is
    what the ~91% of the master that could not be offered at all is worth. The
    resolution cache absorbs the repeat of a line, not its first read.

    **Read the tail, not the median, before calling this cheap.** The p95 is
    where this hurts, and an RFQ is many lines rather than one. It is measured
    on a 6,570-record pool that a real book reaches only at full decode
    coverage — about 21% today — so the tail is the number that grows as
    Phase 1 succeeds, and the one to re-measure when it does.

    What makes the cache safe is that its key is the organization and its value
    carries :func:`pool_version`, so a hit is served only when the book has not
    moved — a stale pool would offer a product the business stopped selling,
    with an explanation attached.

    ``None`` on any failure, for ``mapping_store_for``'s reason: a pool is what
    the organization can *additionally* offer, and losing it degrades resolution
    to the manufacturer catalogue alone rather than failing an intake.

    Not locked across the build. Two threads that miss together build two
    identical pools and one of them is stored — wasted work, not a wrong answer,
    since both are built from the same rows under the same version. A lock held
    for the ~160 ms of a build would instead make every other request for that
    organization wait behind it, which is the more expensive mistake.
    """
    if not organization_id:
        return None
    try:
        version = pool_version(session, organization_id)
        cached = _pool_cache.get(organization_id)
        if cached is not cache_module.MISS and cached.fingerprint() == version:
            return cached
        pool = build_pool(session, organization_id)
        if pool is not None:
            _pool_cache.set(organization_id, pool)
        else:
            # An organization that had a pool and now has none must not keep
            # serving the old one. Dropping the entry is the retraction.
            _pool_cache.invalidate(organization_id)
        return pool
    except Exception:  # noqa: BLE001 — the book is never a reason to fail intake
        log.exception("could not build the sellable pool for %s", organization_id)
        return None
