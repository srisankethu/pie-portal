# Caching and the message queue

Two pieces of infrastructure with one thing in common: both are places where
work can quietly stop being correct without anything failing. A stale cache
answers with facts from a world that no longer exists; a lost message means
work that was asked for and never happened. Neither shows up as an error, so
both are built around the property that makes them safe rather than around the
speed-up they buy.

---

## Caching — `backend/app/cache.py`

A bounded, keyed, in-process cache. LRU eviction, an optional TTL, hit/miss
counters, and a registry so `/api/health` can report what every cache is doing.

In-process rather than Redis, for the reason `ingestion/jobs.py` gives about
threads: this deployment is one process, an external cache is a service to run,
secure and invalidate, and the honest version of what is needed here is a dict
with a bound on it. Consequences, stated rather than discovered: N processes
warm N copies, and a restart starts cold. Both are acceptable because **every
entry is re-derivable** — nothing is ever true only inside the cache.

### The two rules

**Every input that varies the answer is in the key, versions included.** This
is where caches actually go wrong. A key that omits the ruleset version serves
a resolution computed under a catalogue that has since been rebuilt, and there
is no way to tell that answer from a fresh one. Keys are built with
`cache.fingerprint(...)`, which is content-addressed, so a key that *gains* a
component is a new key rather than an old one with a new meaning.

**Nothing cached carries cost or margin.** This is an availability
optimisation, not a projection layer. A cached role-projected response would be
a new way for a below-floor fact to reach a salesperson — the `MFLOOR` shape
from the working agreement's §1, arriving through a key that forgot the reader.
If something money-shaped ever wants caching, the recipient's role is part of
the key or it does not go in.

### Where it is applied, and where it was measured not to be

Every entry below came out of profiling the real read path — the app booted
against a seeded database, every parameterless `GET` hit once, wall time and SQL
statements recorded per request (`before_cursor_execute`), then the slow ones
re-run warmed under `cProfile`. Query counts decide more than milliseconds do: a
count that is already per-row is what becomes slow with a real book, and on
Postgres each one is a network round trip.

| Path | Before | After | What it was |
|---|---|---|---|
| `/api/health` (steady state) | 45 ms, 76 queries | 7 ms, 3 queries | reflected all ~70 tables *and* re-walked every migration file, per poll |
| A name vault page, 500 names | 442 ms, 1000 queries | 39 ms, 2 queries | the tenant key re-read and re-unwrapped per value — see below |
| One RFQ line, repeat resolution | full catalogue scan | cached | the engine's verdict |

Rejected, with the measurement that rejected them:

- **`/api/v1/connections/catalog`** looked like the worst endpoint at 262 ms on
  two queries. Warmed, it is 7 ms: that was one-off import cost, not per-request
  work. Nothing to cache.
- **`load_for_org`** (the commercial thresholds) is called on most intelligence
  endpoints, and is two cheap reads. Caching it would put a stale *margin
  policy* behind a screen that says which policy judged it, which is a trade
  nothing here justifies.
- **The tenant data key.** The measured waste was real and large, and a cache
  was still the wrong fix — see the next section.

### What is cached today

One thing: **the nomenclature engine's verdict for a product code**
(`pie_service.resolve`). It is the most expensive thing a quote does — an
identity lookup plus a scored pass over the whole decoded catalogue, per line —
and a quote re-resolves the same codes every time it is rebuilt, while a repeat
enquiry is often the same text as last month's.

| In the key | Why |
|---|---|
| catalogue ruleset version | a rebuilt catalogue decodes differently; this is the fact that explains why the same text resolved to a different product last March |
| the requested text | the question |
| the customer identity it is resolved under | a scoped code means something different for a different customer |
| a fingerprint of the confirmed mappings | confirming "their X means MM# Y" changes the answer *now*; a cache that outlived the confirmation would keep saying it had not been recorded |

Deliberately **not** in the key: the equivalence bands. They are commercial
policy applied by `_map` after the engine has run, so two organizations with
different bands read one cached engine result differently — which is the
correct relationship between a fact and the policy that judges it.

Two more rules the resolution cache follows:

- A mapping store that cannot be fingerprinted is **never cached**. Refusing
  costs a scan; caching against an input nothing can see costs a customer a
  stale answer with a confident explanation attached.
- A `PIE_DOWN` result is never stored. Pinning a transient failure would keep a
  recovered engine offline until the entry expired.

### The one that should not be a cache

`decrypt_for` costs 515 µs per value here, of which 53 µs is the decryption. The
rest is reading the tenant's key row — **one SQL statement per value** — and
unwrapping the DEK under the master key. Every screen showing customer or vendor
names paid it per name; `vault.backfill` paid it per customer, product and vendor
at the end of every sync.

