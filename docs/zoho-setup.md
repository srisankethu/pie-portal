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
ZohoBooks.contacts.READ,ZohoBooks.settings.READ,ZohoBooks.invoices.READ,ZohoBooks.bills.READ
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

> If the item pull later fails with *"scope is not permitted"*, regenerate the
> code adding `ZohoBooks.items.READ` — the scope covering `/items` has differed
> between Books editions. Everything else is stable.

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
ZOHO_HISTORY_DAYS=730                # how far back to pull
ZOHO_PAGE_SIZE=200
ZOHO_MAX_PAGES=50
ZOHO_TIMEOUT_SECONDS=30
```

**`ZOHO_SOURCE=api` is the switch.** Until it is set, the platform keeps using
the offline fixture source no matter what other credentials are present.

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
curl -s -X POST localhost:8000/api/v1/internal/sync/zoho -H "Authorization: Bearer $TOKEN" | jq
```

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

**Call volume:** Zoho's list endpoints do not return line items, so a pull costs
one list call per page **plus one detail call per invoice and per bill** in the
window. For a few thousand documents that is a few thousand calls — well within
daily limits, but it is why the history window exists. Reduce
`ZOHO_HISTORY_DAYS` for a first smoke test.

## Two things to expect on first real data

1. **Salesperson queues will be empty.** Zoho's salesperson field is not yet
   mapped to `customer.assigned_user_id`, and a salesperson only sees their own
   assigned accounts. Managers and owners see everything. Mapping this is a
   known outstanding item.
2. **Margin and cost pass-through will suppress heavily** wherever cost is
   missing, zero/placeholder, or above the selling price. That is correct
   behaviour, not a failure — the platform refuses to assert a margin it cannot
   stand behind.
