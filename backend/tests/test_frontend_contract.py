"""The API's real responses, checked against the types the browser is written to.

`frontend/src/types.ts` is hand-written, and the backend declares a response
model on 5 of its 113 two-hundred responses — so FastAPI's OpenAPI document is
too thin to generate types from, and `tsc -b` only ever checks the frontend
against *its own* description of the server. The two halves are kept in
agreement by hand across 91 endpoints, and until this file nothing compared
them. Rename a field in a router and every check in the gate stays green: the
backend suite asserts the new name, the frontend compiles against the old one,
and the screen breaks in a browser.

What this asserts, in both directions:

  · every non-optional field the interface declares is present in the response
  · every key the response carries is declared in the interface
  · values match the declared type, recursively through nested interfaces

The second direction is the one that catches a *rename*: without it, a field
that changed name looks like a missing optional field on one side and an
unremarkable extra on the other.

This is a contract check, not a wiring check. It proves the shapes agree; it
cannot prove a screen is pointed at the endpoint it should be. That is what
`frontend/e2e/role-scoping.spec.ts` covers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

# The quote endpoints, their seeded database and the two role headers already
# exist next door. A third copy of that fixture is exactly the responsibility
# duplication CLAUDE.md §2 asks to prevent, so this imports them.
from test_quote_flow import client, mgmt_hdr, sales_hdr  # noqa: F401

TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts"

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_INTERFACE_HEAD = re.compile(r"export interface (\w+)\s*\{")
_FIELD = re.compile(r"^\s*(\w+)(\??)\s*:\s*(.+)$", re.S)
_OPENERS, _CLOSERS = "{[(<", "}])>"

_PRIMITIVES: dict[str, type | tuple[type, ...]] = {
    "string": str, "number": (int, float), "boolean": bool,
}
#: Types this checker deliberately does not police. `unknown` and `any` say so
#: themselves; a `Record<…>` value is checked as far as being an object.
_OPAQUE = {"unknown", "any", "object"}


@dataclass(frozen=True)
class Field:
    optional: bool
    ts: str


@dataclass(frozen=True)
class Interface:
    fields: dict[str, Field]
    unparsed: list[str]


def _balanced_body(source: str, start: int) -> tuple[str, int]:
    """The text between `start` (just past a `{`) and its matching `}`."""
    depth, i = 1, start
    while i < len(source) and depth:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[start:i - 1], i


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep`, ignoring any that sits inside brackets of any kind.

    Needed twice over: fields are separated by `;` but an inline object type
    contains its own, and a union is separated by `|` but `Record<string, A | B>`
    is a single branch.
    """
    depth, current, out = 0, "", []
    for ch in text:
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
        if ch == sep and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return out


def _parse_body(body: str) -> Interface:
    fields: dict[str, Field] = {}
    unparsed: list[str] = []
    for chunk in _split_top_level(body, ";"):
        if not chunk.strip():
            continue
        m = _FIELD.match(chunk)
        if m:
            fields[m.group(1)] = Field(bool(m.group(2)), " ".join(m.group(3).split()))
        else:
            unparsed.append(" ".join(chunk.split()))
    return Interface(fields, unparsed)


def _parse(source: str) -> dict[str, Interface]:
    source = _COMMENTS.sub("", source)
    out: dict[str, Interface] = {}
    for m in _INTERFACE_HEAD.finditer(source):
        body, _ = _balanced_body(source, m.end())
        out[m.group(1)] = _parse_body(body)
    return out


@pytest.fixture(scope="module")
def types() -> dict[str, Interface]:
    assert TYPES_TS.exists(), f"the browser's type file is missing: {TYPES_TS}"
    return _parse(TYPES_TS.read_text())


def _is_number(v) -> bool:
    # bool is an int in Python and is not a TypeScript number.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check(value, ts: str, ifaces: dict[str, Interface], path: str) -> list[str]:
    """Problems found comparing one value against one declared type."""
    branches = [b.strip() for b in _split_union(ts)]
    best: list[str] | None = None
    for branch in branches:
        problems = _check_one(value, branch, ifaces, path)
        if not problems:
            return []
        # Report the most specific failure rather than every branch's.
        if best is None or len(problems) < len(best):
            best = problems
    return best or [f"{path}: no branch of `{ts}` matches {value!r}"]


