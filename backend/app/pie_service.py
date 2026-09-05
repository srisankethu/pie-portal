"""In-process bridge to the pie-parser Product Intelligence Engine.

This is the heart of the integration. It loads pie-parser (the ./pie-parser
submodule) once, reusing its identity-first ``resolve_rfq.run()`` orchestration,
and maps
the engine's output onto the quote-builder's *relationship* vocabulary
(EXACT / TECH / COMPAT / POSSIBLE / AMBIGUOUS / UNRESOLVED) that the design's
supply column and drawer render.

pie-parser deals only with product *nomenclature* — it never carries price or
stock (see its README). Availability, cost and list price come from the Zoho
layer, keyed by the manufacturer MM# that resolution returns.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import logging
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cache as cache_module
from . import catalog as catalog_module
from . import retrieval
from .config import settings

log = logging.getLogger("pie_portal.pie")

#: The engine's verdict for one product code, remembered.
#:
#: Resolution is the most expensive thing a quote does — an identity lookup and
#: a scored pass over the whole decoded catalogue, per line — and a quote
#: re-resolves the same codes every time it is rebuilt, while a customer's
#: repeat enquiry is *the same text* as last month's. The answer is a pure
#: function of the engine's inputs, which is what makes it cacheable at all.
#:
#: What is in the key, and why each one has to be (see ``app/cache.py``):
#:
#: * the catalogue's ruleset version — a rebuilt catalogue decodes differently,
#:   and that is exactly the fact that explains why the same text resolved to a
#:   different product last March;
#: * the requested text, and the customer identity it is resolved under;
#: * a fingerprint of the confirmed mappings the engine could read — confirming
#:   "this customer's X means MM# Y" changes the answer for that customer
#:   immediately, and a cache that outlived the confirmation would keep telling
#:   them it had not been recorded;
#: * a fingerprint of the organization's own candidate pool. This cache is
#:   process-wide and read by every tenant, so this is the entry that keeps one
#:   organization's answer — computed against that organization's products —
#:   from being served to another. It also moves when a sync decorates the
#:   book, which is what stops a pool that has grown from being answered out of
#:   the pool it replaced.
#:
#: Not in the key, deliberately: the equivalence *bands*. They are commercial
#: policy applied by ``_map`` after the engine has run, so two organizations
#: with different bands read one cached engine result differently — which is
#: the correct relationship between a fact and the policy that judges it (§1 —
#: "an equivalence score is policy, never an identity").
#:
#: There is no money in a cached value: this is nomenclature, and price, cost
#: and stock are read from the books afterwards by ``store._enrich_from_zoho``.
_resolution_cache = cache_module.register(cache_module.Cache(
    "pie_resolution",
    maxsize=settings.PIE_CACHE_SIZE,
    ttl_seconds=settings.PIE_CACHE_TTL_SECONDS,
))

# ── relationship model (mirrors the design's REL table) ──────────────────────
# rank orders lines by how much attention they need (lower = calmer).
REL_RANK = {
    "EXACT": 0, "TECH": 1, "COMPAT": 2, "POSSIBLE": 3,
    "INSUFF": 7, "AMBIGUOUS": 7, "INCOMPATIBLE": 8, "UNRESOLVED": 9,
    "PIE_DOWN": 6, "NONE": 9,
}

#: Score bands that turn a fuzzy equivalence suggestion into a relationship.
#:
#: The numbers themselves are commercial policy and live in
#: ``CommercialThresholds`` so they carry a version — see the note there. This
#: is only the fallback for a caller with no organization in hand (the Quote
#: Builder before anyone signs in to the platform), and it reads the same
#: dataclass rather than restating the values, so the two cannot drift.
@dataclass(frozen=True)
class Bands:
    """Where 'technically equivalent' stops and 'merely compatible' begins."""

    tech: float
    compat: float

    @classmethod
    def default(cls) -> "Bands":
        from .commercial.config import CommercialThresholds
        t = CommercialThresholds()
        return cls(tech=t.equivalence_tech_band, compat=t.equivalence_compat_band)


#: The decoded slots worth carrying out of the engine, and the reason this
#: tuple is duplicated from pie-parser rather than imported: it is the *portal's*
#: statement of what a line may show, and importing the engine's copy would let
#: a pack change silently widen what reaches a screen. The two are deliberately
#: identical today — pie-parser's ``equivalence/query.py`` builds a suggestion's
#: ``attributes`` from this same list — because the EXACT path and the
#: suggestion path must describe a product the same way. If they ever diverge,
#: the same insert would read differently depending on how it was found.
ATTRIBUTE_FIELDS = (
    "product_family", "iso_shape", "iso_clearance_letter", "iso_tolerance",
    "iso_fixing", "insert_polarity", "cutting_dia_mm", "edge_length_mm",
    "corner_radius_mm", "thickness_mm", "flute_count", "coating",
    "coating_process", "material_class",
)


def _attributes_of(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Project a decoded catalogue row onto the fields a line may show.

    Absent slots are omitted rather than emitted as ``None``: a key whose value
    is null reads, on a screen and in a diff, as "the engine decoded this and
    found nothing", which is a different claim from "this was never decoded".
    """
    if not record:
        return {}
    return {k: record[k] for k in ATTRIBUTE_FIELDS if record.get(k) is not None}


@dataclass
class Candidate:
    """One ranked supply option for a requested line."""

    code: str                       # manufacturer MM# / record id
    desc: str
    rel: str                        # relationship vocabulary
    grade: Optional[str] = None
    brand: Optional[str] = None
    score: Optional[float] = None   # combined equivalence score (None for exact)
    reason: str = ""                # human explanation from the engine
    attributes: Dict[str, Any] = field(default_factory=dict)
    #: The comparison did not cover everything the request specified, so the
    #: score beside it is a ceiling nothing pushed down rather than a measure of
    #: fit. Two ways that happens, and they are one fact for a reader:
    #:
    #: * **nothing** was comparable — the engine's own ``dimensionally_vacuous``.
    #:   A bearing scored 1.0 against a carbide insert.
    #: * **something** was: the request named a dimension this record does not
    #:   carry, and `distance.py` skips it rather than penalising it
    #:   (``candidate_absent``). A record silent on the one dimension the
    #:   customer changed, agreeing on two incidental ones, scored 1.0.
    #:
    #: Either way the engine could not verify the fit, and an unverified fit is
    #: not a technical equivalence. Carried so the band mapping and the
    #: auto-selection can both refuse it.
    unverified: bool = False
    #: The engine's own ranking key for this candidate, minus the record-id tail
    #: that breaks ties by sort order rather than by evidence. Two candidates
    #: with equal tiers are two the ranking did not choose between. Carried so
    #: `_is_discriminating` asks the ranking's question instead of comparing one
    #: published component of it — see that method. ``None`` where the engine
    #: did not supply it.
    rank_tier: Optional[List[Any]] = None
    #: Found by nearest-neighbour retrieval over the catalogue's descriptions
    #: (``app/retrieval``) rather than by the engine's ranked pass. The engine
    #: still compared it — a record its gates reject is never offered — but
    #: nothing ranked it, so it carries no ``score``, is always ``POSSIBLE``,
    #: and is never the supply product. Carried so a screen can say which of
    #: the two a candidate is, because "the nearest description" and "the
    #: technical equivalent" must not read alike.
    retrieved: bool = False
    #: The confirmed customer code this retrieved record was found through,
    #: when it was — a near miss of a code this customer already confirmed
    #: means this product (``app/retrieval/aliases``). Still ``retrieved``,
    #: still POSSIBLE, still never selected: the confirmation answered an
    #: exact question once, and this line is not that question.
    alias: Optional[str] = None
    #: What ``alias`` is: ``"code"`` — a mapping a person confirmed, which the
    #: engine would resolve exactly if the line were the code; ``"phrase"`` —
    #: words a person had quoted as this record for this customer, which the
    #: engine never reads and which asserts nothing. One flag on the wire so a
    #: screen does not say "confirmed" about a choice.
    alias_kind: Optional[str] = None
    #: Which of the company's catalogues this record came from — the
    #: manufacturer's catalogue key (``kennametal``, ``yg-1``), read off the
    #: union record. A company resolves against every catalogue it has built
    #: at once, and "which manufacturer's product is this" is part of what a
    #: provenanced answer has to say. ``None`` where the record could not be
    #: read back, never a guess.
    catalogue: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "desc": self.desc, "rel": self.rel,
            "grade": self.grade, "brand": self.brand, "score": self.score,
            "reason": self.reason, "attributes": self.attributes,
            "unverified": self.unverified, "retrieved": self.retrieved,
            "alias": self.alias, "alias_kind": self.alias_kind,
            "catalogue": self.catalogue,
        }


