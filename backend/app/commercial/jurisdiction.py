"""Which country's statutes reach a tenant, and that country's statutory calendar.

The statutory modules in ``insight/`` — ``msme`` (MSMED s.15 / 43B(h)) and
``withholding`` (s.194Q) — are Indian law. Until this module existed they
answered for every tenant: ``Organization`` had no country, the April financial
year was a literal in ``msme.py``, and a Gulf distributor would have been shown
Indian deadlines with an explanation attached. That is the confidently-wrong
output CLAUDE.md §1 forbids — when the evidence for a claim is missing, the
answer is a refusal that names what is missing, never the benign default. And
here the benign default was worse than benign: it was another country's tax law.

So this module is the one place that knows, per ISO 3166-1 alpha-2 country:

- the statutory calendar — which month the financial year starts in;
- whether the MSME payment-timing statute applies;
- whether the purchase-withholding statute applies.

India is the only supported jurisdiction today, and the table says so rather
than pretending to generality it does not have. Adding a second jurisdiction is
adding an entry here (and, realistically, a second statute module — the MSMED
sections are not parameterisable into another country's law by changing a
month; ``msme.py`` says the same thing about its own hard-coded calendar).

Three states, never two, exactly as ``msme.Status.scope`` argues: a country can
be **supported**, **unsupported**, or **not set** — and the last is not the
first. A NULL country collapsed into "assume India" would put the statutory
screens back where they started; collapsed into "assume nothing applies" it
would silently hide real Indian exposure from an Indian tenant that simply
predates the column. Both directions are wrong, so both refuse, and the two
refusals name different gaps because they need different things done about
them: one is a fact to record, the other a jurisdiction this platform does not
know.

Deterministic and import-free within ``commercial/`` — no session, no clock,
no I/O. The routers read ``Organization.country`` and ask; the statute modules
read the calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional


@dataclass(frozen=True)
class Jurisdiction:
    """One country's statutory facts, as far as this platform knows them."""

    #: ISO 3166-1 alpha-2, upper-case.
    country: str
    #: The month the statutory financial year starts in (India: April, 4).
    fy_start_month: int
    #: Whether the MSME payment-timing statute (MSMED s.15 / 43B(h)) applies.
    msme_applies: bool
    #: Whether the purchase-withholding statute (s.194Q) applies.
    withholding_applies: bool

    def fy_of(self, day: date) -> str:
        """``FY2026-27`` — which statutory financial year a date falls in."""
        start = day.year if day.month >= self.fy_start_month else day.year - 1
        return f"FY{start}-{str(start + 1)[-2:]}"

    def fy_bounds(self, fy_label: str) -> tuple[date, date]:
        """``FY2026-27`` as the half-open range ``[start, next_start)``."""
        start_year = int(fy_label[2:6])
        return (date(start_year, self.fy_start_month, 1),
                date(start_year + 1, self.fy_start_month, 1))


INDIA = Jurisdiction(country="IN", fy_start_month=4,
                     msme_applies=True, withholding_applies=True)

#: Every jurisdiction this platform can answer for. One entry, on purpose —
#: the table growing is a deliberate act with a statute review behind it, not
#: a fallback picking the nearest calendar.
SUPPORTED: dict[str, Jurisdiction] = {INDIA.country: INDIA}


def normalize(country: Optional[str]) -> Optional[str]:
    """An upper-cased alpha-2 code, or ``None`` for blank/unset."""
    code = (country or "").strip().upper()
    return code or None


#: Country labels a connected ERP reports, mapped to alpha-2. Zoho states the
#: organization's country as a display name ("India"), not a code, and this
#: table places only labels it can place *exactly* — an unknown label maps to
#: nothing, the column stays NULL, and the gate keeps refusing. Guessing here
#: would turn a refusal into a wrong answer, which is the trade §1 forbids.
_LABELS: dict[str, str] = {
    "IN": "IN", "INDIA": "IN",
    "AE": "AE", "UAE": "AE", "UNITED ARAB EMIRATES": "AE",
    "US": "US", "USA": "US", "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "GB": "GB", "UK": "GB", "UNITED KINGDOM": "GB",
    "SG": "SG", "SINGAPORE": "SG",
    "SA": "SA", "SAUDI ARABIA": "SA",
    "DE": "DE", "GERMANY": "DE",
}


def alpha2_from_label(label: Optional[str]) -> Optional[str]:
    """The alpha-2 code a reported country label places to, or ``None``.

    Exact matches only, after trimming, upper-casing and dropping dots
    ("U.A.E." places; "Republic of India" does not). ``None`` means "could not
    place", never "no country" — the caller leaves the column alone.
    """
    key = (label or "").strip().upper().replace(".", "")
    return _LABELS.get(key)


def for_country(country: Optional[str]) -> Optional[Jurisdiction]:
    """The jurisdiction on file for a country code.

    ``None`` covers both "country not set" and "country not supported" — a
    caller that needs to tell them apart (the refusals do) goes through the
    ``*_refusal`` functions, which is why those return the reason rather than
    a boolean.
    """
    code = normalize(country)
    return SUPPORTED.get(code) if code else None


def _refusal(country: Optional[str], statute: str,
             applies: Callable[[Jurisdiction], bool]) -> Optional[str]:
    """Why a statutory screen cannot answer for this country, or ``None``.

    The two refusals are deliberately different sentences: an unset country is
    a fact somebody can record today, an unsupported one is a limit of the
    platform — and telling a reader to "set the country" when the country is
    set and simply not Indian would send them to fix the wrong thing.
    """
    code = normalize(country)
    if code is None:
        return (f"This organization's country is not set, so {statute} cannot "
                f"be applied — a statute is the law of one country, and an "
                f"unknown country is not India. Record the organization's "
                f"country to enable this screen: running a check on a Zoho "
                f"connection fills it from that organization's own profile.")
    jurisdiction = SUPPORTED.get(code)
    if jurisdiction is None:
        return (f"Statutory rules for {code} are not supported — India (IN) is "
                f"the only jurisdiction on file. Answering anyway would apply "
                f"Indian deadlines to a business they do not govern, so this "
                f"screen stays silent instead.")
    if not applies(jurisdiction):
        return f"In {code}, {statute} do not apply, so there is nothing to show."
    return None


def msme_refusal(country: Optional[str]) -> Optional[str]:
    """Why the MSME payment-timing screens cannot answer, or ``None``."""
    return _refusal(country, "the MSME payment-timing rules",
                    lambda j: j.msme_applies)


def withholding_refusal(country: Optional[str]) -> Optional[str]:
    """Why the 194Q withholding screen cannot answer, or ``None``."""
    return _refusal(country, "the purchase-withholding rules",
                    lambda j: j.withholding_applies)


def calendar_refusal(country: Optional[str]) -> Optional[str]:
    """Why a screen folded over the statutory financial year cannot answer.

    The FY calendar is jurisdictional the same way the statutes are: April to
    March is India's year, not a universal one. A screen that buckets money
    into ``FY2026-27`` periods for a tenant those periods do not govern is the
    same confidently-wrong output the statute screens refused — so anything
    reading ``fy_of``/``fy_bounds`` gates here first. Every supported
    jurisdiction has a calendar, so only "not set" and "not supported" refuse.
    """
    return _refusal(country, "the statutory financial-year calendar",
                    lambda j: True)
