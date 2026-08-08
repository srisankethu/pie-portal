#!/usr/bin/env python3
"""What a deploy of this range actually requires, in the order it requires it.

``docs/operations.md`` says, deliberately and after an incident:

    No auto-migration — schema changes become a deliberate, reviewed deploy
    step.

This does not reverse that. It writes the review down. The step stays human;
what stops being human is *remembering* which migrations landed, which of them
rewrite tables, and whether a backup is optional this time.

Run it locally against any range:

    python3 scripts/deploy_runbook.py --range v1.4..HEAD

CI runs it against the range a merge added and posts the result, so the runbook
arrives with the release rather than being reconstructed from the log
afterwards.

**It does not say what production is at.** It cannot: production's revision is
a fact about production, and guessing it is exactly the mistake the "usual
cause is a pending alembic upgrade head" story in §4 is about. The output ends
by telling you to ask ``/api/health``, which knows.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys
from typing import Optional

#: Repo-relative, and every git call is made from the repository root — so the
#: answer does not depend on which directory somebody happened to run this in.
MIGRATIONS = "backend/alembic/versions"


def repo_root() -> pathlib.Path:
    return pathlib.Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True).stdout.strip())

#: What each risky operation costs, in the words somebody deploying at 9pm
#: needs. Keyed on the Alembic op, and only ever read from ``upgrade()`` — a
#: ``drop_column`` in ``downgrade()`` is the *undo*, and flagging it would make
#: every additive migration look destructive.
RISK: dict[str, str] = {
    "drop_table": "DESTROYS a table and everything in it",
    "drop_column": "DESTROYS a column and everything in it",
    "alter_column": "changes a column in place; a type narrowing can lose data",
    "drop_constraint": "removes a constraint — rows that violate it can land after",
    "drop_index": "removes an index; queries that relied on it get slower",
    "execute": "runs raw SQL — read it before deploying",
    "bulk_insert": "writes rows — re-running it may duplicate them",
}

#: Operations that force SQLite to rebuild a table when they appear inside a
#: ``batch_alter_table`` block. Creating an *index* in batch mode does not:
#: Alembic recreates only when the operation genuinely requires it, so treating
#: every batch block as a rewrite would flag a purely additive migration and
#: teach the reader to skip the risk column. Which is worse than no column.
BATCH_REWRITES = frozenset({
    "add_column", "drop_column", "alter_column",
    "create_unique_constraint", "drop_constraint",
    "create_foreign_key", "drop_foreign_key",
})


def _called(node: ast.AST) -> Optional[str]:
    """The Alembic op a call node invokes, ``op.add_column`` → ``add_column``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else None


def _upgrade_risks(text: str) -> list[tuple[str, str]]:
    """What ``upgrade()`` does that deserves a backup.

    Parsed rather than grepped, for the same reason the layer-boundary check
    is: a ``drop_table`` named in a docstring explaining why the migration
    does *not* drop anything must not raise an alarm.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [("unparseable", "this file does not parse — read it by hand")]
    upgrade = next((n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    if upgrade is None:
        return []

    found: dict[str, str] = {}
    for node in ast.walk(upgrade):
        op = _called(node)
        if op in RISK:
            found[op] = RISK[op]
        # A non-nullable column with no server default fails the moment the
        # table has a row in it — which production always does and a fresh
        # test database never does.
        if op == "add_column":
            for arg in ast.walk(node):
                if _called(arg) == "Column":
                    kw = {k.arg: k.value for k in arg.keywords}
                    nullable = kw.get("nullable")
                    not_null = (isinstance(nullable, ast.Constant)
                                and nullable.value is False)
                    if not_null and "server_default" not in kw:
                        found["add_column"] = (
                            "adds a NOT NULL column with no server_default — "
                            "this fails on any table that already has rows")

    # Batch blocks: risky only for what is inside them.
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.With):
            continue
        if not any(_called(item.context_expr) == "batch_alter_table"
                   for item in node.items):
            continue
        inner = {_called(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
        if inner & BATCH_REWRITES:
            found["batch_alter_table"] = (
                "rewrites the table on SQLite (copy, swap, drop) — slow on a "
                "large one, and the copy needs room for a second copy")

    return sorted(found.items())


def _changed(rev_range: str, how: str) -> list[pathlib.Path]:
    """Migration files this range added (``A``) or modified (``M``).

    The distinction is the point: a released migration must never be edited
    (§4), so a range that *changes* one is a defect this runbook reports rather
    than folds into the list of things to run.
    """
    root = repo_root()
    out = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-status",
         f"--diff-filter={how}", rev_range, "--", MIGRATIONS],
        capture_output=True, text=True, check=True).stdout.strip()
    return [root / line.split("\t", 1)[1]
            for line in out.splitlines() if line.strip()]


def describe(path: pathlib.Path) -> dict:
    """The revision, its subject line, and anything risky it does."""
    text = path.read_text()
    revision = re.search(r'^revision\s*=\s*["\']([^"\']+)', text, re.M)
    down = re.search(r'^down_revision\s*=\s*["\']([^"\']+)', text, re.M)
    # The docstring's first line is written to be read by whoever deploys it.
    doc = re.search(r'^"""(.+)', text)
    return {
        "path": str(path),
        "revision": revision.group(1) if revision else "?",
        "down_revision": down.group(1) if down else None,
        "summary": doc.group(1).strip() if doc else path.stem,
        "risks": _upgrade_risks(text),
    }


