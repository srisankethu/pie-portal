---
name: Incident
about: Production is wrong, stuck, or down
labels: incident
---

> **First, before anything else:** get the real state. Do not act on an error
> message that guesses at the cause — CLAUDE.md §4 exists because somebody did.
>
> ```bash
> curl -s https://YOUR-HOST/api/health | python3 -m json.tool
> ```

## What `/api/health` says

<!-- Paste it. `state` is EMPTY / UNSTAMPED / BEHIND / UNKNOWN_REV / CURRENT,
     and each of those has a *different* fix. UNSTAMPED and UNKNOWN_REV are
     never fixed by `alembic upgrade head`. -->

```json
```

## What is wrong

## What was done immediately

<!-- Including anything that made it worse. That is the most useful line in the
     whole report and the one most often left out. -->

## What would have caught it

<!-- A test, a CI step, a health check, a runbook line. If the answer is
     "nothing", say that — it is a finding. -->
