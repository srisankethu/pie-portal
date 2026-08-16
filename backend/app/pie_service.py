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
import importlib.util
import json
import logging
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import ensure_catalog
from .config import settings

log = logging.getLogger("pie_portal.pie")

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

    Read from the first record rather than recomputed: the checksum belongs to
    the run that built the file, and deriving our own would be a second answer
    to a question the parser has already answered.
    """
    if path is None:
        return ""
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return str(json.loads(line).get("ruleset_checksum") or "")
    except (OSError, ValueError):
        log.warning("could not read a ruleset checksum from %s", path)
    return ""


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
    """
    global _families_memo
    if _families_memo is not _FAMILIES_UNREAD:
        return _families_memo
    try:
        root = str(settings.PIE_PARSER_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import yaml  # noqa: PLC0415 — deferred, like every pie-parser import here
        from engine.pack import families_from_config  # noqa: PLC0415

        manifest = settings.PIE_PACK / "manifest.yaml"
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        _families_memo = families_from_config(doc, source=str(manifest))
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
            try:
                root = str(settings.PIE_PARSER_ROOT)
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
            args = self._make_args(text, customer_scope, mapping_store)
            result, _human = self._mod.run(args, self._sources)
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
