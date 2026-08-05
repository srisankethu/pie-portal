"""Provisional hold on new accounts, released retrospectively.

A new account earns at 0.5x until it has recorded at least three invoices
across at least two quarters, at which point the withheld half is released in
full. A genuine new customer therefore costs the salesperson nothing — the
money arrives late, not never. A one-shot or fabricated logo earns half and the
other half never releases.

This is the entire defence against exploits 7-10, and it needs no per-logo
bonus to police, because there is no per-logo bonus in the mechanism at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from .config import Config

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class ConfidenceResult:
    rate: Decimal
    released: bool
    invoices: int
    quarters: int
    held_back: Decimal


def _quarter(day: date) -> tuple[int, int]:
    return (day.year, (day.month - 1) // 3 + 1)


def assess(cfg: Config, invoice_dates: Sequence[date],
           points: Decimal) -> ConfidenceResult:
    need_invoices = cfg.int_("bayesian", "release_min_invoices")
    need_quarters = cfg.int_("bayesian", "release_min_quarters")
    rate = cfg.dec("bayesian", "provisional_rate")

    invoices = len(invoice_dates)
    quarters = len({_quarter(d) for d in invoice_dates})
    released = invoices >= need_invoices and quarters >= need_quarters
    return ConfidenceResult(
        rate=_ONE if released else rate,
        released=released,
        invoices=invoices,
        quarters=quarters,
        held_back=_ZERO if released else points * (_ONE - rate),
    )
