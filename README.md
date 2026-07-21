# pie-portal — Sanketh Quote Builder

A production B2B quote builder for cutting-tool distribution, implementing the
*Sanketh · Quote Builder* design and wired to the
[**pie-parser**](https://github.com/srisankethu/pie-parser) Product Intelligence
Engine for product resolution.

A salesperson (or manager) pastes a messy RFQ — manufacturer codes, loose
descriptions, quantities — and each line is resolved through pie-parser into a
concrete **supply product** with a **relationship** (exact identity, technical
equivalent, compatible, possible, ambiguous, unresolved) plus ranked
alternatives. Lines are priced against Zoho Books, with a management-only margin
engine and a margin-floor guardrail, and turned into a Zoho estimate.

```
   RFQ text ──▶ pie-parser (identity-first resolution + equivalence)
                     │  reqCode → supplyCode + relationship + ranked alternatives
                     ▼
   Zoho Books ──▶ availability · list price · landed cost · item creation · estimate
                     │
                     ▼
   Pricing engine (management-only) ──▶ recommended price · margin · floor guardrail
                     │
                     ▼
   Role-gated API ──▶ React quote grid (sales view has NO economics)
```

## Architecture

| Layer | Tech | Role |
|-------|------|------|
| `pie-parser/` | Python (pinned clone) | Deterministic nomenclature parser + identity resolver + equivalence engine, fetched at a pinned commit by `scripts/setup_pie_parser.sh`. **Nomenclature only — never price or stock.** |
| `backend/` | FastAPI | Imports pie-parser **in-process** (no subprocess), maps its output to the quote model, gates economics by role, integrates Zoho. |
| `frontend/` | React + Vite + TS | The quote-builder UI, ported from the design system in the source artifact. |

### The pie-parser integration (`backend/app/pie_service.py`)

The heart of the portal. It loads pie-parser once and reuses its identity-first
`resolve_rfq.run()` orchestration, then maps the engine's verdict onto the
design's relationship vocabulary:

| pie-parser outcome | Portal relationship |
|--------------------|---------------------|
| Authoritative identity (`AUTO_MATCH` / `CONFIRMED`, `SAME_PRODUCT`) | **EXACT** |
| Requirement + top equivalence score ≥ 0.85 | **TECH** (technical equivalent) |
| … score 0.60–0.85 | **COMPAT** |
| … score < 0.60 | **POSSIBLE** |
| `AMBIGUOUS` / `CONFLICT` | **AMBIGUOUS** (abstain, show options) |
| Unresolved / no match | **UNRESOLVED** |
| Engine failure | **PIE OFFLINE** (line degrades; the quote never fails) |

Availability, list price and landed cost come from the **Zoho** layer (keyed by
the manufacturer MM# resolution returns), because pie-parser deliberately never
carries commercial data.

### Zoho boundary (`backend/app/zoho.py`)

A `ZohoService` protocol with a deterministic `MockZoho` adapter so the whole
flow works offline today. Values (list price, cost, stock, in-books) are derived
from a stable hash of the item code, and a few codes are steered into the
zero-stock / not-in-books / unknown-availability states the design exercises.
See **Wiring real Zoho** below.

### Role-gated economics

`Line.to_dict(mgmt)` only serialises the `economics` block (cost, margin,
below-floor) and the margin-floor banner for a **management** principal. The
sales client never receives cost or margin over the wire — enforced in the
serializer, not by the UI.

## Setup

Requires Python 3.11+ and Node 20+.

```bash
git clone https://github.com/srisankethu/pie-portal
cd pie-portal

# fetch the pie-parser engine at its pinned commit (into ./pie-parser)
./scripts/setup_pie_parser.sh

# backend
python3 -m pip install -r backend/requirements.txt
python3 scripts/build_catalog.py          # decode the PIE catalogue (~13 MB, gitignored)

# frontend
cd frontend && npm install && cd ..
```

`pie-parser` is fetched at a pinned commit into `./pie-parser` (gitignored)
rather than committed here, so the integration always builds against a known-good
engine revision. Point `PIE_PARSER_ROOT` at an existing checkout to reuse one.

The catalogue is built from the corpus bundled in pie-parser
(`corpora/kmt_zcnc_2026-07_nomenclature.csv`, 6,717 real Kennametal/WIDIA rows).
It is deterministic, large, and therefore gitignored; the backend also builds it
lazily on first startup if absent.

## Run

```bash
make dev          # backend on :8000, frontend on :5173 (proxies /api → :8000)
# or separately:
make backend
make frontend
```

Open http://localhost:5173 and sign in:

- **r.nair@sanketh.in** — salesperson (no economics)
- **s.menon@sanketh.in** — management (full economics + margin floor)

Any password. Click **Paste RFQ → Use sample RFQ → Resolve & add**.

## Test

```bash
make test         # backend pytest (pie-parser integration, pricing, API flow)
```

The backend suite covers the real pie-parser resolution (exact identity, fuzzy
requirement → ranked candidates, unresolved), the pricing/margin engine, and the
end-to-end API including the role-gating of economics.

## Scope

This first implementation covers the **core quoting flow**: sign-in + roles, RFQ
intake, the pie-parser resolution grid, the supply-selection drawer, pricing with
the management margin floor, Zoho item creation, and estimate creation. The
design's price-exception requests, management approvals, audit log and analytics
are intentionally deferred to a follow-up (the role model and economics gate are
already in place to build on).

## Wiring real Zoho

Implement the `ZohoService` protocol (`backend/app/zoho.py`) against the live
Zoho Books API — `get_item`, `create_item`, `create_estimate`, `available` — and
return it from `get_zoho` in `backend/app/deps.py`. No routers or UI change: the
mock and the real adapter are interchangeable.

## Layout

```
pie-parser/            pinned clone — the resolution engine (fetched, gitignored)
backend/
  app/
    pie_service.py     in-process pie-parser bridge + relationship mapping
    catalog.py         build/locate products.jsonl from the pie-parser corpus
    zoho.py            ZohoService protocol + deterministic MockZoho
    pricing.py         management-only recommended price + margin floor
    security.py        demo accounts, roles, signed tokens
    store.py           quote/line state, status derivation, role-gated serialize
    routers/           auth + quote endpoints
  tests/               pie-parser integration, pricing, full API flow
frontend/src/          React quote-builder UI (ported design system)
scripts/build_catalog.py
```
