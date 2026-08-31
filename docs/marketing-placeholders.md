# Marketing placeholders — what has to be real before this deploys

The public site (`frontend/src/landing/`) carries 22 `{{PLACEHOLDER}}` tokens.
Each one is a slot the repositioning needed and the repository could not fill
truthfully: a price nobody has fixed, a link nobody has created, a customer
nobody has permission to name, a certification that does not exist yet.

They are rendered **verbatim**, as visible tokens on the page, and that is
deliberate. The whole argument this site makes is that its numbers re-derive
and its claims are checkable; a slot filled with something plausible would be
worth less than an empty one, because it would make every other claim on the
page a guess. An empty slot embarrasses. An invented customer ends the
argument.

**Nothing here may be filled in with an approximation, a rounded conversion, a
representative example, or a name that has not given permission.** If the real
answer is not available yet, the honest options are to leave the token, write
the true state ("in progress"), or delete the block.

## How to check what is still outstanding

```bash
cd frontend && npm run build
```

The last line of the build lists every `{{…}}` token left in the built pages —
a warning, not a failure, so the branch stays deployable to a preview while
the values are decided. To see them in place, open `dist/index.html` and
`dist/erp/*.html`.

Two tests guard the discipline rather than the values:
`src/landing/prerender.test.tsx` refuses any token that is not on its declared
list (so a new placeholder cannot be added without landing on this checklist),
and it fails if the compliance row is ever made to claim a certification.

---

## 1 · Prices — `frontend/src/landing/pricing.ts`

The landing page is the **only** place a price lives; `backend/app/entitlements.py`
deliberately holds none. Indian visitors already see real rupee prices; these
three are the dollar list every other visitor sees.

| Token | What it is | Intended, per the repositioning |
|---|---|---|
| `{{PRICE_TIER_1_USD}}` | Commercial Intelligence, per month | $1,500–$2,500 |
| `{{PRICE_TIER_2_USD}}` | Platform, per month | $3,500+ |
| `{{PRICE_CATALOG_BUILD_USD}}` | One-time catalog build | No dollar figure exists. The rupee price is ₹4,999; a converted price would be a guess dressed as a decision |

Write them as they should read, symbol included — `"$1,950"` — and keep the
`/month` suffix in the markup rather than in the string. The page displays
these unchanged.

The intended ranges above are also written in the comment on `PRICING.INTL` in
that file, deliberately: this list is for a person deciding the numbers and
that comment is for whoever next opens the code. They are the only two places
either range appears, and both should be edited to the real figure at the same
time — after which neither is a range any more.

## 2 · The demo booking link — `frontend/src/landing/cta.ts`

| Token | What it is |
|---|---|
| `{{DEMO_BOOKING_URL}}` | The scheduling link behind every **Book a demo** button — six of them, across the landing page and the three ERP pages |

Replace the constant with the real URL (Cal.com, Calendly, HubSpot, whatever is
chosen). The moment it stops looking like a token, the buttons start opening in
a new tab and announcing that they do; until then they stay in the current tab
so a broken destination is noticed rather than left open behind the page.

**This is the one placeholder that makes the page actively misleading if it
ships**: a primary call to action that goes nowhere.

## 3 · Customers — Section F of `frontend/src/landing/Landing.tsx`

| Token | What it is |
|---|---|
| `{{CUSTOMER_LOGO_1}}` … `{{CUSTOMER_LOGO_4}}` | Four customers who run PIE **and have given written permission to be named** |

Names in plain type, not image marks: this site has no images at all and the
stylesheet has no rule for one, and four names a buyer recognises say more than
four grey logos. Fewer than four real names is fine — delete the spare plates.
No real names at all means deleting the strip, which is honest; a strip of
plausible-sounding companies is not.

## 4 · The case study — Section F of `frontend/src/landing/Landing.tsx`

One real customer, one real ERP, figures read off **their own value ledger**
inside PIE — not a reconstruction, not an estimate, not a round number
somebody remembers. The section states in its own copy that the figure is
re-derivable, and that has to be true: it must be a number the customer could
open and check.

| Token | What it is |
|---|---|
| `{{CASE_STUDY_ERP}}` | Which ERP that customer runs |
| `{{CASE_STUDY_DISTRIBUTOR_PROFILE}}` | Their profile, anonymised or named with permission — e.g. size, sector, branches |
| `{{CASE_STUDY_NARRATIVE}}` | Two or three sentences: what the desk was doing before, what the floor caught, what the owner did. Their words where possible |
| `{{CASE_STUDY_MARGIN_RECOVERED}}` | The ledger's figure, in that customer's currency |
| `{{CASE_STUDY_LINES_CHECKED}}` | Quote lines checked in the window |
| `{{CASE_STUDY_LINES_HELD}}` | How many were below floor and held |
| `{{CASE_STUDY_POLICY_VERSION}}` | The thresholds version stamped on those rows (`ci_…`) |
| `{{CASE_STUDY_WINDOW}}` | The period the figures cover. The section's chip says "first 90 days" — change the chip if the real window differs |

## 5 · Compliance — Section F of `frontend/src/landing/Landing.tsx`

State what is true on the day the page ships. "In progress" is a perfectly good
answer to all three and reads far better than silence; a certification that does
not exist is the claim a diligence process takes apart.

| Token | What it is |
|---|---|
| `{{SOC2_TYPE_II_STATUS}}` | The real status — not started / in progress / audit scheduled / report available on request |
| `{{DATA_RESIDENCY}}` | Where this deployment stores customer rows, named as a region |
| `{{GDPR_DPA_STATUS}}` | Whether a DPA is available, and how to get one |

## 6 · The ERP pages — `frontend/src/landing/erp.ts`

| Token | What it is |
|---|---|
| `{{PROPHET21_DISTRIBUTOR_EVIDENCE}}` | What distributors running Prophet 21 actually say about margin on their own quote desk |
| `{{NETSUITE_DISTRIBUTOR_EVIDENCE}}` | The same, for NetSuite |
| `{{ACUMATICA_DISTRIBUTOR_EVIDENCE}}` | The same, for Acumatica |

This is the one field on those pages with no source in the code. Everything
else there — how PIE connects, what it reads, what it writes, what it cannot
see — is read from the connector module and pinned by `src/landing/erp.test.ts`
against that module's own source. These three are research, and until the
research exists the panel should carry a token or be deleted.

---

## Not a placeholder, but decide it before launch

- **A social preview image.** The site has none, and `twitter:card` is
  deliberately `summary` rather than `summary_large_image` so the tag does not
  promise an asset that does not exist. Shares render as text cards until a
  real image is designed. (Carried since the Aug 2026 SEO audit, §5.)
- **The founder's name and a link.** The founder section deliberately carries
  neither — none was supplied, and a marketing page is the wrong place to
  invent one. A name and a LinkedIn link would strengthen the strongest three
  sentences on the site.
- **`SITE_ORIGIN`.** Every canonical URL, `og:url` and sitemap entry is built
  from it, defaulting to the Vercel address. A custom domain is one environment
  variable at build time (`SITE_ORIGIN=https://…`), not an edit.
