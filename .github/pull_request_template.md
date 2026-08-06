## What changed, and why

<!-- The situation this addresses, not the diff. The diff is below. -->

Closes #

---

## Redundancy & SOLID self-review

<!-- CLAUDE.md §8. Filled in against real tool output, not from memory — the
     point of the section is that a tool actually looked. Every flag gets a fix
     or a visible trade-off; a silent pass is the one outcome that is not
     allowed. Delete the rows that genuinely do not apply and say why. -->

**Capability search run:** yes/no — searched: <!-- keywords, not file names -->
**Near-matches found:** none | `file:line` …
**Reuse rejected because:** reason | "extended it instead"

**Duplicate scan:** not run | clean | N blocks — action
**Invariant checks (§1):** clean | violation + fix
**SOLID**
- SRP:
- OCP:
- LSP:
- ISP:
- DIP:

**Below the size floor (§7):** n/a | yes — checks skipped deliberately

**Verdict:** APPROVED / APPROVED WITH NOTED TRADE-OFF / NEEDS REWORK

---

## Checks

<!-- CI runs all of these. Tick what you also ran locally, and say what you
     could not run — "did not run" is information; a blank box is not. -->

- [ ] `cd backend && python -m pytest tests -q`
- [ ] `cd frontend && npx tsc -b && npm run build`
- [ ] `ruff check backend/app`
- [ ] Migrations, if models or migrations changed — **on an empty database**:
      `rm -f /tmp/mig.db && DATABASE_URL="sqlite:////tmp/mig.db" python -m alembic upgrade head`
- [ ] Rendered in a browser, if a screen changed

## Deploy

<!-- The runbook bot comments here when this touches the schema. If it did,
     say anything it cannot know — data that needs backfilling, a scope that
     needs granting, an order that matters. -->

- [ ] No schema change, or the runbook comment below is accurate and complete
