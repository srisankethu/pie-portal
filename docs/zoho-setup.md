# Connecting a live Zoho Books account

What you need to create in Zoho, and what to send back. Written for the
**4U Precision** organization, but nothing here is specific to it.

The connection is **read-only**. The platform never creates, updates or deletes
anything in Zoho — every call it makes is a GET.

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

---

## 5. Configure

Put these in `.env` (gitignored) or the deployment's environment:

```bash
ZOHO_SOURCE=api                      # switches off the offline fixture source
ZOHO_ORGANIZATION_ID=60036630626
ZOHO_CLIENT_ID=1000.xxxxxxxx
ZOHO_CLIENT_SECRET=xxxxxxxx
ZOHO_REFRESH_TOKEN=1000.xxxxxxxx.xxxxxxxx
ZOHO_ACCOUNTS_BASE=https://accounts.zoho.in
ZOHO_API_BASE=https://www.zohoapis.in/books/v3

# Optional, sane defaults
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

**You do not have to set `ZOHO_SYNC_FROM`.** The **Data & connection** screen
asks for the start date each time you sync, and defaults to eighteen months
back. Use the variable only if you want a different default.

**`ZOHO_SOURCE=api` is the switch.** Until it is set, the platform keeps using
the offline fixture source no matter what other credentials are present.

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

## 6. Verify — from the app

Sign in as the owner or a manager and open **Data & connection** in the nav.
It states in words whether you are looking at your books or sample data:

| Badge | Meaning | What to do |
|---|---|---|
| **CONNECTED** | Live, with the organization name, id, currency and history window | Press **Sync now** |
| **SAMPLE DATA** | `ZOHO_SOURCE` is not `api` — everything on screen is demonstration data | Set `ZOHO_SOURCE=api` and restart |
| **WRONG ORGANIZATION** | Credentials work, but the login cannot see the configured id | The panel lists the ids it *can* see — copy the right one |
| **REJECTED** | Zoho refused the credentials | Usually the data centre; check `ZOHO_ACCOUNTS_BASE` |
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
| `detail` mentions *data centre* | Token was issued in a different DC |
| `organization_found: false` | Credentials fine, but `ZOHO_ORGANIZATION_ID` is wrong — pick from `visible_organizations` |
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