The obvious fix is to cache the unwrapped key. It is the wrong one: a cached DEK
keeps a **destroyed** key usable — in this process until it expires, and in every
other process for as long as its own copy lives — and "destroy the key and the
ciphertext is unreadable everywhere" is what the erasure receipt attests to a
customer.

So the repeated work is removed rather than remembered. `keys.cipher_for()`
opens the key once and returns a `TenantCipher` the caller reuses for the whole
batch: `vault.resolve_many`, `vault.backfill` and `disclosure.reveal_many` each
open one key per page instead of one per row. 500 names went from 442 ms and
1,000 statements to 39 ms and 2 — and a key destroyed a moment ago is still
refused by the very next batch, because nothing holds it.

The general rule this leaves behind: **when repeated work has a correctness
promise attached to its freshness, remove the repetition instead of caching the
answer.**

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `SCHEMA_GAP_CACHE_TTL_SECONDS` | `30` | How long `/api/health` may answer the schema question from memory. The key carries the database's Alembic revision, so a migration is reflected on the next poll whatever this is; the TTL bounds only a schema altered by hand under an unchanged stamp. `0` disables it |
| `PIE_CACHE_SIZE` | `2048` | Entries. `0` disables the cache entirely — the off switch for a deployment that suspects a stale answer, with no code change |
| `PIE_CACHE_TTL_SECONDS` | `1800` | A ceiling on staleness for the one input the key cannot see: a catalogue rebuilt in place under an unchanged version |

---

### What is verified, and where

The queue's tests run twice: on SQLite with the rest of the suite, and on
PostgreSQL in `scripts/verify.sh`'s step 6 whenever a server is available
(`PG_VERIFY_URL`, or the throwaway cluster `pg_sandbox.sh` starts). The
concurrent-claim test only runs on Postgres and skips elsewhere, and that is not
a formality: the SQLite test fixture hands every session one shared connection,
so racing two threads over it tests the pool rather than the queue. Where no
Postgres server exists the gate says the step was skipped rather than passing
it — the one property that makes multiple workers safe is the one that must not
be assumed.

---

## The message queue — `backend/app/messaging/`

A durable queue over one table, `queued_messages`, with a worker thread that
drains it.

### Why it exists

Background work has been dispatched to a thread since the first sync took
minutes, and a thread dies with its process. A run that is `QUEUED` when the
container is replaced — a deploy, an OOM, a host moving — is simply never done:
ten minutes later the reaper marks its `SyncRun` row stale and the work has
evaporated with nothing to say so. The queue closes that gap by committing the
*request* before anything starts.

A table rather than a broker: the depths here are one message per organization
per sync cadence, the database is already the thing every process shares, and a
broker would be one more service to reason about for a workload that fits in
one indexed query.

`compose.yaml` *does* provision Redis, "for cross-replica state (rate limits,
cache, job coordination)", with a comment saying no feature requires it yet.
That is still true, and it is a choice rather than an oversight: the queue needs
durability and an atomic claim, which the database it already writes to
provides, and putting the work list somewhere the business data is not would
mean a queued job and the row it acts on can disappear independently. Redis
remains available for the things it is actually better at.

### The shape

```
PENDING --claim--> CLAIMED --complete--> DONE
   ^                  |
   |                  +--fail (attempts left)--> PENDING, later
   |                  +--fail (attempts spent)--> DEAD_LETTER
   |                  |
   +---- reap_stale --+   (the worker holding it stopped reporting)
```

| File | Owns |
|---|---|
| `messaging/queue.py` | every state transition a message can make |
| `messaging/handlers.py` | topic → callable. One handler per topic; registering a second raises |
| `messaging/worker.py` | the loop: claim, run, record, reap |

The four properties it has to get right:

- **Claiming is atomic.** `status == 'PENDING'` is in the UPDATE's WHERE
  clause, so two workers racing produce one winner and one zero-row update.
  This is what makes more than one process safe — which the thread dispatch
  never was, and said so.
- **A dead worker releases its work.** `heartbeat_at`, the same mechanism
  `SyncRun` uses, and `reap_stale` hands the message back.
- **Failure is bounded and visible.** Attempts are counted and retried with a
  growing delay; a message that exhausts them becomes `DEAD_LETTER` with its
  last error, which `/api/health` reports. Never silently dropped.
- **Duplicate work is refused, not run twice.** `dedupe_key` names the work,
  so the same sync enqueued twice while the first is in flight is one message.

Topics are owned by the module that owns the work — `ingestion/jobs.py`
registers `sync.run`, `commercial/jobs.py` registers `commercial.recompute` — so
the queue itself knows nothing about syncs, metrics, Zoho, or what any payload
means.

### What is queued today

