"""Which floor family an item prices in, and why that is not its business line.

``incentive_engine/config/parameters.yaml`` publishes ``m_floor`` per family —
inserts 0.22, solid_carbide 0.26, holders_toolsystems 0.30, metrology 0.34,
chemicals 0.18, machines 0.12, and a default of 0.25. Nothing mapped a product
onto those names, so ``floor.resolve`` received ``family=None`` from every
caller and every line in the catalogue priced at ``default``. This module is
that mapping.

**Two things were wrong while every item shared one multiplier.**

*The floors were wrong, family by family.* A machine priced at 0.25 instead of
0.12 carries a floor thirteen points above the one policy set for it, and a
metrology line at 0.25 instead of 0.34 carries one nine points below. Those are
not rounding errors — they are the calibration the parameter block exists to
express, applied to nothing. A shadow run solving ``r`` from CAF computed this
way would solve it from floors the owner never chose.

*The disclosure defence was down to one layer.* ``floor.py`` states the design:
``m_floor`` is unpublished **and varies by family**, so a salesperson holding one
observed line cannot invert F to cost, and holding two lines in different
families does not help because the two multipliers differ. Collapse every item
onto one multiplier and the second clause stops being true — a single
constant, once inferred, unlocks the whole catalogue permanently. That is the
same structural hazard ``config.py`` documents at length for
``carrying_cost_annual_pct``, and it is why the family restores defence in
depth rather than merely correcting an average.

────────────────────────────────────────────────────────────────────────────
WHY THIS IS NOT ``categories.py``
────────────────────────────────────────────────────────────────────────────

``categories`` answers "which line of the business is this", for coverage and
mix: five lines, chosen so a reader can see that a cutting-tool customer buys no
coolant. This answers "which floor bucket does this price in": six families,
chosen so a margin policy can differ between an insert and the holder it sits
in. The two questions have different shapes and the cutting-tool line is where
they come apart — ``CUTTING_TOOLS`` spans *three* floor families, so knowing an
item is a cutting tool tells you nothing about which multiplier applies.

They are therefore separate modules rather than one with two outputs, and the
coarse line is used only where it genuinely determines the family: metrology,
machines and coolants map one-to-one, and cutting tools and consumables
resolve nothing.

────────────────────────────────────────────────────────────────────────────
THE FAMILY IS OWNER ZONE. IT NEVER TRAVELS WITH A FLOOR.
────────────────────────────────────────────────────────────────────────────

A floor price is disclosable; the family it was priced in is not. Telling a
salesperson that two items share a family tells them the two share a
multiplier, and one leaked cost then inverts both — which is precisely the
inference the family variation exists to block. So this resolution reaches
``FloorReconciliation`` and never ``ResolvedFloor``: the operations type has no
field to carry it, the same way it has no field for cost.

────────────────────────────────────────────────────────────────────────────
THE EVIDENCE ORDER, AND WHY UNRESOLVED IS NOT A GUESS
────────────────────────────────────────────────────────────────────────────

``OVERRIDE``  what somebody in the business said. Wins outright.
``ZOHO``      the item's own category, where it is specific enough to name a
              family. "Inserts" is; "Cutting Tools" is not, and resolves
              nothing rather than picking one of the three it could mean.
``HSN``       the tariff heading. The strongest objective source *for this
              question*, because the tariff already draws the distinction the
              floor families need — 8209 is unmounted tips, 8207 is the
              interchangeable tool itself, 8466 is the holder it mounts in.
``LINE``      the business line from ``categories``, for the three lines that
              determine a family on their own.
``NONE``      nothing resolved it, and the caller prices at ``default``.

An unresolved item falls to ``default`` and is **counted**, because that is the
honest state and because ``coverage`` is what tells an owner how much of the
book is still priced on a fallback. Guessing a family would move a real floor
on no evidence, and a floor that moved for no reason is worse than one that is
admittedly generic.

**The ranges live in ``CommercialThresholds``**, beside ``hsn_category_ranges``
and for the same reason: they are policy that changes every floor downstream of
them, so they belong inside the version hash. Re-map a heading and last
quarter's floors stay explicable, because the ``thresholds_version`` recorded on
those rows says what the rules were. The *rates* stay in ``parameters.yaml``
under I6 — a classification is not a rate, and the two are governed
differently on purpose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..domain import models
from . import categories
from .config import CommercialThresholds

#: The families ``m_floor_by_family`` publishes. Spelled exactly as the
#: parameter block spells them — this module's whole job is to produce a key
#: that block will recognise, and a near-miss would silently price at default.
INSERTS = "inserts"
SOLID_CARBIDE = "solid_carbide"
HOLDERS_TOOLSYSTEMS = "holders_toolsystems"
METROLOGY = "metrology"
CHEMICALS = "chemicals"
MACHINES = "machines"
#: Not a family. What ``m_floor_for_family`` falls back to, named here so a
#: caller can say "priced at default" without spelling the string again.
DEFAULT = "default"

FAMILIES: tuple[str, ...] = (INSERTS, SOLID_CARBIDE, HOLDERS_TOOLSYSTEMS,
                             METROLOGY, CHEMICALS, MACHINES)

LABELS: dict[str, str] = {
    INSERTS: "Inserts",
    SOLID_CARBIDE: "Solid carbide",
    HOLDERS_TOOLSYSTEMS: "Holders & toolsystems",
    METROLOGY: "Metrology",
    CHEMICALS: "Chemicals",
    MACHINES: "Machines",
    DEFAULT: "Priced at the default multiplier",
}

BY_OVERRIDE = "OVERRIDE"
BY_ZOHO = "ZOHO"
BY_HSN = "HSN"
BY_LINE = "LINE"
BY_NOTHING = "NONE"

SOURCE_LABEL: dict[str, str] = {
    BY_OVERRIDE: "Set by hand",
    BY_ZOHO: "From the catalogue",
    BY_HSN: "From the HSN code",
    BY_LINE: "From the product line",
    BY_NOTHING: "Not placed — priced at the default multiplier",
}

#: Words a Zoho category might use, mapped to a family. First match wins, so
#: the order is most-specific-first: "drill chuck" must reach ``chuck`` before
#: it reaches ``drill``, or a workholder prices as a solid-carbide drill.
#:
#: Deliberately absent: "cutting tool", "tooling", "carbide" on their own. Each
#: is true of three different families, and a hint that cannot separate them is
#: a hint that picks one at random.
_ZOHO_HINTS: tuple[tuple[str, str], ...] = (
    # Holders first — most holder words contain a tool word.
    ("toolholder", HOLDERS_TOOLSYSTEMS), ("tool holder", HOLDERS_TOOLSYSTEMS),
    ("holder", HOLDERS_TOOLSYSTEMS), ("collet", HOLDERS_TOOLSYSTEMS),
    ("chuck", HOLDERS_TOOLSYSTEMS), ("arbor", HOLDERS_TOOLSYSTEMS),
    ("adapter", HOLDERS_TOOLSYSTEMS), ("adaptor", HOLDERS_TOOLSYSTEMS),
    ("toolsystem", HOLDERS_TOOLSYSTEMS), ("tool system", HOLDERS_TOOLSYSTEMS),
    ("shank", HOLDERS_TOOLSYSTEMS),
    # Then the two that "carbide" alone cannot separate.
    ("insert", INSERTS), ("indexable", INSERTS), ("cermet", INSERTS),
    ("solid carbide", SOLID_CARBIDE), ("endmill", SOLID_CARBIDE),
    ("end mill", SOLID_CARBIDE), ("drill", SOLID_CARBIDE),
    ("reamer", SOLID_CARBIDE), ("router", SOLID_CARBIDE),
    # These three are unambiguous whatever the order.
    ("metrolog", METROLOGY), ("gauge", METROLOGY), ("gage", METROLOGY),
    ("micrometer", METROLOGY), ("vernier", METROLOGY), ("caliper", METROLOGY),
    ("measuring", METROLOGY),
    ("coolant", CHEMICALS), ("lubricant", CHEMICALS), ("cutting oil", CHEMICALS),
    ("cutting fluid", CHEMICALS), ("grease", CHEMICALS),
    ("rust prevent", CHEMICALS), ("chemical", CHEMICALS),
    ("machine", MACHINES), ("cnc", MACHINES), ("vmc", MACHINES),
    ("lathe", MACHINES),
)

#: The business lines that determine a family on their own. ``CUTTING_TOOLS``
#: is absent because it spans three of them, and ``CONSUMABLES`` because
#: abrasives and hardware are none of the six — both resolve nothing rather
#: than being swept into the nearest match.
_LINE_TO_FAMILY: dict[str, str] = {
    categories.METROLOGY: METROLOGY,
    categories.MACHINES: MACHINES,
    categories.COOLANTS: CHEMICALS,
}


@dataclass(frozen=True)
class FamilyResolution:
    """A floor family, and the evidence that put the item in it.

    OWNER ZONE. See the module docstring: this must not be attached to a
    ``ResolvedFloor`` or serialised onto any operations payload.
    """

    family: Optional[str]
    source: str

    @property
    def known(self) -> bool:
        return self.family is not None

    @property
    def key(self) -> str:
        """What ``m_floor_for_family`` should be asked for.

        ``default`` when nothing resolved, which is what the engine falls back
        to anyway — returned explicitly so a caller never has to spell the
        fallback and the two cannot drift apart.
        """
        return self.family or DEFAULT

    def to_dict(self) -> dict:
        return {"family": self.family, "family_key": self.key,
                "label": LABELS[self.key], "source": self.source,
                "source_label": SOURCE_LABEL[self.source]}


def from_zoho(category_name: Optional[str]) -> Optional[str]:
    """A family from the catalogue's own category, or None if it says nothing.

    None is the common and correct answer here: most catalogues say "Cutting
    Tools", which names a line and not a family.
    """
    text = (category_name or "").strip().lower()
    if not text:
        return None
    for hint, family in _ZOHO_HINTS:
        if hint in text:
            return family
    return None


def from_hsn(hsn: Optional[str], th: CommercialThresholds) -> Optional[str]:
    """A family from the tariff heading, by inclusive range.

    Narrower ranges first, so a specific heading beats the block it sits inside
    — the same rule ``categories.from_hsn`` applies, and the reason 8209 can
    mean inserts while 8207 means the tool itself.
    """
    heading = categories.heading_of(hsn)
    if heading is None:
        return None
    for lo, hi, family in sorted(th.hsn_floor_family_ranges,
                                 key=lambda r: r[1] - r[0]):
        if lo <= heading <= hi:
            return family
    return None


def resolve(product: models.Product, th: CommercialThresholds, *,
            override: Optional[str] = None,
            line: Optional[str] = None) -> FamilyResolution:
    """One item → one floor family, by the order in the module docstring.

    ``line`` is this product's business line from ``categories``, where the
    caller already has it. Passed in rather than recomputed because
    ``categories.resolve_all`` needs the whole catalogue for its vendor pass,
    and a per-item function that re-derived a weaker answer would disagree with
    the mix screen about the same product.
    """
    if override and override in FAMILIES:
        return FamilyResolution(override, BY_OVERRIDE)
    zoho = from_zoho(getattr(product, "category", None))
    if zoho:
        return FamilyResolution(zoho, BY_ZOHO)
    hsn = from_hsn(product.hsn, th)
    if hsn:
        return FamilyResolution(hsn, BY_HSN)
    if line and line in _LINE_TO_FAMILY:
        return FamilyResolution(_LINE_TO_FAMILY[line], BY_LINE)
    return FamilyResolution(None, BY_NOTHING)


def resolve_all(products: Iterable[models.Product], th: CommercialThresholds, *,
                overrides: Optional[dict[str, str]] = None,
                lines: Optional[dict[str, str]] = None,
                ) -> dict[str, FamilyResolution]:
    """Every product's floor family, keyed by ``product_id``.

    ``lines`` maps ``product_id`` to the business line ``categories.resolve_all``
    produced, so the last pass can use it. Absent, the line pass simply does not
    fire and more items fall to ``default`` — which is a smaller answer, not a
    wrong one.
    """
    by_id = overrides or {}
    by_line = lines or {}
    return {
        p.product_id: resolve(p, th, override=by_id.get(p.product_id),
                              line=by_line.get(p.product_id))
        for p in products
    }


def coverage(resolutions: dict[str, FamilyResolution]) -> dict:
    """How much of the catalogue prices on a real family, and by what evidence.

    OWNER ZONE, and the number that matters before a shadow run: CAF computed
    over a catalogue that is half unplaced is CAF computed at the default
    multiplier for half the book, and ``r`` solved from it inherits that.
    """
    families: dict[str, int] = {}
    sources: dict[str, int] = {}
    for r in resolutions.values():
        families[r.key] = families.get(r.key, 0) + 1
        sources[r.source] = sources.get(r.source, 0) + 1
    total = len(resolutions)
    unplaced = sum(1 for r in resolutions.values() if not r.known)
    return {
        "products": total,
        "by_family": families,
        "by_source": sources,
        "unplaced": unplaced,
        "placed_share": round((total - unplaced) / total, 4) if total else None,
    }


def families() -> list[dict]:
    """The families, described once, for an owner-facing settings screen."""
    return [{"family": f, "label": LABELS[f]} for f in FAMILIES]
