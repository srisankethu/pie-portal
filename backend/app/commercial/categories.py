"""Which line of the business an item belongs to, and how that is decided.

The platform's growth question is not only "who is close to us" but "who takes
one line and not the others" — a cutting-tool customer who has never bought a
drum of coolant, a machine buyer with no metrology. That question needs every
item placed in a line, and nothing in the read model placed one: ``Product``
carried a name, a unit, an HSN code and nothing that said *what kind of thing
this is*.

**Three sources, in order, and the order is the whole design.**

``OVERRIDE``  what somebody in the business said, in Settings. Wins outright.
              Stored outside ``Product`` on purpose: products are derived from
              Zoho and rebuilt by a full re-sync, and a mapping a person typed
              must not be destroyed by one.
``ZOHO``      the item's own category, as Zoho holds it. Free, already
              maintained by whoever maintains the catalogue, and wrong only
              where the catalogue is.
``HSN``       the tax code, mapped by prefix. Every item that is invoiced has
              one, so this is the source with the widest reach — and the
              coarsest, which is why it is last.
``NONE``      nothing resolved it.

**Uncategorised is a category of its own and is never guessed at.** A mix grid
exists to show gaps, and a gap it invented is an opportunity that is not there —
somebody drives to a customer to sell them a line they already buy under a name
this map did not recognise. So an unresolved item is counted, named, and
reported as work to do in Settings, which is the honest version of the same
information.

**The HSN map lives in ``CommercialThresholds``**, not here, for the reason
``target_margin_by_family`` does: it is a policy that changes every number
downstream of it, so it belongs inside the version hash. Re-map a prefix and
last quarter's mix figures stay explainable, because the version that produced
them says what the map was.
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
BY_NOTHING = "NONE"

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


def from_hsn(hsn: Optional[str], th: CommercialThresholds) -> Optional[str]:
    """A line from the tax code, by longest matching prefix.

    Longest-first so a specific prefix beats a general one: 8207 is cutting
    tools while 82 alone is "base metal tools" and covers spanners too. Matching
    shortest-first would let the vague entry win every time, which is the kind
    of bug that is invisible until somebody asks why every item is in one
    column.
    """
    digits = "".join(c for c in (hsn or "") if c.isdigit())
    if not digits:
        return None
    for prefix, category in sorted(th.hsn_category_map,
                                   key=lambda kv: -len(kv[0])):
        if digits.startswith(prefix):
            return category
    return None


def resolve(product: models.Product, th: CommercialThresholds, *,
            override: Optional[str] = None) -> Resolution:
    """One item → one line, by the order documented at the top of this file."""
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
                ) -> dict[str, Resolution]:
    """Every product's line, keyed by ``product_id``. One pass, one map."""
    by_id = overrides or {}
    return {p.product_id: resolve(p, th, override=by_id.get(p.product_id))
            for p in products}


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
