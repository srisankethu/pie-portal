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


def _capped(value: Decimal, full: Decimal) -> Decimal:
    if full <= _ZERO:
        return _ZERO
    return min(Decimal("1"), max(_ZERO, value / full))


def compute(cfg: Config, attrs: CustomerAttributes,
            tenure_months: int) -> RSIResult:
    w = cfg.get("rsi", "weights")
    parts: dict[str, Decimal] = {
        "tenure": _capped(Decimal(tenure_months),
                          Decimal(cfg.int_("rsi", "tenure_months_full"))),
        "regularity": _capped(Decimal(attrs.active_months_12),
                              Decimal(cfg.int_("rsi", "regularity_months_full"))),
        "breadth": _capped(Decimal(attrs.families_bought),
                           Decimal(cfg.int_("rsi", "breadth_families_full"))),
        "share_of_wallet": _capped(attrs.share_of_wallet_est, Decimal("1")),
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
    )


def band_for(cfg: Config, score: int) -> dict:
    for band in cfg.get("rsi", "bands"):
        if band["from"] <= score <= band["to"]:
            return band
    raise ValueError(f"no RSI band covers {score}")
