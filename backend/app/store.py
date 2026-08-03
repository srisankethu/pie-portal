"""In-memory quote + line state, with role-gated serialization.

A ``Quote`` is a list of ``Line``s built from a pasted RFQ. Each line carries
the pie-parser resolution plus the Zoho-derived commercial facts, and derives
its own status (the design's READY / NEEDS ATTENTION / NOT IN BOOKS / NO PRICE
/ UNRESOLVED … states). Economics (cost, margin, below-floor) are computed here
but only serialized for a management principal — the sales client never receives
them.

State lives in process memory (single-node demo). A real deployment persists
quotes; the shape here is the persistence contract.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import pricing
from .pie_service import Candidate, Resolution, pie_service
from .zoho import ZohoService

_REL_LABELS = {
    "EXACT": "EXACT", "TECH": "TECH EQUIV", "COMPAT": "COMPATIBLE",
    "POSSIBLE": "POSSIBLE", "AMBIGUOUS": "AMBIGUOUS", "INCOMPATIBLE": "INCOMPATIBLE",
    "UNRESOLVED": "UNRESOLVED", "INSUFF": "INSUFF. INFO", "PIE_DOWN": "PIE OFFLINE",
    "NONE": "—",
}

_ids = itertools.count(1)


def _split_rfq(text: str) -> List[Dict[str, Any]]:
    """Split pasted RFQ text into (raw, code, qty) rows.

    Accepts ``code, qty`` / ``code  qty`` / ``code xNN`` / bare ``code``.
    Qty parsing mirrors the design's intake heuristic.
    """
    rows: List[Dict[str, Any]] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        code, qty = raw, 1
        # trailing quantity: "<code><sep><digits>"
        import re
        m = re.match(r"^(.*?)[\s,\t]+x?\s*(\d+)\s*$", raw, re.IGNORECASE)
        if m and any(ch.isalnum() for ch in m.group(1)):
            code = m.group(1).strip().rstrip(",").strip()
            code = re.sub(r"\s*x$", "", code, flags=re.IGNORECASE).strip()
            qty = int(m.group(2))
        else:
            code = raw.rstrip(",").strip()
        code = re.sub(r"\s{2,}", " ", code)
        rows.append({"raw": raw, "code": code, "qty": max(1, qty)})
    return rows


@dataclass
class Line:
    id: str
    raw: str
    reqCode: str
    reqDesc: str
    reqQty: int
    rel: str
    supplyCode: Optional[str]
    candidates: List[Candidate]
    outcome: str
    semantics: str
    notes: List[str] = field(default_factory=list)
    # supply selection
    sel: str = "AUTO"                 # AUTO | USER | MANUAL
    # zoho-derived
    supplyDesc: str = ""
    inBooks: Optional[bool] = None
    avail: Optional[int] = None
    listPrice: Optional[float] = None
    cost: Optional[float] = None
    family: Optional[str] = None
    # commercial
    quoted: Optional[float] = None
    createPhase: Optional[str] = None  # None | progress | failed
    service: Optional[str] = None      # None | BOOKS | AVAIL | PIE
    incompatReason: Optional[str] = None

    # ── derivation ───────────────────────────────────────────────────────────
    def substituted(self) -> bool:
        return bool(self.supplyCode) and self.supplyCode != self.reqCode

    def shortage(self) -> Optional[int]:
        if self.avail is None or not self.supplyCode:
            return None
        return max(0, self.reqQty - self.avail)

    def economics(self) -> pricing.Economics:
        return pricing.compute_economics(self.cost, self.listPrice, self.quoted, self.family)

    def status(self) -> Dict[str, str]:
        """(kind, label) — matches the design's status taxonomy."""
        rel = self.rel
        if self.service == "PIE" or rel == "PIE_DOWN":
            return {"kind": "technical", "label": "PIE OFFLINE"}
        if rel == "UNRESOLVED":
            return {"kind": "technical", "label": "UNRESOLVED"}
        if rel == "AMBIGUOUS":
            return {"kind": "technical", "label": "AMBIGUOUS"}
        if rel == "INCOMPATIBLE":
            return {"kind": "technical", "label": "INCOMPATIBLE"}
        if self.createPhase == "failed":
            return {"kind": "operational", "label": "CREATE FAILED"}
        if self.createPhase == "progress":
            return {"kind": "operational", "label": "CREATING…"}
        if self.service == "BOOKS":
            return {"kind": "operational", "label": "BOOKS OFFLINE"}
        if self.inBooks is False:
            return {"kind": "operational", "label": "NOT IN BOOKS"}
        if self.quoted is None:
            return {"kind": "commercial", "label": "NO PRICE"}
        return {"kind": "ready", "label": "READY · SUBST" if self.substituted() else "READY"}

    def flags(self) -> Dict[str, bool]:
        st = self.status()
        kind = st["kind"]
        sh = self.shortage()
        return {
            "attention": kind in ("technical", "operational", "commercial"),
            "procurement": sh is not None and sh > 0,
            "missingBooks": (self.inBooks is False or self.service == "BOOKS"
                             or self.createPhase in ("failed", "progress")),
            "manualReview": self.sel == "MANUAL" or self.rel in ("INCOMPATIBLE", "AMBIGUOUS"),
            "unresolved": self.rel in ("UNRESOLVED", "AMBIGUOUS", "INSUFF") or self.service == "PIE",
            "substituted": self.substituted(),
        }

    # ── serialization (role-gated) ───────────────────────────────────────────
    def to_dict(self, mgmt: bool) -> Dict[str, Any]:
        st = self.status()
        econ = self.economics()
        sh = self.shortage()
        base: Dict[str, Any] = {
            "id": self.id, "raw": self.raw,
            "reqCode": self.reqCode, "reqDesc": self.reqDesc, "reqQty": self.reqQty,
            "rel": self.rel, "relLabel": _REL_LABELS.get(self.rel, self.rel),
            "supplyCode": self.supplyCode, "supplyDesc": self.supplyDesc,
            "sel": self.sel,
            "avail": self.avail, "availUnknown": self.avail is None and bool(self.supplyCode),
            "inBooks": self.inBooks,
            "shortage": sh,
            "quoted": self.quoted,
            "recommended": econ.recommended,   # decision support, safe for both roles
            "lineTotal": (self.quoted * self.reqQty) if self.quoted is not None else None,
            "createPhase": self.createPhase,
            "service": self.service,
            "incompatReason": self.incompatReason,
            "status": st,
            "flags": self.flags(),
            "candidates": [c.to_dict() for c in self.candidates],
            "notes": self.notes,
            "substituted": self.substituted(),
        }
        if mgmt:
            base["economics"] = econ.to_dict()
        return base


