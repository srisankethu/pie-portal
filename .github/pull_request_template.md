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

<!-- One command. `make verify` runs lint, the §1 invariants, the backend suite,
     the frontend build, and migrations on an EMPTY database — and CI runs the
     same script, so a green run here is a green run there.

     Paste the verdict. A blank box reads as "did not run", which is information;
     a ticked box with nothing behind it is what CLAUDE.md §6 exists to prevent. -->

- [ ] `make verify` passed

```
paste the verdict lines here
```

- [ ] Rendered in a browser, if a screen changed
- [ ] `make verify-fast` only — **say why**, and note that the frontend build and
      the empty-database migration check did not run

## Deploy

<!-- The runbook bot comments here when this touches the schema. If it did,
     say anything it cannot know — data that needs backfilling, a scope that
     needs granting, an order that matters. -->

- [ ] No schema change, or the runbook comment below is accurate and complete
