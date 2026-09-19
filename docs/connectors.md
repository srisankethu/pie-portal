# ERP connectors — reading a US client's books

The platform was born on Zoho Books. US clients run different systems, so the
ingestion layer now reads six more through one registry:

| Connector | System | API used | Sign-in |
|---|---|---|---|
| `netsuite` | Oracle NetSuite | SuiteQL for the reads (queries only), the REST record API for the estimate write | Token-based auth (four values from one integration record + access token) |
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
card; rotation replaces the stored **secrets** in one operation for every
company using it (`POST /api/v1/connections/{id}/rotate-erp`). The non-secret
settings beside them — the environment, the endpoint version, the OData path —
are kept where the rotation did not name them, and the form opens showing them
so an omission is a decision rather than an accident. Replacing them wholesale
is how a Business Central connection that re-typed only its client secret lost
`environment`, fell back to production, and passed its check: the same company
GUID exists on the live tenant. Clearing one now means reconnecting, which is
the accepted side of that trade.

## What a connector may create

Reading a system and writing to it are different grants, different code and
different days, so they are declared separately: `READ_STAGES` names what a
connector pulls, `WRITE_STAGES` names what the platform can create there. Today
`WRITE_STAGES` is `("sales_quotes",)` and four connectors declare it — `zoho`,
`netsuite`, `dynamics365` and `acumatica`, which is half the registry plus the
incumbent. `prophet21`, `sagex3` and `sage100` read only, and their access copy
says so.

Declaring the write and being able to perform it stay two questions.
`connections.writes_for` answers the first from the connector's spec, and from
`REQUIRED_SCOPES` for Zoho; `quote_writer_ready` answers the second by also
requiring a quote-write adapter on this side, and it is that second answer the
catalog serves as `can_write_quotes`, so a screen never offers a send that
would refuse. The two coincide at all four today. They come apart exactly while
a writer is being built, which is the window the distinction exists for.

Both lists are pinned against the implementation **in both directions**. A
declared capability with no `create_<stage>` method sends an owner to grant a
permission for something that cannot happen; a `create_<stage>` method nobody
declared creates records in a system nobody was asked to permit it in. Zoho is
pinned separately (`test_zohos_declared_writes_match_what_the_adapter_can_actually_create`)
because it is not in the `ingestion/erp` registry and the registry-wide pin
cannot see it — which is exactly how its write scopes went years undeclared.

**A registry connector's write is never replayed.** In `erp/transport.py` an
unmarked non-GET is abandoned on all four of the answers that cannot be told
apart from a success whose response was lost — a 5xx, a dropped connection, a
429 and a 401. Each raises `SourceWriteUncertain`, and the adapter settles it by
reading the record back under a caller-supplied reference — see
`ingestion/write_settle.py`. A connector whose target system cannot carry a
re-checkable reference cannot support a write at all, and should declare none.

**Zoho is not quite that, and this sentence used to cover it anyway.**
`zoho_client.py` is the older transport and still replays a write on two of
those four: its 401 branch reasons that a rejected token never reached the books
and its 429 branch that the limiter refuses a call outright rather than
half-applying it. `erp/transport.py` was written afterwards and refuses both
readings in as many words — a gateway can mint either *after* the backend has
accepted the call, which is how one quote becomes two. Only the 5xx case agrees
across the two. The fourth, a dropped connection, is not replayed on either
side, but `zoho_client._request` wraps nothing around the call itself, so the
fault escapes as a bare transport error instead of `ZohoWriteUncertain` — which
reads to a write caller as "nothing happened", the reading `erp/transport.py`
added its own handler to stop. So the guarantee is the registry's, and the
incumbent — the connector carrying most of the quote writes today — has the
weaker one on three of the four. Written down rather than smoothed over: a
blanket claim is exactly how the narrower path stops being looked at.