@dataclass
class Quote:
    id: str
    customer: str
    number: str
    lines: List[Line] = field(default_factory=list)
    savedAt: Optional[str] = None

    def to_dict(self, mgmt: bool) -> Dict[str, Any]:
        line_dicts = [ln.to_dict(mgmt) for ln in self.lines]
        subtotal = sum(
            (ln.quoted * ln.reqQty) for ln in self.lines if ln.quoted is not None
        )
        gst = subtotal * 0.18
        counts = self._filter_counts()
        floor = self._margin_floor() if mgmt else None
        return {
            "id": self.id, "customer": self.customer, "number": self.number,
            "savedAt": self.savedAt,
            "lines": line_dicts,
            "summary": {
                "subtotal": round(subtotal, 2),
                "gst": round(gst, 2),
                "grand": round(subtotal + gst, 2),
                "total": len(self.lines),
            },
            "filterCounts": counts,
            "marginFloor": floor,
        }

    def _filter_counts(self) -> Dict[str, int]:
        fl = [ln.flags() for ln in self.lines]
        return {
            "ALL": len(self.lines),
            "NEEDS": sum(f["attention"] for f in fl),
            "PROC": sum(f["procurement"] for f in fl),
            "BOOKS": sum(f["missingBooks"] for f in fl),
            "MANUAL": sum(f["manualReview"] for f in fl),
            "UNRES": sum(f["unresolved"] for f in fl),
            "SUBST": sum(f["substituted"] for f in fl),
            "MFLOOR": sum(1 for ln in self.lines if ln.economics().below_floor),
        }

    def _margin_floor(self) -> Optional[Dict[str, Any]]:
        below = [ln for ln in self.lines if ln.economics().below_floor]
        if not below:
            return None
        worst = min(ln.economics().margin for ln in below
                    if ln.economics().margin is not None)
        return {
            "count": len(below),
            "worst": round(worst, 4),
            "floor": pricing.MARGIN_FLOOR,
        }


