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

`.github/workflows/gate.yml` runs everything CLAUDE.md §6 tells a person to run,
on every pull request:

| Job | What it protects |
|---|---|
| **§1 invariants** | A deterministic layer importing `ai/`, or `ai/` importing `commercial/`. The rule that makes every number auditable. |
| **backend** | `ruff check backend/app`, then the whole suite. |
| **migrations on an empty database** | `alembic upgrade head` from nothing, the drift test, and exactly one head. |
| **frontend** | `tsc -b` and the production build. |

The third job is the one worth understanding. A developer's own database is
already migrated, so it can never exercise the empty case — and the empty case
is the one production runs. §4 of CLAUDE.md is an account of what happens when
nobody checks it: the schema and the models had drifted apart in 130 places,
invisibly.

`main` should require these to pass: **Settings → Branches → Add rule**, require
status checks `§1 invariants`, `backend — ruff + tests`,
`migrations on an empty database`, `frontend — types + build`.

### What is deliberately not in the gate

The SOLID heuristics and the duplicate scan. CLAUDE.md §9 is explicit about why:
tests, types and the §1 invariants block; the heuristics advise. A check that
fires on things that turn out to be fine gets muted, and it takes the checks
that mattered with it. They belong in the PR template, where a person answers
them.

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