## What each sign-in must already be granted

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
| `netsuite` | Setup → Users/Roles → Manage Roles, on the token's role | Setup and Reports permissions plus View on each list/transaction, and Create on Estimate for the send |
| `dynamics365` | Entra ID app registration + permission sets on the app's user | `API.ReadWrite.All` with admin consent (BC publishes no read-only variant), then read on each entity plus create on sales quotes |
| `acumatica` | User Security → Access Rights by Role | Endpoint access plus View Only per screen, and Insert on Sales Orders for the send |
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
- **Prophet 21**: AP invoices booked against receipts rather than item lines
  import as payables without cost lines, named per bill in the sync report.
- **Sage 100**: purchase costs — its AP history carries GL distributions, not
  item lines, so margin stays UNKNOWN for a Sage 100 book. It is also the one
  connector with no bills stage at all; the two facts are the same fact.
- **Customer payments — four of the six**: Business Central, Prophet 21,
  Sage X3 and Sage 100. Only NetSuite and Acumatica read them. Business
  Central's receivables still age correctly from invoice `remainingAmount`, but
  what none of the four has is the date money actually arrived, so on those
  books an invoice looks paid the day it was raised. This entry named Business
  Central alone for as long as four connectors were missing it: once a gap is
  on the list under one connector's name, nobody rereads it to count who else
  has it.
- **Quotes, users and vendor payments — all six**. No ERP connector reads any
  of the three. `REQUIRED_SCOPES` spells out what each buys, because Zoho asks
  for all three: quotes are the denominator of a win rate, so without them a
  book sees only what it invoiced and a lost quote leaves no trace; users map a
  salesperson to a platform account, and without them accounts stay unassigned
  and every decision routes to management; vendor payments are the money-out
  half of liquidity, which one side of the ledger cannot give. On any of the
  six, those screens are empty by construction. That is the correct behaviour
  — and it is a gap, which is what this list is for.
