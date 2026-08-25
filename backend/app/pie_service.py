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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cache as cache_module
from .catalog import catalog_stamp, ensure_catalog
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
#:   them it had not been recorded.
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "desc": self.desc, "rel": self.rel,
            "grade": self.grade, "brand": self.brand, "score": self.score,
            "reason": self.reason, "attributes": self.attributes,
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_text": self.input_text,
            "reqCode": self.reqCode, "reqDesc": self.reqDesc,
            "rel": self.rel, "supplyCode": self.supplyCode,
            "candidates": [c.to_dict() for c in self.candidates],
            "outcome": self.outcome, "semantics": self.semantics,
            "notes": self.notes, "pie_offline": self.pie_offline,
        }


def _read_catalog_version(path: Optional[Path]) -> str:
    """The ruleset checksum stamped on the catalogue's rows.

    Read from the first record rather than recomputed — ``catalog_stamp`` is
    the one reader of that fact; this narrows its answer to the field the
    cache key and provenance stamps need.
    """
    if path is None:
        return ""
    return str(catalog_stamp(Path(path)).get("ruleset_checksum") or "")


#: ``pack_families``' memo. A sentinel rather than ``None`` because ``None``
#: is a real answer ("no pack readable") — and deliberately not a cached one:
#: a pack fetched after boot must be seen on the next call, or every family
#: edit stays refused until a restart for a failure that has been fixed.
_FAMILIES_UNREAD = object()
_families_memo: Any = _FAMILIES_UNREAD


