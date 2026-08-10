"""Relationship Strength Index, 0-100, per customer group.

Computed at GROUP level (Q7). A customer buying from SLS and UPS is one
relationship; scoring the two ledgers separately would count the same tenure
twice and make ledger-splitting an exploit that pays.

Contact depth carries a full 15 points deliberately. It is the only defence
against single-threading — a salesperson who is the sole contact on a large
account has made themselves irreplaceable at the company's expense — and the
same measure appears again in Q at 20%, because the behaviour is worth
suppressing twice.

RSI advances on tenure automatically, whatever the volume. That is what stops a
salesperson holding an account in the high-w_inc "New" band by underselling it.

**Share of wallet is weighted zero, and the reason is worth keeping.** It scored
15 of 100 here — inside the index that decides w_base and w_inc — from a number
the salesperson supplies about their own account. That is a self-report in a pay
loop, and it survived review because the field looked like every other input on
`CustomerAttributes`. It was also never populated: the only constructions of
that type were in tests, so the weight was live while the value was not.

The component still computes and still appears in `components`, at weight zero.
A component deleted outright is one nobody can find the argument about later.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .config import Config
from .models import CustomerAttributes

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class RSIResult:
    score: int
    band: str
    components: dict
    w_base: Optional[Decimal]
    w_inc: Decimal
    toolkit_cap_pct: Decimal
    retention_gate: str
    #: Components with nothing behind them, named the way ``insight/bonds.py``
    #: names its missing facets — so a reader knows which question the score
    #: did not answer rather than reading a zero as a measurement.
    #:
    #: The score is **not** renormalised over the rest. Bonds renormalises
    #: because a missing facet there is a gap in Zoho; here the only unmeasured
    #: component carries zero weight, and renormalising would make an RSI move
    #: — and a payout with it — because somebody typed a number into a form.
    unmeasured: tuple[str, ...] = ()


def _capped(value: Decimal, full: Decimal) -> Decimal:
    if full <= _ZERO:
        return _ZERO
    return min(Decimal("1"), max(_ZERO, value / full))


def compute(cfg: Config, attrs: CustomerAttributes,
            tenure_months: int) -> RSIResult:
    w = cfg.get("rsi", "weights")
    declared = attrs.share_of_wallet_declared
    parts: dict[str, Decimal] = {
        "tenure": _capped(Decimal(tenure_months),
                          Decimal(cfg.int_("rsi", "tenure_months_full"))),
        "regularity": _capped(Decimal(attrs.active_months_12),
                              Decimal(cfg.int_("rsi", "regularity_months_full"))),
        "breadth": _capped(Decimal(attrs.families_bought),
                           Decimal(cfg.int_("rsi", "breadth_families_full"))),
        # Undeclared contributes nothing, and says so through ``unmeasured``
        # rather than through a zero that reads like a measured 0% share. At
        # weight zero the arithmetic is the same either way; the distinction is
        # for the person reading the components, who cannot otherwise tell
        # "nobody has said" from "somebody said none".
        "share_of_wallet": (_capped(declared.share, Decimal("1"))
                            if declared is not None else _ZERO),
        # Inverted: 100 at zero days beyond terms, 0 at the stated ceiling.
        "payment_behaviour": Decimal("1") - _capped(
            max(_ZERO, attrs.avg_days_beyond_terms_12),
            Decimal(cfg.int_("rsi", "days_beyond_terms_zero"))),
        "contact_depth": _capped(Decimal(attrs.live_contacts),
                                 Decimal(cfg.int_("rsi", "contacts_full"))),
    }
    score = sum(parts[k] * Decimal(w[k]) for k in parts)
    rounded = int(score.quantize(Decimal("1")))
    rounded = max(0, min(100, rounded))
    band = band_for(cfg, rounded)
    return RSIResult(
        score=rounded,
        band=band["name"],
        components={k: str((v * Decimal(w[k])).quantize(Decimal("0.1")))
                    for k, v in parts.items()},
        w_base=(Decimal(str(band["w_base"])) if band["w_base"] is not None else None),
        w_inc=Decimal(str(band["w_inc"])),
        toolkit_cap_pct=Decimal(str(band["toolkit_cap_pct"])),
        retention_gate=str(band["retention_gate"]),
        unmeasured=(("share_of_wallet",) if declared is None else ()),
    )


def band_for(cfg: Config, score: int) -> dict:
    for band in cfg.get("rsi", "bands"):
        if band["from"] <= score <= band["to"]:
            return band
    raise ValueError(f"no RSI band covers {score}")