def _split_union(ts: str) -> list[str]:
    """Split on top-level `|` only — `Record<string, A | B>` is one branch."""
    return _split_top_level(ts, "|")


def _check_one(value, ts: str, ifaces: dict[str, Interface], path: str) -> list[str]:
    ts = ts.strip()
    if ts in _OPAQUE or ts.startswith("Record<"):
        if ts.startswith("Record<") and not isinstance(value, dict):
            return [f"{path}: declared {ts}, got {type(value).__name__}"]
        return []
    if ts == "null":
        return [] if value is None else [f"{path}: declared null, got {value!r}"]
    if ts in _PRIMITIVES:
        ok = _is_number(value) if ts == "number" else isinstance(value, _PRIMITIVES[ts])
        return [] if ok else [f"{path}: declared {ts}, got {type(value).__name__}"]
    if ts.startswith('"') and ts.endswith('"'):        # a string literal
        want = ts[1:-1]
        return [] if value == want else [f"{path}: declared {ts}, got {value!r}"]
    if ts.endswith("[]"):
        if not isinstance(value, list):
            return [f"{path}: declared {ts}, got {type(value).__name__}"]
        problems: list[str] = []
        for i, item in enumerate(value):
            problems += _check(item, ts[:-2], ifaces, f"{path}[{i}]")
        return problems
    if ts.startswith("{") and ts.endswith("}"):        # an inline object type
        inline = _parse_body(ts[1:-1])
        assert not inline.unparsed, f"{path}: unreadable inline type {ts}"
        return check_object(value, path or "<inline>", {**ifaces, path or "<inline>": inline}, path)
    if ts in ifaces:
        return check_object(value, ts, ifaces, path)
    # A type alias (`export type Severity = …`) or an import. Those carry no
    # field list to compare against, so there is nothing to check here — the
    # object-level both-directions check above is what catches drift.
    return []


def check_object(payload, iface: str, ifaces: dict[str, Interface],
                 path: str = "") -> list[str]:
    """Compare one JSON object against one interface, both directions."""
    spec = ifaces[iface]
    if not isinstance(payload, dict):
        return [f"{path or iface}: declared {iface}, got {type(payload).__name__}"]

    problems: list[str] = []
    for name, field in spec.fields.items():
        where = f"{path}.{name}" if path else f"{iface}.{name}"
        if name not in payload:
            if not field.optional:
                problems.append(
                    f"{where}: declared by the browser, absent from the response")
            continue
        problems += _check(payload[name], field.ts, ifaces, where)

    for key in payload:
        if key not in spec.fields:
            problems.append(
                f"{path or iface}.{key}: sent by the server, undeclared in "
                f"types.ts — the browser cannot be relying on it, and a renamed "
                f"field looks exactly like this")
    return problems


def assert_matches(payload, iface: str, ifaces: dict[str, Interface]) -> None:
    problems = check_object(payload, iface, ifaces)
    assert not problems, (
        f"the API and frontend/src/types.ts disagree about {iface}:\n  "
        + "\n  ".join(problems))


# ── the checker must not be able to pass by understanding nothing ───────────
def test_the_type_file_is_fully_understood(types):
    """A parser that silently skips a field turns every assertion below into a
    tautology. Anything it could not read is named here rather than dropped."""
    unreadable = {name: i.unparsed for name, i in types.items() if i.unparsed}
    assert not unreadable, f"unparsed declarations in types.ts: {unreadable}"
    assert len(types) >= 15, f"only {len(types)} interfaces parsed — parser broken?"
    # Spot-check the shapes these tests lean on, so a regex that quietly matches
    # nothing cannot look like a clean run.
    assert len(types["Line"].fields) == 29
    assert len(types["Economics"].fields) == 6
    assert types["Line"].fields["supplyCode"].ts == "string | null"
    assert types["Quote"].fields["marginFloor"].optional is True