class QuoteStore:
    """Process-wide quote registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: Dict[str, Quote] = {}

    def get(self, quote_id: str) -> Optional[Quote]:
        return self._quotes.get(quote_id)

    def create(self, customer: str) -> Quote:
        with self._lock:
            qid = f"q{next(_ids)}"
            num = f"QB-{int(time.time()) % 100000:05d}"
            q = Quote(id=qid, customer=customer or "New customer", number=num)
            self._quotes[qid] = q
            return q

    # ── line construction ────────────────────────────────────────────────────
    def build_lines(self, rows: List[Dict[str, Any]], zoho: ZohoService) -> List[Line]:
        lines: List[Line] = []
        for row in rows:
            res: Resolution = pie_service.resolve(row["code"])
            ln = Line(
                id=f"l{next(_ids)}",
                raw=row["raw"],
                reqCode=res.reqCode or row["code"],
                reqDesc=res.reqDesc or row["code"],
                reqQty=row["qty"],
                rel=res.rel,
                supplyCode=res.supplyCode,
                candidates=res.candidates,
                outcome=res.outcome,
                semantics=res.semantics,
                notes=res.notes,
                service="PIE" if res.pie_offline else None,
            )
            self._enrich_from_zoho(ln, zoho)
            lines.append(ln)
        return lines

    def add_rfq(self, quote: Quote, text: str, zoho: ZohoService) -> List[Line]:
        rows = _split_rfq(text)
        new = self.build_lines(rows, zoho)
        with self._lock:
            quote.lines.extend(new)
        return new

    def _enrich_from_zoho(self, ln: Line, zoho: ZohoService) -> None:
        """Attach commercial facts + auto-price a resolved, in-books line."""
        if not ln.supplyCode:
            return
        if not zoho.available:
            ln.service = "BOOKS"
            return
        item = zoho.get_item(ln.supplyCode)
        if item is None:
            ln.inBooks = False
            return
        ln.supplyDesc = item.name if item.name != ln.supplyCode else ln.reqDesc
        ln.inBooks = item.in_books
        ln.avail = item.stock
        ln.listPrice = item.list_price
        ln.cost = item.cost
        ln.family = self._family_of(ln)
        if item.in_books and item.list_price is not None:
            ln.quoted = item.list_price      # auto-quote at list; user may edit

    @staticmethod
    def _family_of(ln: Line) -> Optional[str]:
        for c in ln.candidates:
            fam = (c.attributes or {}).get("product_family")
            if fam:
                return fam
        return None

    # ── mutations ────────────────────────────────────────────────────────────
    def select_supply(self, ln: Line, code: str, zoho: ZohoService, manual: bool = False) -> None:
        cand = next((c for c in ln.candidates if c.code == code), None)
        was_exact = code == ln.reqCode
        ln.supplyCode = code
        if was_exact:
            ln.rel, ln.sel = "EXACT", "AUTO"
        elif manual:
            ln.rel = cand.rel if cand else "COMPAT"
            ln.sel = "MANUAL"
        else:
            ln.rel = cand.rel if cand else "COMPAT"
            ln.sel = "USER"
        if cand:
            ln.reqDesc = ln.reqDesc  # requested stays; supplyDesc updated below
        ln.service = None
        ln.incompatReason = None
        self._enrich_from_zoho(ln, zoho)
        if cand:
            ln.supplyDesc = cand.desc

    def set_price(self, ln: Line, price: Optional[float]) -> None:
        ln.quoted = price

    def delete_line(self, quote: Quote, line_id: str) -> Line:
        with self._lock:
            line = next((ln for ln in quote.lines if ln.id == line_id), None)
            if line is None:
                raise KeyError(line_id)
            quote.lines.remove(line)
            return line

    def apply_discount(self, lines: List[Line], pct: float) -> int:
        n = 0
        for ln in lines:
            if ln.listPrice is not None:
                ln.quoted = round(ln.listPrice * (1 - pct / 100.0))
                n += 1
        return n

    def create_item(self, ln: Line, zoho: ZohoService) -> None:
        if not ln.supplyCode:
            return
        ln.createPhase = "progress"
        item = zoho.create_item(ln.supplyCode, ln.supplyDesc or ln.reqDesc, ln.listPrice)
        ln.inBooks = True
        ln.createPhase = None
        ln.cost = item.cost
        if ln.quoted is None and item.list_price is not None:
            ln.quoted = item.list_price

    def blockers(self, quote: Quote) -> List[Line]:
        """Technical-status lines that must be resolved before an estimate."""
        return [ln for ln in quote.lines if ln.status()["kind"] == "technical"]


store = QuoteStore()
