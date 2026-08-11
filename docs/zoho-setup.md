# Connecting a live Zoho Books account

What you need to create in Zoho, and how to connect it. Written for the
**4U Precision** organization, but nothing here is specific to it — the
platform supports any number of organizations, each with its own Zoho Books
connection (see [Multiple organizations](#multiple-organizations) below).

The **analysis** connection is read-only: the sync that feeds every metric,
signal and decision on this platform only ever issues GETs, and nothing on that
path writes back.

The **Quote Builder** is the one part that can write, and only when it is
switched on deliberately — see [Letting the Quote Builder write to
Zoho](#letting-the-quote-builder-write-to-zoho) at the end. Everything in steps
1–7 below is the read-only setup, and a connection configured only that far
cannot create anything in your books.

---

## 1. Create a Self Client

Zoho's "Self Client" is the right grant type here: there is no user to redirect,
the backend authenticates as itself with a long-lived refresh token.

1. Go to **https://api-console.zoho.in/**
   *(that is the India console — see the data-centre table below if the account
   is not in India)*
2. **Add Client → Self Client → Create**
3. Note the **Client ID** and **Client Secret**

Because this is a Self Client there is **no redirect/callback URL to register**.
If you are asked for one, you have selected the wrong client type.

## 2. Generate a grant token with these scopes

In the console, open your Self Client → **Generate Code** tab, then paste this
scope string exactly:

```
ZohoBooks.contacts.READ,ZohoBooks.settings.READ,ZohoBooks.invoices.READ,ZohoBooks.bills.READ,ZohoBooks.users.READ
```

- **Time duration:** 10 minutes is plenty
- **Scope Description:** anything, e.g. `pie-portal read sync`

What each scope is for:

| Scope | Reads | Used for |
|---|---|---|
| `ZohoBooks.contacts.READ` | `/contacts` | The customer list |
| `ZohoBooks.settings.READ` | `/items` | The product/item master |
| `ZohoBooks.invoices.READ` | `/invoices` + detail | Sales history (revenue, price) |
| `ZohoBooks.bills.READ` | `/bills` + detail | Purchase cost (margin, cost pass-through) |
| `ZohoBooks.users.READ` | `/users` | Turning an invoice's salesperson into a platform user, so accounts have owners |

> If the item pull later fails with *"scope is not permitted"*, regenerate the
> code adding `ZohoBooks.items.READ` — the scope covering `/items` has differed
> between Books editions. Everything else is stable.
>
> `ZohoBooks.users.READ` is the only optional one: without it the sync still
> runs and records `ASSIGNMENT_UNAVAILABLE`, but every account stays unassigned
> and salesperson queues stay empty.

Copy the **grant code**. It expires in minutes, so do step 3 immediately.

## 3. Exchange the grant code for a refresh token

Run this once, within the code's lifetime:

```bash
curl -X POST "https://accounts.zoho.in/oauth/v2/token" \
  -d "grant_type=authorization_code" \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "code=THE_GRANT_CODE"
```

The response contains `refresh_token`. **That value is what the platform
stores** — it does not expire unless revoked. The `access_token` in the same
response is short-lived and the backend fetches its own.

## 4. Find the organization id

**https://books.zoho.in** → ⚙ **Settings → Organization Profile**, or read it
from the URL. The **Data & connection** screen confirms it and, if it is wrong, names the
ids the login can actually see — so a wrong id is caught immediately rather
than producing a confusing empty result.

---

## If a credential has been exposed

**Rotation is the only thing that revokes a leaked secret.** Nothing else does:
not deleting the line, not rewriting git history, not restricting scopes. Once
a refresh token has been out of your control, treat it as out of your control
permanently — it is only a bearer token, and whoever holds it can mint access
tokens until Zoho is told to stop.

A credential counts as exposed if it has ever been:

- pasted into a chat, an issue, a ticket or an email;
- committed to a repository, **including in a test file or a comment** — a
  commit is permanent even after the line is deleted, and a pushed commit is on
  someone else's disk;
- printed in a CI log, a crash report or a support bundle.

To rotate:

1. Zoho API console → your Self Client → **Revoke** the refresh token. Anything
   still using it stops working immediately, which is the point.
2. Generate a fresh grant code and exchange it (steps 2 and 3 above).
3. Update the credential **in the app**, under Data & connection — not in a
   `.env`. The app encrypts `client_secret` and `refresh_token` at rest.
4. If the client *secret* also leaked, regenerate the Self Client itself; the
   secret is not rotatable on its own. The new token then belongs to a new app,
   so rotate it **with** its client id and secret — a token-only rotation would
   pair a new token with the dead app and Zoho would answer
   `invalid_client_secret`.

`test_no_live_secret_is_committed` fails the build if anything shaped like a
live Zoho token or client secret appears anywhere in the tree. It scans by
shape rather than by value, so it catches the next one and not just the last.

## Data centres

Zoho accounts live in one data centre and **a refresh token from one is
rejected by every other**. This is by far the most common setup failure. Use one
row consistently:

| DC | `ZOHO_ACCOUNTS_BASE` | `ZOHO_API_BASE` | API console |
|---|---|---|---|
| **India** | `https://accounts.zoho.in` | `https://www.zohoapis.in/books/v3` | api-console.zoho.in |
| US | `https://accounts.zoho.com` | `https://www.zohoapis.com/books/v3` | api-console.zoho.com |
| Europe | `https://accounts.zoho.eu` | `https://www.zohoapis.eu/books/v3` | api-console.zoho.eu |
| Australia | `https://accounts.zoho.com.au` | `https://www.zohoapis.com.au/books/v3` | api-console.zoho.com.au |
| Japan | `https://accounts.zoho.jp` | `https://www.zohoapis.jp/books/v3` | api-console.zoho.jp |

The defaults are India.

**One client id, one secret per data centre.** If the API console offers a
*multi-DC* option and you leave it off, the client keeps the same id in every
data centre but is issued a **different secret in each**. A secret copied from
`api-console.zoho.com` is therefore refused by `accounts.zoho.in` — and the
refusal names the secret, not the console it came from. Copy the id and the
secret together, from the console row matching the account.

## What a rejected sign-in is actually telling you

Zoho reports these in the response body, usually with HTTP 200, and the app
repeats the code verbatim followed by what to change. They are not
interchangeable — each one has already ruled the others out:

| Zoho says | What it means | What to change |
|---|---|---|
| `invalid_client_secret` | The client id was **recognised** and the secret rejected — so the data centre being asked is the right one | The secret does not match the id, or it came from another data centre's console, or the refresh token was issued by a *different app* — see below |
| `invalid_client` | No such client id at this accounts host | A typo, or the app is registered in another data centre |
| `invalid_code` | The refresh token itself is revoked, superseded, or from another data centre | Generate a fresh token — this is the one a DC mismatch usually shows as |

**`invalid_client_secret` right after a rotation is almost always the third
cause.** Replacing the token leaves the client pair alone by design — re-typing
a correct secret is how a working connection gets broken — so a token generated
under a **new** Self Client arrives paired with the **old** app, and Zoho
refuses the pair. Open **Replace the token** again and use *"The token came
from a different app"* to send the new client id and secret with it.

Do not respond to `invalid_client_secret` by changing the data centre. Zoho
found the app at that host; switching it makes the refresh token unknown there
too, and one wrong setting becomes two.

---

## 5. Turn on live mode, then connect the credentials in the app

One environment variable is still needed, because it is the process-wide
switch between the offline sample source and Zoho:

```bash
ZOHO_SOURCE=api                      # switches off the offline fixture source

# Optional, sane defaults — shared pull behaviour, not credentials
ZOHO_HISTORY_DAYS=730                # rolling fallback when no start date is chosen
ZOHO_SYNC_FROM=                      # e.g. 2025-01-01 — an explicit start date wins
ZOHO_PAGE_SIZE=200
ZOHO_MAX_PAGES=50
ZOHO_TIMEOUT_SECONDS=30

# Rate limiting. Zoho allows roughly 100 calls/minute per organization and a pull
# costs one call per document, so calls are paced rather than fired flat out.
ZOHO_REQUESTS_PER_MINUTE=90
ZOHO_MAX_RETRIES=6
ZOHO_THROTTLE_BACKOFF_SECONDS=15     # 15s, 30s, 60s, 90s… on HTTP 429
ZOHO_MAX_BACKOFF_SECONDS=90
```

**`ZOHO_SOURCE=api` is the switch.** Until it is set, the platform keeps using
the offline fixture source no matter what other credentials are present. Every
setting above is shared, global pull behaviour, not an account identity — it
applies the same way to every connected organization.

Setting it also does two things automatically, so demo data never lingers next
to your real books:

- The app stops seeding the sample dataset on startup — there is no window
  where fabricated customers sit next to your real ones.
- The **first live sync removes any sample data already there** (from before
  you linked Zoho). This is exact and safe: the demo customers/products have
  fixed ids no real Zoho sync ever produces, so a real customer — even one
  that happens to share a name with a demo one, like "Rane Madras" — is never
  touched. The sync result names what it removed; every sync after the first
  finds nothing left to remove.

**The credentials themselves are entered in the app, not `.env`.** Each
organization is a fully separate tenant with its own Zoho connection, so
credentials live per-organization in the database (encrypted — see
`CREDENTIAL_ENCRYPTION_KEY` in [operations.md](operations.md)), not in a
process-wide environment variable. Sign in as that organization's **owner**,
open **Data & connection**, and its badge reads **Not connected** with a form
right there:

| Field | Where it comes from |
|---|---|
| Zoho Books organization id | Step 4 above |
| Data centre | The table above — pick the row matching the account |
| Client ID / Client secret | Step 1 |
| Refresh token | Step 3 |

Press **Connect Zoho**. The page re-checks the connection immediately using
what you just entered, so you find out right away if something doesn't match —
same states as the table in the next section. The secret and refresh token are
encrypted before they touch the database and are never echoed back by any
response.

**Or from the command line**, once signed in as that organization's owner:

```bash
curl -X PUT localhost:8000/api/v1/data/connection \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "zoho_organization_id": "60036630626",
    "client_id": "1000.xxxxxxxx",
    "client_secret": "xxxxxxxx",
    "refresh_token": "1000.xxxxxxxx.xxxxxxxx",
    "accounts_base": "https://accounts.zoho.in",
    "api_base": "https://www.zohoapis.in/books/v3"
  }'
```

`DELETE /api/v1/data/connection` unlinks it (data already pulled is untouched;
only the credentials are removed) — owner-only, same as connecting.

**The exception is the platform's original default organization.** If it has
no stored connection, it falls back to `ZOHO_ORGANIZATION_ID` /
`ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` /
`ZOHO_ACCOUNTS_BASE` / `ZOHO_API_BASE` in the environment, exactly as before —
so an existing single-tenant deployment configured that way keeps working with
no changes required. Connecting that organization from the app (or via the
`PUT` above) takes over from the environment variables from then on.

## Multiple organizations

Each Zoho connection is a **fully separate tenant** — its own users, its own
decision queue, no data crosses between them. This is how Sanketh's several
legal entities (each its own Zoho Books account) run on one deployment without
their numbers ever mixing.

**Provisioning a new organization** (its first time only — one per legal
entity, not one per Zoho reconnection) is a backend step, the same way the
default organization's demo users are seeded:

```bash
python -m app.provision_org \
  --org-id org_sls --name "SLS Engineers" \
  --owner-email owner@sls.example --owner-name "S. Owner"
```

This creates the organization and its first (owner) user. It does **not**
connect Zoho — that owner signs in with their email (any password, until real
auth is wired up) and connects their own account from **Data & connection**,
exactly as in the previous section. Add a manager or salesperson to that
organization the same way:

```bash
python -c "
from app.db import SessionLocal
from app.domain.enums import Role
from app.provision_org import add_user
s = SessionLocal()
add_user(s, organization_id='org_sls', email='m.rao@sls.example', name='M. Rao', role=Role.SALES_MANAGER)
s.commit()
"
```

Email is the platform's login key and is globally unique — the same address
cannot head two different organizations. Signing in always resolves to exactly
one organization; there is no "switch organization" step because each is a
separate tenant with separate accounts.

## 6. Verify — from the app

Sign in as the owner or a manager and open **Data & connection** in the nav.
It states in words whether you are looking at your books or sample data:

| Badge | Meaning | What to do |
|---|---|---|
| **CONNECTED** | Live, with the organization name, id, currency and history window | Press **Sync now** |
| **NOT CONNECTED** | `ZOHO_SOURCE=api`, but this organization has no Zoho connection yet | An owner connects one — see above |
| **SAMPLE DATA** | `ZOHO_SOURCE` is not `api` — everything on screen is demonstration data | Set `ZOHO_SOURCE=api` and restart |
| **WRONG ORGANIZATION** | Credentials work, but the login cannot see the connected id | The panel lists the ids it *can* see — copy the right one and reconnect |
| **REJECTED** | Zoho refused the credentials | The detail line carries Zoho's own code — read it against [the table above](#what-a-rejected-sign-in-is-actually-telling-you) rather than assuming the data centre |
| **UNREACHABLE** | The network could not reach Zoho | Firewall/proxy on the host |

**Sync now** runs the whole cycle — pull, detect signals, generate decisions —
and records the result, so the page always shows when data last arrived, how
many rows were skipped and why, and what is in the read model.

Before pressing it, choose **Read the books from**. That date is the single
biggest lever on how long the pull takes, because every invoice and bill after
it is read individually. Eighteen months is the default and covers what the
detectors need: a 90-day recent window, the 90-day window before it, and enough
depth to clear the six-month history floor on `CUSTOMER_DECLINE`.

A run reports one of three results:

| Result | Meaning |
|---|---|
| **Succeeded** | The whole window was read and the pipeline ran end to end |
| **Stopped part-way** | The pull was interrupted — almost always rate limiting. Everything read so far was kept; press **Sync now** again and it continues from there |
| **Failed** | Nothing was read at all — a credential, organization or network problem |

**Stopped part-way is not a failure to undo.** Documents already read are
recorded, so the next run pays only for the list calls and whatever is still
missing. Tick **Re-read everything** only when you want to discard that and pull
the window again from scratch.

## 6b. Or verify from the command line

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"s.menon@sanketh.in","password":"demo"}' | jq -r .token)

curl -s localhost:8000/api/v1/internal/zoho/check -H "Authorization: Bearer $TOKEN" | jq
```

This authenticates and lists the organizations the login can see, **without
pulling any data**. It separates the three failures that look alike:

| Result | Meaning |
|---|---|
| `"ok": true` + your org name | Ready to sync |
| `detail` mentions *no Zoho connection* | This organization hasn't connected one yet — see [step 5](#5-turn-on-live-mode-then-connect-the-credentials-in-the-app) |
| `detail` carries `invalid_code` | The refresh token is revoked or was issued in a different DC |
| `detail` carries `invalid_client_secret` | The secret does not belong to the client id — **not** the data centre; see [the table above](#what-a-rejected-sign-in-is-actually-telling-you) |
| `organization_found: false` | Credentials fine, but the connected organization id is wrong — pick from `visible_organizations` and reconnect |
| `"source": "fixture"` | `ZOHO_SOURCE=api` is not set |

## 7. First sync

```bash
curl -s -X POST "localhost:8000/api/v1/internal/sync/zoho?since=2025-01-01" \
  -H "Authorization: Bearer $TOKEN" | jq
```

`since` is the start date; omit it to use `ZOHO_SYNC_FROM` and then the rolling
window. Add `&full=true` to discard the resume cursor and re-read every
document.

Returns exactly what was written and what was skipped and why — nothing is
dropped silently. Then run the pipeline:

```bash
curl -s -X POST localhost:8000/api/v1/internal/detectors/run    -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8000/api/v1/internal/decisions/generate -H "Authorization: Bearer $TOKEN"
```

`sync → detectors/run → decisions/generate` is the normal operating cycle; each
step is idempotent, so re-running is safe.

---

## What the pull does and does not include

- **Customers** — contacts of type `customer` only.
- **Items** — the full item master.
- **Invoices** — excludes `draft` and `void`, so a cancelled invoice never
  counts as revenue a customer stopped spending. Lines without an `item_id`
  (comment/charge rows) are not product lines.
- **Bills** — same exclusions. Bills are the cost side; they are what makes
  margin and cost pass-through computable, and they are RESTRICTED data that
  never reaches a salesperson.
- **History** — bounded by `ZOHO_HISTORY_DAYS` (default 2 years). The detectors
  compare a recent window against a prior one, so a decade of ledger costs API
  calls and buys nothing.

- **Order** — contacts and items first (sales and cost lines need them to
  resolve), then **bills**, then invoices. Bills come first deliberately: there
  are far fewer of them, and they are the only thing that makes margin and cost
  pass-through computable at all, so they are not left hostage to a long invoice
  pull finishing.
- **Ownership** — the salesperson on a customer's most recent invoice is matched
  to a platform user **by email, exactly**. Anything else leaves the account
  unassigned and records why. An unassigned account is visible to managers and
  owners; a wrongly assigned one would be invisible to the person who should act
  on it, which is the worse failure.

**Call volume and rate limiting:** Zoho's list endpoints do not return line
items, so a pull costs one list call per page **plus one detail call per invoice
and per bill** in the window. For a few thousand documents that is a few
thousand calls, against a limit of roughly 100 per minute per organization.

Two things keep that survivable:

- Calls are **paced** to `ZOHO_REQUESTS_PER_MINUTE` rather than fired as fast as
  the network allows. Not provoking the limiter matters more than recovering
  from it.
- A pull that is throttled out anyway **resumes**. Each document is recorded
  once its lines are written, so the next run skips it — and a document edited
  in Zoho since is re-read, because its modification stamp moved.

## Two things to expect on first real data

1. **Margin and cost pass-through will suppress heavily** wherever cost is
   missing, zero/placeholder, or above the selling price. That is correct
   behaviour, not a failure — the platform refuses to assert a margin it cannot
   stand behind. If *no* product has cost, check that bills were actually read:
   the **Cost records** count on the last-sync panel is the answer.
2. **Salesperson queues fill only where Zoho names a salesperson.** Invoices with
   no salesperson leave the account unassigned, and so does a salesperson whose
   Zoho email does not match a platform user — both are listed under **Skipped
   rows** as `UNMAPPED_SALESPERSON`. Managers and owners see everything
   regardless.

## Purchase cost is the bill line's *effective* rate, after discount

A bill line's `rate` is the pre-discount list price, not what was actually paid.
When a line carries a discount, the platform computes the effective per-unit
cost and stores that everywhere margin, cost pass-through, and pricing
intelligence read cost from — `rate` and the discount are kept alongside it,
never overwritten, purely for audit.

Zoho resolves a discount in more than one way depending on the payload, and the
platform prefers whichever is most authoritative rather than re-deriving it:

1. **`item_total`** — the line's own post-discount, pre-tax total. Preferred
   whenever present, because Zoho has already resolved the discount for you.
2. **`discount_amount`** — Zoho's own resolved monetary discount for the line.
3. **`discount`** — a percentage (as a bare number or a `"50%"` string),
   applied to `rate`.
4. Nothing present — no discount; cost equals `rate`.

This is pre-tax cost, unchanged from before — bill-level adjustments and tax
are still not folded into it.

**If this looks wrong for your books**, check `POST /api/v1/data/status` →
`read_model.cost_records_pending_discount_backfill`. That count is bill lines
synced *before* this fix, when the discount was never read from Zoho at all —
only the (wrong) resulting cost was stored, so there is nothing locally to
correct it from. The only fix is a full re-sync, which re-fetches those bills
and overwrites the cost record in place:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/data/sync" `
  -Headers @{ Authorization = "Bearer $t" } -ContentType "application/json" `
  -Body '{"full": true}'
```

or tick **Re-read everything** on **Data & connection** before **Sync now**.
Once it completes, `cost_records_pending_discount_backfill` should read `0`.

**Decisions generated before the backfill may be stale.** `MARGIN_DETERIORATION`
and `COST_PASS_THROUGH` decisions computed from the wrong (pre-discount) cost
are not automatically corrected by the backfill alone — run the pipeline again
afterward:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/internal/detectors/run" -Headers @{ Authorization = "Bearer $t" }
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/internal/decisions/generate" -Headers @{ Authorization = "Bearer $t" }
```

This re-detects signals from the corrected cost and re-evaluates any decision
whose underlying facts changed — decisions already resolved in an earlier week
are not reopened by design and should be reviewed manually if they touched an
affected product.

---

## Letting the Quote Builder write to Zoho

Everything above is read-only. This section is the only thing that lets the
platform create anything in your books, and it is off by default: with no
setting changed, the Quote Builder prices from a deterministic offline stand-in
and creates nothing anywhere.

Turn it on only when you are ready for somebody pressing **Send** to put a real
estimate in a real ledger.

### 1. Add the write scopes

The read scopes in step 2 are not enough — a token without these authenticates,
reads prices, and then fails at the moment of the write. Regenerate the grant
code with the read scopes **plus**:

```
ZohoBooks.estimates.CREATE,ZohoBooks.estimates.READ,ZohoBooks.settings.CREATE
```

| Scope | Used for |
|---|---|
| `ZohoBooks.estimates.CREATE` | `POST /estimates` — creating the quote |
| `ZohoBooks.estimates.READ` | `GET /estimates?reference_number=…` — the duplicate check, and finding out what happened when a write's reply is lost. **Not optional**: without it every send refuses, because an estimate that cannot be looked up cannot be created safely. |
| `ZohoBooks.settings.CREATE` | `POST /items` — the **Create in books** action on a NOT IN BOOKS line. Leave it out if you would rather items were only ever created by a person in Zoho; everything else still works. |

Then redo steps 3–5 with the new refresh token, or rotate the credential in
**Data & connection**.

### 2. Turn it on

```bash
ZOHO_QUOTE_SERVICE=live      # default is "mock" — the offline stand-in
```

Deliberately separate from `ZOHO_SOURCE`. That one decides where *analysis reads
history from*; this one decides whether the *quoting screen may write*. Being
connected for analysis grants no ability to write.

### 3. Which company an estimate goes to

With three legal entities under one Zoho login, "create the estimate" is not a
complete instruction on its own. The company comes from the customer: every
imported record stores the triple `domain/origin.py` defines — connector,
connected company, and that system's own id — so the estimate is written to the
company the customer was imported *from*, addressed by that book's own contact
id. It is a lookup, not a name match, which matters because "ABC Industries" can
exist in all three books and be three different customers.

Where that cannot be answered, the platform **refuses** rather than picking one:

- the connection the customer came from is disabled or gone
- the customer came from a different connector (a Tally ledger has no Zoho
  contact to write against)
- the customer predates provenance being recorded (`connection_id` is null) *and*
  more than one company is connected

The line reads **BOOKS OFFLINE** and the send fails with the reason. The fix is
to re-sync the company that customer belongs to — not to retry. An organization
with a single connected company has nothing to choose between and resolves
directly.

### 4. Verify, in this order

Do these against a **test Zoho organization** first. An estimate is outward
facing and Zoho has no API to delete one.

1. **Credentials and scopes reach the right company.** `POST /api/v1/data/status`
   → `connection.organization_found` must be `true`. This is the same call the
   Quote Builder's reachability probe makes, so if it fails, every quote line
   will read BOOKS OFFLINE.
2. **A real item resolves.** Paste one real MM# into a quote and confirm the
   list price and stock match what Zoho shows for that item. A wrong price here
   means the code matched a different item, and no later step will catch it.
3. **The duplicate guard works before you rely on it.** Send a one-line quote,
   note the estimate number, then press **Send** again on the *same* quote. It
   must report *"This quote was already sent"* and Zoho must still hold exactly
   one estimate under that reference. If a second appears, stop and do not use
   live mode.
4. **A refusal is honest.** Add a line for a code that is not in these books and
   send. It must fail, name that code, and create nothing.

### If a send fails and you cannot tell whether it worked

The message carries the quote's **reference** — search Zoho's estimates for it.
That reference is written onto every estimate this platform creates precisely so
that question always has an answer, and so that re-sending the same quote
returns the existing estimate rather than a second one.