def test_the_checker_reports_a_drift_it_should_catch(types):
    """The checker's own failure modes, since every test below is an assertion
    that it found *nothing*."""
    good = {"kind": "ready", "label": "ready"}
    assert check_object(good, "LineStatus", types) == []
    # renamed field: caught from both sides at once
    assert len(check_object({"kind": "ready", "labell": "x"}, "LineStatus", types)) == 2
    # wrong primitive
    assert check_object({"kind": "ready", "label": 7}, "LineStatus", types)
    # a value outside the declared literal union
    assert check_object({"kind": "nope", "label": "x"}, "LineStatus", types)


# ── the contract, against real responses ────────────────────────────────────
def test_a_new_quote_matches_the_quote_interface(client, mgmt_hdr, types):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti Engineering"},
                    headers=mgmt_hdr)
    assert q.status_code == 200, q.text
    assert_matches(q.json(), "Quote", types)


def test_a_fetched_quote_matches_the_quote_interface(client, mgmt_hdr, types):
    qid = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                      headers=mgmt_hdr).json()["id"]
    got = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr)
    assert got.status_code == 200, got.text
    assert_matches(got.json(), "Quote", types)


@pytest.mark.requires_pie
def test_an_intake_response_matches_line_and_economics(client, mgmt_hdr, types):
    """The whole grid in one payload: Line, LineStatus, LineFlags, Candidate and
    the manager's Economics, each checked field by field."""
    qid = client.post("/api/v1/quotes", json={"customer": "Pitti Engineering"},
                      headers=mgmt_hdr).json()["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 20"}, headers=mgmt_hdr)
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["lines"], "no lines to check the contract against"
    assert_matches(body, "Quote", types)
    assert any(ln.get("economics") for ln in body["lines"]), (
        "a manager's lines must carry economics, or this asserts nothing")


@pytest.mark.requires_pie
def test_a_salespersons_payload_matches_the_same_interface_without_economics(
        client, sales_hdr, types):
    """The role-scoped response is the same contract with the restricted fields
    *absent* — which is only expressible because `economics` and `marginFloor`
    are declared optional. A salesperson's payload passing the same check as a
    manager's is the point: the shape does not fork by role, the fields do.
    """
    qid = client.post("/api/v1/quotes", json={"customer": "Pitti Engineering"},
                      headers=sales_hdr).json()["id"]
    body = client.post(f"/api/v1/quotes/{qid}/intake",
                       json={"text": "2001174, 20"}, headers=sales_hdr).json()
    assert_matches(body, "Quote", types)
    assert "marginFloor" not in body
    assert body["lines"] and all("economics" not in ln for ln in body["lines"])


# ── constants the browser matches on ─────────────────────────────────────────

def test_the_browser_and_the_server_agree_what_an_own_book_candidate_is_called():
    """`rel.ts:OWN_BOOK_LABEL` must equal `sellable_catalog.SELLABLE_LABEL`.

    The Supply Drawer marks a candidate that came from this organization's own
    item master by comparing its `brand` against a string. A rename on either
    side would not break a type, would not empty a screen and would not fail a
    test — every book candidate would simply stop being marked, and a book item
    and a catalogue item for the same physical product would go back to reading
    as two unrelated options in one list.

    That is a silent loss of a distinction somebody picks a product with, which
    is why it is pinned here rather than trusted. This file exists for exactly
    this class: "rename a field in a router and every check in the gate stays
    green".
    """
    from app.sellable_catalog import SELLABLE_LABEL

    rel_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "rel.ts")
    source = rel_ts.read_text(encoding="utf-8")
    match = re.search(r'export const OWN_BOOK_LABEL\s*=\s*"([^"]+)"', source)
    assert match, "rel.ts no longer declares OWN_BOOK_LABEL"
    assert match.group(1) == SELLABLE_LABEL, (
        f"the browser looks for brand == {match.group(1)!r} and the server "
        f"sends {SELLABLE_LABEL!r}; every own-book candidate would go unmarked")
