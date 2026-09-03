"""What a tenant's words mean, learned from what was chosen — as counts.

Every phrase alias is a request in a customer's own words beside the catalogue
record a person chose for it, and that record carries the engine's decoded
attributes: family, application group, coating, series. Across a tenant's
aliases those pairs say, as plain counts, which words go with which technical
facts: at this tenant ``SS`` appeared in 41 requests and 39 of the chosen
records were M-group; ``BOHRER`` appeared in 5 and all 5 were solid-carbide
drills. That is a vocabulary — the tenant's, learned from the first pair rather
than the thousandth, and never shared with another tenant because it is
computed from one organization's rows.

Three scopes, in order:

* the **customer's** own usage, when that customer alone has enough pairs for
  the word — a customer whose ``SS`` means something else keeps its meaning;
* the **tenant's** usage across all its customers otherwise;
* nothing, when neither has enough support. No hint is a hint.

What a hint is for. It is *evidence about words*, not a decision: it expands
the retrieval query with the attribute's own tokens so records carrying that
attribute come up as neighbours, it is written beside a candidate that agrees
with it, and it is reported on the line with its counts. It never enters the
engine's spec, never changes a relationship, and never selects. The engine's
geometry comparison is what stands between a word and a product, and this
leaves it exactly there.

Deterministic: counts over sorted rows, thresholds that are named constants,
ties broken by value. Two runs over the same aliases give the same hints.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .embedder import tokens

#: A word needs this many pairs behind it before it means anything here.
MIN_SUPPORT = 3
#: And that many pairs must agree on the attribute this often. Below it the
#: word is used for several things, and a hint would be a guess.
MIN_SHARE = 0.6

#: The record attributes a word may be evidence about. Technical facts the
#: engine decoded, not dimensions (a number is not vocabulary) and not the
#: record id (a code is the alias index's job).
ATTRIBUTE_FIELDS = (
    "product_family", "product_subfamily", "iso_shape", "applications",
    "coating", "coating_process", "material_class", "series", "grade",
)

#: A learned family, as the word the engine's own fuzzy decoder reads it by.
#: These are terms from pie-parser's ``resolver/lookups/product_category.csv``
#: whose category is one of the engine's real families — the only families a
#: word can become a hard gate for. A family with no such word (a grooving
#: insert, a drill tip) cannot be taught to the ranking this way, and is not.
#: Written out rather than read from the engine's lookup: this is the portal's
#: statement of which readings it will feed back, and a pack edit must not
#: silently widen it.
FAMILY_WORDS = {
    "solid_carbide_drill": "drill",
    "solid_carbide_endmill": "end mill",
    "turning_insert": "turning insert",
    "milling_insert": "milling insert",
    "reamer": "reamer",
}

#: Words that are about the request rather than the product, and would
#: otherwise learn whatever the tenant sells most of.
STOPWORDS = frozenset({
    "A", "AN", "AND", "FOR", "OF", "OR", "THE", "TO", "WITH", "IN", "ON", "PER",
    "PCS", "PC", "NOS", "NO", "QTY", "QUANTITY", "PIECES", "PIECE", "SET", "SETS",
    "EA", "EACH", "MM", "CM", "M", "INCH", "IN", "X", "REQD", "REQUIRED", "REQ",
    "URGENT", "PLS", "PLEASE", "NEED", "NEEDED", "WANT", "SEND", "QUOTE",
    "RATE", "PRICE", "BEST", "ASAP", "KINDLY", "ALSO",
})


def words(text: str) -> List[str]:
    """The tokens of ``text`` that can carry meaning: letters only, at least
    two of them, not a stopword, each once."""
    seen: List[str] = []
    for tok in tokens(text):
        if len(tok) < 2 or not tok.isalpha() or tok in STOPWORDS or tok in seen:
            continue
        seen.append(tok)
    return seen


def attribute_values(record: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """The ``(field, value)`` facts a record carries, one per list element."""
    out: List[Tuple[str, str]] = []
    for field_name in ATTRIBUTE_FIELDS:
        value = record.get(field_name)
        if value in (None, "", [], {}):
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for v in values:
            if v not in (None, ""):
                out.append((field_name, str(v)))
    return out


@dataclass(frozen=True)
class Hint:
    """One learned reading of one word, with the evidence behind it."""

    token: str
    field: str
    value: str
    #: How many pairs carried the word, and how many of those agreed.
    support: int
    agreeing: int
    #: ``"customer"`` when the customer's own pairs decided it, else ``"tenant"``.
    scope: str

    @property
    def share(self) -> float:
        return round(self.agreeing / self.support, 3) if self.support else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"token": self.token, "field": self.field, "value": self.value,
                "support": self.support, "agreeing": self.agreeing,
                "share": self.share, "scope": self.scope}

    def sentence(self) -> str:
        who = "this customer's" if self.scope == "customer" else "this tenant's"
        return (f"read {self.token!r} as {self.field}={self.value} — "
                f"{who} usage in {self.agreeing} of {self.support} quotes")


class Vocabulary:
    """Word -> attribute counts, per tenant and per customer."""

    def __init__(self, pairs: Iterable[Tuple[Optional[str], str, Mapping[str, Any]]]
                 ) -> None:
        """``pairs`` are ``(customer_scope, text, record)``: whose request,
        the words, and the chosen record with its decoded attributes."""
        # token -> (field, value) -> count ; token -> count
        self._tenant: Dict[str, Dict[Tuple[str, str], int]] = defaultdict(
            lambda: defaultdict(int))
        self._tenant_total: Dict[str, int] = defaultdict(int)
        self._customer: Dict[str, Dict[str, Dict[Tuple[str, str], int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int)))
        self._customer_total: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        self.pairs = 0
        for scope, text, record in sorted(
                ((str(s) if s else "", t, r) for s, t, r in pairs),
                key=lambda p: (p[0], p[1])):
            facts = attribute_values(record)
            toks = words(text)
            if not facts or not toks:
                continue
            self.pairs += 1
            for tok in toks:
                self._tenant_total[tok] += 1
                if scope:
                    self._customer_total[scope][tok] += 1
                for fact in facts:
                    self._tenant[tok][fact] += 1
                    if scope:
                        self._customer[scope][tok][fact] += 1

    @staticmethod
    def _best(counts: Mapping[Tuple[str, str], int], total: int
              ) -> List[Tuple[Tuple[str, str], int]]:
        """Every fact the word supports past the thresholds, ordered by field,
        then strength, then value, so the answer is stable.

        Not capped at one value per field on purpose. A single-valued field
        cannot have two values above ``MIN_SHARE`` (the shares sum to one), so
        the cap would be automatic there; a list-valued one can — a drill
        chosen for "SS" is rated for M *and* P, and a word may honestly imply
        both groups.
        """
        if total < MIN_SUPPORT:
            return []
        passing = [(fact, n) for fact, n in counts.items() if n / total >= MIN_SHARE]
        return sorted(passing, key=lambda fn: (fn[0][0], -fn[1], fn[0][1]))

    def hints(self, text: str, customer_scope: Optional[str] = None) -> List[Hint]:
        """What this text's words usually mean — for this customer where their
        own usage is established, for the tenant otherwise."""
        out: List[Hint] = []
        scope = str(customer_scope) if customer_scope else ""
        for tok in words(text):
            ctotal = self._customer_total.get(scope, {}).get(tok, 0) if scope else 0
            if ctotal >= MIN_SUPPORT:
                found = self._best(self._customer[scope][tok], ctotal)
                out += [Hint(tok, f, v, ctotal, n, "customer") for (f, v), n in found]
                continue
            ttotal = self._tenant_total.get(tok, 0)
            found = self._best(self._tenant.get(tok, {}), ttotal)
            out += [Hint(tok, f, v, ttotal, n, "tenant") for (f, v), n in found]
        return out

    @staticmethod
    def expansion(hints: Iterable[Hint]) -> List[str]:
        """The tokens the hints add to a retrieval query: each attribute
        value's own words, once — the same tokens the catalogue index carries
        for that attribute, so records that have it come up as neighbours."""
        extra: List[str] = []
        for hint in hints:
            for tok in tokens(hint.value):
                if tok not in extra:
                    extra.append(tok)
        return extra

    @staticmethod
    def ranking_reading(hints: Iterable[Hint]) -> Optional[Tuple[Hint, str]]:
        """The one learned reading the engine's ranking may be given, if any:
        a ``product_family`` hint whose family has a word the fuzzy decoder
        reads, so the engine can apply its own family gate. The strongest
        such hint, ties by family name. None when there is none — the
        ranking is then left exactly as the engine made it."""
        family_hints = sorted(
            (h for h in hints if h.field == "product_family" and h.value in FAMILY_WORDS),
            key=lambda h: (-h.agreeing, -h.support, h.value))
        if not family_hints:
            return None
        return family_hints[0], FAMILY_WORDS[family_hints[0].value]

    @staticmethod
    def agreeing(hints: Iterable[Hint], record: Mapping[str, Any]) -> List[Hint]:
        """The hints this record's own attributes bear out."""
        facts = set(attribute_values(record))
        return [h for h in hints if (h.field, h.value) in facts]
