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

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `PIE_CACHE_SIZE` | `2048` | Entries. `0` disables the cache entirely — the off switch for a deployment that suspects a stale answer, with no code change |
| `PIE_CACHE_TTL_SECONDS` | `1800` | A ceiling on staleness for the one input the key cannot see: a catalogue rebuilt in place under an unchanged version |

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
per sync cadence, the database is already the thing every process shares, and
Redis would be a service to run for a workload that fits in one indexed query.

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
registers `sync.run` — so the queue itself knows nothing about syncs, Zoho, or
what any payload means.

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

`thread` is the default deliberately. The queue path needs a worker running to
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
