"""In-process bridge to the pie-parser Product Intelligence Engine.

This is the heart of the integration. It loads pie-parser (a pinned clone)
once, reusing its identity-first ``resolve_rfq.run()`` orchestration, and maps
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

# Score bands that turn a fuzzy equivalence suggestion into a relationship.
# The engine's combined score is geometry+grade agreement in [0, 1].
_TECH_BAND = 0.85
_COMPAT_BAND = 0.60


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


class PieService:
    """Loads pie-parser once and resolves RFQ lines through it."""

    def __init__(self) -> None:
        self._mod = None
        self._sources = None
        self._lock = threading.Lock()
        self._catalog_path: Optional[Path] = None

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
            args = self._make_args("")
            self._sources = mod._build_sources(args)
            self._mod = mod
            log.info("pie-parser loaded; catalogue=%s", self._catalog_path)

    def warm(self) -> None:
        """Eagerly load the engine + catalogue (called on app startup)."""
        self._ensure_loaded()

    def _make_args(self, text: str,
                   customer_scope: Optional[str] = None) -> argparse.Namespace:
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
        )

    # ── resolution ───────────────────────────────────────────────────────────
    def resolve(self, text: str, customer_scope: Optional[str] = None) -> Resolution:
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
            args = self._make_args(text, customer_scope)
            result, _human = self._mod.run(args, self._sources)
            return self._map(text, result)
        except Exception:  # noqa: BLE001 — deliberate: isolate engine failures
            log.exception("pie-parser resolution failed for %r", text)
            return Resolution(
                input_text=text, reqCode=text, reqDesc="Awaiting PIE",
                rel="PIE_DOWN", supplyCode=None, candidates=[],
                outcome="ERROR", semantics="UNKNOWN", pie_offline=True,
                notes=["The resolution engine is unavailable for this line."],
            )

    # ── mapping: engine output -> portal Resolution ──────────────────────────
    def _map(self, text: str, result: Dict[str, Any]) -> Resolution:
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
            cands = [Candidate(code=code, desc=desc, rel="EXACT",
                               grade=m.get("grade"), brand=m.get("brand"),
                               reason="Exact manufacturer identity.")]
            cands += self._candidates_from_suggestions(suggestions, exclude=code)
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
                           reason=m.get("note") or "Candidate identity — needs review.")
                 for m in cands_m],
                outcome, semantics, notes)

        # (2) Ambiguous / conflicting identity -> AMBIGUOUS (abstain, show options).
        if outcome in ("AMBIGUOUS", "CONFLICT"):
            cands = self._candidates_from_suggestions(suggestions, force_rel="POSSIBLE")
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
        cands = self._candidates_from_suggestions(suggestions)
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
        self, suggestions: List[Dict[str, Any]],
        exclude: Optional[str] = None, force_rel: Optional[str] = None,
    ) -> List[Candidate]:
        out: List[Candidate] = []
        for s in suggestions:
            code = str(s.get("record_id"))
            if exclude and code == exclude:
                continue
            scores = s.get("scores", {}) or {}
            combined = scores.get("combined")
            rel = force_rel or self._rel_from_score(combined)
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
    def _rel_from_score(combined: Optional[float]) -> str:
        if combined is None:
            return "POSSIBLE"
        if combined >= _TECH_BAND:
            return "TECH"
        if combined >= _COMPAT_BAND:
            return "COMPAT"
        return "POSSIBLE"

    @staticmethod
    def _requirement_desc(text: str, top: Candidate) -> str:
        # For a fuzzy requirement, the "requested" description is the raw text;
        # the resolved supply carries the catalogue description.
        return text


# Process-wide singleton (the engine loads a pack; reuse it across requests).
pie_service = PieService()
