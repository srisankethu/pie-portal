# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Salesperson (`SALESPERSON`)** — the primary user, and the one this product is
for. Runs the quoting desk: reads an inbound RFQ (email, WhatsApp, PDF, phone),
turns it into priced lines, and sends a quotation. Works at a desk most of the
day and on a phone in a customer's factory some of it. Sees 19 nav items.
**Never sees cost, margin, purchase spend, or any rule whose boundary is cost** —
these are absent from the API response, not hidden in the browser.

**Sales manager (`SALES_MANAGER`)** — the desk plus economics: margin, cost
basis, supplier side, approvals up to manager authority. 34 nav items.

**Owner (`OWNER`)** — everything, plus users and roles, margin policy, AI keys,
connections, trust surface. Below-cost approval is the owner's signature alone.

Not screen users but real: an API client (resolution/enquiries/outcomes), an
operator (sync, worker, entitlements CLI), and a read-only demo visitor.

## Product Purpose

An AI-native commercial decision platform for a B2B cutting-tool distributor.
It checks every quote line against the organization's own margin policy before
the quote goes out, holds what breaches it for a manager, and reports the margin
that held. Success is a salesperson quoting faster and with more confidence,
and a manager never being surprised by a number after the fact.

## Positioning

**The AI never computes a number.** Prices, margins, priorities and thresholds
are computed deterministically in `app/commercial/` and `app/signals/` from
persisted rows; a model may read those numbers and phrase them, never produce
one. Every figure on screen is openable and re-derivable from rows the
customer's own ERP already wrote, and is stamped with the version of the policy
that judged it. It reads the ERP the business already runs and replaces none of
it.

## Operating Context

- **Books:** Zoho Books across three legal entities (SLS Engineers, 4U
  Precision, UPS); for US clients, NetSuite, Dynamics 365 BC, Acumatica, Epicor
  Prophet 21 and Sage through the `ingestion/erp/` connector registry. Zoho is
  the system of record; `state/` is derived and a complete re-sync rebuilds it.
- **The quoting loop, which is the product:** RFQ arrives → paste it into the
  Quote Builder → each line resolves through the pie-parser engine against a
  decoded catalogue → confirm misread lines one at a time → choose a supply
  product or substitute → price each line → the assessment re-runs → override or
  request approval → create the ERP document → record won/lost.
- **Field sales:** a salesperson standing in a customer's factory, on a phone,
  one-handed, on poor signal. The tasks that matter there are customer lookup,
  customer context, product lookup, price, and availability.
- **Currency INR, timezone Asia/Kolkata.** Money is `Decimal`; margin is a ratio,
  movement is percentage points.

## Capabilities and Constraints

- **Role-gating is server-side and absolute.** `quote_service.project` withholds
  any rule naming something the recipient may not see, substituting one fixed
  `APPROVAL_REQUIRED`. A field hidden only in a component is a defect, because a
  network tab is not hard to open.
- **`recommended` and the negotiation floor reach the salesperson deliberately**
  (`store.py:563`, "decision support, safe for both roles"). This is an accepted,
  documented residual: cost is derivable from either by algebra, and coarsening
  them would blunt the screens the role uses to decide. One boundary per distinct
  action the recipient can take is the budget.
- **An equivalence score is policy, not identity.** A `rel` is true of this quote
  under this organization's bands; it is never persisted as a relationship
  between products, and a derived `rel` is never fed back in as an input.
- **Absence of evidence is not a pass.** Where the evidence for a claim is
  missing the answer is UNKNOWN or a refusal naming what is missing — never a
  benign default.
- **Thresholds carry a version.** No computed row is written without one.
- **Quantity on a quote line cannot be edited** — there is no mutator in
  `store.py`, no route in `routers/quote.py`, no client call. Changing a
  quantity means deleting the line and re-pasting it. *Open product decision;
  out of scope for UI work.*
- **There is no activity log and no follow-up entity.** No call/visit/email in
  `domain/models.py`. *Open product decision.*
- **Manual product entry is built server-side and unreachable in the UI** —
  `routers/quote.py:658` accepts `manual`, and no client ever sends it true.
  *Deliberately out of scope as of 2026-09; the owner declined it when asked.*

## Brand Commitments

- **Name:** PIE. Wordmark is "PIE." with the full stop.
- **Voice:** plain, specific, and unhedged. Screens state what a number is and
  where it came from. An empty screen says *why* it is empty. A withheld figure
  says it is absent for this role rather than pretending it does not exist.
- **Type:** Barlow Condensed for headings and controls, Barlow for body — an
  industrial, drawing-office register, already committed in `theme.ts` and
  `@fontsource`.
- **Existing design authority:** `docs/ui-standards.md` is a standing standard,
  and `theme.ts` + `platform/kit.tsx` are its implementation. Material UI is the
  design system; AG Grid through `platform/DataGrid.tsx` is the table.

## Evidence on Hand

- 49 screenshots of the running application, both roles, 1440×900 and 390×844,
  captured by `frontend/e2e/.shots/capture.mjs` against a seeded backend.
- `docs/user-flows.md` and `docs/user-flow-audit.md` — traced workflows.
- A five-agent discovery audit of routes, components, tokens, workflows and
  responsive behaviour, 2026-09.

## Assumptions

Written without a live interview: the owner answered three structured questions
(surface `recommended`: yes; add an account→quote path: yes; field-sales parity
for the core path: yes) and the rest is inferred from the repository and the
original brief. Everything above is drawn from code, `CLAUDE.md`,
`docs/ui-standards.md` and the captured screens rather than invented, but the
Users, Voice and Operating Context sections have not been confirmed by a human
in conversation and should be corrected rather than trusted where they are wrong.