| Topic | Producer | Why it is not a request |
|---|---|---|
| `sync.run` | `jobs.queue_dispatch`, when `SYNC_DISPATCH=queue` | a pull reads every document individually; minutes, and it must survive a replaced container |
| `commercial.recompute` | `POST /commercial/recompute` with `background: true` | a whole-organization rebuild is O(customers × items) with a detector pass per pair — the shape the sync had before it moved to the background, and it fails the same way: a gateway gives up before the work does |

`background: true` is refused with a 409 where no worker would drain it. A
queued message in a deployment running `SYNC_DISPATCH=thread` is work that
silently never happens, which is worse than an answer saying so.

### Operating it — depth, dead letters, retention

`GET /api/v1/internal/queue` is the operator's view: the dispatch this process
uses, whether *this* process drains the queue, the depth per status, the recent
messages, and — listed separately, because it is the only part that is
somebody's job — the dead letters. `GET /api/v1/internal/queue/{id}` shows one
message with its payload; the listing withholds payloads on purpose, since a
listing that prints job arguments eventually prints something it should not.

`POST /api/v1/internal/queue/{id}/retry` puts a dead-lettered message back
(owner only). Attempts reset to zero — the operator is asserting the cause is
fixed, so this is a first attempt at work that now has a chance — and
`last_error` is kept as the record of why it needed a person. A message that is
still pending or in flight is refused with 409 rather than quietly ignored:
"retrying" it would put two workers on one job.

Finished messages are swept out by the worker on an interval
(`QUEUE_PRUNE_INTERVAL_SECONDS`, hourly). Receipts and failures are kept for
different spans because they mean different things: `QUEUE_DONE_RETENTION_DAYS`
(14) for a DONE message, `QUEUE_DEAD_LETTER_RETENTION_DAYS` (90) for a dead
letter, since deleting one of those is deleting the evidence of a failure. `0`
on either keeps them forever, which is the setting that makes this the largest
table in the database.

### Turning it on

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_DISPATCH` | `thread` | `thread` starts a daemon thread (today's behaviour); `queue` commits a message and lets a worker claim it |
| `QUEUE_WORKER` | *(follows `SYNC_DISPATCH`)* | `1`/`0` forces a worker on or off — an API process with `QUEUE_WORKER=0` beside a dedicated worker process is the shape this splits into |
| `QUEUE_POLL_SECONDS` | `2` | Idle poll interval; the latency of starting a queued job |
| `QUEUE_STALE_MINUTES` | `10` | How long a claimed message may go without a heartbeat before it is reclaimed |
| `QUEUE_MAX_ATTEMPTS` | `3` | Attempts before dead-lettering |
| `QUEUE_BACKOFF_SECONDS` | `30` | First retry delay; doubles per attempt |
| `QUEUE_MAX_BACKOFF_SECONDS` | `900` | Cap on that delay |
| `QUEUE_DONE_RETENTION_DAYS` | `14` | How long a finished message is kept; `0` keeps them forever |
| `QUEUE_DEAD_LETTER_RETENTION_DAYS` | `90` | The same for a failure — longer, because it is evidence |
| `QUEUE_PRUNE_INTERVAL_SECONDS` | `3600` | How often the worker sweeps |

**The compose stack sets `SYNC_DISPATCH=queue`** and runs a `worker` service
beside the API: same image, same environment, `QUEUE_WORKER=1` against the
API's `QUEUE_WORKER=0`, so long jobs run somewhere that is not answering
requests. `docker compose up -d --scale worker=3` for a backlog — the claim is
a conditional UPDATE, so workers take different messages rather than racing for
one. A single-service host (Railway) does the same thing with one process:
`SYNC_DISPATCH=queue` and `QUEUE_WORKER=1` on the API.

`python -m app.worker` is that process. It starts the same drain loop the API
can run in-process, waits for SIGTERM, and **exits non-zero if its
configuration declines to start a worker** — a worker container that stays
green while the queue fills is the failure this is built to avoid.

The code default is still `thread`, deliberately. The queue path needs a worker running to
do anything at all, and a default that silently requires a second moving part
is a default that makes a fresh clone's Sync button do nothing. Turn it on
where containers are replaced under load, or where more than one process runs.

### Operating it

```bash
# What is queued, in flight, done, or failed for good.
curl -s localhost:8000/api/health | python3 -m json.tool   # "queue" component

# The same, from a shell.
cd backend && python3 -c "
from app.db import SessionLocal
from app.messaging import depth
with SessionLocal() as s: print(depth(s))"
```

A dead-lettered message is work that failed every attempt and is waiting for a
person: read `last_error` on the row, fix the cause, and re-enqueue. The health
component reports `DEGRADED` while any exist — the platform is serving, one job
is not — rather than reporting healthy over failed work.

`drain_once(session)` is the whole loop minus the waiting. It is what the tests
use, and what a cron or a CLI would call: a queue exercised only through a
background thread is a queue tested with a sleep and a hope.