@dataclass
class Resolution:
    """The engine's verdict for one requested item, in portal terms."""

    input_text: str
    reqCode: str
    reqDesc: str
    rel: str                        # top-level relationship for the line
    supplyCode: Optional[str]       # auto-selected supply MM# (None if unresolved)
    candidates: List[Candidate]
    outcome: str                    # raw engine outcome (for audit/debug)
    semantics: str                  # IDENTITY | REQUIREMENT | MIXED
    notes: List[str] = field(default_factory=list)
    pie_offline: bool = False
    #: Provenance of the retrieval pass, when one ran: the index's model id,
    #: how many records it searched and how many it offered. None where
    #: retrieval did not run — an exact identity, an ambiguity, no index — so
    #: absence reads as "not searched" and never as "nothing near".
    retrieval: Optional[Dict[str, Any]] = None
    #: The record the engine proposed as this line's IDENTITY — "this customer's
    #: code is probably MM# X, confirm it" — or None, which is the normal case.
    #:
    #: **Set by exactly one branch of :meth:`PieService._map`, and that is the
    #: whole point.** It used to be reconstructed downstream from ``outcome ==
    #: "NEEDS_REVIEW" and len(candidates) == 1``, and those two fields do not
    #: carry the distinction the boundary needs: a candidate can be an exact
    #: catalogue hit the engine declined to assert (branch 1b, read out of
    #: ``matches``) or a scored equivalence suggestion (branch 3, read out of
    #: ``suggestions``), and both can arrive carrying that outcome and that
    #: count. Only ``_map`` knows which, so only ``_map`` may say.
    #:
    #: A confirmed mapping is *asserted* identity: afterwards the engine
    #: resolves that code AUTHORITATIVELY and derives a requirement from the
    #: record. Letting a scored suggestion become one is how ``tolerance ∘
    #: tolerance`` gets licensed permanently — see CLAUDE.md §1 and
    #: ``tests/test_identity_confirmation_gate.py``.
    identity_candidate: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_text": self.input_text,
            "reqCode": self.reqCode, "reqDesc": self.reqDesc,
            "rel": self.rel, "supplyCode": self.supplyCode,
            "candidates": [c.to_dict() for c in self.candidates],
            "outcome": self.outcome, "semantics": self.semantics,
            "notes": self.notes, "pie_offline": self.pie_offline,
            "retrieval": self.retrieval,
            "identity_candidate": self.identity_candidate,
        }


#: ``rule_set_families``' memo, keyed by rule-set path. Kept per path because
#: an organization's price lists decode through several. A *failed* read is
#: deliberately not cached: a rule set fetched after boot must be seen on the
#: next call, or every family edit stays refused until a restart for a failure
#: that has been fixed.
_families_memo: Dict[str, tuple] = {}


