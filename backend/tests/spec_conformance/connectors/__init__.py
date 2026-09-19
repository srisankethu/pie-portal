"""One fixture per registered ERP: that system's own records, and its real
source class driven over them.

Each module holds a stub *client* rather than a stub source. The difference is
the point: every translator, every sign convention, every line filter and every
window check in ``ingestion/erp`` runs for real, and only the HTTP call is
replaced. A stub source would test the fixture.

What varies between these modules is a native dress — SuiteQL columns, OData
resources, ``{"value": …}`` wrapping, ``_0`` suffixes, Atom localnames — which
is why they are separate modules and not one table. What does *not* vary is
anything the suite asserts: that is all in ``harness.py`` and reads the
published contract.

**A connector with no fixture would report clean.** ``UNDER_TEST`` is checked
against ``erp.catalog()`` in ``test_conformance.py``, so a seventh connector
registering itself fails this suite until somebody writes its rows. A check
that runs against nothing is the failure this package exists to prevent, and
the registry is the one place it could enter unnoticed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.ingestion import erp

from . import acumatica, dynamics365, netsuite, prophet21, sage100, sagex3


@dataclass(frozen=True)
class UnderTest:
    """One registered connector and the fixture that exercises it."""

    key: str
    build_source: Callable[[], Any]

    @property
    def spec(self):
        """The live registration, looked up rather than held.

        A copy taken at import would be a second statement of what the registry
        says, and the declaration this suite reads off it — whether the ERP
        exposes a system-record timestamp — is exactly the kind of field a copy
        would go stale on.
        """
        return erp.get_spec(self.key)


UNDER_TEST: tuple[UnderTest, ...] = (
    UnderTest("acumatica", acumatica.build_source),
    UnderTest("dynamics365", dynamics365.build_source),
    UnderTest("netsuite", netsuite.build_source),
    UnderTest("prophet21", prophet21.build_source),
    UnderTest("sage100", sage100.build_source),
    UnderTest("sagex3", sagex3.build_source),
)
