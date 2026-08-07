# How work gets in

`CLAUDE.md` constrains how code is *written*. This is how it gets from an idea
to production, and what is automated along the way.

---

## The loop

```
issue  →  branch  →  PR  →  gate goes green  →  merge  →  branch deleted
                                                  ↓
                                          deploy runbook
                                                  ↓
                                     a person reads it and deploys
```

One issue, one branch, one PR. The issue is where the *decision to do the work*
lives; the PR is where the work is argued with. Both outlive the conversation
that produced them, which a chat thread does not.

### Branch names

```
<kind>/<issue-number>-<short-slug>

feature/42-receivables-reducer
fix/57-oversold-priced-at-zero
incident/61-sync-wedged-on-unstamped-db
chore/64-ci-gate
```

The number first, so the branch and the issue find each other from either end
six months later. GitHub's *Create a branch* button on an issue produces this
shape; so does `gh issue develop <n> --name feature/<n>-slug`.

Branches are deleted on merge. Turn on **Settings → General → Automatically
delete head branches** and it happens without anybody remembering.

---

## The gate

### One command

```bash
make setup      # once, from a bare clone: pie-parser, pinned tooling, npm, database
make verify     # the gate — ~4 min
```

`make verify` runs `scripts/verify.sh`, and **CI runs that same script**. That is
the whole design, and it is a correction rather than a preference — see the next
section for what it replaced.

| # | Check | Guards |
|---|---|---|
| 1 | `ruff check .` | The rule set in `ruff.toml`, at the version pinned in `backend/requirements-dev.txt`. Now covers tests and scripts, not just `backend/app` |
| 2 | §1 layer invariants | A deterministic layer importing `ai/`, or `ai/` importing `commercial/`. The rule that makes every number auditable |
| 3 | backend suite | 1174 tests, parallel, each worker on its own database |
| 4 | frontend | `tsc -b` and the production build |
| 5 | migrations from nothing | `alembic upgrade head` on an **empty** database, the drift test, and exactly one head |

Check 5 is the one worth understanding. A developer's own database is already
migrated, so it can never exercise the empty case — and the empty case is the one
production runs. CLAUDE.md §4 is an account of what happens when nobody checks:
the schema and the models had drifted apart in 130 places, invisibly.

`verify.sh` runs every step and reports all failures at the end rather than
stopping at the first, so one red build tells you everything that is wrong.

For the edit loop, `make verify-fast` drops 4 and 5 and finishes in about two and
a half minutes. It deliberately does **not** stamp, so it cannot be mistaken for
a verified state, and it is not enough to merge on.

### Why it is one script now

This used to be four jobs in `gate.yml` that restated what CLAUDE.md §6 told a
person to run. Two lists of the same checks drift, and the drift was invisible:

- **CI installed `ruff` unpinned.** A newer release shipped a broader default
  rule set, so the gate reported **1314 lint errors with no code change behind
  them**. Because `ruff` and `pytest` were steps in one job, the backend suite
  was **skipped on every run**.
- **CI never fetched pie-parser**, which the backend imports in-process. All 25
  migration-integrity tests died at collection with `PIE corpus not found` — so
  the drift check above had never actually executed.

Both survived **eight consecutive merges to `main`**. A check that is always red
is a check nobody reads, and it takes the checks that mattered with it.

The fix has three parts, and all three are needed:

1. **One list.** `scripts/verify.sh`, called by `make verify`, by CI, and by the
   Claude Code stop-hook.
2. **Pinned tooling.** `backend/requirements-dev.txt` pins `ruff`, `pytest`,
   `pytest-xdist` and `cffi` exactly; `ruff.toml` writes the rule set down. A
   gate whose meaning changes on someone else's release schedule is not a gate.
   Upgrading is now a deliberate commit that does nothing else.
3. **Loud preconditions.** `verify.sh` stops immediately with a legible message
   when pie-parser is missing, instead of producing twenty-five collection
   errors that look like a data problem.

`main` should require both checks to pass: **Settings → Branches → Add rule**,
requiring `§1 invariants` and `verify — lint, tests, frontend, migrations`.
Without that rule the gate is advisory, and an advisory gate is how a repository
ends up eight merges deep into a red build.

