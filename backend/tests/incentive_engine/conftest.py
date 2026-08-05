"""Fixtures for the incentive engine.

The config used in tests is the *shipped* one with r set — not a separate test
config. A test suite that invents its own parameters proves the arithmetic and
nothing about the file that will actually run.
"""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal as D

import pytest

from incentive_engine.config import load_config
from incentive_engine.models import InvoiceLine

AS_OF = date(2026, 7, 1)


@pytest.fixture()
def cfg():
    c = load_config(as_of=AS_OF)
    # r is deliberately zero in the shipped file until the shadow run. Tests
    # set the brief's 8% so the worked baselines are reproducible.
    raw = copy.deepcopy(c.raw)
    raw["payout"]["r_by_entity"] = {"SLS": "0.08", "4U": "0.08", "UPS": "0.08"}
    return type(c)(version=c.version, effective_from=c.effective_from,
                   effective_to=c.effective_to, raw=raw)


@pytest.fixture()
def line():
    def _make(**kw):
        base = dict(entity_id="SLS", invoice_id="INV-1",
                    invoice_date=date(2026, 6, 1), customer_id="c1",
                    customer_group_id="g1", item_id="CNMG-120408",
                    item_family="inserts", brand="Kennametal",
                    qty=D("500"), unit_price_net=D("340"),
                    floor_price=D("280"), salesperson_id="s1")
        base.update(kw)
        return InvoiceLine(**base)
    return _make
