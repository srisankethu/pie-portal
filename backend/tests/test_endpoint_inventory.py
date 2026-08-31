"""The endpoint appendix in `docs/user-flows.md`, kept honest by the suite.

`docs/user-flows.md` ends in a table of every HTTP endpoint reachable in the
flows it describes. Nothing checked it, and a hand-maintained inventory of a
surface that changes every week is a document that is wrong shortly after it is
written — this one already was. Two endpoints (`GET /api/v1/data/catalog` and
`POST /api/v1/data/catalog/build`) arrived under it between the day it was
authored and the day it merged, and four rows named a path parameter, `{id}`,
that the routes do not declare; they use `{decision_id}`.

Both directions are pinned here because they rot differently:

* **Mounted but undocumented** is the one that happens by itself. Someone adds a
  router and the appendix silently stops being an inventory. This is the check
  that earns the file.
* **Documented but not mounted** catches a row nobody can act on — a typo, a
  path parameter renamed in the code, an endpoint deleted. That is the class the
  `{decision_id}` rows were in, and reading the table would never have found it.

The comparison is against `app.openapi()` rather than a walk of `app.routes`,
because the API is a sub-application and the schema is FastAPI's own answer to
"what is the surface". The appendix is deliberately *smaller* than the mounted
route table — it lists flows, not plumbing — so the exclusions below are stated
rather than inferred, and a new exclusion has to be argued for in a diff.

This is a documentation test and it is meant to be cheap to satisfy: when it
fails, the fix is one row in a table, not a design decision.
"""
from __future__ import annotations

import pathlib
import re

from app.main import app

REPO = pathlib.Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "user-flows.md"

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

#: A row in the appendix table: `| GET | `/api/v1/thing` | guard | purpose |`.
_ROW = re.compile(rf"^\|\s*({'|'.join(_METHODS)})\s*\|\s*`([^`]+)`")

#: Real routes the appendix documents that never appear in the OpenAPI schema.
#:
#: The Zoho OAuth redirect target carries ``include_in_schema=False`` — it is a
#: browser landing point, not an API a caller writes against — but it is very
#: much a flow a person walks, so the document is right to carry it and this
#: check has to know that. Anything added here needs the same two sentences:
#: why it is hidden, and why it is still a flow.
HIDDEN_BUT_REAL = {
    ("GET", "/api/v1/connections/zoho/callback"),
}


def _documented() -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2))
            for m in (_ROW.match(line) for line in DOC.read_text().splitlines())
            if m}


def _mounted() -> set[tuple[str, str]]:
    return {(method.upper(), path)
            for path, operations in app.openapi()["paths"].items()
            for method in operations
            if method.upper() in _METHODS}


def test_the_appendix_is_not_empty_and_the_parser_still_reads_it():
    """A screen that finds nothing reports clean, so this asserts the screen.

    If the table's formatting changes and `_ROW` stops matching, both checks
    below pass vacuously and the inventory silently stops being verified — the
    exact failure mode `verify.sh` guards against for the §1 literal screen.
    """
    documented = _documented()
    assert len(documented) > 150, (
        f"only {len(documented)} rows parsed out of the appendix — the table "
        f"format probably changed and this file is no longer reading it")


def test_every_mounted_endpoint_appears_in_the_flow_appendix():
    """The direction that rots on its own: a router gains an endpoint and the
    appendix quietly stops being an inventory."""
    missing = sorted(_mounted() - _documented())
    assert not missing, (
        "these endpoints are mounted but absent from the appendix in "
        "docs/user-flows.md — add a row for each:\n" +
        "\n".join(f"  | {m} | `{p}` |" for m, p in missing))


def test_every_appendix_row_names_a_route_that_exists():
    """The direction reading the table cannot catch: a row nobody can act on.

    Four rows wrote `/api/v1/decisions/{id}` where the routes declare
    `{decision_id}`. Plausible on the page, wrong against the code.
    """
    stale = sorted(_documented() - _mounted() - HIDDEN_BUT_REAL)
    assert not stale, (
        "the appendix in docs/user-flows.md names endpoints that are not "
        "mounted — check the path and the parameter names against the router, "
        "or delete the row:\n" +
        "\n".join(f"  | {m} | `{p}` |" for m, p in stale))