def rule_set_families(rule_set_path: Path) -> Optional[tuple]:
    """The family vocabulary one rule set declares, or None if it cannot be read.

    A rule set is what pie-parser keeps as an org-layer pack; the portal calls
    it by what it is to the portal — the decoder half of one price list's
    decoding config — and there is no default one, so the path is required.

    Read from the manifest alone: resolving a code needs the whole engine, but
    the vocabulary is one YAML list, and a policy save must not pay for grammar
    compilation to check five key names. Parsed by pie-parser's own
    ``families_from_config`` rather than a local re-reading of the YAML, so
    "what counts as a declared family" keeps exactly one definition — the one
    ``load_pack`` itself uses.

    ``None`` means *this rule set is not readable here* — a checkout without
    the private submodule, or an id this engine no longer ships — which is a
    different answer from an empty vocabulary. The caller must treat it as
    "there is nothing to validate against", never as "every name is fine";
    ``commercial.policy.save_for_org`` refuses a family edit outright in that
    state rather than waving it through.

    **A rule set per price list, so a path argument and no default.** This
    used to read ``settings.PIE_PACK`` and document at length why one
    deployment meant one organisation layer: the catalogue was built from one
    pack into one process-wide index. That premise is gone — every price list
    decodes through the rule set its own decoding config names — so the
    vocabulary follows the rule sets that actually decoded the rows.
    ``commercial.policy`` unions the rule sets the organization's saved
    decoding configs name, which is the honest vocabulary for a policy that
    applies to all of them.
    """
    cached = _families_memo.get(str(rule_set_path))
    if cached is not None:
        return cached
    try:
        root = str(settings.PIE_PARSER_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        # Asked of the engine rather than read out of the manifest by hand.
        # This used to yaml.safe_load PIE_PACK/manifest.yaml and pass the raw
        # document to families_from_config, which worked while a rule set was one
        # flat directory. Packs are layered now — an organisation layer extends
        # a shared nomenclature layer — and `families` moved to the layer, so
        # reading the org manifest directly found none and the vocabulary went
        # silently empty. Following the `nomenclature:` reference here would
        # mean a second implementation of the engine's layer resolution, which
        # is the drift CLAUDE.md §2 is about: load_pack already owns it.
        from engine.pack import load_pack  # noqa: PLC0415

        families = tuple(load_pack(rule_set_path).families)
        _families_memo[str(rule_set_path)] = families
        return families
    except Exception:  # noqa: BLE001 — an absent rule set must not 500 a policy save
        log.warning("PIE rule-set manifest unreadable; no family vocabulary to "
                    "validate against", exc_info=True)
        return None


#: How many companies' catalogues stay resident at once.
#:
#: Each is a decoded index over roughly 6,700 records plus the resolver's
#: sources, so "one per company, forever" is a memory profile nobody measured.
#: Three is chosen against the shape of the business this serves — a group runs
#: two or three legal entities and a person works one at a time — not against a
#: benchmark. The number to watch is the eviction rate: if a deployment thrashes
#: here, raise it deliberately with that measurement in hand rather than because
#: a larger number feels safer.
MAX_RESIDENT_CATALOGUES = 3


@dataclass
class _View:
    """One company's loaded catalogue: the union of every catalogue it has built.

    ``path`` is the union file ``catalog.union_catalogue`` keeps current, and
    ``version`` is that union's version — one catalogue's own ruleset checksum
    where a company has one, a hash over every member's stamp where it has
    several. ``catalogues`` says which manufacturers' catalogues are in it,
    for the provenance a resolution reports.

    ``sources`` is built lazily and separately from ``index``: a sync asks only
    "is this SKU a catalogue record?", and making every item pull construct the
    RFQ resolver's equivalence sources to answer it would charge it for
    machinery it never calls. That split is the old ``_ensure_index`` versus
    ``_ensure_loaded`` distinction, kept — it is now per company rather than
    per process.
    """

    path: Path
    version: str
    index: Any
    sources: Any = None
    #: The nearest-neighbour index, loaded on the first requirement that could
    #: use it — a sync never needs it, for the same reason ``sources`` is lazy.
    #: ``None`` is not yet tried; ``False`` is tried and unavailable, remembered
    #: so a missing index is not re-attempted per line.
    retriever: Any = None
    #: The dense re-ranker, when a model directory is configured; ``False``
    #: once tried and unavailable, like ``retriever``.
    reranker: Any = None
    #: The catalogues the union was made from, as ``catalog.union_catalogue``
    #: reports them: key, name, rule sets, stamp and record count each.
    catalogues: List[Dict[str, Any]] = field(default_factory=list)


class PieService:
    """Loads pie-parser once, and each company's catalogue on demand.

    The engine module is process-wide — it is code, and every company runs the
    same code. What is per company is the *data*: the decoded catalogue, the
    index built from it, and the sources the resolver searches. Those live in a
    small bounded cache keyed by connection id.
    """

    def __init__(self) -> None:
        self._mod = None
        self._lock = threading.Lock()
        # connection_id -> loaded view, or None meaning "tried, and there is
        # nothing there". The None entries are the memo that keeps a missing
        # catalogue from being re-attempted once per row of a 15,000-item sync.
        self._views: "OrderedDict[str, Optional[_View]]" = OrderedDict()
        self._view_lock = threading.Lock()
        # Alias indexes over confirmed codes, keyed by the mapping store's
        # fingerprint — the same value the resolution cache keys on, because
        # it changes exactly when the set of confirmed codes does. Bounded and
        # small: a store is one organization's snapshot, and a 200-line quote
        # must not build the same index 200 times.
        self._alias_indexes: "OrderedDict[str, Any]" = OrderedDict()
        # Learned vocabularies, keyed by the store's fingerprint *and* the
        # catalogue: the pairs are a store's phrases beside this company's
        # records, so the same phrases read against another company's
        # catalogue are another vocabulary.
        self._vocabularies: "OrderedDict[tuple, Any]" = OrderedDict()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _ensure_module(self):
        """The engine itself, loaded once for the process.

        Raises when pie-parser is absent, because a caller asking to resolve
        has no useful degraded answer — :meth:`resolve` catches it and returns
        PIE_DOWN for that line.
        """
        if self._mod is not None:
            return self._mod
        with self._lock:
            if self._mod is not None:
                return self._mod
            root = str(settings.PIE_PARSER_ROOT)
            if not (settings.PIE_PARSER_ROOT / "tools" / "resolve_rfq.py").exists():
                raise RuntimeError(
                    f"pie-parser not found at {root}. Fetch it with "
                    "./scripts/setup_pie_parser.sh (or set PIE_PARSER_ROOT)."
                )
            if root not in sys.path:
                sys.path.insert(0, root)
            spec = importlib.util.spec_from_file_location(
                "pie_resolve_rfq", settings.PIE_PARSER_ROOT / "tools" / "resolve_rfq.py"
            )
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            self._mod = mod
            log.info("pie-parser engine loaded from %s", root)
            return self._mod

    def warm(self) -> None:
        """Eagerly load the engine (called on app startup).

        The *engine* only. Which companies exist is a database question this
        object has no session for, and warming every company's catalogue at
        boot would trade a slow first quote for a slow start and a memory
        spike — on a deployment where most of those companies may not be
        quoted against today at all.
        """
        try:
            self._ensure_module()
        except RuntimeError:
            log.warning("pie-parser is not present; resolution will report the "
                        "engine as unavailable", exc_info=True)

    def reload(self, connection_id: Optional[str] = None) -> None:
        """Forget what was loaded for one company, so the next use re-reads it.

        Called after one of that company's catalogues is rebuilt or removed
        (``catalog.refresh_union``). Everything dropped is downstream of the
        union file: the index, the resolver's sources, and the version. The remembered *failure* goes with
        it — that memo exists so a missing catalogue is not re-tried per row,
        and a rebuild is precisely the event that makes retrying right again.

        **One company, not all of them.** A rebuild for one must not cost every
        other company its warm index; that was the whole reason for keying the
        cache. ``connection_id=None`` clears everything, which is what a test
        or an engine-level change wants and what a rebuild must not do.

        The resolution cache is left alone either way: its keys carry the
        catalogue version, so entries from a superseded build are unreachable
        and entries from an identical rebuild stay valid.

        A request mid-resolution when this runs may find its view gone and
        degrade to PIE_DOWN for that one line — the same isolation any engine
        failure gets, and an accepted cost of a rare administrative action.
        """
        with self._view_lock:
            if connection_id is None:
                self._views.clear()
            else:
                self._views.pop(connection_id, None)
        # The family vocabulary too: a company that has just rebuilt may have
        # done so through a different rule set, and a memo from the previous
        # one would validate its policy against a vocabulary nothing decodes
        # with.
        _families_memo.clear()

    # ── exact catalogue identity ─────────────────────────────────────────────
    def _view(self, connection_id: Optional[str]):
        """This company's loaded catalogue, or None if it has none.

        Loads on first use and keeps at most :data:`MAX_RESIDENT_CATALOGUES`
        resident, evicting least-recently-used. Failure is remembered *per
        company*, not retried per row: a company without a catalogue would
        otherwise re-attempt once for each of 15,000 items in one sync.

        ``connection_id=None`` is not a company and never resolves to one. It
        returns None rather than falling back to anything — a resolution that
        cannot say which company it is for has no catalogue it may honestly
        answer from, and the deployment-wide default this used to reach for is
        exactly what the per-company design removed.
        """
        if connection_id is None:
            return None
        with self._view_lock:
            if connection_id in self._views:
                view = self._views[connection_id]
                if view is not None:
                    self._views.move_to_end(connection_id)
                return view

            # Absent and broken are different facts, and only one of them is a
            # defect. A deployment built without the private pie-parser
            # submodule reported `ModuleNotFoundError: No module named
            # 'identity'` with a traceback, which reads as a bug in this file;
            # it is a build that shipped without an optional engine, exactly as
            # `deploy/backend.Dockerfile` says it may. Say which one it is.
            root_path = settings.PIE_PARSER_ROOT
            if not (root_path / "identity").is_dir():
                log.warning(
                    "pie-parser is not present at %s, so item links will be left "
                    "unresolved. This deployment was built without the engine "
                    "(see deploy/backend.Dockerfile); nothing else is affected. "
                    "Fetch it with ./scripts/setup_pie_parser.sh, or set "
                    "PIE_PARSER_ROOT.", root_path)
                self._views[connection_id] = None
                return None

            view: Optional[_View] = None
            try:
                root = str(root_path)
                if root not in sys.path:
                    sys.path.insert(0, root)
                from identity.store import AuthoritativeIndex  # noqa: PLC0415

                # The union of every catalogue this company has built, made
                # current from the files on disk. A company resolves against
                # all of its manufacturers at once; which one answered is
                # carried on each union record as `catalogue_key`.
                union = catalog_module.union_catalogue(connection_id)
                if union is None:
                    # Not an error and not a warning: a company that has not
                    # built a catalogue yet is an ordinary state the screens
                    # report. Logging it per row would bury the real failures.
                    log.info("no catalogue built for connection %s", connection_id)
                    self._views[connection_id] = None
                    return None
                view = _View(path=union.path,
                             version=union.version,
                             index=AuthoritativeIndex.from_jsonl(union.path),
                             catalogues=union.catalogues)
                log.info("catalogue loaded for connection %s from %s (%d catalogue(s), "
                         "version %s)", connection_id, union.path,
                         len(union.catalogues), view.version or "unknown")
            except Exception:  # noqa: BLE001 — a sync must not fail on this
                log.warning("catalogue for connection %s could not be loaded; "
                            "item links will be left unresolved",
                            connection_id, exc_info=True)
                view = None

            self._views[connection_id] = view
            # Evict only *loaded* views: the None entries are the memo, they
            # cost nothing to keep, and dropping them would reinstate the
            # per-row retry this cache exists to prevent.
            resident = [cid for cid, v in self._views.items() if v is not None]
            while len(resident) > MAX_RESIDENT_CATALOGUES:
                oldest = resident.pop(0)
                self._views.pop(oldest, None)
                log.info("evicted the catalogue for connection %s", oldest)
            return view

    @staticmethod
    def _record(view: Optional["_View"], identifier: str) -> Optional[Dict[str, Any]]:
        """A decoded row from a view already in hand.

        Separate from :meth:`lookup_record` so ``_map`` cannot re-enter the
        cache while holding a view — and, more importantly, so the decode shown
        beside a match always comes from the *same* catalogue that produced the
        match. Reaching back through the connection id would let an eviction
        between the two answer from a freshly reloaded one.

        No view is no record. It cannot be reached from :meth:`resolve`, which
        holds one by then, and it is the honest answer for a caller mapping a
        result without a catalogue in hand: attributes are *absent*, not empty.
        """
        if view is None:
            return None
        try:
            rec = view.index.lookup_material(str(identifier))
        except Exception:  # noqa: BLE001 — provenance must not break a quote
            log.exception("PIE index lookup failed for %r", identifier)
            return None
        return rec if isinstance(rec, dict) else None

    def catalog_available(self, connection_id: Optional[str]) -> bool:
        """Whether this company has a catalogue loaded to answer lookups against.

        Callers need this to tell "the pack does not cover this item" from
        "nobody asked the pack" — both return None from
        :meth:`lookup_record`, and only the first is evidence about the item.

        **A method taking a company, where it used to be a property.** That is
        the change PR 2 is: there is no longer one answer for the process, and
        a property could only have given one by picking a company on the
        caller's behalf.
        """
        return self._view(connection_id) is not None

    def lookup_record(self, identifier: Optional[str],
                      connection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The decoded catalogue row this identifier *is*, or None.

        Exact only, and exact in pie-parser's sense rather than ours — the
        engine's ``normalize_identifier`` upper-cases and trims but deliberately
        keeps internal separators, because those distinguish two real catalogue
        numbers. Matching more loosely here would put a different manufacturer's
        product on a quote, so anything short of an exact hit is None and the
        caller records nothing.
        """
        if not identifier or not str(identifier).strip():
            return None
        view = self._view(connection_id)
        if view is None:
            return None
        try:
            rec = view.index.lookup_material(str(identifier))
        except Exception:  # noqa: BLE001 — provenance must not break a sync
            log.exception("PIE index lookup failed for %r", identifier)
            return None
        # A bare lookup can also return the store's structured ambiguity when
        # one identifier exists in several namespaces — possible only once a
        # second manufacturer pack is indexed. That is not the decoded row this
        # method promises: an ambiguity is short of an exact hit, so per the
        # contract above the caller records nothing.
        return rec if isinstance(rec, dict) else None

    def catalog_version(self, connection_id: Optional[str] = None) -> str:
        """The version of the catalogue this company resolves against.

        With one catalogue it is that catalogue's ruleset checksum: pie-parser
        derives it from the input bytes plus the rule set's own checksum, which is
        what makes a rerun reproducible — and it is the one fact that explains,
        months later, why the same RFQ text resolved to a different product
        than it does today. With several it is a hash over every member's key,
        ruleset checksum and run id (``catalog.union_catalogue``), which moves
        when any of them is rebuilt; :meth:`catalogues` names the members.

        Empty when this company has no catalogue. Never raises: provenance must
        not be the thing that fails a quote.
        """
        view = self._view(connection_id)
        return view.version if view else ""

    def catalogues(self, connection_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Which manufacturers' catalogues this company resolves against, each
        with its key, name, rule sets and stamp — the members behind
        :meth:`catalog_version`. Empty when it has none."""
        view = self._view(connection_id)
        return [dict(c) for c in view.catalogues] if view else []

    def _make_args(self, text: str,
                   customer_scope: Optional[str] = None,
                   mapping_store: Any = None,
                   catalog_path: Optional[Path] = None) -> argparse.Namespace:
        return argparse.Namespace(
            text=text,
            # This company's decoded catalogue, and nothing else. There is no
            # fallback path here on purpose: an argument that quietly named
            # another catalogue would produce a confidently provenanced answer
            # about the wrong company's product.
            pie_data=catalog_path,
            zoho_fixture=None,
            brands="all",
            top_n=settings.TOP_N,
            json=True,
            # The customer's cross-connector identity, never one connector's row
            # for them and never their name — see identity.identity_for_source.
            # Left None when the customer is unlinked, which resolves exactly as
            # it always has.
            source_customer=customer_scope,
            source_vendor=None,
            source_manufacturer=None,
            # This organization's confirmed "their code means this product"
            # rows. None falls back to pie-parser's packaged store, which is
            # empty — the behaviour before any of this existed.
            mapping_store=mapping_store,
        )

    # ── resolution cache ─────────────────────────────────────────────────────
    @staticmethod
    def _input_fingerprint(value: Any, what: str) -> Optional[str]:
        """A value naming one per-organization engine input, or None.

        None means "do not cache this resolution". That is the answer for an
        input this code cannot fingerprint — a test double, or a future object
        of a different shape — because caching against an unknown input is how
        a confirmed mapping silently stops taking effect. Refusing to cache
        costs a scan; guessing costs the customer a wrong answer with a
        confident explanation attached.

        Two inputs now share this, which is why it is no longer named after the
        mapping store: the confirmed mappings the engine may read, and the
        organization's own candidate pool. They fail identically — a wrong
        answer served confidently from another organization's key — and one of
        those failures crosses a tenant boundary, so the shape of the rule
        matters more than the two call sites do.
        """
        if value is None:
            return "none"                    # the packaged empty store; no pool
        fingerprint = getattr(value, "fingerprint", None)
        if not callable(fingerprint):
            return None
        try:
            return str(fingerprint())
        except Exception:  # noqa: BLE001 — an unfingerprintable input is uncached
            log.exception("could not fingerprint the %s; resolving this line "
                          "without the cache", what)
            return None

    def _cache_key(self, text: str, customer_scope: Optional[str],
                   mapping_store: Any, version: str,
                   pool: Any = None) -> Optional[str]:
        """The key this resolution is stored under, or None if it may not be
        cached at all.

        ``_resolution_cache`` is process-wide and every organization reads it,
        so everything that could make two callers' answers differ has to be in
        the key. Two things can, and both are here.

        ``version`` is the company's ruleset checksum, and it is what keeps two
        companies apart here — deliberately *instead of* the connection id.
        Two companies that uploaded the same export and saved the same rule
        set for it have byte-identical catalogues, so they have identical
        answers, and a key carrying the connection would miss a hit that is
        genuinely correct.

        ``pool`` is the organization's own sellable book, searched beside that
        catalogue, and a key naming only the catalogue would serve one tenant's
        answer — computed against that tenant's products — to another. It goes
        in as a *fingerprint* rather than an organization id for the same
        reason ``version`` does: two organizations are different questions, and
        so is one organization before and after a sync decorated its book.

        **An empty version is never cached.** It means the checksum could not
        be read, and every company whose checksum is unreadable would otherwise
        share one key — the one way this scheme could serve one company's
        answer to another. Refusing to cache costs a scan; guessing costs the
        customer a wrong product with a confident explanation attached, which
        is the same trade ``_input_fingerprint`` makes just below for both the
        mapping store and the pool.
        """
        if _resolution_cache.maxsize == 0 or not version:
            return None
        mappings = self._input_fingerprint(mapping_store, "mapping store")
        if mappings is None:
            return None
        candidates = self._input_fingerprint(pool, "candidate pool")
        if candidates is None:
            return None
        return cache_module.fingerprint(
            "pie_resolution", version, text, customer_scope, mappings,
            candidates)

    @staticmethod
    def _cached_result(key: Optional[str]) -> Optional[Dict[str, Any]]:
        """The stored engine result for this key, deep-copied, or None.

        Copied on the way out for the reason the write side gives: callers keep
        references into this dict, and a shared one would make two quotes one.
        """
        if key is None:
            return None
        stored = _resolution_cache.get(key)
        if stored is cache_module.MISS:
            return None
        return copy.deepcopy(stored)

    # ── resolution ───────────────────────────────────────────────────────────
    def resolve(self, text: str, customer_scope: Optional[str] = None,
                bands: Optional[Bands] = None,
                mapping_store: Any = None,
                connection_id: Optional[str] = None,
                pool: Any = None) -> Resolution:
        """Resolve one RFQ line's text against one company's catalogue.

        ``connection_id`` names the company this line is quoted from, and its
        catalogue is the only one searched. A line with no company resolves
        against nothing and comes back UNRESOLVED — never against some other
        company's products, which would be a wrong part carrying a real stamp.

        ``customer_scope`` is the customer's cross-connector identity when the
        quote is for a linked customer. Passing it lets the engine prefer a
        confirmed "this customer's code means MM# X" mapping over re-reading the
        text; without a mapping the engine still consults the catalogue but
        returns the hit as a candidate to confirm rather than an assertion, so
        naming the customer can only ever add caution, never resolution.

        ``pool`` is this organization's own book as candidate records —
        ``sellable_catalog.sellable_pool_for``. It is an argument and not
        something this service looks up, because it is **tenant data** and this
        object is a process-wide singleton: a pool held on ``self`` would be one
        organization's products answering another organization's request, which
        is the worst defect available here. ``None`` resolves against the
        manufacturer catalogue alone, exactly as this method always has, which
        is what an organization that has never been decorated must still get.

        Any failure inside the engine degrades to a PIE_OFFLINE resolution
        rather than raising, so a single bad line never fails the whole quote —
        this is the design's ``PIE OFFLINE`` line state.
        """
        text = (text or "").strip()
        try:
            mod = self._ensure_module()
            view = self._view(connection_id)
            if view is None:
                # No catalogue for this company. Distinct from the engine being
                # down, and reported as such: nothing was searched, so nothing
                # is known about this text. `resolution.py` asks
                # `catalog_available` first and says so in the caller's own
                # words; this is the floor under that.
                return Resolution(
                    input_text=text, reqCode=text, reqDesc="No catalogue",
                    rel="UNRESOLVED", supplyCode=None, candidates=[],
                    outcome="UNRESOLVED", semantics="UNKNOWN",
                    notes=["This company has no decoded catalogue, so nothing "
                           "was searched for this line."],
                )
            if view.sources is None:
                # Built on first resolution rather than at load: an item sync
                # only needs the index, and paying for the equivalence sources
                # on every catalogue that is merely looked up would be the cost
                # the index/sources split exists to avoid.
                view.sources = mod._build_sources(
                    self._make_args("", catalog_path=view.path))
            if view.retriever is None:
                view.retriever = self._load_retriever(view)

            def run(query: str) -> Dict[str, Any]:
                key = self._cache_key(query, customer_scope, mapping_store,
                                      view.version, pool)
                cached = self._cached_result(key)
                if cached is not None:
                    return cached
                args = self._make_args(query, customer_scope, mapping_store,
                                       catalog_path=view.path)
                # Composed per call and never stored on the view. ``view``
                # is this *company's catalogue* — a decoded file, cached on
                # the service and reused across requests; the pool beside it
                # is one organization's own book, and writing it onto
                # ``view.sources`` would leave one tenant's products answering
                # the next caller's request. That is the worst defect
                # available here, and the composition is local so it cannot
                # happen.
                #
                # Appended to the source *list* rather than handed to the
                # equivalence engine directly, so it passes through everything
                # ``run`` does with a source: ``_sellable_namespaces`` sees its
                # ``pack_id`` and so treats it as sellable, and
                # ``_NamespaceRestrictedSource`` wraps it like the rest. A Zoho
                # item is sellable by definition and still goes *through* the
                # restriction rather than around it.
                sources = (view.sources if pool is None
                           else [*view.sources, pool])
                fresh, _human = mod.run(args, sources)
                if key is not None:
                    # A copy, so the object handed to ``_map`` below — and to
                    # every Candidate that keeps a reference into it — cannot
                    # be reached from the cache. A Line built from a cached
                    # resolution that shared its ``attributes`` dict would let
                    # one quote's edit change another's.
                    _resolution_cache.set(key, copy.deepcopy(fresh))
                return fresh

            result = run(text)
            result = self._with_ranking_reading(
                text, result, run, view, customer_scope, mapping_store)
            resolution = self._map(text, result, bands or Bands.default(), view,
                                   customer_scope=customer_scope,
                                   mapping_store=mapping_store)
            # Which of the company's catalogues each candidate came from, read
            # off the union record. One place rather than one per branch of
            # `_map`, so no path can forget it.
            for cand in resolution.candidates:
                rec = self._record(view, cand.code)
                cand.catalogue = rec.get("catalogue_key") if rec else None
            return resolution
        except Exception:  # noqa: BLE001 — deliberate: isolate engine failures
            log.exception("pie-parser resolution failed for %r", text)
            return Resolution(
                input_text=text, reqCode=text, reqDesc="Awaiting PIE",
                rel="PIE_DOWN", supplyCode=None, candidates=[],
                outcome="ERROR", semantics="UNKNOWN", pie_offline=True,
                notes=["The resolution engine is unavailable for this line."],
            )

    # ── mapping: engine output -> portal Resolution ──────────────────────────
    def _map(self, text: str, result: Dict[str, Any], bands: Bands,
             view: Optional["_View"] = None, *,
             customer_scope: Optional[str] = None,
             mapping_store: Any = None) -> Resolution:
        res = result.get("resolution", {}) or {}
        outcome = res.get("outcome", "UNRESOLVED")
        semantics = res.get("input_semantics", "REQUIREMENT")
        matches = res.get("matches", []) or []
        suggestions = result.get("suggestions", []) or []
        notes = list(result.get("notes", []) or [])
        notes += list(result.get("narrowing_questions", []) or [])

        # (1) Authoritative identity -> EXACT. The matched record IS the product.
        #
        #     ``outcome`` alone does not establish that. On the engine's two
        #     *reference* paths — "same as X but 0.4 corner radius", and an
        #     exact hit in a namespace this business does not sell — the
        #     identity is a legitimate AUTO_MATCH over a legitimate
        #     AUTHORITATIVE match and is still not the answer: the caller named
        #     the product in order to move away from it. Reading those as EXACT
        #     put the *unvaried* product on the line, auto-selected and priced,
        #     for a request that asked to change it.
        #
        #     ``identity_role`` is the engine's own statement of which it is.
        #     The two fallbacks below cover an engine that predates the field —
        #     absence must not read as "answer", which is the benign default
        #     this codebase refuses everywhere else.
        auth = [m for m in matches if m.get("certainty") == "AUTHORITATIVE"]
        role = result.get("identity_role")
        is_reference = (role == "REFERENCE"
                        or (role is None and (semantics == "MIXED"
                                              or "effective_requirement" in result)))
        if auth and not is_reference and outcome in ("AUTO_MATCH", "CONFIRMED"):
            m = auth[0]
            code = str(m.get("record_id"))
            desc = m.get("description") or code
            # The decode, on the one path that was losing it. A match carries
            # the whole catalogue row internally, but pie-parser's
            # ``IdentityMatch.to_dict`` projects it down to four fields, so the
            # geometry never crossed the boundary — while the *lower*-confidence
            # suggestion path below has always carried it. That asymmetry was
            # backwards: this branch is the 0.97-confidence one.
            cands = [Candidate(code=code, desc=desc, rel="EXACT",
                               grade=m.get("grade"), brand=m.get("brand"),
                               reason="Exact manufacturer identity.",
                               attributes=_attributes_of(self._record(view, code)))]
            cands += self._candidates_from_suggestions(suggestions, bands, exclude=code)
            return Resolution(text, code, desc, "EXACT", code, cands,
                              outcome, semantics, notes)

        # (1r) The identity resolved exactly and is a *reference*. Keep it
        #      visible — a person may well decide to offer it and ask — but
        #      never as the auto-selected supply, and never labelled EXACT,
        #      which would assert it meets a requirement the caller defined by
        #      changing it. It is appended after the derived candidates in (3)
        #      rather than given its own return, so the ranking, the
        #      discrimination guard and the abstention all stay in one place.
        ref_cand: Optional[Candidate] = None
        if auth and is_reference:
            m = auth[0]
            code = str(m.get("record_id"))
            ref_cand = Candidate(
                code=code, desc=m.get("description") or code, rel="POSSIBLE",
                grade=m.get("grade"), brand=m.get("brand"),
                reason=("The reference you named, not a match for the change you "
                        "asked for. Offer it only as a deliberate substitution."),
                attributes=_attributes_of(self._record(view, code)))

        # (1b) A candidate identity the engine will not assert: the quote names a
        #      customer, the catalogue holds this exact code, but nobody has
        #      confirmed that *this customer's* code means that product. The
        #      record is real and worth showing; auto-selecting and pricing it
        #      would assert the very thing the engine declined to.
        #
        #      Without this branch the match is simply dropped — `matches` is
        #      read nowhere else — and the line would show "no PIE match" while
        #      the engine had in fact found the record and said "confirm this".
        #
        #      Only for an input that *is* a code. A confirmation files "this
        #      customer's code means this product" forever, so the thing being
        #      confirmed has to be a code — and for a scoped customer the
        #      reference in "same as X but 0.4 corner radius" is demoted to
        #      CANDIDATE/NEEDS_REVIEW and arrived here, offering to record that
        #      the whole variation sentence means the unvaried product X. It
        #      could never have fired at resolution time (the mapping is keyed
        #      on the sentence, and lookups are keyed on identifier tokens), so
        #      it was a permanent, audited, wrong assertion rather than a wrong
        #      answer. ``IDENTITY`` is the engine already deciding "this text is
        #      an identifier", which is exactly the precondition.
        cands_m = [m for m in matches if m.get("certainty") == "CANDIDATE"]
        if cands_m and outcome == "NEEDS_REVIEW" and semantics == "IDENTITY":
            notes += [str(e) for e in (res.get("explanation") or [])]
            return Resolution(
                text, text, "Confirm this is the right product", "AMBIGUOUS", None,
                [Candidate(code=str(m.get("record_id")),
                           desc=m.get("description") or str(m.get("record_id")),
                           rel="POSSIBLE", grade=m.get("grade"), brand=m.get("brand"),
                           reason=m.get("note") or "Candidate identity — needs review.",
                           attributes=_attributes_of(
                               self._record(view, str(m.get("record_id")))))
                 for m in cands_m],
                outcome, semantics, notes,
                # THE ONLY PLACE A CONFIRMABLE PROPOSAL IS CREATED. These records
                # come out of `matches`, so each is an exact catalogue hit the
                # engine declined to assert across namespaces — the one thing a
                # person may answer "yes, that is what my code means" to. One
                # only: two candidates is an ambiguity, and there is no single
                # answer to confirm. Every other branch leaves this None by
                # omission, which is why the default matters as much as this line.
                identity_candidate=(str(cands_m[0].get("record_id"))
                                    if len(cands_m) == 1 else None))

        # (2) Ambiguous / conflicting identity -> AMBIGUOUS (abstain, show options).
        if outcome in ("AMBIGUOUS", "CONFLICT"):
            cands = self._candidates_from_suggestions(suggestions, bands,
                                                      force_rel="POSSIBLE")
            desc = "Ambiguous — confirm the intended product"
            return Resolution(text, text, desc, "AMBIGUOUS", None, cands,
                              outcome, semantics, notes)

        # (3) Requirement with ranked suggestions -> best suggestion becomes the
        #     supply; its relationship comes from the equivalence score band.
        #
        #     Only when the ranking actually discriminates. If the engine did not
        #     resolve the input, or every candidate carries the same score, the
        #     "top" suggestion is an artefact of ordering, not a technical
        #     equivalent — auto-selecting and pricing it would put a fabricated
        #     match on a customer quote. Abstain and show the options instead.
        cands = self._candidates_from_suggestions(
            suggestions, bands,
            # The reference must not appear twice, and the copy that has to go
            # is the *ranked* one. It is normally top of the list: the effective
            # requirement is derived from the reference's own facts, so the
            # reference matches it better than anything else does — which is how
            # "same as X but 3 flute" came back TECH on X, through the very
            # branch added to stop it. Excluding it here, rather than skipping
            # the append when it is already present, is the difference between
            # demoting the reference and re-promoting it.
            exclude=ref_cand.code if ref_cand is not None else None)
        # Re-added last and carrying no score, so it cannot become ``top`` and
        # cannot affect whether the ranking discriminates. Offerable, never
        # auto-selected.
        if ref_cand is not None:
            cands = cands + [ref_cand]
        # Nearest-by-description records, appended after everything the engine
        # ranked. Computed here, before the decision below is taken on
        # ``cands`` alone, so a retrieved record can never be ``top`` and never
        # changes whether the ranking discriminated. They are extra options for
        # the person, compared by the engine but never chosen by it.
        retrieved, retrieval_info = self._retrieved(
            view, text, result, exclude={c.code for c in cands},
            customer_scope=customer_scope, mapping_store=mapping_store)
        reading = result.get("_ranking_reading")
        if reading:
            notes.append(
                f"Read {reading['token']!r} as {reading['word']} — "
                f"{'this customer' if reading['scope'] == 'customer' else 'this tenant'}"
                f"'s usage in {reading['agreeing']} of {reading['support']} quotes. "
                f"The ranking below was made with that reading; confirm it fits.")
            if retrieval_info is not None:
                retrieval_info["ranking_reading"] = reading
        if (cands and self._is_discriminating(cands) and outcome != "UNRESOLVED"
                and not cands[0].unverified
                # Appending the reference last is not enough on its own: when
                # the derived requirement matches nothing, the reference is the
                # *only* candidate, and `_is_discriminating` returns True for a
                # single one. Position was doing the work; this states the rule.
                and cands[0] is not ref_cand):
            top = cands[0]
            desc = self._requirement_desc(text, top)
            return Resolution(text, text, desc, top.rel, top.code,
                              cands + retrieved, outcome, semantics, notes,
                              retrieval=retrieval_info)
        if cands:
            # Two different reasons to abstain, and they must not be reported as
            # one: a tie means the ranking could not choose, while a vacuous
            # leader means the ranking chose on a comparison with no dimension
            # in it. ``_is_discriminating`` cannot see the second — those scores
            # genuinely do separate — so it gets its own sentence, and the
            # reader is told which of the two happened.
            notes.append(
                "No dimension of the request could be compared against these "
                "candidates, so their scores say nothing about fit — pick the "
                "intended product before quoting."
                if cands[0].unverified else
                "The engine could not distinguish between these candidates for "
                "this input — pick the intended product before quoting.")
            return Resolution(
                text, text, "Not resolved — choose the intended product",
                "AMBIGUOUS", None,
                [Candidate(code=c.code, desc=c.desc, rel="POSSIBLE", grade=c.grade,
                           brand=c.brand, score=c.score, reason=c.reason,
                           attributes=c.attributes, unverified=c.unverified,
                           rank_tier=c.rank_tier)
                 for c in cands] + retrieved,
                outcome, semantics, notes, retrieval=retrieval_info)

        # (3r) The engine ranked nothing, and retrieval found records whose
        #      descriptions read like this text and that the engine's gates
        #      did not reject. Shown as options to choose from, under the same
        #      abstention as a tie: nothing here was matched, so nothing here
        #      is selected. The note says which of the two happened — a person
        #      told "not resolved" beside four plausible products would
        #      otherwise read them as the engine's shortlist.
        if retrieved:
            notes.append(
                "The engine could not rank any catalogue record for this "
                "request. The options shown are the catalogue descriptions "
                "nearest to this text, compared by the engine but not matched "
                "by it — pick the intended product before quoting.")
            return Resolution(
                text, text, "Not resolved — choose the intended product",
                "AMBIGUOUS", None, retrieved, outcome, semantics, notes,
                retrieval=retrieval_info)

        # (4) Nothing resolved -> UNRESOLVED (no PIE match).
        return Resolution(text, text, "No PIE match", "UNRESOLVED", None, [],
                          outcome, semantics, notes, retrieval=retrieval_info)

    # ── retrieval: nearest descriptions as extra options ─────────────────────
    @staticmethod
    def _load_retriever(view: "_View") -> Any:
        """This company's nearest-neighbour index, or ``False`` if there is none.

        Built here when the catalogue predates the index or a build could not
        write one, because the index is derived from the catalogue and nothing
        else — the same reason a missing catalogue is a state and a missing
        index is not. ``False`` rather than raising: retrieval is beneath the
        engine's answer, and a line must resolve exactly as before without it.
        """
        if settings.RETRIEVAL_TOP_K <= 0:
            return False
        try:
            index = retrieval.ensure_index(view.path)
            if view.reranker is None:
                embedder = retrieval.embedder_from_settings(settings.EMBEDDER_MODEL_DIR)
                view.reranker = (retrieval.DenseReranker(view.path, embedder)
                                 if embedder is not None else False)
            return index
        except Exception:  # noqa: BLE001 — retrieval must not take the line down
            log.warning("retrieval index for %s unavailable; resolving without "
                        "nearest-neighbour candidates", view.path, exc_info=True)
            return False

    def _with_ranking_reading(self, text: str, result: Dict[str, Any], run: Any,
                              view: "_View", customer_scope: Optional[str],
                              mapping_store: Any) -> Dict[str, Any]:
        """Give the engine's ranking one learned word, when it decoded no family.

        The vocabulary's readings widen the *search* beneath the ranking; this
        is the one place a reading reaches the ranking itself. Only when the
        engine decoded no ``product_family`` from the text — a line it could
        not place at all — and only a family reading whose family has a word
        the engine's own fuzzy decoder reads (``FAMILY_WORDS``): the text is
        resolved again with that word appended, so the engine applies its own
        family gate and ranks within the family, and the second result is
        used only if it did decode the family. The original text stays the
        line's; the reading is written into the result so ``_map`` reports it
        beside the ranking it changed. Nothing is invented: the engine still
        decodes every dimension itself and still scores every record.
        """
        try:
            spec = ((result.get("understood_spec") or {}).get("engine_spec")) or {}
            if spec.get("product_family") or not text:
                return result
            vocabulary = self._vocabulary(mapping_store, view)
            if vocabulary is None:
                return result
            reading = retrieval.Vocabulary.ranking_reading(
                vocabulary.hints(text, customer_scope))
            if reading is None:
                return result
            hint, word = reading
            second = run(f"{text} {word}")
            spec2 = ((second.get("understood_spec") or {}).get("engine_spec")) or {}
            if spec2.get("product_family") != hint.value:
                return result
            second = dict(second)
            second["_ranking_reading"] = {**hint.to_dict(), "word": word}
            return second
        except Exception:  # noqa: BLE001 — a reading must never take the line down
            log.exception("could not apply a learned reading to the ranking for %r", text)
            return result

    def _vocabulary(self, mapping_store: Any, view: "_View") -> Any:
        """What this tenant's words mean against this company's catalogue,
        or None without a store to learn from.

        Built from the store's phrase aliases — a customer's words beside the
        record a person chose — with each record's decoded attributes read
        off this company's catalogue. Memoised by the store fingerprint and
        the catalogue path, for the reason ``_alias_index`` gives; a store
        that cannot be fingerprinted is learned from afresh each time.
        """
        rows = getattr(mapping_store, "aliases", None)
        if not callable(rows):
            return None
        fingerprint = self._input_fingerprint(mapping_store, "mapping store")
        key = (fingerprint, str(view.path)) if fingerprint is not None else None
        if key is not None and key in self._vocabularies:
            self._vocabularies.move_to_end(key)
            return self._vocabularies[key]
        pairs = []
        for scope, text, record_id, *rest in rows():
            if (rest[0] if rest else "code") != "phrase":
                continue                     # a code carries no words
            rec = self._record(view, record_id)
            if rec is not None:
                pairs.append((scope, text, rec))
        vocabulary = retrieval.Vocabulary(pairs)
        if key is not None:
            self._vocabularies[key] = vocabulary
            while len(self._vocabularies) > 8:
                self._vocabularies.popitem(last=False)
        return vocabulary

    @staticmethod
    def _compare_geometry(spec: Dict[str, Any],
                          record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The engine's own comparison of one record against the decoded
        request, as the dict ``GeometryComparison.to_dict`` emits — the same
        function, the same tolerance model and the same gates the ranking
        uses, so a retrieved record is judged by exactly what a ranked one is.
        ``None`` when the engine cannot be reached, which the caller treats as
        "not compared" rather than "compared and fine"."""
        try:
            from equivalence.distance import compare_geometry  # noqa: PLC0415
        except ImportError:
            return None
        return compare_geometry(spec, record).to_dict()

    def _alias_index(self, mapping_store: Any) -> Any:
        """The alias index over this store's confirmed codes, or None.

        Memoised by the store's fingerprint. A store that cannot be
        fingerprinted — a test double, a future store of another shape — is
        indexed afresh each time rather than not at all: the index is cheap,
        and refusing it would silently drop a feature for exactly the callers
        least able to notice.
        """
        rows = getattr(mapping_store, "aliases", None)
        if not callable(rows):
            return None
        key = self._input_fingerprint(mapping_store, "mapping store")
        if key is not None and key in self._alias_indexes:
            self._alias_indexes.move_to_end(key)
            return self._alias_indexes[key]
        index = retrieval.AliasIndex(rows())
        if key is not None:
            self._alias_indexes[key] = index
            while len(self._alias_indexes) > 8:
                self._alias_indexes.popitem(last=False)
        return index

    def _retrieved(self, view: Optional["_View"], text: str,
                   result: Dict[str, Any], exclude: set, *,
                   customer_scope: Optional[str] = None,
                   mapping_store: Any = None,
                   ) -> tuple[List[Candidate], Optional[Dict[str, Any]]]:
        """Nearest-by-description records the engine's gates accept, as
        POSSIBLE candidates, plus the provenance of the search.

        Every record is put through :meth:`_compare_geometry` against the spec
        the engine decoded from this text (``understood_spec.engine_spec``): a
        gated-out record — a milling insert for a turning request, a reamer
        for an end mill — is dropped, and one the engine could not compare on
        any dimension is marked unverified, exactly as a ranked suggestion
        would be. What the comparison never does is promote: the relationship
        is POSSIBLE whatever it scored, because similarity of *text* is not
        evidence of fit and the engine did not rank this record above the
        ones it did not offer.

        Empty, with no provenance, when there is no index or the search fails:
        retrieval sits beneath the engine's answer and must never change it.
        """
        retriever = getattr(view, "retriever", None) if view is not None else None
        if not retriever or not text:
            return [], None
        try:
            # This customer's confirmed codes first: a near miss of a code a
            # person already confirmed is stronger evidence than a description
            # that reads alike, so it is listed first and a record found both
            # ways is reported as the confirmation. Nothing for a line with no
            # customer — another customer's code is another customer's part.
            aliases = self._alias_index(mapping_store) if customer_scope else None
            alias_hits = (aliases.search(customer_scope, text,
                                         k=settings.RETRIEVAL_TOP_K, exclude=exclude)
                          if aliases is not None else [])
            # What this tenant's — or this customer's — words usually mean,
            # as counts over past choices. Widens the description search with
            # the attribute's own tokens, so "BOHRER 12MM" reaches the drills
            # once five quotes have said BOHRER means a drill here. Evidence
            # about words: it never enters the engine's spec and never scores.
            vocabulary = self._vocabulary(mapping_store, view)
            hints = vocabulary.hints(text, customer_scope) if vocabulary else []
            expanded = " ".join([text, *retrieval.Vocabulary.expansion(hints)])
            hits = retriever.search(
                expanded, k=settings.RETRIEVAL_TOP_K,
                exclude=set(exclude) | {h.record_id for h in alias_hits})
            # By meaning, when a dense model is configured: the same
            # candidates, re-ordered by what the text means rather than how
            # it is spelt. Provenance beside each; the score stays the engine's.
            reranker = getattr(view, "reranker", None)
            if reranker and hits:
                hits = reranker.rerank(text, hits, lambda rid: self._record(view, rid))
            spec = ((result.get("understood_spec") or {}).get("engine_spec")) or {}
            out: List[Candidate] = []
            for hit in [*alias_hits, *hits]:
                alias = getattr(hit, "alias", None)
                alias_kind = getattr(hit, "kind", None) if alias is not None else None
                rec = self._record(view, hit.record_id)
                if rec is None:
                    continue
                geo = self._compare_geometry(spec, rec)
                gate_reason: Optional[str] = None
                if geo is not None and geo.get("gated_out"):
                    # A description neighbour the engine gates out is gone: the
                    # text decoded to a family or shape and the record is not
                    # it. A *confirmed code* is different evidence. The spec
                    # here was decoded from a line that is mostly the code —
                    # "PITTI 7781 x 10" read as an ISO P-shape — and a person
                    # already answered what that code means. Kept, unverified,
                    # with the engine's objection stated beside the
                    # confirmation, so the reader sees both and decides.
                    if alias is None:
                        continue
                    gate_reason = str(geo.get("gate_reason") or "geometry differs")
                # Read through the same predicate a ranked suggestion is read
                # through; the comparison dict names its matches differently
                # from a suggestion, and this is the one place that knows it.
                unverified = geo is None or gate_reason is not None or self._unverified({
                    "dimensionally_vacuous": geo.get("dimensionally_vacuous"),
                    "dimensions_compared": geo.get("dimensions_compared"),
                    "field_breakdown": geo.get("field_matches"),
                })
                if alias is None:
                    dense = getattr(hit, "dense_similarity", None)
                    reason = (f"Nearest catalogue description to this text "
                              f"({hit.similarity:.2f} similar"
                              + (f", {dense:.2f} by meaning" if dense is not None else "")
                              + "), not a ranked match. ")
                elif alias_kind == "phrase":
                    reason = (f"This customer was quoted this product before for "
                              f"{alias!r} ({hit.similarity:.2f} similar to this "
                              f"line). A past choice, not a match: they may mean "
                              f"the same thing, or not. ")
                else:
                    reason = (f"Near a code this customer confirmed as this product "
                              f"({alias!r}, {hit.similarity:.2f} similar to this line), "
                              f"not the confirmed code itself. ")
                if gate_reason is not None:
                    reason += (f"The engine read this line's own words as a "
                               f"different geometry ({gate_reason}); "
                               + ("the past choice is worth seeing beside that, "
                                  "but check. " if alias_kind == "phrase" else
                                  "the confirmation is the stronger evidence, "
                                  "but check. "))
                elif unverified:
                    reason += ("No dimension of the request could be compared "
                               "against this record. ")
                elif geo is not None and geo.get("explanation"):
                    reason += f"Engine comparison: {geo['explanation']}. "
                agreed = retrieval.Vocabulary.agreeing(hints, rec)
                if agreed:
                    reason += ("Agrees with what " + ", ".join(
                        f"{h.token!r} usually means here ({h.field}={h.value}, "
                        f"{h.agreeing} of {h.support})" for h in agreed) + ". ")
                out.append(Candidate(
                    code=hit.record_id,
                    desc=rec.get("description_raw") or rec.get("description")
                    or hit.record_id,
                    rel="POSSIBLE", grade=rec.get("grade"), brand=rec.get("brand"),
                    score=None, reason=reason.strip(),
                    attributes=_attributes_of(rec), unverified=unverified,
                    retrieved=True, alias=alias, alias_kind=alias_kind))
            stamp = retriever.stamp
            return out, {"model_id": stamp.model_id, "searched": stamp.records,
                         "offered": len(out),
                         # The learned readings applied to this line, with the
                         # counts behind each — so a reader can see why the
                         # search was widened and argue with the evidence.
                         "vocabulary": [h.to_dict() for h in hints],
                         "vocabulary_pairs": vocabulary.pairs if vocabulary else 0,
                         "dense_model": reranker.model_id if reranker else None,
                         # The confirmed codes searched for this customer, and
                         # how many of the offers came through one. Zero when
                         # the line names no customer or the customer has none
                         # confirmed — searched, and nothing to search.
                         "aliases_searched": (aliases.count(customer_scope)
                                              if aliases is not None else 0),
                         "aliases_offered": sum(1 for c in out if c.alias is not None)}
        except Exception:  # noqa: BLE001 — beneath the answer, never above it
            log.exception("retrieval failed for %r; resolving without it", text)
            return [], None

    def _candidates_from_suggestions(
        self, suggestions: List[Dict[str, Any]], bands: Bands,
        exclude: Optional[str] = None, force_rel: Optional[str] = None,
    ) -> List[Candidate]:
        out: List[Candidate] = []
        for s in suggestions:
            code = str(s.get("record_id"))
            if exclude and code == exclude:
                continue
            scores = s.get("scores", {}) or {}
            combined = scores.get("combined")
            # The engine stamps this on a comparison where no dimension of the
            # request was comparable — the marker exists so a consumer does not
            # have to re-derive it from tied scores, and reading it is the whole
            # of the fix. A vacuous comparison may not be called technically
            # equivalent however high it scored: nothing was compared, so the
            # honest ceiling is POSSIBLE. `_rel_from_score` is left alone — it
            # maps a score to a band and that mapping is still correct; what
            # was wrong was feeding it a score that measured nothing.
            unverified = self._unverified(s)
            rel = force_rel or self._rel_from_score(combined, bands)
            if unverified and rel in ("TECH", "COMPAT"):
                rel = "POSSIBLE"
            reason = s.get("explanation") or ""
            if unverified:
                reason = (("No dimension of the request could be compared "
                           "against this record, so its score is not a measure "
                           "of fit. ") + reason).strip()
            out.append(Candidate(
                code=code,
                desc=s.get("description") or code,
                rel=rel,
                grade=s.get("grade"),
                brand=s.get("brand"),
                score=combined,
                reason=reason,
                attributes=s.get("attributes", {}) or {},
                unverified=unverified,
                rank_tier=s.get("rank_tier"),
            ))
        return out

    @staticmethod
    def _unverified(suggestion: Dict[str, Any]) -> bool:
        """Did the comparison cover everything the request specified?

        Reads the engine's own markers rather than re-deriving the conditions —
        ``dimensionally_vacuous`` for "nothing was comparable", and a
        ``candidate_absent`` dimension in ``field_breakdown`` for "the request
        named a dimension this record does not carry". ``distance.py`` skips
        that second case rather than penalising it, deliberately (missing data
        must not look like a mismatch) — which is right for the *score* and
        wrong for a claim of equivalence built on it.

        Absence of the marker must not read as "a real comparison happened",
        for the same reason absence of ``identity_role`` must not read as
        ANSWER. The engine is loaded from ``PIE_PARSER_ROOT`` rather than
        vendored, so a payload predating a marker is a live possibility, and
        both conditions are derivable from the same payload. Only a payload
        carrying none of the three keys is taken at face value.
        """
        flagged = suggestion.get("dimensionally_vacuous")
        if flagged:
            return True
        breakdown = suggestion.get("field_breakdown")
        if breakdown is not None and any(
                m.get("tier") == "dimension" and m.get("status") == "candidate_absent"
                for m in breakdown):
            return True
        return flagged is None and suggestion.get("dimensions_compared") == 0

    @staticmethod
    def _is_discriminating(cands: List[Candidate]) -> bool:
        """Does the ranking actually separate the top candidate from the rest?

        A set of candidates the ranking did not choose between tells us nothing
        about which product was meant. Treating the first of those as "the
        technical equivalent" manufactures certainty the engine never
        expressed.

        The question is asked of ``rank_tier`` — the engine's own ordering key,
        minus the record-id tail that is sort order rather than evidence —
        because the combined score is only the *last* of that key's components.
        Comparing it alone gets a different answer in both directions: an exact
        designation hit beside a neighbour at the same score reads as a tie,
        and, less obviously, a genuine separation on geometry reads as a tie
        wherever the leader's combined score is *lower* than its neighbour's.
        That second case is not hypothetical — a variation request ranks the
        varied record first at a combined score its own reference exceeds — and
        it made this method abstain on a ranking that had chosen.

        The score is the fallback, not the rule. The engine is loaded from
        ``PIE_PARSER_ROOT`` rather than vendored, so a payload predating
        ``rank_tier`` is a live possibility; where a tier is missing this falls
        back to the older comparison, which errs towards abstention. That is
        the safe direction here, and deliberately not the one the RFQ harness
        takes — a measuring instrument that silently gets more cautious is
        reporting a different system, so ``tools/eval_rfq.py`` raises instead.
        """
        if len(cands) < 2:
            return True                      # nothing to compare against
        top, runner_up = cands[0], cands[1]
        if top.rank_tier is not None and runner_up.rank_tier is not None:
            return top.rank_tier != runner_up.rank_tier
        if top.score is None or runner_up.score is None:
            return True
        return top.score > runner_up.score

    @staticmethod
    def _rel_from_score(combined: Optional[float], bands: Optional[Bands] = None) -> str:
        bands = bands or Bands.default()
        if combined is None:
            return "POSSIBLE"
        if combined >= bands.tech:
            return "TECH"
        if combined >= bands.compat:
            return "COMPAT"
        return "POSSIBLE"

    @staticmethod
    def _requirement_desc(text: str, top: Candidate) -> str:
        # For a fuzzy requirement, the "requested" description is the raw text;
        # the resolved supply carries the catalogue description.
        return text


# Process-wide singleton (the engine loads a pack; reuse it across requests).
pie_service = PieService()
