# ERP connectors — reading a US client's books

The platform was born on Zoho Books. US clients run different systems, so the
ingestion layer now reads six more through one registry:

| Connector | System | API used | Sign-in |
|---|---|---|---|
| `netsuite` | Oracle NetSuite | SuiteQL (read-only queries) | Token-based auth (four values from one integration record + access token) |
| `dynamics365` | Dynamics 365 **Business Central** | Standard API v2.0 (OData) | Entra ID app registration, client-credentials grant |
| `acumatica` | Acumatica | Contract-based REST | Session sign-in as an integration user (signed out after every pull) |
| `prophet21` | Epicor Prophet 21 | Middleware OData views | API user token sign-in |
| `sagex3` | Sage X3 | SData REST (JSON) | Basic auth as an integration user |
| `sage100` | Sage 100 | SData feed (Atom) | Basic auth |

"Dynamics 365" names a product family; this connector reads **Business
Central**. Finance & Operations is a different product with a different API
and would be its own connector.

## How it fits the architecture

Nothing below the source knows connectors exist. Each connector module in
`backend/app/ingestion/erp/` does exactly two jobs:

1. **A client** for that system's auth, pagination and rate manners, built on
   the shared paced/retrying transport (`erp/transport.py`) and raising the
   neutral failure taxonomy (`ingestion/errors.py`) so the sync reacts to the
   *kind* of failure without knowing the system.
2. **A translator** from that system's native records to the platform's
   canonical payload shape — the shape `ingestion/normalize.py` validates.
   One normalizer for every connector means one place where dates, decimals
   and the discount ladder are checked; the translators vary, the validation
   does not.

The sync (`SyncService`) then runs unchanged: same ordering, same
resumability, same skip reporting, same provenance. Every row carries the
identity triple `connector + connection + external id`, and `SourceRef.system`
is stamped with the connection's own connector — a NetSuite pull leaves no
row anywhere claiming Zoho (`test_multi_connector_sync` pins this).

Connections and credentials live in the same two tables Zoho uses
(`zoho_connections` / `zoho_credentials` — the names are historical; both
carry a `connector` column now). A non-Zoho credential stores its secrets as
**one encrypted JSON document** whose field list the connector's spec
declares, and its identifying values (account id, tenant, environment)
readable beside it. Everything downstream of the credential row — sharing,
one-operation rotation, the delete guard, the multi-company plan gate, the
intelligence trial — is the same mechanism for every connector because it is
literally the same rows.

## Connecting one

Data & connection → **Add a company** → pick the system. The form renders
from the connector's own spec (`GET /api/v1/connections/catalog`), so what it
asks for is exactly what that system needs:

- **NetSuite** — an admin enables token-based authentication, creates one
  integration record (consumer key/secret) and one access token
  (token id/secret) for a role with query permission. OneWorld accounts can
  scope a connection to one subsidiary as `account:subsidiary`.
- **Business Central** — an admin registers an Entra ID app, grants it the
  Business Central API application permission with admin consent, and creates
  a client secret. After entering the credentials, “List the company choices”
  fills the company GUID by name.
- **Acumatica** — a dedicated integration user's name and password, the site
  URL, the tenant's login name, optionally a branch and the endpoint version.
- **Prophet 21** — the middleware URL and an API-enabled user. Sites whose
  OData views live under a non-default path or prefix enter theirs.
- **Sage X3** — the Syracuse URL, an integration user, and the folder
  (endpoint) the company's books live in.
- **Sage 100** — the SData provider URL, a user with SData access, and the
  three-character company code.

Every connection is checked at connect time and can be re-checked from its
card; rotation replaces the whole stored sign-in in one operation for every
company using it (`POST /api/v1/connections/{id}/rotate-erp`).

## What each connector does not read yet, said plainly

Gaps degrade visibly — screens say UNKNOWN or stay empty, and the sync report
names each skipped row. Nothing estimates around a gap.

- **NetSuite**: item stock levels and payment-to-invoice applications.
- **Business Central**: customer payments (receivables still age correctly
  from invoice `remainingAmount`).
- **Prophet 21**: AP invoices booked against receipts rather than item lines
  import as payables without cost lines, named per bill in the sync report.
- **Sage 100**: purchase costs — its AP history carries GL distributions, not
  item lines, so margin stays UNKNOWN for a Sage 100 book.
- **All six**: credit notes and per-location stock (Zoho-only today).

A US client organization sets its own `currency` (e.g. USD) and timezone; a
document denominated in anything else is refused at the seam and named on the
sync report, exactly as the Zoho pull refuses them.

## Adding connector number seven

Write one module in `backend/app/ingestion/erp/` that registers a
`ConnectorSpec` (fields, company term, source factory), import it from
`erp/__init__.py`, and add a display row to `domain/origin.CONNECTORS`.
The catalog endpoint, the connect form, validation, encryption, rotation,
checks, sync dispatch and provenance all follow from the spec — nothing else
branches on the key. Field-level truth to keep: translators never invent a
value; an unreadable date or missing field surfaces as a named skip, never a
default.