**CI needs one secret.** pie-parser is private and the automatic `GITHUB_TOKEN`
is scoped to this repository only. Create a fine-grained PAT with `Contents:read`
on `srisankethu/pie-parser` and add it as the repository secret
`PIE_PARSER_TOKEN` (**Settings → Secrets and variables → Actions**). Without it
the `verify` job stops at the checkout and says so.

### What is deliberately not in the gate

The SOLID heuristics and the duplicate scan. CLAUDE.md §9 is explicit about why:
tests, types and the §1 invariants block; the heuristics advise. A check that
fires on things that turn out to be fine gets muted, and it takes the checks
that mattered with it. They belong in the PR template, where a person answers
them.

---

## Working with Claude Code

`.claude/` is committed, because the process belongs to the repository rather
than to whoever's session is open. It automates the two steps that were actually
being missed:

**`SessionStart`** installs the pinned toolchain and locates pie-parser. This
matters more than it looks. A fresh container cannot run this suite at all —
Debian's `cryptography` binds to Rust bindings that import `_cffi_backend`, and
without `cffi` installed, `import app.main` dies with
`pyo3_runtime.PanicException` and takes 22 test modules with it at collection.
An agent that meets that does not stop to fix the toolchain; it reasons about the
diff and writes "tests should pass", which reads exactly like a result.

**`Stop`** refuses to end a turn that leaves source edited but unverified. It
does not run the suite on a timer: `verify.sh` stamps a content signature when it
passes, and the hook compares against that stamp, so a turn that changed nothing
costs nothing. When the change touches `backend/app/domain` or `backend/alembic`
it names the empty-database check specifically, because that is the one a local
database cannot exercise. A second consecutive block is allowed through — if the
gate is genuinely failing, the right outcome is to say so with the output, not to
be held in a retry that cannot succeed.

`/verify` runs the gate and asks for the real numbers back.

Both hooks fail open. A missing helper or a broken install lets the turn end
rather than wedging the session.

---

## Deploys are still manual, on purpose

`docs/operations.md`:

> **No auto-migration** — schema changes become a deliberate, reviewed deploy
> step.

That decision was made after an incident and this pipeline does not reverse it.
What is automated is the *review*, not the apply.

`.github/workflows/runbook.yml` runs `scripts/deploy_runbook.py` and produces:

- which migrations the change adds, and what each one's docstring says it does;
- which of them **rewrite or destroy** — parsed from `upgrade()`, so a
  `drop_table` in `downgrade()` or in a comment does not raise a false alarm;
- the steps in order, with the backup **before** the migrate;
- and a reminder to ask `/api/health` where production actually is, because
  nothing in CI can know that and guessing is the original mistake.

On a pull request it comments only when the schema is touched. On a merge it
becomes the release note. Run it yourself any time:

```bash
python3 scripts/deploy_runbook.py --range v1.4..HEAD
```

**`UNSTAMPED` and `UNKNOWN_REV` are never fixed by `alembic upgrade head`.**
CLAUDE.md §4 has the table of five states and the different fix each one needs.

---

## Writing the issue

Three templates, and they ask for different things because the three kinds of
work fail differently.

**Feature** asks what decision it helps somebody make, what Business State it
needs, and — the field that changes the shape of the work most often — whether
the data is ingested at all. Roughly a third of the categories in the Decision
Intelligence brief turned out not to be computable from what Zoho gives this
book. Finding that out in the issue is cheap; finding it out in a PR is not.

It also asks what the feature must **not** do. No forecast, no probability, no
cost in front of a salesperson, no threshold widened to make a screen
non-empty. Writing the refusals down early is what stops a plausible-looking
number appearing later, when it is much harder to argue with.

**Bug** asks where the chain stopped being right. A decision card traces to the
ERP record it came from; the place the chain stops making sense is usually the
bug, and it is much faster than a repro.

**Incident** starts by refusing to let you guess. The first thing in the
template is `/api/health`, because "the usual cause is a pending
`alembic upgrade head`" was once printed for any undescribed 500 — and acting
on it turned a ten-minute problem into a wedged database.