def render_range(rev_range: str) -> str:
    """The runbook for what a git range changed."""
    return render([describe(p) for p in _changed(rev_range, "A")],
                  edited=[p.name for p in _changed(rev_range, "M")])


def render(added: list[dict], *, edited: list[str] = ()) -> str:
    """The runbook itself, from already-described migrations.

    Separate from the git lookup so it can be tested against a made-up release
    rather than against this repository's own history — which a rebase or a
    shallow CI clone would move under the test.
    """
    lines: list[str] = ["## Deploy runbook", ""]

    if edited:
        lines += [
            "> [!CAUTION]",
            "> **A released migration was edited.** Two databases that ran "
            "\"the same\" revision now have different schemas, and nothing can "
            "tell you which is which. Reconcile forward with a new migration "
            "instead (CLAUDE.md §4).",
            "", *(f"> - `{p}`" for p in edited), ""]

    if not added:
        lines += ["No migrations in this range. Deploy is code only:", "",
                  "```bash", "git pull && systemctl restart pie-portal   # or your equivalent",
                  "curl -s localhost:8000/api/health | python3 -m json.tool", "```", ""]
        return "\n".join(lines)

    risky = [m for m in added if m["risks"]]
    lines += [f"**{len(added)} migration(s) in this range.**"
              + (f" {len(risky)} of them rewrite or destroy — **back up first**."
                 if risky else " All are additive."), ""]

    lines += ["| Revision | What it does | Risk |", "|---|---|---|"]
    for m in added:
        risk = ("<br>".join(f"`{op}` — {why}" for op, why in m["risks"])
                if m["risks"] else "additive")
        lines.append(f"| `{m['revision']}` | {m['summary']} | {risk} |")
    lines.append("")

    lines += [
        "### Order",
        "",
        "```bash",
        "# 1. Where is production actually? Never assume — this is the question",
        "#    §4 exists because somebody answered from memory.",
        "curl -s https://YOUR-HOST/api/health | python3 -m json.tool",
        "",
    ]
    if risky:
        lines += [
            "# 2. Back up. Not optional for this release: see the table above.",
            "#    SQLite:",
            "cp backend/data/platform.db backend/data/platform.db.$(date +%F-%H%M)",
            "#    Postgres:",
            "pg_dump \"$DATABASE_URL\" > backup-$(date +%F-%H%M).sql",
            "",
        ]
    lines += [
        f"# {3 if risky else 2}. Migrate, then restart.",
        "cd backend && python -m alembic upgrade head",
        "",
        f"# {4 if risky else 3}. Verify. CURRENT, and no schema gap.",
        "curl -s https://YOUR-HOST/api/health | python3 -m json.tool",
        "```",
        "",
        "If `/api/health` reports `UNSTAMPED` or `UNKNOWN_REV`, **stop** — "
        "`upgrade` is not the fix for either. CLAUDE.md §4 has the table.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", dest="rev_range", required=True,
                    help="git revision range, e.g. abc123..HEAD")
    ap.add_argument("--out", type=pathlib.Path,
                    help="write here as well as to stdout")
    args = ap.parse_args()
    text = render_range(args.rev_range)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
