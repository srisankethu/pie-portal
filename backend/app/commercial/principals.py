"""Which principal an item belongs to, for attributing *sales*.

An authorised distributor's book is organised by principal. Targets are set by
them, dependency is measured against them, and "which of our principals' lines
does this customer take" is the cross-sell question. All of that needs one
answer to a deceptively simple question: whose product is this item?

**Two facts answer it, and neither is a substitute for the other.**

``BILL``   the vendor on a purchase bill — who this book actually *paid* for
           the item. The stronger fact by a distance, because it is a
           transaction rather than an attribute: it survives the item master
           being wrong, and it is the only thing a principal's own statement
           will agree with.
``BRAND``  the manufacturer on the item master — whose product it *is*.

**They fail in complementary ways, which is the whole reason to keep both.**
The bill-derived vendor has a horizon: ``cost_records`` only reaches back as far
as the sync window, so an item sold today out of stock bought four years ago has
no bill to name a vendor, and a book on its first sync has almost none at all.
The brand has no horizon — the item master has known whose product it is the
whole time — but it is missing wherever nobody tagged it, which on a live master
here is a third of the catalogue. Chained, the residue is items that are both
untagged *and* bought outside the window. Either alone leaves far more.

**Where this must not be used.** Purchase spend, supply dependency, sole-source
counts and target progress take the vendor off the bill with no brand fallback,
and they call ``dominant_vendor`` rather than ``resolve_all``. A principal's
target is measured on the invoices *they* raised on this distributor; a number
that included brand-inferred lines would drift from their statement, and at year
end the one that is wrong is ours. The fallback answers "whose line is this
customer buying", never "what do we owe this principal against their target".
The separation is structural — two functions, and the purchase-side one cannot
reach the brand — because a comment would not have survived the third screen.

The one place worth spelling out is a **target**, because both readings live in
the same table. ``VendorTarget.basis`` says whether a principal's number is on
what this distributor *buys* from them or *sells* of their product.
``dependency.progress_of`` takes the purchase-basis figure from bills and the
sales-basis one from flows, so a purchase target is provably free of brand
inference — the fallback cannot reach a cost row — while a sell-through target
inherits it, which is correct, since what a sell-through target measures *is*
sales of that brand. That falls out of which list each figure is summed from
rather than from a flag anybody has to remember to set.

**An unmatched brand is still a principal.** Where a brand names no vendor this
book has a record for, it gets a key of its own rather than being dropped.
Dropping it would understate the book against a principal we demonstrably sell,
and the whole point of the fallback is the items no bill covers.

**Matching a brand to a vendor is deliberately timid.** The failure that matters
is not a missed match — that costs a separate column with the right name on it —
but a *wrong* one, which silently merges two principals and makes both numbers
untrue. So an ambiguous brand matches nothing, and says so. Like
``identity/matchers.py``, every match carries the value it matched on rather
than a score: "matched on ``kennametalindia``" settles an argument, "confidence
0.94" starts one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

#: Where an attribution came from, strongest first.
BY_BILL = "BILL"
BY_BRAND = "BRAND"
BY_NOTHING = "NONE"

#: How each source reads on a screen. Phrased as what somebody can check rather
#: than as a code — "from a purchase bill" is auditable, "BILL" is a token.
SOURCE_LABEL = {
    BY_BILL: "From a purchase bill",
    BY_BRAND: "From the item's brand",
    BY_NOTHING: "Not attributed",
}

#: Principals that exist only as a brand are keyed apart from real vendor rows
#: so nothing can mistake one for the other — a synthetic id must never be
#: handed to something that will look it up as a ``Vendor``.
BRAND_PREFIX = "brand:"

#: Dropped before two names are compared. Legal form and incorporation noise is
#: not identity: "KENNAMETAL INDIA LIMITED" and "Kennametal India Pvt Ltd" are
#: one principal written by two people. Country and product words are *not* in
#: here — "Sandvik" and "Sandvik India" may genuinely be two supply
#: relationships with different terms, and collapsing them is the wrong-match
#: failure this module exists to avoid.
_LEGAL_FORMS = frozenset({
    "pvt", "private", "ltd", "limited", "llp", "inc", "incorporated",
    "corp", "corporation", "co", "company", "gmbh", "ag", "sa", "srl",
    "bv", "nv", "plc", "kg", "spa", "oy", "ab", "as",
})

#: Below this, a prefix match is a coincidence rather than evidence. Two
#: characters would let a brand like "3M" claim any vendor beginning "3m".
_MIN_PREFIX = 3


@dataclass(frozen=True)
class Principal:
    """One item's principal, and how that was decided.

    ``principal_id`` is a real ``Vendor.vendor_id`` where one was matched and a
    ``brand:`` key otherwise. ``matched_on`` carries the evidence — the
    normalised form two names agreed on — and is None for a bill, where the
    vendor is the fact rather than an inference from one.
    """

    principal_id: str
    name: str
    source: str
    matched_on: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.source != BY_NOTHING

    @property
    def is_brand_only(self) -> bool:
        """True where this principal exists only because the item master said so.

        The number a screen should show alongside anything built on this: it is
        the share of the picture that no purchase bill corroborates.
        """
        return self.principal_id.startswith(BRAND_PREFIX)


@dataclass(frozen=True)
class Purchase:
    """One line of what this book bought, for attributing an item to a supplier."""

    product_id: str
    vendor_id: str
    amount: float


def normalise_name(raw: Optional[str]) -> str:
    """The canonical form two trading names are compared on.

    Lower-cased, punctuation dropped, legal forms removed, and finally closed up
    entirely — ``YG1`` and ``YG-1`` are one brand, and whether somebody typed
    the hyphen is not identity. Returns "" for anything with no letters or
    digits in it, which never matches.
    """
    words = re.sub(r"[^a-z0-9]+", " ", (raw or "").lower()).split()
    kept = [w for w in words if w not in _LEGAL_FORMS]
    return "".join(kept)


def brand_key(brand: str) -> str:
    """The principal id for a brand that matched no vendor row."""
    return BRAND_PREFIX + normalise_name(brand)


def match_brand(brand: Optional[str], vendor_names: dict[str, str],
                ) -> Optional[tuple[str, str]]:
    """A brand to a vendor this book has a record for, or None.

    Exact first, then a unique prefix — ``NOGA`` against "Noga Engineering
    Technology" is one principal, and requiring the full name would split it.
    **Ambiguity resolves to nothing.** If two vendors could take the prefix,
    neither gets it, and the brand becomes a principal of its own with its own
    name on it. That is an honest extra column; picking one of the two would be
    a silent merge of two suppliers into a number nobody can reconcile.

    Returns ``(vendor_id, matched_on)`` so the caller can show what it agreed
    on, not just that it agreed.
    """
    key = normalise_name(brand)
    if not key:
        return None
    normalised = {vid: normalise_name(name) for vid, name in vendor_names.items()}

    exact = [vid for vid, n in normalised.items() if n and n == key]
    if len(exact) == 1:
        return exact[0], key
    if exact:
        # Two vendor rows with the same trading name. A real possibility after a
        # re-sync that duplicated a contact, and not something to guess through.
        return None

    if len(key) < _MIN_PREFIX:
        return None
    prefixed = [vid for vid, n in normalised.items() if n and n.startswith(key)]
    return (prefixed[0], key) if len(prefixed) == 1 else None


def dominant_vendor(purchases: Iterable[Purchase]) -> dict[str, str]:
    """Each item's supplier, by what this book has spent with them.

    The purchase-side answer, and the *only* one anything reconciling against a
    principal's statement may use. An item bought from two suppliers is
    attributed wholly to the larger — a real inference, and the reason every
    view built on this reports what share it could attribute rather than quietly
    showing a share of a fraction of the book.
    """
    spend: dict[str, dict[str, float]] = {}
    for p in purchases:
        if not p.vendor_id:
            continue
        bucket = spend.setdefault(p.product_id, {})
        bucket[p.vendor_id] = bucket.get(p.vendor_id, 0.0) + float(p.amount or 0.0)
    return {product_id: max(by_vendor.items(), key=lambda kv: kv[1])[0]
            for product_id, by_vendor in spend.items() if by_vendor}


def resolve_all(brands: dict[str, Optional[str]], purchases: Iterable[Purchase],
                vendor_names: dict[str, str]) -> dict[str, Principal]:
    """Every item's principal for **sales** attribution, keyed by product_id.

    ``brands`` is every product in the book mapped to its item-master brand,
    including the null ones — an item with neither a bill nor a brand must still
    appear, as an explicit refusal rather than a missing key, or a caller cannot
    tell "not attributed" from "not a product".

    Brand matching is resolved once per brand rather than once per item. A
    catalogue has thousands of items and, measured on the live masters, six
    distinct brands; matching per item would do the same work hundreds of times
    and could not be memoised without this function knowing it was hot.
    """
    by_bill = dominant_vendor(purchases)
    resolved: dict[str, Principal] = {}
    matched: dict[str, Optional[tuple[str, str]]] = {}

    for product_id, brand in brands.items():
        vendor_id = by_bill.get(product_id)
        if vendor_id:
            resolved[product_id] = Principal(
                principal_id=vendor_id,
                name=vendor_names.get(vendor_id, vendor_id),
                source=BY_BILL)
            continue

        text = (brand or "").strip()
        if not text:
            resolved[product_id] = Principal(
                principal_id="", name="", source=BY_NOTHING)
            continue

        if text not in matched:
            matched[text] = match_brand(text, vendor_names)
        hit = matched[text]
        if hit is not None:
            vid, evidence = hit
            resolved[product_id] = Principal(
                principal_id=vid, name=vendor_names.get(vid, text),
                source=BY_BRAND, matched_on=evidence)
        else:
            # No vendor row answers to this brand. It is still a principal we
            # sell, so it keeps its own key and its own name as typed.
            resolved[product_id] = Principal(
                principal_id=brand_key(text), name=text, source=BY_BRAND)

    return resolved


def names_of(resolved: dict[str, Principal], vendor_names: dict[str, str],
             ) -> dict[str, str]:
    """Display names for every principal in play, real vendors and brands alike.

    A caller rendering columns has a ``vendor_names`` map that by definition
    cannot name a ``brand:`` key. This closes that gap in one place rather than
    leaving each screen to fall back to showing an id.
    """
    names = dict(vendor_names)
    for p in resolved.values():
        if p.known and p.principal_id not in names:
            names[p.principal_id] = p.name
    return names


def coverage_report(resolved: dict[str, Principal],
                    revenue: Optional[dict[str, float]] = None) -> dict:
    """What share of the book this attribution actually covers, and from where.

    Reported in **revenue** as well as item count, for the reason the Catalogue
    screen leads with revenue: "38% of items have no principal" can look alarming
    while those items sell nothing, and can look survivable while the one item a
    tenth of the book runs through is among them.

    ``brand_only_share`` is the number to read before trusting anything built on
    this — the part of the picture that rests on the item master alone, with no
    purchase bill corroborating it.
    """
    money = revenue or {}
    counts = {BY_BILL: 0, BY_BRAND: 0, BY_NOTHING: 0}
    value = {BY_BILL: 0.0, BY_BRAND: 0.0, BY_NOTHING: 0.0}
    brand_only = 0.0

    for product_id, p in resolved.items():
        amount = float(money.get(product_id, 0.0))
        counts[p.source] += 1
        value[p.source] += amount
        if p.is_brand_only:
            brand_only += amount

    items = sum(counts.values())
    total = sum(value.values())
    attributed = value[BY_BILL] + value[BY_BRAND]
    return {
        "products": items,
        "from_bill": counts[BY_BILL],
        "from_brand": counts[BY_BRAND],
        "unattributed": counts[BY_NOTHING],
        "resolved_share": (items - counts[BY_NOTHING]) / items if items else None,
        "revenue": total,
        "revenue_from_bill": value[BY_BILL],
        "revenue_from_brand": value[BY_BRAND],
        "revenue_unattributed": value[BY_NOTHING],
        # None rather than 0.0 where there is no revenue at all: a book that has
        # not traded has an unknown attributed share, not a perfect one.
        "attributed_share": (attributed / total) if total else None,
        "brand_only_share": (brand_only / total) if total else None,
    }
