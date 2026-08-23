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

## What each sign-in must already be granted

## What a connector may create

Reading a system and writing to it are different grants, different code and
different days, so they are declared separately: `READ_STAGES` names what a
connector pulls, `WRITE_STAGES` names what the platform can create there. Today
`WRITE_STAGES` is `("sales_quotes",)` and two connectors declare it — `zoho`
and `dynamics365`. The rest read only, and their access copy says so.

Both lists are pinned against the implementation **in both directions**. A
declared capability with no `create_<stage>` method sends an owner to grant a
permission for something that cannot happen; a `create_<stage>` method nobody
declared creates records in a system nobody was asked to permit it in. Zoho is
pinned separately (`test_zohos_declared_writes_match_what_the_adapter_can_actually_create`)
because it is not in the `ingestion/erp` registry and the registry-wide pin
cannot see it — which is exactly how its write scopes went years undeclared.

A write is never replayed. A non-idempotent call that fails without an answer
raises `SourceWriteUncertain`, and the adapter settles it by reading the record
back under a caller-supplied reference — see `ingestion/write_settle.py`. A
connector whose target system cannot carry a re-checkable reference cannot
support a write at all, and should declare none.

A half-granted sign-in is the most common way a connection authenticates and
then returns nothing: the credential works, one endpoint refuses, and the sync
reports zero rows of that kind with nothing obviously wrong. So every system
declares its own access requirements, and the **Add a company** panel lists
them under the connector the tabs have selected — the same panel, so the two
have nowhere to disagree.

They are declared as `Permission(name, why, required, reads, writes)` on the connector's
own spec, and Zoho's scope list is the same type in `ingestion/connections.py`
(Zoho is a row in the catalog, not a separate panel — that separation is what
let the screen show `ZohoBooks.*.READ` while NetSuite was selected). `name` is
what that system's admin console calls the grant, because that is what the
person granting it is reading:

| Connector | Where it is granted | Shape |
|---|---|---|
| `zoho` | Zoho API console, scope field | Ten `ZohoBooks.*.READ` scopes plus `estimates.CREATE`, `estimates.READ` and `settings.CREATE` for the write, pasted as one string |
| `netsuite` | Setup → Users/Roles → Manage Roles, on the token's role | Setup and Reports permissions plus View on each list/transaction |
| `dynamics365` | Entra ID app registration + permission sets on the app's user | `API.ReadWrite.All` with admin consent (BC publishes no read-only variant), then read on each entity plus create on sales quotes |
| `acumatica` | User Security → Access Rights by Role | Endpoint access plus View Only per screen |
| `prophet21` | P21 user API flag + the middleware's exposed views | Per OData view |
| `sagex3` | Syracuse role | SData access plus read per X3 table |
| `sage100` | Library Master → Role Maintenance | SData access plus inquiry per module |

`reads` names the sync stages a grant feeds, and that is what keeps the lists
honest: `test_connector_permissions` asserts the stages a connector's spec asks
for are exactly the `list_*` stages its source implements, in both directions.
A stage read but never asked for is a grant *nobody can ever have given*; a
stage asked for but never read is access requested for no reason. Zoho gets the
same pinning from `test_every_scope_the_pull_uses_is_declared`.

Only Zoho takes its grants as one pasteable string; every ERP in the registry
is clicked in an admin console, so their entries carry no `permission_string`
and the screen shows no copy box for them.

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
`ConnectorSpec` (fields, company term, source factory, and the permissions its
sign-in needs), import it from `erp/__init__.py`, and add a display row to
`domain/origin.CONNECTORS`.
The catalog endpoint, the connect form, validation, encryption, rotation,
checks, sync dispatch and provenance all follow from the spec — nothing else
branches on the key. Field-level truth to keep: translators never invent a
value; an unreadable date or missing field surfaces as a named skip, never a
default.
