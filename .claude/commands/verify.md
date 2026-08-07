---
description: Run the full gate (lint, §1 invariants, backend tests, frontend build, migrations) and report the real numbers
allowed-tools: Bash(make:*), Bash(./scripts/verify.sh:*)
---

Run the gate and report what it actually said.

```bash
make verify
```

Then fill in CLAUDE.md §8 against the output, not from memory:

- **tests** — how many passed, and any that did not
- **lint** — clean, or what it flagged
- **§1 invariants** — deterministic layers still free of `ai/`
- **frontend** — `tsc -b` and the production build
- **migrations** — only meaningful on an **empty** database; the run inside
  `verify.sh` uses a fresh temp file, which your own database can never be

If anything failed, fix it and run again. If you cannot fix it, say so plainly
and quote the output.

Do not report a `--fast` run as verified: it skips the frontend build and the
empty-database migration check, and deliberately does not stamp. The empty case
is the one production runs, and the one CLAUDE.md §4 was written about.
