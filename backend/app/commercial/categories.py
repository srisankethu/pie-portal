"""Which line of the business an item belongs to, and how that is decided.

The platform's growth question is not only "who is close to us" but "who takes
one line and not the others" — a cutting-tool customer who has never bought a
drum of coolant, a machine buyer with no metrology. That question needs every
item placed in a line, and nothing in the read model placed one: ``Product``
carried a name, a unit, an HSN code and nothing that said *what kind of thing
this is*.

**Four sources, in order, and the order is the whole design.**

``OVERRIDE``  what somebody in the business said, in Settings. Wins outright.
              Stored outside ``Product`` on purpose: products are derived from
              Zoho and rebuilt by a full re-sync, and a mapping a person typed
              must not be destroyed by one.
``ZOHO``      the item's own category, as Zoho holds it. Free, already
              maintained by whoever maintains the catalogue, and wrong only
              where the catalogue is.
``HSN``       the tax code, by inclusive range over the four-digit heading.
              Every item that is invoiced has one, so this has the widest
              reach of the objective sources.
``VENDOR``    the principal who supplies it. An authorised distributor's
              suppliers are mostly single-line — everything from a coolant
              principal is coolant — so where the tariff code is blank, who
              sold it to us is real evidence. Last of the four because it is
              the weakest: a major tooling brand sells inserts, holders and
              gauges, and the HSN heading separates those where the vendor name
              cannot. Inferred from that vendor's own placed catalogue rather
              than typed, so it improves as the catalogue fills in.
``NONE``      nothing resolved it.

**Uncategorised is a category of its own and is never guessed at.** A mix grid
exists to show gaps, and a gap it invented is an opportunity that is not there —
somebody drives to a customer to sell them a line they already buy under a name
this map did not recognise. So an unresolved item is counted, named, and
reported as work to do in Settings, which is the honest version of the same
information.

**The HSN ranges and the vendor-inference floors live in
``CommercialThresholds``**, not here, for the reason ``target_margin_by_family``
does: they are policy that changes every number downstream of them, so they
belong inside the version hash. Re-map a heading, or loosen how readily a
supplier's line is inferred, and last quarter's mix figures stay explainable —
because the version that produced them says what the rules were.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..domain import models
from .config import CommercialThresholds

#: The lines this business sells. Ordered as somebody would read them —
#: consumable-and-frequent first, capital last — because every screen built on
#: this renders them as columns and a column order that changes per screen is a
#: grid nobody can scan twice.
CUTTING_TOOLS = "CUTTING_TOOLS"
COOLANTS = "COOLANTS"
CONSUMABLES = "CONSUMABLES"
METROLOGY = "METROLOGY"
MACHINES = "MACHINES"
UNCATEGORISED = "UNCATEGORISED"

ORDER: tuple[str, ...] = (CUTTING_TOOLS, COOLANTS, CONSUMABLES, METROLOGY,
                          MACHINES)

LABELS: dict[str, str] = {
    CUTTING_TOOLS: "Cutting tools",
    COOLANTS: "Coolants & lubricants",
    CONSUMABLES: "Consumables",
    METROLOGY: "Metrology",
    MACHINES: "Machines",
    UNCATEGORISED: "Not categorised",
}

MEANINGS: dict[str, str] = {
    CUTTING_TOOLS: "Inserts, drills, endmills, reamers, holders — the core line.",
    COOLANTS: "Cutting fluids, neat oils, way lubricants, rust preventives.",
    CONSUMABLES: "Abrasives, hardware and the smaller repeat lines.",
    METROLOGY: "Gauges, micrometers, height masters and measuring consumables.",
    MACHINES: "Machine sales. A long cycle, so a gap here is not read the same "
              "way as a gap in a consumable line.",
    UNCATEGORISED: "Nothing resolved a line for these. They are excluded from "
                   "coverage rather than counted against anybody — see Settings.",
}

#: Where a resolution came from. Carried beside the answer, because "cutting
#: tools, because somebody said so" and "cutting tools, because the HSN starts
#: 8207" are different levels of confidence and a screen should be able to say
#: which it is holding.
BY_OVERRIDE = "OVERRIDE"
BY_ZOHO = "ZOHO"
BY_HSN = "HSN"
BY_VENDOR = "VENDOR"
BY_NOTHING = "NONE"

#: How sure each source is, worst to best. A screen showing where a line came
#: from should be able to rank them without re-deciding the order.
SOURCE_CONFIDENCE: tuple[str, ...] = (BY_NOTHING, BY_VENDOR, BY_HSN, BY_ZOHO,
                                      BY_OVERRIDE)

SOURCE_LABEL: dict[str, str] = {
    BY_OVERRIDE: "Set by hand",
    BY_ZOHO: "From the catalogue",
    BY_HSN: "From the HSN code",
    BY_VENDOR: "Inferred from the supplier",
    BY_NOTHING: "Not placed",
}

#: Words a Zoho category might use, mapped to a line. Matched on a normalised
#: containment test rather than equality: catalogues say "Cutting Tool",
#: "CUTTING TOOLS", "Tooling - Cutting" and mean one thing.
_ZOHO_HINTS: tuple[tuple[str, str], ...] = (
    ("coolant", COOLANTS), ("lubricant", COOLANTS), ("cutting oil", COOLANTS),
    ("cutting fluid", COOLANTS), ("grease", COOLANTS),
    ("metrolog", METROLOGY), ("gauge", METROLOGY), ("gage", METROLOGY),
    ("measuring", METROLOGY), ("micrometer", METROLOGY),
    ("machine", MACHINES), ("cnc", MACHINES), ("vmc", MACHINES),
    ("lathe", MACHINES),
    ("cutting tool", CUTTING_TOOLS), ("tooling", CUTTING_TOOLS),
    ("insert", CUTTING_TOOLS), ("drill", CUTTING_TOOLS),
    ("endmill", CUTTING_TOOLS), ("end mill", CUTTING_TOOLS),
    ("carbide", CUTTING_TOOLS), ("holder", CUTTING_TOOLS),
    ("consumable", CONSUMABLES), ("abrasive", CONSUMABLES),
    ("spare", CONSUMABLES),
)


@dataclass(frozen=True)
class Resolution:
    """A line, and the evidence that put the item in it."""

    category: str
    source: str

    @property
    def known(self) -> bool:
        return self.category != UNCATEGORISED

    def to_dict(self) -> dict:
        return {"category": self.category, "label": LABELS[self.category],
                "source": self.source}


def _normalise(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def from_zoho(category_name: Optional[str]) -> Optional[str]:
    """A line from the catalogue's own category, or None if it says nothing."""
    text = _normalise(category_name)
    if not text:
        return None
    for hint, category in _ZOHO_HINTS:
        if hint in text:
            return category
    return None


