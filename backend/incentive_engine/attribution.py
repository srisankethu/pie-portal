"""Assist credit, handover tail, dormancy, and group-level resolution (Q7).

**Assist credit is COMPANY-FUNDED.** That single detail is what makes knowledge
sharing Pareto-improving: helping a colleague costs the account owner nothing,
so nobody has a reason to refuse help or to hoard a vendor contact. Deducting
the assist from the owner would turn every collaboration into a negotiation.

**Accounts are leased, not owned.** Zero CAF and no logged contact for two
consecutive quarters and the account returns to the pool. Hoarding a dormant
account to block a colleague stops being free.

**Handover pays the outgoing salesperson.** 25% of the account's CAF for two
quarters, conditional on a documented handover. Without it, the rational move
before leaving an account is to strip it.

**Q7 — group resolution.** RSI, baselines and concentration are computed on the
customer GROUP (GSTIN-resolved), never the ledger. A customer buying from SLS
and UPS is one relationship; scoring the ledgers separately counts the same
tenure twice and makes splitting a customer into three ledger entities a
strategy that pays. Points still attribute to the salesperson on the invoice's
own entity — the relationship is shared, the sale is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import Config

_ZERO = Decimal("0")


@dataclass(frozen=True)
class AssistCredit:
    assisting_id: str
    account_owner_id: str
    customer_group_id: str
    points: Decimal
    company_funded: bool


@dataclass(frozen=True)
class HandoverTail:
    outgoing_id: str
    customer_group_id: str
    quarters_remaining: int
    points: Decimal
    documented: bool


def assist(cfg: Config, *, assisting_id: str, account_owner_id: str,
           customer_group_id: str, account_points: Decimal) -> AssistCredit:
    share = cfg.dec("attribution", "assist_share")
    return AssistCredit(
        assisting_id=assisting_id, account_owner_id=account_owner_id,
        customer_group_id=customer_group_id,
        points=account_points * share,
        company_funded=bool(cfg.get("attribution", "assist_company_funded")))


def handover(cfg: Config, *, outgoing_id: str, customer_group_id: str,
             account_points: Decimal, quarters_elapsed: int,
             documented: bool) -> HandoverTail:
    """No documentation, no tail. The tail is payment for a clean handover."""
    total_quarters = cfg.int_("attribution", "handover_tail_quarters")
    remaining = max(0, total_quarters - quarters_elapsed)
    share = cfg.dec("attribution", "handover_tail_share")
    points = account_points * share if (documented and remaining > 0) else _ZERO
    return HandoverTail(outgoing_id, customer_group_id, remaining, points,
                        documented)


def is_dormant(cfg: Config, quarters_without_activity: int) -> bool:
    return quarters_without_activity >= cfg.int_(
        "attribution", "dormancy_quarters_to_pool")


def resolve_group(customer_id: str, mapping: dict[str, str]) -> str:
    """Ledger id -> group id. An unmapped ledger is its own group.

    Falling back to the ledger id rather than raising is deliberate: a customer
    the identity layer has not linked yet must still earn points, and the
    failure mode of raising here would be a salesperson unpaid because
    somebody had not finished a master-data task.
    """
    return mapping.get(customer_id, customer_id)


def concentration_shares(points_by_group: dict[str, Decimal]) -> list[Decimal]:
    total = sum(points_by_group.values(), _ZERO)
    if total <= _ZERO:
        return []
    return sorted((v / total for v in points_by_group.values()), reverse=True)
