# Marketing placeholders — what the site is waiting for

The public site carries `{{PLACEHOLDER}}` tokens for content nobody has
supplied yet: prices, a scheduling link, customer names, case-study figures,
compliance statuses.

**None of them reaches a visitor.** A block whose content is still a token is
not rendered at all — no empty panel, no heading over nothing, no "coming
soon". Where a whole section has nothing to show, the section goes, its
navigation link goes with it, and the section lettering closes up so there is
no gap in the alphabet where it used to be. The site is deployable exactly as
it stands.

That is a change from how this started. The tokens used to render, on the
theory that a visible placeholder is the loudest possible reminder to replace
it. True of whoever maintains the page and false of everybody else — the
reminder had three other homes and the visitor got a building site.

**Filling one in is all it takes.** Put real content in the source below and
its block appears; there is no second switch. And the rule the whole site
rests on is unchanged: *nothing here may be invented to make a section
appear.* Hiding a section costs a section. Filling it with something plausible
costs the argument every other claim on the page depends on.

## How to check what is still outstanding

```bash
cd frontend && npm run build
```

The build ends with every empty slot and what a visitor is not seeing because
of it — for example:

```
prerender: 10 content slots are still empty, and the pages hide what depends on them:
    DEMO_BOOKING_URL          → every "Book a demo" button falls back to the trial door
    PRICE_CATALOG_BUILD_USD   → the catalog-build sentence is omitted for non-Indian visitors
    CUSTOMER_LOGO_1..4        → no customer strip
    CASE_STUDY_*              → no case study
    — all three above —       → the Proof section is hidden entirely
```

Nothing in that list is broken. Each line is a section the site is not showing.

The reverse condition — a token actually reaching a built page — **fails the
build**, because it means something rendered a placeholder instead of hiding
its block. `prerender.test.tsx` catches the same thing in the three-second
test loop, along with the section's navigation link and the lettering.

## 1 · Prices — there are none, and that is the design

**The site states no price, in any currency, on any page.** There is no slot to
fill and no token to replace: `frontend/src/landing/pricing.ts` is now
`worked-example.ts` and holds only the illustrative quote line the hero card and
the plans note share.

What a distributor pays turns on how many companies are connected, which ERP
each of them sits on and how much catalogue there is to build. A figure on a
panel answers that before asking any of it, and it is wrong for somebody — who
then acts on it without ever getting in touch. So the plans section describes
what each plan *is* and ends in a form (`ContactForm.tsx` → `POST
/api/v1/contact`), and the enquiry lands in a queue an operator works:

```bash
cd backend
python3 -m app.contact                    # who is waiting for a reply
python3 -m app.contact handled <id>       # after replying
python3 -m app.entitlements requests      # this queue plus the two in-product ones
```

Two tests hold the line, because "just this one figure" is how it comes back:
`worked-example.test.ts` fails if the module grows a price-shaped export, and
`prerender.test.tsx` fails if any built page carries `/month`, `per month` or
the old "Priced per organization" fallback.

If a price list is ever wanted again, this is the section to rewrite — not a
literal to paste into a panel.

## 2 · The demo booking link — `frontend/src/landing/cta.ts`

| Token | What it is |
|---|---|
| `{{DEMO_BOOKING_URL}}` | The scheduling link behind every **Book a demo** button — four of them: the landing page's hero and closing block, and each ERP page's |

Replace the constant with the real URL (Cal.com, Calendly, HubSpot, whatever is
chosen). Nothing else has to change: the moment it stops looking like a token,
all four buttons switch to it, say "Book a demo", open in a new tab and announce
that they do.

**Until then the page does not offer a meeting it cannot arrange.** Each button
falls back to the trial door under a label that says so, and the second button
beside a fallback is dropped rather than rendered as a duplicate of it.
`cta.test.ts` pins both states, and `prerender.test.tsx` asserts no built page
carries a `{{…}}` token inside an `href` at all.

It stopped being a conversion blocker when the plans section grew its form: a
buyer who is ready to talk now has a door on every one of these pages whether
or not a scheduling link exists. Filling this in adds the faster one.

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
| `{{ZOHO_DISTRIBUTOR_EVIDENCE}}` | The same, for Zoho Books — the one of the four where we run the book ourselves, so this is the panel most likely to be fillable first, and still not with our own words |

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