- **All six**: credit notes and per-location stock (Zoho-only today).
- **`created_time`** — *when the source system recorded the document*, which is
  a different fact from its date and from when this platform synced it. Every
  connector now states whether its system exposes one at all, in
  `ConnectorSpec.records_source_time` and `source_time_note`; both are required
  fields with no default, so a connector cannot answer by staying silent. Five
  of the six carry it (Acumatica `CreatedDateTime`, Prophet 21 `date_created`,
  Sage X3 `CREDAT`/`CRETIM`, Sage 100 `DateCreated`/`TimeCreated`, NetSuite
  `createddate` — the SuiteQL column, not the `datecreated` saved-search id).
  Business Central declares **False**: its published v2.0 invoice resources
  expose only `lastModifiedDateTime`, so the gap is that API's rather than ours.
  None of these names is verified against a live tenant.

  **Carrying it is necessary and not sufficient, and three connectors stop
  here.** `clock.utc_stamp` refuses a stamp with no UTC offset and
  `normalize._recorded_at` drops what it refuses, so `'2026-01-29T14:11:02'` and
  a bare date both become `None`. NetSuite states `createddate` in PST whatever
  the account's zone; Sage X3 and Sage 100 split the date and the time across
  two columns and zone neither. Only Acumatica (`datetimeoffset`) and probably
  Prophet 21 deliver something placeable on the UTC line. **No offset is
  invented anywhere** — which zone a book states is an owner's answer, and a
  fixed one for NetSuite would be a DST bug.

  Normalisation is no longer the second ceiling, and this paragraph said it was
  for one release too long. `SourceRef.recorded_at` is carried by **all
  seventeen** normalizers; it said three, which was true until 17337ae ("Stop
  dropping the source's own clock in fourteen normalizers") and was not updated
  with it. `domain/spec.py` marks the field EXPECTED at all nineteen places a
  `source_ref` appears, which is correct and always was — so a reader had no
  reason to distrust the seventeen. What that cost is an investigation pointed
  at the wrong layer: a null `source_recorded_at` on a Sage X3 location or
  purchase order sends someone hunting `normalize.py` for a missing
  `recorded_at=` that is already there, when the ceiling is the one the
  paragraph above states — X3 splits `CREDAT`/`CRETIM` and zones neither, and
  `_recorded_at` drops what `clock.utc_stamp` refuses.

  The degradation is deliberate and safe at every one of those steps:
  `_recorded_at` returns `None` rather than a guess, and a row with no recorded
  time is **excluded from quote-diagnosis evidence and counted**, never imputed
  from its own date. A book on one of these connectors diagnoses nothing rather
  than diagnosing from evidence it could not have had.

**No test catches a gap of this kind, and none can.** The both-ways pin in
`test_connector_permissions` holds a connector's *declared* reads against its
*implemented* reads, and a stage that is neither declared nor implemented
satisfies it on both sides. That pin is what stops the two answers drifting
apart; it has nothing to say about a stage that went missing from both, which
is why the only thing keeping this list honest is somebody writing it down.

A US client organization sets its own `currency` (e.g. USD) and timezone; a
document denominated in anything else is refused at the seam and named on the
sync report, exactly as the Zoho pull refuses them.

## What the read-only three cannot be written to, and why

Desk research against each vendor's own documentation, so the next person does
not repeat it. It was written when every ERP in the registry declared
`writes=()` and that framing is gone: NetSuite, Business Central and Acumatica
have writers now, and `prophet21`, `sagex3` and `sage100` are what is left. The
research is why they are what is left, which is worth more than it was when it
described all of them — the answers differ enough that "add a writer" is a
different size of job for each, and for one of them it is not a job at all.

The question in each case is the one the settle protocol forces: **is there a
write surface reachable from the transport this connector already speaks, and
can it carry a caller-supplied reference that a later read can find?** Without
the second, a write must be refused outright rather than sent — an uncertain
write that cannot be looked up is unanswerable.

The three writers are what a yes looks like, and all three answer the second
half with the quote's own reference: NetSuite upserts on an `externalId` built
from it, Business Central sends it as `externalDocumentNumber`, and Acumatica
reads on it before inserting. Each refuses outright, before sending anything,
when the quote has no reference to carry — so the P21 header's customer PO
number below is being measured against a bar three connectors have cleared,
not a hypothetical one.

- **Prophet 21 — viable, on a surface the connector does not yet speak.** The
  OData Data Services API this connector reads through is for retrieval; on
  cloud P21 the underlying SQL access is read-only. Writes go through the
  separate **Transaction API**, which creates native `oe_hdr` / `oe_line`
  records and returns a real P21 order number. Its header carries a customer
  **PO number**, which is the re-checkable reference the protocol needs. The
  token sign-in the connector already performs reaches it, so the credential
  shape does not change — the API surface does.

- **Sage X3 — uncertain, and site-specific.** X3's REST layer creates records
  only for modules exposed through Classes and representations
  (`POST …/x3/$$prod/<ENTITY>?representation=<ENTITY>.$edit`). Whether the
  sales-order object `SOH` has such a representation on a given installation is
  not something the documentation settles; the route the community documents
  for creating an order is a **SOAP web service against `SOH` using its `save`
  method**, which is neither REST nor the SData surface this connector reads
  through. Two hops away from where the connector stands, and the answer likely
  varies per site. Do not plan a writer for X3 without a specific
  installation's own API configuration in hand.

- **Sage 100 — negative, and that is a complete answer.** There is no native
  REST write. SData, which this connector reads through, is **deprecated**.
  Writes go through the **Business Object Interface**, a COM component invoked
  in-process on Windows — not something a Python service reaches over HTTP at
  all. A Sage 100 book cannot be written to from this platform without
  third-party middleware standing in front of it, and that middleware would be
  the thing this platform integrated with, not Sage 100.

  The SData deprecation is worth knowing for the **read** path too: it is the
  surface this connector's entire sync depends on.

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
