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

## What a connector may create

Reading a system and writing to it are different grants, different code and
different days, so they are declared separately: `READ_STAGES` names what a
connector pulls, `WRITE_STAGES` names what the platform can create there. Today
`WRITE_STAGES` is `("sales_quotes",)` and four connectors declare it — `zoho`,
`dynamics365`, `acumatica` and `netsuite`, which is also the set
`connections._QUOTE_ADAPTERS` names as having a writer wired. Prophet 21, Sage 100
and Sage X3 read only, and their access copy says so.

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

The reference is also what makes an amended quote a *new* document. Every
writer's pre-flight answers with the document already under the reference it
was given, so the send mints a fresh reference per revision (`QB-0042-…` for
the first send, `-r2`, `-r3` after — `quote_service.revision_reference`) and
the adapter's job is unchanged: exact match on the reference, create only when
nothing is there. An adapter that matched loosely — a prefix, a case-folded
`-r2` against the bare reference — would report the old document as already
sent and the amendment would never reach the book; the connector tests pin the
exact re-check for Zoho, Business Central and Acumatica.

## Which connectors read quotes, and which deliberately do not

A quote read back is what gives a win rate a denominator: `READ_STAGES`
includes `quotes`, and the sync runs that stage for any source offering
`list_quotes`, with no branch naming a connector. Four do — `zoho`,
`dynamics365`, `acumatica` and `netsuite` — which is the same four that can
write one, and not by accident: each reads the quote back under **the same id
its writer returned**, so a quote this platform sent is recognisable as the
same document on the next pull rather than arriving as a stranger.

| Connector | Where a quote lives | Read back under |
|---|---|---|
| `zoho` | `estimates` | the estimate id |
| `dynamics365` | `salesQuotes` (lines via `$expand`) | the row's GUID, which the create returns |
| `acumatica` | `SalesOrder` rows of `OrderType` `QT` | the record's `id` GUID |
| `netsuite` | `transaction` rows of type `Estim` | the internal `t.id` |

Acumatica's is the one split: quotes and orders are one entity there, so
`list_quotes` and `list_sales_orders` each filter it and a `QT` row reaches
exactly one of them. The filter is **client-side**, against this module's own
warning that the contract API's filter grammar varies by build — a server-side
filter that silently matched nothing would not read as a slow pull but as a
finished listing of an empty book, and a finished empty listing is what the
deletion sweep acts on.

**Prophet 21 and Sage read no quotes yet, and that is a decision rather than a
backlog item.** P21 keeps quotes in `oe_hdr` beside orders and Sage in its own
sales documents, and which header field separates the two has not been
confirmed against vendor documentation. A guessed field name has two failure
modes here and both are silent: it matches nothing, which reads as a company
that has never quoted anybody, or it matches the wrong rows, which puts orders
in a win rate. They keep reading orders exactly as before. (P21's `oe_hdr`
read may therefore already include quotes as orders — the same unconfirmed
field would be needed to exclude them, so it is named here rather than fixed
on a guess.)

**A quote is read whatever status it wears, cancelled included.** The
`_is_trade` helper each connector shares asks an invoice's question — is this a
financial fact — and a quote's is *was this offered*. A draft or on-hold quote
plainly was; a cancelled one is the honest hard case, and it is read anyway.
Excluded, it would vanish from the denominator **and** the deletion sweep would
retire the row, dangling whatever loss reason a person had recorded against it,
which is the one thing in that table a re-sync cannot rebuild. Included, it sits
on a worklist wearing its ERP's own word until somebody looks. This is the same
divergence `ZohoApiSource.list_quotes` already states under "no status
exclusion".

**No registry connector classifies a quote's status.**
`normalize._QUOTE_VOCABULARY` and `_QUOTE_SENT_STATUSES` have an entry for Zoho
and for nobody else, so every quote from Business Central, Acumatica or
NetSuite reads `UNRECORDED` and not-known-to-be-sent whatever word its ERP
wrote on it. That under-claims on purpose: an unanswered quote sits on a
worklist somebody works, where a status read as WON invents a customer
decision and puts it in a win rate.
`test_no_registry_connectors_status_word_is_read_as_a_customer_decision` pins
it, and a row leaves that test only together with the vendor's own status list
cited beside it.

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
| `netsuite` | Setup → Users/Roles → Manage Roles, on the token's role | Setup and Reports permissions plus View on each list/transaction, and Create on Estimate — the one grant above View, which also carries the estimate read |
| `dynamics365` | Entra ID app registration + permission sets on the app's user | `API.ReadWrite.All` with admin consent (BC publishes no read-only variant), then read on each entity plus create on sales quotes |
| `acumatica` | User Security → Access Rights by Role | Endpoint access plus View Only per screen; Sales Orders (SO301000) carries both the order read and the quote read, and Insert for the send |
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
- **Prophet 21 and Sage (all three)**: quotes — see the section above for why a guessed header field is worse than the gap.
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

  A second ceiling used to sit behind that, and it is gone: `normalize`
  populated `SourceRef.recorded_at` on invoices, bills and quote documents only
  — three of its seventeen normalizers — although the field exists on every
  entity and `domain/spec.py` marks it EXPECTED at all nineteen. Every
  normalizer that builds a `SourceRef` now calls `_recorded_at`, so what a
  connector carries is what reaches the record, and a missing stamp is once
  again a statement about the source rather than about this layer.

  The Zoho client is the other half of that and was the last to close: it
  projected `created_time` on invoices, bills and estimates and dropped it from
  every other pull, so a synced contact, item, sales order, purchase order,
  credit note, vendor credit, vendor, customer payment or vendor payment
  carried no source clock at all. All of those carry it now. `/locations` is
  the one Zoho list that sends none, so a location still reaches `normalize`
  without one — the API's gap, not the projection's. Zoho is not in the
  `ingestion/erp/` registry and so declares no `ConnectorSpec`; there is no
  `records_source_time` for it to state.

  The degradation is deliberate and safe at every one of those steps:
  `_recorded_at` returns `None` rather than a guess, and a row with no recorded
  time is **excluded from quote-diagnosis evidence and counted**, never imputed
  from its own date. A book on one of these connectors diagnoses nothing rather
  than diagnosing from evidence it could not have had.

A US client organization sets its own `currency` (e.g. USD) and timezone; a
document denominated in anything else is refused at the seam and named on the
sync report, exactly as the Zoho pull refuses them.

## What each connector cannot be written to, and why

Desk research against each vendor's own documentation, so the next person does
not repeat it. Business Central, Acumatica and NetSuite have writers now (the
section above); Prophet 21, Sage 100 and Sage X3 still declare `writes=()`, and
the answers below differ enough that "add a writer" is a different size of job
for each of those three.

The question in each case is the one the settle protocol forces: **is there a
write surface reachable from the transport this connector already speaks, and
can it carry a caller-supplied reference that a later read can find?** Without
the second, a write must be refused outright rather than sent — an uncertain
write that cannot be looked up is unanswerable.

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
