"""Aged-stock recovery — a separate pool, and the causation control.

**Q1(a): aged lines do NOT run through CAF.** Beyond roughly a year the aged
floor sits below cost, so a cash-losing clearance would produce positive CAF
and a salesperson learning the mechanism would learn the wrong lesson from it:
that "above floor" sometimes means "below cost". CAF's meaning has to stay
exactly one thing — above the policy margin — or the price discipline it
carries stops being legible. Recovery is a different economic act, converting a
sunk asset into cash, and it gets its own clearly-labelled instrument.

**Q1(c): the bounty is open to anyone in the company.** Back office spots the
match at allocation; the salesperson carries the relationship cost of a bad
substitution. Split 40/60 proposer/closer, because the closer bears the risk
that the customer is unhappy and the proposer bears none. Either a salesperson
or a written customer acceptance is required — a substitution nobody agreed to
is a dispute waiting to happen, and the clawback window is there for the ones
that slip through anyway.

**Q1(d) and Q3(b) are ONE rule, not two.** Whoever caused the stock to exist
earns a quarter of the normal bounty on that specific stock, and the same
attribution governs whether commitment-linked vendor yield ever releases.
Written as two rules they would drift, and the drift would reopen the highest-
return exploit in the whole design: create dead stock, then get paid to clear
it.

The causer multiplier is 0.25 and not zero on purpose. At zero the causer's
best move is to hide the stock and let nobody clear it, and hidden dead stock
is strictly worse for the company than cheaply-cleared dead stock.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .caf import LineCAF
from .config import Config

_ZERO = Decimal("0")


@dataclass(frozen=True)
class RecoveryBounty:
    invoice_id: str
    item_id: str
    age_days: int
    recovered_above_writedown: Decimal
    base_rate: Decimal
    applied_rate: Decimal
    proposer_id: Optional[str]
    closer_id: str
    proposer_bounty: Decimal
    closer_bounty: Decimal
    causer_reduced: bool
    accepted: bool

    @property
    def total(self) -> Decimal:
        return self.proposer_bounty + self.closer_bounty


def rate_for_age(cfg: Config, age_days: int) -> Decimal:
    """Declines with age so recovery is front-loaded.

    A flat rate would make waiting free; a rate that rises with age would pay
    people to let stock rot. Declining is the only shape that creates urgency
    without creating a reason to delay.
    """
    for band in cfg.get("recovery", "bounty_rate_by_age"):
        lo, hi = band["from_days"], band["to_days"]
        if age_days >= lo and (hi is None or age_days <= hi):
            return Decimal(str(band["rate"]))
    return _ZERO


def bounty(cfg: Config, line: LineCAF, *, closer_id: str,
           proposer_id: Optional[str] = None,
           accepted: bool = False) -> Optional[RecoveryBounty]:
    """Bounty on one cleared aged line, or None if it does not qualify.

    ``accepted`` is the salesperson-or-written-customer acceptance. Without it
    there is no bounty at all: an unaccepted substitution is somebody else
    spending the salesperson's relationship.
    """
    src = line.line
    if not line.is_aged or src.stock_age_days is None:
        return None
    if not accepted:
        return None

    # Recovered value is measured against the aged write-down floor, which is
    # what the line's floor_price already is by the time it reaches here.
    recovered = src.qty * (src.unit_price_net - src.floor_price)
    if recovered <= _ZERO:
        return None

    base_rate = rate_for_age(cfg, src.stock_age_days)
    causer = src.aged_causer_id
    reduced = causer is not None and causer in {closer_id, proposer_id}
    applied = (base_rate * cfg.dec("recovery", "causer_bounty_multiplier")
               if reduced else base_rate)

    pool = recovered * applied
    p_share = cfg.dec("recovery", "proposer_share")
    c_share = cfg.dec("recovery", "closer_share")
    if proposer_id is None or proposer_id == closer_id:
        # Nobody separate proposed it; the closer did the whole job.
        proposer_bounty, closer_bounty = _ZERO, pool
    else:
        proposer_bounty, closer_bounty = pool * p_share, pool * c_share

    return RecoveryBounty(
        invoice_id=src.invoice_id, item_id=src.item_id,
        age_days=src.stock_age_days, recovered_above_writedown=recovered,
        base_rate=base_rate, applied_rate=applied,
        proposer_id=proposer_id, closer_id=closer_id,
        proposer_bounty=proposer_bounty, closer_bounty=closer_bounty,
        causer_reduced=reduced, accepted=accepted)