def heading_of(hsn: Optional[str]) -> Optional[int]:
    """The four-digit HSN heading, as a number.

    India writes these as 4, 6 or 8 digits — ``8207``, ``820730``, ``82073010``
    are all the same heading — and plenty of catalogues carry a dot or a space.
    Everything but the digits is dropped and the first four are the heading; a
    code with fewer than four digits is not one and resolves nothing.
    """
    digits = "".join(c for c in (hsn or "") if c.isdigit())
    return int(digits[:4]) if len(digits) >= 4 else None


def from_hsn(hsn: Optional[str], th: CommercialThresholds) -> Optional[str]:
    """A line from the tax code, by inclusive range over the heading.

    Numeric ranges rather than string prefixes: the tariff is organised in runs
    — 8456 through 8465 is "machine tools" as one block — and writing that as
    ten prefix entries is ten places to leave a gap. Narrower ranges are tried
    first so a specific heading beats the block it sits inside.
    """
    heading = heading_of(hsn)
    if heading is None:
        return None
    for lo, hi, category in sorted(th.hsn_category_ranges,
                                   key=lambda r: r[1] - r[0]):
        if lo <= heading <= hi:
            return category
    return None


def resolve(product: models.Product, th: CommercialThresholds, *,
            override: Optional[str] = None) -> Resolution:
    """One item → one line, by the order documented at the top of this file.

    The vendor pass is deliberately *not* here: it needs the whole catalogue to
    know what a vendor's dominant line is, and a per-item function cannot see
    that. ``resolve_all`` applies it as a second pass.
    """
    if override and override in LABELS:
        return Resolution(override, BY_OVERRIDE)
    zoho = from_zoho(getattr(product, "category", None))
    if zoho:
        return Resolution(zoho, BY_ZOHO)
    hsn = from_hsn(product.hsn, th)
    if hsn:
        return Resolution(hsn, BY_HSN)
    return Resolution(UNCATEGORISED, BY_NOTHING)


