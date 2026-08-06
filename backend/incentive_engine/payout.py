"""The master formula, the 70/30 split, and the Relationship Bank.

    Payout = r x Q x SUM_c [ w_base(RSI_c) x B_c^collected
                           + w_inc(RSI_c) x IncCAF_c^collected ]

r is per entity. SLS at roughly 19 crore and 4U at roughly 37 lakh cannot share
a rate — the same r would either bankrupt one or pay nothing at the other. The
shipped values are zero pending the shadow run (Q4), because a calibration
nobody has performed must not silently pay whatever a developer typed.

**Gates withhold; they do not destroy.** A failed period is computed in full,
reported in full, and held. That is what makes cure the rational response.

**70/30.** Thirty per cent into the Relationship Bank for twelve months makes
the salesperson a bondholder in their own customer portfolio: churn, disputes
and bad debt reduce a balance they can already see. Deferral does the work that
a retention clause cannot.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Sequence

from .config import Config
from .gates import GateStatus
from .models import CustomerPoints, SalespersonPayout

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class UncalibratedRate(ValueError):
    """The entity's rate has not been solved yet.

    Distinct from a missing entity, and distinct from a rate that is genuinely
    zero, because the three need different answers from a person.
    """


def rate_for_entity(cfg: Config, entity_id: str) -> Decimal:
    """The share rate for one entity, or a refusal.

    **Zero is not a rate.** The parameter block ships r at 0.0000 pending the
    Phase-1 shadow run, and the comment beside it says an unrun calibration
    must not silently pay whatever a developer typed. Nothing enforced that:
    a payout run against the shipped block would have paid every salesperson
    exactly nothing and looked, from every screen and every log line, like the
    mechanism working correctly.

    A zero rate is therefore refused rather than applied. If an entity is ever
    genuinely meant to pay nothing, that is a decision somebody makes — set
    ``r_by_entity`` to ``null`` and the refusal below says so by name, which is
    a sentence in a config file rather than a silence nobody can see.
    """
    table = cfg.get("payout", "r_by_entity")
    if entity_id not in table:
        raise ValueError(
            f"no incentive rate calibrated for entity {entity_id!r}. r is "
            "per-entity by design and must be solved from that entity's own "
            "shadow-run CAF, never inherited from another.")
    raw = table[entity_id]
    if raw is None:
        raise UncalibratedRate(
            f"entity {entity_id!r} is deliberately set to pay nothing "
            "(r_by_entity: null). If that is wrong, calibrate it.")
    rate = Decimal(str(raw))
    if rate == 0:
        raise UncalibratedRate(
            f"the incentive rate for {entity_id!r} is still 0.0000 — the "
            "placeholder this parameter block ships with, pending the Phase-1 "
            "shadow run. Running the payout on it would pay everybody nothing "
            "and look exactly like a working month.\n\n"
            "Solve r from that entity's own shadow-run CAF and set it in "
            "incentive_engine/config/parameters.yaml, or set it to null to "
            "state on purpose that this entity pays no share.")
    if rate < 0:
        raise ValueError(
            f"the incentive rate for {entity_id!r} is negative ({rate}). A "
            "share of contribution cannot be negative; a clawback is a "
            "separate mechanism.")
    return rate


def apply_accelerator(cfg: Config, r: Decimal, points: Decimal,
                      target_points: Optional[Decimal]) -> tuple[Decimal, Decimal]:
    """Returns (base portion, accelerated portion) of the payout.

    The accelerator applies only to the EXCESS above target, which is why it
    creates no threshold worth gaming: crossing it changes the rate on the next
    rupee, never on the rupees already earned. A whole-balance accelerator
    would make the boundary worth hoarding orders around.
    """
    if target_points is None or target_points <= _ZERO:
        return points * r, _ZERO
    threshold = target_points * cfg.dec("payout", "accelerator_above_target_pct")
    if points <= threshold:
        return points * r, _ZERO
    excess = points - threshold
    boosted = r * cfg.dec("payout", "accelerator_rate_multiple")
    return threshold * r, excess * boosted


@dataclass(frozen=True)
class PayoutInputs:
    salesperson_id: str
    entity_id: str
    period: str
    customer_points: Sequence[CustomerPoints]
    q: Decimal
    gates: GateStatus
    recovery_bounty: Decimal = _ZERO
    clawbacks: Decimal = _ZERO
    target_points: Optional[Decimal] = None


def compute(cfg: Config, inp: PayoutInputs) -> SalespersonPayout:
    total_points = sum((cp.weighted_points for cp in inp.customer_points), _ZERO)
    r = rate_for_entity(cfg, inp.entity_id)

    base, accelerated = apply_accelerator(cfg, r, total_points, inp.target_points)
    # Q multiplies the CAF-derived payout only. The recovery bounty is a
    # separate pool and is deliberately NOT scaled by portfolio health: it is
    # payment for converting a sunk asset to cash, and it would be perverse to
    # pay less for clearing dead stock because the book is concentrated.
    gross = _money((base + accelerated) * inp.q + inp.recovery_bounty)
    gross = max(_ZERO, gross - inp.clawbacks)

    cash_share = cfg.dec("payout", "cash_share")
    bank_share = cfg.dec("payout", "relationship_bank_share")
    if cash_share + bank_share != _ONE:
        # Checked rather than assumed. The bank is taken as the remainder below
        # so the two halves always tie back to the gross exactly; if the config
        # said 0.60/0.30 the remainder would silently be 0.40 and the published
        # split would be a lie.
        raise ValueError(
            f"payout split must sum to 1: cash {cash_share} + bank {bank_share}")

    if not inp.gates.passed:
        # Computed in full, reported in full, paid as nothing until cured.
        cash, bank = _ZERO, _ZERO
    else:
        cash = _money(gross * cash_share)
        bank = _money(gross - cash)     # remainder, so the two always tie back

    audit = tuple(
        {"customer_id": cp.customer_id, "rsi": cp.rsi, "band": cp.rsi_band,
         "baseline_caf": str(cp.baseline_caf),
         "incremental_caf": str(cp.incremental_caf),
         "weighted_points": str(cp.weighted_points),
         "recovery_bounty": str(cp.recovery_bounty)}
        for cp in inp.customer_points)

    return SalespersonPayout(
        salesperson_id=inp.salesperson_id, period=inp.period,
        entity_id=inp.entity_id,
        total_weighted_points=total_points,
        q_multiplier=inp.q,
        gate_status=inp.gates.to_dict(),
        payout_gross=gross,
        payout_cash_70=cash,
        relationship_bank_30=bank,
        clawbacks_applied=inp.clawbacks,
        config_version=cfg.version,
        audit=audit + ({"r": str(r), "base": str(_money(base)),
                        "accelerated": str(_money(accelerated)),
                        "q": str(inp.q),
                        "recovery_bounty": str(inp.recovery_bounty),
                        "gates_passed": inp.gates.passed},),
    )