def pack_families() -> Optional[tuple]:
    """The family vocabulary the configured pack declares, or None without a pack.

    Read from the manifest alone: resolving a code needs the whole engine, but
    the vocabulary is one YAML list, and a policy save must not pay for grammar
    compilation to check five key names. Parsed by pie-parser's own
    ``families_from_config`` rather than a local re-reading of the YAML, so
    "what counts as a declared family" keeps exactly one definition — the one
    ``load_pack`` itself uses.

    ``None`` means *no pack is readable here* — a checkout without the private
    submodule — which is a different answer from an empty vocabulary. The
    caller must treat it as "there is nothing to validate against", never as
    "every name is fine"; ``commercial.policy.save_for_org`` refuses a family
    edit outright in that state rather than waving it through.

    A successful read is cached for the life of the process, like the
    catalogue: the pack is loaded once and never reloaded, so re-reading the
    manifest could only ever disagree with the engine already running. A
    *failed* read is not cached — the pack may be fetched after boot, and a
    memoized failure would keep refusing family edits until a restart.

    **One pack per deployment, not per organisation, and that is a stated limit
    rather than an oversight.** ``settings.PIE_PACK`` is a deployment-wide
    setting, so a deployment serves one organisation layer. pie-parser's packs
    are layered precisely so a second distributor can have its own
    (``packs/org/<source>/`` over a shared ``packs/nomenclature/``), and the
    obvious next step — an ``Organization.config["pie_pack"]`` resolved per
    request — was deliberately **not** taken here.

    The reason is the catalogue. ``app/catalog.py`` builds one JSONL index by
    running the corpus through *one* pack, and ``PieService._ensure_index``
    loads exactly that file into one process-wide index; every identity lookup
    and every line resolution reads it. Making the *vocabulary* per
    organisation while the index stayed deployment-wide would leave two
    organisations validating family names against different packs and resolving
    products against the same one — a half-measure that is less coherent than
    the single-pack state it replaced, and the kind of thing §1 means by not
    weakening a rule to make output appear.

    Doing it properly is a design question with an answer this function cannot
    supply: whether the index is built per organisation and held per
    organisation (memory, and a build step per tenant), or resolution becomes
    index-per-request (a load on the hot path), or the deployment stays
    single-pack and a second distributor gets a second deployment — which is
    what happens today and is a legitimate answer for three legal entities
    selling the same manufacturer's product.
    """
    global _families_memo
    if _families_memo is not _FAMILIES_UNREAD:
        return _families_memo
    try:
        root = str(settings.PIE_PARSER_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        # Asked of the engine rather than read out of the manifest by hand.
        # This used to yaml.safe_load PIE_PACK/manifest.yaml and pass the raw
        # document to families_from_config, which worked while a pack was one
        # flat directory. Packs are layered now — an organisation layer extends
        # a shared nomenclature layer — and `families` moved to the layer, so
        # reading the org manifest directly found none and the vocabulary went
        # silently empty. Following the `nomenclature:` reference here would
        # mean a second implementation of the engine's layer resolution, which
        # is the drift CLAUDE.md §2 is about: load_pack already owns it.
        from engine.pack import load_pack  # noqa: PLC0415

        _families_memo = list(load_pack(settings.PIE_PACK).families)
        return _families_memo
    except Exception:  # noqa: BLE001 — an absent pack must not 500 a policy save
        log.warning("PIE pack manifest unreadable; no family vocabulary to "
                    "validate against", exc_info=True)
        return None


class PieService:
    """Loads pie-parser once and resolves RFQ lines through it."""

    def __init__(self) -> None:
        self._mod = None
        self._sources = None
        self._lock = threading.Lock()
        self._catalog_path: Optional[Path] = None
        self._catalog_version: str = ""
        self._index = None
        self._index_lock = threading.Lock()
        self._index_tried = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._mod is not None:
            return
        with self._lock:
            if self._mod is not None:
                return
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
            self._catalog_path = ensure_catalog()
            self._catalog_version = _read_catalog_version(self._catalog_path)
            args = self._make_args("")
            self._sources = mod._build_sources(args)
            self._mod = mod
            log.info("pie-parser loaded; catalogue=%s ruleset=%s",
                     self._catalog_path, self._catalog_version or "unknown")

    def warm(self) -> None:
        """Eagerly load the engine + catalogue (called on app startup)."""
        self._ensure_loaded()

    def reload(self) -> None:
        """Forget everything derived from the catalogue file, so the next use
        re-reads it from disk. Called after a rebuild.

        Everything cleared here is downstream of that one file: the resolver's
        sources load it, the authoritative index is built from it, and the
        version is read off its first record. The remembered *failure* is
        cleared too — that memo exists so a missing catalogue is not re-tried
        per row, and a rebuild is precisely the event that makes retrying
        right again. The resolution cache is left alone: its keys carry the
        catalogue version, so entries from a superseded build are unreachable
        and entries from an identical rebuild stay valid.

        A request mid-resolution when this runs may find ``_mod`` gone and
        degrade to PIE_DOWN for that one line — the same isolation any engine
        failure gets, and an accepted cost of a rare administrative action.
        """
        global _families_memo
        with self._lock, self._index_lock:
            self._mod = None
            self._sources = None
            self._catalog_path = None
            self._catalog_version = ""
            self._index = None
            self._index_tried = False
        _families_memo = _FAMILIES_UNREAD

    def loaded_state(self) -> Dict[str, Any]:
        """What this process is answering resolutions with right now — without
        loading anything to find out.

        Deliberately not :attr:`catalog_available`: that property loads the
        index (and, with AUTO_BUILD_CATALOG on, builds the catalogue) to
        answer, which would make a status read a mutation. A status surface
        reports; ``index_loaded: False`` beside an existing file means only
        "not asked yet", and the screen says so rather than reading it as
        down.
        """
        return {
            "index_loaded": self._index is not None,
            "ruleset_checksum": self._catalog_version or None,
        }

    # ── exact catalogue identity ─────────────────────────────────────────────
    def _ensure_index(self):
        """The authoritative index alone, without the RFQ resolver behind it.

        A sync needs to ask one question — "is this SKU a catalogue record?" —
        and loading ``resolve_rfq`` and its equivalence sources to answer it
        would make every item pull pay for machinery it never calls. This is
        deliberately the *narrow* half of :meth:`_ensure_loaded`.

        Failure is remembered, not retried per row: a missing submodule would
        otherwise re-raise and re-log 15,000 times in one sync.
        """
        if self._index is not None or self._index_tried:
            return self._index
        with self._index_lock:
            if self._index is not None or self._index_tried:
                return self._index
            self._index_tried = True
            # Absent and broken are different facts, and only one of them is a
            # defect. A deployment built without the private pie-parser
            # submodule reported `ModuleNotFoundError: No module named
            # 'identity'` with a traceback, which reads as a bug in this file;
            # it is a build that shipped without an optional engine, exactly as
            # `deploy/backend.Dockerfile` says it may. Say which one it is, in
            # the words `_ensure_loaded` already uses.
            root_path = settings.PIE_PARSER_ROOT
            if not (root_path / "identity").is_dir():
                log.warning(
                    "pie-parser is not present at %s, so item links will be left "
                    "unresolved. This deployment was built without the engine "
                    "(see deploy/backend.Dockerfile); nothing else is affected. "
                    "Fetch it with ./scripts/setup_pie_parser.sh, or set "
                    "PIE_PARSER_ROOT.", root_path)
                self._index = None
                return self._index
            try:
                root = str(root_path)
                if root not in sys.path:
                    sys.path.insert(0, root)
                from identity.store import AuthoritativeIndex  # noqa: PLC0415
                path = ensure_catalog()
                self._index = AuthoritativeIndex.from_jsonl(path)
                if not self._catalog_version:
                    self._catalog_version = _read_catalog_version(path)
                log.info("PIE authoritative index loaded from %s", path)
            except Exception:  # noqa: BLE001 — a sync must not fail on this
                log.warning("PIE catalogue unavailable; item links will be left "
                            "unresolved", exc_info=True)
                self._index = None
            return self._index

    @property
    def catalog_available(self) -> bool:
        """Whether a catalogue was actually loaded to answer lookups against.

        Callers need this to tell "the pack does not cover this item" from
        "nobody asked the pack" — both return None from
        :meth:`lookup_record`, and only the first is evidence about the item.
        """
        return self._ensure_index() is not None

    def lookup_record(self, identifier: Optional[str]) -> Optional[Dict[str, Any]]:
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
        index = self._ensure_index()
        if index is None:
            return None
        try:
            rec = index.lookup_material(str(identifier))
        except Exception:  # noqa: BLE001 — provenance must not break a sync
            log.exception("PIE index lookup failed for %r", identifier)
            return None
        # A bare lookup can also return the store's structured ambiguity when
        # one identifier exists in several namespaces — possible only once a
        # second manufacturer pack is indexed. That is not the decoded row this
        # method promises: an ambiguity is short of an exact hit, so per the
        # contract above the caller records nothing.
        return rec if isinstance(rec, dict) else None

    @property
    def catalog_version(self) -> str:
        """The ruleset checksum of the catalogue this process resolves against.

        pie-parser derives it from the input bytes plus the pack's own checksum,
        which is what makes a rerun reproducible — and it is the one fact that
        explains, months later, why the same RFQ text resolved to a different
        product than it does today. It is uniform across a build, and the
        catalogue is loaded once and never reloaded, so reading it from the
        first record is exact rather than a sample.

        Empty when the engine has not loaded. Never raises: provenance must not
        be the thing that fails a quote.
        """
        try:
            self._ensure_loaded()
        except Exception:  # noqa: BLE001 — matches resolve()'s degrade-not-raise
            return ""
        return self._catalog_version

    def _make_args(self, text: str,
                   customer_scope: Optional[str] = None,
                   mapping_store: Any = None) -> argparse.Namespace:
        return argparse.Namespace(
            text=text,
            pie_data=self._catalog_path or settings.PIE_CATALOG,
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
    def _mapping_fingerprint(mapping_store: Any) -> Optional[str]:
        """A value naming the confirmed mappings the engine will read, or None.

        None means "do not cache this resolution". That is the answer for a
        store this code cannot fingerprint — a test double, or a future store
        of a different shape — because caching against an unknown input is how
        a confirmed mapping silently stops taking effect. Refusing to cache
        costs a scan; guessing costs the customer a wrong answer with a
        confident explanation attached.
        """
        if mapping_store is None:
            return "none"                    # the packaged empty store
        fingerprint = getattr(mapping_store, "fingerprint", None)
        if not callable(fingerprint):
            return None
        try:
            return str(fingerprint())
        except Exception:  # noqa: BLE001 — an unfingerprintable store is uncached
            log.exception("could not fingerprint the mapping store; resolving "
                          "this line without the cache")
            return None

    def _cache_key(self, text: str, customer_scope: Optional[str],
                   mapping_store: Any) -> Optional[str]:
        """The key this resolution is stored under, or None if it may not be
        cached at all."""
        if _resolution_cache.maxsize == 0:
            return None
        mappings = self._mapping_fingerprint(mapping_store)
        if mappings is None:
            return None
        return cache_module.fingerprint(
            "pie_resolution", self._catalog_version, text, customer_scope,
            mappings)

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
                mapping_store: Any = None) -> Resolution:
        """Resolve one RFQ line's text into a portal Resolution.

        ``customer_scope`` is the customer's cross-connector identity when the
        quote is for a linked customer. Passing it lets the engine prefer a
        confirmed "this customer's code means MM# X" mapping over re-reading the
        text; without a mapping the engine still consults the catalogue but
        returns the hit as a candidate to confirm rather than an assertion, so
        naming the customer can only ever add caution, never resolution.

        Any failure inside the engine degrades to a PIE_OFFLINE resolution
        rather than raising, so a single bad line never fails the whole quote —
        this is the design's ``PIE OFFLINE`` line state.
        """
        text = (text or "").strip()
        try:
            self._ensure_loaded()
            key = self._cache_key(text, customer_scope, mapping_store)
            result = self._cached_result(key)
            if result is None:
                args = self._make_args(text, customer_scope, mapping_store)
                result, _human = self._mod.run(args, self._sources)
                if key is not None:
                    # A copy, so the object handed to ``_map`` below — and to
                    # every Candidate that keeps a reference into it — cannot
                    # be reached from the cache. A Line built from a cached
                    # resolution that shared its ``attributes`` dict would let
                    # one quote's edit change another's.
                    _resolution_cache.set(key, copy.deepcopy(result))
            return self._map(text, result, bands or Bands.default())
        except Exception:  # noqa: BLE001 — deliberate: isolate engine failures
            log.exception("pie-parser resolution failed for %r", text)
            return Resolution(
                input_text=text, reqCode=text, reqDesc="Awaiting PIE",
                rel="PIE_DOWN", supplyCode=None, candidates=[],
                outcome="ERROR", semantics="UNKNOWN", pie_offline=True,
                notes=["The resolution engine is unavailable for this line."],
            )

    # ── mapping: engine output -> portal Resolution ──────────────────────────
    def _map(self, text: str, result: Dict[str, Any], bands: Bands) -> Resolution:
        res = result.get("resolution", {}) or {}
        outcome = res.get("outcome", "UNRESOLVED")
        semantics = res.get("input_semantics", "REQUIREMENT")
        matches = res.get("matches", []) or []
        suggestions = result.get("suggestions", []) or []
        notes = list(result.get("notes", []) or [])
        notes += list(result.get("narrowing_questions", []) or [])

        # (1) Authoritative identity -> EXACT. The matched record IS the product.
        auth = [m for m in matches if m.get("certainty") == "AUTHORITATIVE"]
        if auth and outcome in ("AUTO_MATCH", "CONFIRMED"):
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
                               attributes=_attributes_of(self.lookup_record(code)))]
            cands += self._candidates_from_suggestions(suggestions, bands, exclude=code)
            return Resolution(text, code, desc, "EXACT", code, cands,
                              outcome, semantics, notes)

        # (1b) A candidate identity the engine will not assert: the quote names a
        #      customer, the catalogue holds this exact code, but nobody has
        #      confirmed that *this customer's* code means that product. The
        #      record is real and worth showing; auto-selecting and pricing it
        #      would assert the very thing the engine declined to.
        #
        #      Without this branch the match is simply dropped — `matches` is
        #      read nowhere else — and the line would show "no PIE match" while
        #      the engine had in fact found the record and said "confirm this".
        cands_m = [m for m in matches if m.get("certainty") == "CANDIDATE"]
        if cands_m and outcome == "NEEDS_REVIEW":
            notes += [str(e) for e in (res.get("explanation") or [])]
            return Resolution(
                text, text, "Confirm this is the right product", "AMBIGUOUS", None,
                [Candidate(code=str(m.get("record_id")),
                           desc=m.get("description") or str(m.get("record_id")),
                           rel="POSSIBLE", grade=m.get("grade"), brand=m.get("brand"),
                           reason=m.get("note") or "Candidate identity — needs review.",
                           attributes=_attributes_of(
                               self.lookup_record(str(m.get("record_id")))))
                 for m in cands_m],
                outcome, semantics, notes)

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
        cands = self._candidates_from_suggestions(suggestions, bands)
        if cands and self._is_discriminating(cands) and outcome != "UNRESOLVED":
            top = cands[0]
            desc = self._requirement_desc(text, top)
            return Resolution(text, text, desc, top.rel, top.code, cands,
                              outcome, semantics, notes)
        if cands:
            notes.append(
                "The engine could not distinguish between these candidates for "
                "this input — pick the intended product before quoting.")
            return Resolution(
                text, text, "Not resolved — choose the intended product",
                "AMBIGUOUS", None,
                [Candidate(code=c.code, desc=c.desc, rel="POSSIBLE", grade=c.grade,
                           brand=c.brand, score=c.score, reason=c.reason,
                           attributes=c.attributes) for c in cands],
                outcome, semantics, notes)

        # (4) Nothing resolved -> UNRESOLVED (no PIE match).
        return Resolution(text, text, "No PIE match", "UNRESOLVED", None, [],
                          outcome, semantics, notes)

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
            rel = force_rel or self._rel_from_score(combined, bands)
            out.append(Candidate(
                code=code,
                desc=s.get("description") or code,
                rel=rel,
                grade=s.get("grade"),
                brand=s.get("brand"),
                score=combined,
                reason=s.get("explanation") or "",
                attributes=s.get("attributes", {}) or {},
            ))
        return out

    @staticmethod
    def _is_discriminating(cands: List[Candidate]) -> bool:
        """Does the ranking actually separate the top candidate from the rest?

        A set of candidates that all share one score (commonly every score at
        1.0, which is what a vacuous match looks like) tells us nothing about
        which product was meant. Treating the first of those as "the technical
        equivalent" manufactures certainty the engine never expressed.
        """
        scores = [c.score for c in cands if c.score is not None]
        if len(scores) < 2:
            return True                      # nothing to compare against
        return scores[0] > scores[1]

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
