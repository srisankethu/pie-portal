"""w_base / w_inc, and the retention gate.

Three features of the band table are deliberate and load-bearing:

w_base DECLINES with maturity, because holding a mature account is less
effortful than winning a new one — but never approaches zero, so an anchor
always stays worth defending.

w_inc RISES AGAIN at Anchor. Growing share of wallet inside a large established
account is genuinely hard. Without the rise, a salesperson concludes anchors
are dead money and goes hunting, which is exploit 11 returning through the
front door.

The retention gate is collective, not per-account. An Established or Anchor
account allowed to fall more than 20% year on year without approved cause
collapses ALL of that salesperson's w_inc to 1.00 for the period — hunting
cannot be funded by letting the base rot, and making the penalty local to the
neglected account would leave the trade profitable.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from .baseline import CustomerBaseline
from .config import Config
from .rsi import RSIResult

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class RetentionCheck:
    tripped: bool
    offenders: tuple[str, ...]
    w_inc_override: Decimal


def retention_gate(cfg: Config,
                   pairs: Sequence[tuple[RSIResult, CustomerBaseline]],
                   approved_causes: Iterable[str] = ()) -> RetentionCheck:
    drop = cfg.dec("rsi", "retention_gate_drop_pct")
    approved = set(approved_causes)
    offenders = []
    for rsi_result, base in pairs:
        if rsi_result.retention_gate != "hard":
            continue
        if base.customer_group_id in approved:
            # Exogenous cause — plant closure, insolvency, allocated shortage.
            # Punishing an outcome the agent does not control produces
            # concealment, not effort.
            continue
        if base.baseline_caf > _ZERO and base.yoy_change < -drop:
            offenders.append(base.customer_group_id)
    return RetentionCheck(
        tripped=bool(offenders),
        offenders=tuple(sorted(offenders)),
        w_inc_override=cfg.dec("rsi", "retention_gate_w_inc"),
    )


def weighted_points(cfg: Config, rsi_result: RSIResult,
                    base: CustomerBaseline,
                    retention: RetentionCheck) -> Decimal:
    """w_base x B + w_inc x IncCAF, for one customer group."""
    w_inc = retention.w_inc_override if retention.tripped else rsi_result.w_inc
    # The New band has no w_base at all — every rupee from a brand-new account
    # is incremental by definition, and giving it a base multiplier as well
    # would pay twice for the same rupee.
    w_base = rsi_result.w_base if rsi_result.w_base is not None else _ZERO
    countable_base = base.baseline_caf if rsi_result.w_base is not None else _ZERO
    if rsi_result.w_base is None:
        return w_inc * base.current_caf
    return w_base * countable_base + w_inc * base.incremental_caf