def resolve_all(products: Iterable[models.Product], th: CommercialThresholds, *,
                overrides: Optional[dict[str, str]] = None,
                vendor_of: Optional[dict[str, str]] = None,
                ) -> dict[str, Resolution]:
    """Every product's line, keyed by ``product_id``.

    Two passes. The first places what it can from the item itself. The second
    looks at what is left and asks the principal who supplies it — which is
    real evidence for an authorised distributor, whose suppliers are mostly
    single-line, and which is why it runs *after* the tariff code rather than
    instead of it: a mixed principal like a major tooling brand sells inserts,
    holders and gauges, and the HSN heading separates those where the vendor
    name cannot.
    """
    by_id = overrides or {}
    resolved = {p.product_id: resolve(p, th, override=by_id.get(p.product_id))
                for p in products}
    if vendor_of:
        _infer_from_vendors(resolved, vendor_of, th)
    return resolved


def dominant_line(placed: list[str], th: CommercialThresholds
                  ) -> Optional[str]:
    """The one line a set of placed items agrees on, or None.

    Two floors, both policy. Enough items, so a "dominant line" is not one
    coincidence; and actual dominance, so a genuinely mixed principal infers
    nothing and their unplaced items stay honestly uncategorised rather than
    being swept into whichever line happened to be commonest.
    """
    if len(placed) < th.vendor_category_min_items:
        return None
    counts: dict[str, int] = {}
    for category in placed:
        counts[category] = counts.get(category, 0) + 1
    line, n = max(counts.items(), key=lambda kv: kv[1])
    return line if (n / len(placed)) >= th.vendor_category_dominance else None


def _infer_from_vendors(resolved: dict[str, Resolution],
                        vendor_of: dict[str, str],
                        th: CommercialThresholds) -> None:
    """Fill unplaced items from their supplier's own dominant line, in place.

    Only ever *fills*: an item already placed by an override, by the catalogue
    or by its tariff code is never overwritten by an inference from a vendor
    name. Weaker evidence does not get to win.
    """
    placed_by_vendor: dict[str, list[str]] = {}
    for product_id, resolution in resolved.items():
        vendor = vendor_of.get(product_id)
        if vendor and resolution.known:
            placed_by_vendor.setdefault(vendor, []).append(resolution.category)

    lines = {vendor: dominant_line(placed, th)
             for vendor, placed in placed_by_vendor.items()}
    for product_id, resolution in resolved.items():
        if resolution.known:
            continue
        line = lines.get(vendor_of.get(product_id) or "")
        if line:
            resolved[product_id] = Resolution(line, BY_VENDOR)


def coverage_report(resolutions: dict[str, Resolution]) -> dict:
    """How well the catalogue is placed, and by what.

    Rendered rather than hidden: a mix grid built on a catalogue that is 40%
    unresolved is a grid with 40% phantom whitespace, and the reader has to know
    that before they act on a gap.
    """
    counts: dict[str, int] = {}
    sources: dict[str, int] = {}
    for r in resolutions.values():
        counts[r.category] = counts.get(r.category, 0) + 1
        sources[r.source] = sources.get(r.source, 0) + 1
    total = len(resolutions)
    unresolved = counts.get(UNCATEGORISED, 0)
    return {
        "products": total,
        "by_category": counts,
        "by_source": sources,
        "uncategorised": unresolved,
        "resolved_share": round((total - unresolved) / total, 4) if total else None,
    }


def lines() -> list[dict]:
    """The columns every mix view renders, described once."""
    return [{"category": c, "label": LABELS[c], "meaning": MEANINGS[c]}
            for c in ORDER]
