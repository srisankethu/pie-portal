/** What each ERP landing page says, and where every sentence of it came from.
 *
 * These pages exist for two readers. A distributor searching for their own
 * system should land on a page that names it in the first line — "Prophet 21"
 * is what they call their problem, and a page that says "your ERP" is a page
 * about somebody else. And a buyer already talking to us wants to know what
 * this actually does against *their* stack before a call, not during one.
 *
 * The honesty rule the landing page keeps applies here twice over, because
 * these pages make narrower and more checkable claims than any other page on
 * the site. Every field below is read from the connector module that
 * implements it — the auth, the stage list, whether anything can be written,
 * and the gaps — and `erp.test.ts` holds the read stages and the write
 * capability against those modules' own source, in both directions. A stage
 * added to a connector and not to its page fails a test; a stage claimed here
 * and not implemented fails the same test.
 *
 * The gaps are on the page on purpose, and prominently. A distributor who has
 * survived one ERP implementation does not believe a capability list; they
 * believe a vendor who volunteers what their product does not do, and they
 * find out anyway in week three. `CLAUDE.md` §1 states the same rule for the
 * product itself — absence of evidence is never reported as good news — so a
 * page that hid these would be describing a different product.
 *
 * `evidence` is the one field with no source in the code, and it is a
 * placeholder for exactly that reason. See its comment.
 */

/** One thing PIE reads, and the record it reads it from. */
export interface ErpRead {
  /** The connector's own stage name, from `ingestion/erp/base.READ_STAGES`.
   *  `erp.test.ts` matches these against the connector module's declared
   *  permissions, so this is the field that keeps the page true. */
  stage: string;
  /** What a person who runs this system calls it. */
  label: string;
  /** The record or view it comes from, in that system's vocabulary. */
  source: string;
}

export interface ErpPageData {
  /** The URL segment: `/erp/{slug}`. */
  slug: string;
  /** The connector key in `backend/app/ingestion/erp/`, and the file
   *  `erp.test.ts` reads to check this page. */
  connector: string;
  /** Whether this system gives PIE a purchase cost at all — the connector
   *  declares a `bills` read. False on Sage 100, whose AP history records GL
   *  distributions rather than item lines, and a book with no cost has no
   *  margin, no floor and no drift: the page has to sell what it can actually
   *  do rather than the headline. `erp.test.ts` holds this against the
   *  connector's declaration and refuses a floor claim on a page without one,
   *  because the flag exists to stop that sentence being written, not to
   *  record that somebody remembered. */
  costed: boolean;
  /** The system's full name, as its own vendor writes it. */
  name: string;
  /** What people actually call it in a sentence. */
  short: string;
  title: string;
  description: string;
  /** How PIE signs in, from the connector's client. */
  connects: string;
  reads: ErpRead[];
  /** What PIE can create in this system, or null where it creates nothing.
   *  Read-only is a selling point to this buyer, not an omission. */
  writes: string | null;
  /** What has to be true in the ERP before the connect form will work. */
  setup: string;
  /** What PIE does not read from this system. Stated, never softened. */
  gaps: string[];
  /** The three things PIE does with what it read here. */
  fit: { title: string; body: string }[];
  /** PLACEHOLDER — real language from real distributors running this system:
   *  what they say about margin on their own quote desk, in their words, with
   *  permission to print it. Nothing in this repository knows what a Prophet 21
   *  distributor reports, so nothing here may claim to. Replace it with real
   *  research or delete the panel; do not fill it with a plausible sentence. */
  evidence: string;
}

export const ERP_PAGES: ErpPageData[] = [
  {
    slug: "prophet-21",
    connector: "prophet21",
    costed: true,
    name: "Epicor Prophet 21",
    short: "Prophet 21",
    title: "PIE for Epicor Prophet 21 · margin control on the book you already run",
    description:
      "PIE reads your Prophet 21 middleware's OData views — customers, suppliers, the "
      + "item master, invoices and AP invoices with their lines, and orders — then checks "
      + "every new quote line against your own margin floor before it goes out. Read-only: "
      + "nothing is ever created in Prophet 21.",
    connects:
      "A token sign-in as an API-enabled Prophet 21 user, against your middleware's "
      + "OData views, re-minted hourly and paged 500 rows at a time. The form asks for "
      + "the middleware URL, that user's credentials and your company code — and, where "
      + "your site keeps its views under a non-default path or prefix, for those too. "
      + "The connection is checked when you save it, so a wrong value fails there rather "
      + "than on the first nightly sync.",
    reads: [
      { stage: "contacts", label: "Customers", source: "customer" },
      { stage: "vendors", label: "Suppliers", source: "supplier" },
      { stage: "items", label: "The item master", source: "inv_mast" },
      { stage: "invoices", label: "Invoices, with their lines", source: "invoice_hdr · invoice_line" },
      { stage: "bills", label: "AP invoices, with their lines", source: "apinv_hdr · apinv_line" },
      { stage: "sales_orders", label: "Sales orders", source: "oe_hdr" },
      { stage: "purchase_orders", label: "Purchase orders", source: "po_hdr" },
    ],
    writes: null,
    setup:
      "Two things have to be true before the form connects: the API user is enabled for "
      + "API access in P21 (Maintain Users → API), and each view above is exposed on the "
      + "middleware and readable by that user — a view that is not exposed answers 404, "
      + "which reads like a wrong URL rather than a missing grant. And because a P21 "
      + "middleware usually sits on your own network, it has to be reachable on a public "
      + "hostname: PIE refuses a source address that resolves inside a private range, when "
      + "you save it and again on every fetch.",
    gaps: [
      "Customer payments are not read from Prophet 21. Payment timing, the collections "
      + "worklist and days-to-pay have nothing to read on a P21 book, and they say so "
      + "rather than estimate around it.",
      "AP invoices booked against PO receipts rather than item lines arrive as payables "
      + "with no cost lines. Each one is named on the sync report rather than averaged "
      + "into a cost figure nobody could trace.",
      "Stock levels are not read from Prophet 21 in this version, so the stock and GMROI "
      + "screens stay empty on a P21 book.",
      "Quotes are not imported from any ERP, P21 included, so a win rate has no "
      + "denominator until you start quoting here.",
      "Vendor payments are not read from any ERP, so what has actually been paid out is "
      + "outside what PIE can see on this book.",
      "Credit notes are not read from Prophet 21 in this version, so a returned or "
      + "credited line still counts as sold until you say otherwise.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "Every quote line, against your own floor",
        body:
          "Your P21 invoice and AP-invoice lines are what a floor is computed from: what "
          + "you sold, to whom, and what it cost you. PIE checks each new line against the "
          + "policy you set and routes a breach for sign-off — the platform holds it, not "
          + "the salesperson, and the sign-off is on record.",
      },
      {
        title: "Margin drift, per customer and item",
        body:
          "Computed from the line-level history P21 already holds and compared across "
          + "windows, with every figure stamped with the version of the policy that judged "
          + "it. The arithmetic is deterministic and the operands come with it, so any "
          + "number on the screen opens into the rows it came from.",
      },
      {
        title: "The accounts that went quiet",
        body:
          "Decline is read from the invoice history the first pull brings in, so it works "
          + "on a P21 book from the first sync — no quotes to record first, no payments "
          + "needed, nothing to configure.",
      },
    ],
    evidence: "{{PROPHET21_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    slug: "netsuite",
    connector: "netsuite",
    costed: true,
    name: "Oracle NetSuite",
    short: "NetSuite",
    title: "PIE for Oracle NetSuite · margin control on the book you already run",
    description:
      "PIE reads your NetSuite book over SuiteQL — customers, vendors, items, invoices, "
      + "bills, customer payments and orders — checks every new quote line against your own "
      + "margin floor, and can create the quote back in NetSuite as an estimate. Nothing "
      + "else is ever written.",
    connects:
      "Token-based authentication: an administrator creates one integration record and "
      + "one access token for a role with query permission, and every request carries an "
      + "OAuth 1.0a signature built from those four values. No browser round-trip and no "
      + "certificate to renew. Reads go through SuiteQL and every query is a plain SELECT; "
      + "a OneWorld account can scope a connection to a single subsidiary.",
    reads: [
      { stage: "contacts", label: "Customers", source: "customer" },
      { stage: "vendors", label: "Vendors", source: "vendor" },
      { stage: "items", label: "The item master", source: "item" },
      { stage: "invoices", label: "Invoices, with their lines", source: "transaction · CustInvc" },
      { stage: "bills", label: "Vendor bills, with their lines", source: "transaction · VendBill" },
      { stage: "customer_payments", label: "Customer payments", source: "transaction · CustPymt" },
      { stage: "sales_orders", label: "Sales orders", source: "transaction · SalesOrd" },
      { stage: "purchase_orders", label: "Purchase orders", source: "transaction · PurchOrd" },
    ],
    writes:
      "A quote built in PIE can be created in NetSuite as an estimate — and that is the "
      + "only thing PIE ever creates in NetSuite. The write is idempotent by construction: it "
      + "upserts on the external id, so pressing send twice cannot put two estimates in "
      + "front of your customer. It refuses rather than guessing — no NetSuite customer, no "
      + "priced lines, or a line with no price or quantity, and nothing is sent. NetSuite "
      + "would price an omitted rate from the item record, which is a number nobody here "
      + "chose.",
    setup:
      "The permissions are granted on the role the access token is issued for, at Setup → "
      + "Users/Roles → Manage Roles. Three of them are sign-in level — Log in using Access "
      + "Tokens, REST Web Services, and SuiteAnalytics Workbook; a role with every list "
      + "permission and not that last one authenticates and then answers nothing. Creating "
      + "an estimate is the only permission above View level, and it is optional: without "
      + "it everything else works and every send refuses.",
    gaps: [
      "Item stock levels are not read in this version — NetSuite keeps them in a "
      + "structure a flat query reads poorly — so the stock and GMROI screens stay empty "
      + "on a NetSuite book and say why.",
      "Payment applications, which payment settled which invoice, are not read either. "
      + "The payments themselves are, so cash is visible; days-to-pay has no observations "
      + "and reports that rather than a guess.",
      "Estimates already in NetSuite are not imported, so a win rate has no denominator "
      + "until you start quoting here.",
      "Vendor payments are not read from any ERP, NetSuite included, so what has actually "
      + "been paid out is outside what PIE can see on this book.",
      "Credit notes are not read from NetSuite in this version, so a returned or credited "
      + "line still counts as sold until you say otherwise.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "Every quote line, against your own floor",
        body:
          "Cost comes off the vendor bills and the item master you already keep in "
          + "NetSuite. PIE checks each new line against the policy you set, routes a breach "
          + "for sign-off, and — where the grant allows it — writes the agreed quote back "
          + "as an estimate rather than making somebody retype it.",
      },
      {
        title: "Margin drift, per customer and item",
        body:
          "SuiteQL gives PIE the line-level history to compute a margin per customer-item "
          + "and compare it across windows. Every figure is stamped with the version of the "
          + "policy that judged it and carries the operands it was computed from.",
      },
      {
        title: "Decline and payment behaviour",
        body:
          "Customer payments are read on a NetSuite book, so slipping payment and quiet "
          + "decline are both on the attention list from the first sync. What is not read "
          + "— which payment cleared which invoice — is stated on the screen that would "
          + "have used it rather than estimated around.",
      },
    ],
    evidence: "{{NETSUITE_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    slug: "acumatica",
    connector: "acumatica",
    costed: true,
    name: "Acumatica",
    short: "Acumatica",
    title: "PIE for Acumatica · margin control on the book you already run",
    description:
      "PIE reads your Acumatica tenant over the contract-based REST API — customers, "
      + "vendors, stock items with quantities, invoices, bills, payments and orders — checks "
      + "every new quote line against your own margin floor, and can create the quote back "
      + "as a sales quote. Nothing else is ever written.",
    connects:
      "A session sign-in as a dedicated integration user against the contract-based REST "
      + "API, from your site URL, the tenant's login name and — if you use them — a branch "
      + "and an endpoint version. A connection check and a quote write each sign out the "
      + "moment they finish — Acumatica counts live sessions against your licence, and a "
      + "leaked session is a seat you are paying for and nobody is sitting in.",
    reads: [
      { stage: "contacts", label: "Customers", source: "Customer" },
      { stage: "vendors", label: "Vendors", source: "Vendor" },
      { stage: "items", label: "Stock items, with quantity on hand and available", source: "StockItem" },
      { stage: "invoices", label: "Sales invoices, with their lines", source: "SalesInvoice" },
      { stage: "bills", label: "Bills, with their lines", source: "Bill" },
      { stage: "customer_payments", label: "Payments, with the documents they apply to", source: "Payment" },
      { stage: "sales_orders", label: "Sales orders", source: "SalesOrder" },
      { stage: "purchase_orders", label: "Purchase orders", source: "PurchaseOrder" },
    ],
    writes:
      "A quote built in PIE can be created in Acumatica as a sales quote — a sales order "
      + "of type QT — and that is the only thing PIE ever creates in Acumatica. Acumatica's PUT "
      + "always inserts, so it is not idempotent on its own; PIE reads the reference back "
      + "before it sends, so a second press cannot make a second quote. It refuses rather "
      + "than guessing: no customer, no priced lines, or a line with no price or quantity, "
      + "and nothing is sent.",
    setup:
      "The rights are granted on the integration user's role, at User Security → Access "
      + "Rights by Role, and one of them is easy to miss: Web Service Endpoints → Default, "
      + "without which the sign-in succeeds and every request comes back 403. Sales Orders "
      + "(SO301000) with Insert is what allows a quote to be sent and is optional — View "
      + "Only is enough for everything else. A self-hosted Acumatica has to be reachable on "
      + "a public hostname: PIE refuses a source address that resolves inside a private "
      + "range, when you save it and again on every fetch.",
    gaps: [
      "Quotes already in Acumatica are not imported — no ERP's are — so a win rate has "
      + "no denominator until you start quoting here.",
      "Vendor payments are not read from any ERP, Acumatica included, so what has "
      + "actually been paid out is outside what PIE can see on this book.",
      "Credit notes are not read from Acumatica in this version, so a returned or "
      + "credited line still counts as sold until you say otherwise.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "Every quote line, against your own floor",
        body:
          "Cost comes off the stock item's own last and average cost and off your bills. "
          + "PIE checks each new line against the policy you set, routes a breach for "
          + "sign-off, and — where the grant allows it — creates the agreed quote back in "
          + "Acumatica as a QT sales order.",
      },
      {
        title: "Margin drift, with stock in the picture",
        body:
          "Acumatica is the connector that gives PIE quantity on hand and available "
          + "outright, alongside line-level history, so what a line returns on the cash it "
          + "ties up is computable rather than absent — and every figure carries the policy "
          + "version that judged it. Business Central reports stock only where its API "
          + "version does; the rest report none at all.",
      },
      {
        title: "Decline, and who actually pays late",
        body:
          "Payments come with the documents they apply to, so days-to-pay is measured "
          + "rather than approximated on an Acumatica book, and quiet decline is on the "
          + "attention list from the first sync.",
      },
    ],
    evidence: "{{ACUMATICA_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    slug: "dynamics-365-business-central",
    connector: "dynamics365",
    costed: true,
    name: "Microsoft Dynamics 365 Business Central",
    short: "Dynamics 365 BC",
    title:
      "PIE for Microsoft Dynamics 365 Business Central · margin control on the book you already run",
    description:
      "PIE reads your Dynamics 365 BC company over the standard API v2.0 — customers, "
      + "vendors, items, sales and purchase invoices with their lines, and orders — checks "
      + "every new quote line against your own margin floor, and can create the agreed "
      + "quote back in Business Central as a sales quote. Nothing else is ever written.",
    connects:
      "An Entra ID app registration on the client-credentials grant: an administrator "
      + "registers one app, consents to the Business Central API permission and creates "
      + "one client secret. No browser round-trip on a sync and no certificate to renew. "
      + "The form asks for the directory (tenant) id, the application id and secret, and "
      + "the environment — leave it blank for production — and then lists the companies "
      + "that grant can actually see, so the company is picked by name instead of by "
      + "pasting a GUID. A document's lines ride on the same request as its header "
      + "($expand), and the date window is filtered on Microsoft's side rather than here.",
    reads: [
      { stage: "contacts", label: "Customers", source: "customers" },
      { stage: "vendors", label: "Vendors", source: "vendors" },
      { stage: "items", label: "The item master", source: "items" },
      { stage: "invoices", label: "Sales invoices, with their lines", source: "salesInvoices · salesInvoiceLines" },
      { stage: "bills", label: "Purchase invoices, with their lines", source: "purchaseInvoices · purchaseInvoiceLines" },
      { stage: "sales_orders", label: "Sales orders", source: "salesOrders" },
      { stage: "purchase_orders", label: "Purchase orders", source: "purchaseOrders" },
    ],
    writes:
      "A quote built in PIE can be created in Business Central as a sales quote — the "
      + "header, then one call per line, because salesQuoteLines is a child entity set and "
      + "Business Central publishes no single call that carries both. That is the only "
      + "thing PIE ever creates in Business Central. Business Central puts no uniqueness "
      + "on externalDocumentNumber, so a second press would simply make a second quote: "
      + "PIE reads the reference back before it sends rather than relying on the repeat to "
      + "fail. It refuses rather than guessing — no reference to find it by afterwards, no "
      + "Business Central customer, no priced lines, a line naming an item that does not "
      + "already exist there, or a line with no price or quantity, and nothing is sent. "
      + "Business Central would fill an omitted price from the item card, which is a "
      + "number nobody here chose.",
    setup:
      "Two places, and a grant in one of them without the other looks like a broken "
      + "connection rather than a missing permission. On the app registration: the "
      + "Dynamics 365 Business Central application permission — API.ReadWrite.All, with "
      + "admin consent. It is the only application permission Microsoft publishes for this "
      + "API and there is no read-only variant, so the grant is wider than what PIE does "
      + "with it: everything above is a read, and the one write is the sales quote. Inside "
      + "each company: the permission sets on the app's own user (Microsoft Entra "
      + "Applications → the app → Permission sets), starting with D365 BASIC — without it "
      + "the token is valid and every company answers 401 — then read access to the "
      + "entities above.",
    gaps: [
      "“Dynamics 365” names a family, and this reads one member of it: Business "
      + "Central, the ERP that NAV became. Finance & Operations is a different product "
      + "with a different API and PIE does not read it.",
      "Customer payments are not read from Business Central. Payment timing, the "
      + "collections worklist and days-to-pay have nothing to read on this book, and they "
      + "say so rather than estimate around it.",
      "Stock on hand is read only where your API version reports it on the item. Where it "
      + "does not, PIE stores no stock snapshot at all rather than a zero, and the stock "
      + "and GMROI screens stay empty and say why.",
      "Draft, in-review and cancelled sales documents are skipped deliberately: a "
      + "cancelled order must never count as demand a customer stopped placing.",
      "Quotes already in Business Central are not imported — no ERP's are — so a win rate "
      + "has no denominator until you start quoting here.",
      "Credit memos are not read from Business Central in this version, so a returned or "
      + "credited line still counts as sold until you say otherwise.",
      "Vendor payments are not read from any ERP, so what has actually been paid out is "
      + "outside what PIE can see on this book.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "Every quote line, against your own floor",
        body:
          "Cost comes off the purchase-invoice lines and the item card's own unit cost, "
          + "both of which Business Central already holds. PIE checks each new line "
          + "against the policy you set, routes a breach for sign-off — the platform holds "
          + "it, not the salesperson — and, where the grant allows it, creates the agreed "
          + "quote back in Business Central rather than making somebody retype it.",
      },
      {
        title: "Margin drift, per customer and item",
        body:
          "The lines come back with their headers, so PIE has the line-level history to "
          + "compute a margin per customer-item and compare it across windows. Every "
          + "figure is stamped with the version of the policy that judged it and carries "
          + "the operands it was computed from, so a number on the screen opens into the "
          + "rows behind it.",
      },
      {
        title: "The accounts that went quiet",
        body:
          "Decline is measured from the invoice history the first pull brings in, so it "
          + "works on a Business Central book from the first sync — no quotes to record "
          + "first and nothing to configure. What is not read on this book, payments, is "
          + "stated on the screen that would have used it rather than approximated.",
      },
    ],
    evidence: "{{DYNAMICS365_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    slug: "sage-x3",
    connector: "sagex3",
    costed: true,
    name: "Sage X3",
    short: "Sage X3",
    title: "PIE for Sage X3 · margin control on the book you already run",
    description:
      "PIE reads your Sage X3 folder over its SData service — customers, suppliers, the "
      + "item master, sales and purchase invoices with their lines, and orders — then "
      + "checks every new quote line against your own margin floor before it goes out. "
      + "Read-only: nothing is ever created in Sage X3.",
    connects:
      "A sign-in as a dedicated integration user against your Syracuse server's SData "
      + "service, scoped to one X3 folder. The form asks for the Syracuse URL, that "
      + "user's credentials and the folder (endpoint) whose books you want. Listings page "
      + "200 records at a time and are cheap; a document's lines cost one $details call "
      + "each, so a re-sync reads only what X3's own update stamp says has changed. The "
      + "connection is checked when you save it, so a wrong folder or password fails there "
      + "rather than on the first nightly sync.",
    reads: [
      { stage: "contacts", label: "Customers", source: "BPCUSTOMER" },
      { stage: "vendors", label: "Suppliers", source: "BPSUPPLIER" },
      { stage: "items", label: "The item master", source: "ITMMASTER" },
      { stage: "invoices", label: "Sales invoices, with their lines", source: "SINVOICE" },
      { stage: "bills", label: "Purchase invoices, with their lines", source: "PINVOICE" },
      { stage: "sales_orders", label: "Sales orders", source: "SORDER" },
      { stage: "purchase_orders", label: "Purchase orders", source: "PORDER" },
    ],
    writes: null,
    setup:
      "The rights are granted on the integration user's role in Syracuse: SData "
      + "web-service access on the user itself — without it the sign-in succeeds and every "
      + "entity answers 401, which reads like a wrong password — and read access to each "
      + "table above in the connected folder. And because a Syracuse server usually sits "
      + "on your own network, it has to be reachable on a public hostname: PIE refuses a "
      + "source address that resolves inside a private range, when you save it and again "
      + "on every fetch.",
    gaps: [
      "X3 nests a document's lines under a block whose name varies by representation, so "
      + "PIE finds that block rather than assuming it — the first list of rows that name "
      + "an item. A representation that exposes no such block yields a document with no "
      + "lines, and every one of those is named on the sync report rather than averaged "
      + "into a total nobody could trace.",
      "Customer payments are not read from Sage X3. Payment timing, the collections "
      + "worklist and days-to-pay have nothing to read on an X3 book, and they say so "
      + "rather than estimate around it.",
      "Stock levels are not read from Sage X3 in this version, so the stock and GMROI "
      + "screens stay empty on an X3 book.",
      "Credit notes are not read from Sage X3 in this version, so a returned or credited "
      + "line still counts as sold until you say otherwise.",
      "Quotes are not imported from any ERP, X3 included, so a win rate has no "
      + "denominator until you start quoting here.",
      "Vendor payments are not read from any ERP, so what has actually been paid out is "
      + "outside what PIE can see on this book.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "Every quote line, against your own floor",
        body:
          "Your sales and purchase invoice lines are what a floor is computed from: what "
          + "you sold, to whom, and what it cost you. PIE checks each new line against the "
          + "policy you set and routes a breach for sign-off — the platform holds it, not "
          + "the salesperson, and the sign-off is on record. Nothing is written back: the "
          + "agreed quote is entered in X3 by whoever enters them today.",
      },
      {
        title: "Margin drift, per customer and item",
        body:
          "Computed from the line-level history X3 already holds and compared across "
          + "windows, with every figure stamped with the version of the policy that judged "
          + "it. The arithmetic is deterministic and the operands come with it, so any "
          + "number on the screen opens into the rows it came from.",
      },
      {
        title: "The accounts that went quiet",
        body:
          "Decline is read from the invoice history the first pull brings in, so it works "
          + "on an X3 book from the first sync — no quotes to record first, no payments "
          + "needed, nothing to configure.",
      },
    ],
    evidence: "{{SAGEX3_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    // The one page here whose honest answer is that the platform's headline
    // capability does not work on this book. Sage 100's AP invoice history
    // records GL distributions rather than item lines, so there is no
    // per-item cost anywhere in it — and a margin floor with no cost is not a
    // weaker floor, it is no floor. `costed: false` is what says so, and
    // `erp.test.ts` holds it against the connector's own declaration in both
    // directions: this page cannot claim a floor while the connector reads no
    // bills, and it cannot keep denying one if a later version reads them.
    slug: "sage-100",
    connector: "sage100",
    costed: false,
    name: "Sage 100",
    short: "Sage 100",
    title: "PIE for Sage 100 · price discipline on the book you already run",
    description:
      "PIE reads your Sage 100 company over its SData feed — customers, vendors, the item "
      + "master, invoice history with its lines, and open orders. Sage 100 records AP "
      + "history as GL distributions rather than item lines, so purchase cost is not read "
      + "and margin is reported unknown rather than estimated: what this book gives you is "
      + "price history, demand and decline, not a margin floor.",
    connects:
      "A sign-in as a Sage 100 user with SData access, against the server your "
      + "eBusiness/SData provider runs on, scoped to one three-character company code. The "
      + "feed is Atom XML and PIE parses it with the standard library, by local name, so a "
      + "provider namespace revision does not break the read. The connection is checked "
      + "when you save it, so a wrong company code or password fails there rather than on "
      + "the first nightly sync.",
    reads: [
      { stage: "contacts", label: "Customers", source: "AR_Customer" },
      { stage: "vendors", label: "Vendors", source: "AP_Vendor" },
      { stage: "items", label: "The item master", source: "CI_Item" },
      { stage: "invoices", label: "Invoice history, with its lines", source: "AR_InvoiceHistoryHeader · AR_InvoiceHistoryDetail" },
      { stage: "sales_orders", label: "Sales orders", source: "SO_SalesOrderHeader" },
      { stage: "purchase_orders", label: "Purchase orders", source: "PO_PurchaseOrderHeader" },
    ],
    writes: null,
    setup:
      "Granted on the Sage 100 user's role, at Library Master → Main → Role Maintenance: "
      + "SData access — without it the credential is accepted and no resource is served — "
      + "and inquiry rights on each module above. Both invoice-history resources are "
      + "needed rather than one: headers alone import totals with nothing under them, "
      + "which is a book with revenue and no lines. And because a Sage 100 SData provider "
      + "usually sits on your own network, it has to be reachable on a public hostname: "
      + "PIE refuses a source address that resolves inside a private range, when you save "
      + "it and again on every fetch.",
    gaps: [
      "Purchase cost is not read, and this is the gap that decides what the platform can "
      + "do for you here. Sage 100's AP invoice history records GL distributions, not item "
      + "lines, so there is no per-item cost to read anywhere in the book. Margin, the "
      + "margin floor, margin drift and everything derived from them are reported unknown "
      + "on a Sage 100 book — not estimated from a list price, not defaulted to zero, and "
      + "never quietly passed as within policy.",
      "Customer payments are not read from Sage 100, so payment timing, the collections "
      + "worklist and days-to-pay have nothing to read on this book.",
      "Stock levels are not read from Sage 100 in this version, so the stock and GMROI "
      + "screens stay empty on this book.",
      "Credit memos are not read from Sage 100 in this version, so a returned or credited "
      + "line still counts as sold until you say otherwise.",
      "Quotes are not imported from any ERP, Sage 100 included, so a win rate has no "
      + "denominator until you start quoting here.",
      "Vendor payments are not read from any ERP, so what has actually been paid out is "
      + "outside what PIE can see on this book.",
      "Salespeople are not imported, so every decision routes to management until "
      + "accounts are assigned inside PIE.",
    ],
    fit: [
      {
        title: "What this customer has actually paid",
        body:
          "The price references on a quote line — what this customer last paid for the "
          + "item, what they pay at this quantity, what comparable customers pay — are "
          + "arithmetic over invoice lines and need no cost at all, so they are on the "
          + "screen on a Sage 100 book. The two references that are derived from cost are "
          + "absent rather than approximated, and the line says which.",
      },
      {
        title: "The accounts that went quiet",
        body:
          "Decline is measured from the invoice history the first pull brings in: a "
          + "customer's recent revenue against a comparable earlier window, with an "
          + "activity floor and a minimum history so a quiet fortnight is not an alarm. It "
          + "needs no cost and no payments, so it works on this book from the first sync.",
      },
      {
        title: "Unknown, said out loud",
        body:
          "Every screen that would have needed cost reports UNKNOWN and names what is "
          + "missing. That is the product working, not failing: a platform that answered "
          + "“within policy” because it found no cost would be worth less than "
          + "nothing on a book like this one. If your item-level purchase cost lives "
          + "somewhere Sage 100 does not — a second system, or an AP process that could "
          + "record item lines — that is the conversation worth having before you connect.",
      },
    ],
    evidence: "{{SAGE100_DISTRIBUTOR_EVIDENCE}}",
  },
  {
    // The one page on this site whose subject is not a connector in
    // `ingestion/erp/`. Zoho Books is the book this platform was built
    // against and the source `state/` is derived from, so its integration is
    // not a connector at all — it is `ingestion/zoho_client.py`, and it is
    // deeper than any of the connectors above. `erp.test.ts` therefore reads a
    // different file for this page and matches a different declaration; see
    // the note on `stage` below.
    slug: "zoho-books",
    connector: "zoho",
    costed: true,
    name: "Zoho Books",
    short: "Zoho Books",
    title: "PIE for Zoho Books · margin control on the book you already run",
    description:
      "PIE reads your Zoho Books organization in full — customers, vendors, the item "
      + "master, invoices, bills, credit notes, vendor credits, customer and vendor "
      + "payments, sales and purchase orders, estimates, warehouses and per-location "
      + "stock — checks every new quote line against your own margin floor, and can write "
      + "the quote back as an estimate.",
    connects:
      "A Zoho Self Client: an administrator mints one refresh token against the scope "
      + "list below, and PIE exchanges it for a short-lived access token as it works — no "
      + "browser round-trip on a sync and nothing to renew on a schedule. The form asks "
      + "for that refresh token, the client id and secret, your organization id and your "
      + "data centre, and the connection is checked when you save it. One Zoho "
      + "organization is one connection; a group running several books connects each one "
      + "and sees them under a single view on the Platform plan.",
    reads: [
      // `stage` here is the Zoho API path the read goes to — the key of
      // `SCOPE_FOR_PATH` in `zoho_client.py` — rather than an
      // `ingestion/erp/base.READ_STAGES` name, because that vocabulary is the
      // connector registry's and this is not a connector. It is the same kind
      // of anchor: the identifier the implementation itself uses, checked
      // against the implementation by `erp.test.ts` in both directions.
      { stage: "contacts", label: "Customers", source: "contacts · type customer" },
      // The same `contacts` endpoint as the row above, the other contact type
      // — two rows, one path, because a buyer looks for "suppliers" by name
      // and the stage set is compared as a set.
      { stage: "contacts", label: "Suppliers", source: "contacts · type vendor" },
      // Zoho's /items defaults to Status.Active and the client overrides it,
      // deliberately: a distributor deactivates a line the moment it is
      // discontinued, but the bills that priced it do not go with it. Without
      // the override every historical line for a retired item is skipped and
      // the visible effect is missing *margin*, not a missing item.
      { stage: "items", label: "The item master, discontinued lines included", source: "items" },
      { stage: "invoices", label: "Invoices, with their lines", source: "invoices" },
      { stage: "bills", label: "Bills, with their lines", source: "bills" },
      { stage: "creditnotes", label: "Credit notes", source: "creditnotes" },
      { stage: "vendorcredits", label: "Vendor credits", source: "vendorcredits" },
      { stage: "customerpayments", label: "Customer payments", source: "customerpayments" },
      { stage: "vendorpayments", label: "Payments out", source: "vendorpayments" },
      { stage: "salesorders", label: "Sales orders", source: "salesorders" },
      { stage: "purchaseorders", label: "Purchase orders", source: "purchaseorders" },
      { stage: "estimates", label: "Estimates — what was offered", source: "estimates" },
      { stage: "locations", label: "Warehouses", source: "locations" },
      { stage: "itemdetails", label: "Stock, per location", source: "itemdetails" },
      { stage: "users", label: "Users, to match salespeople by email", source: "users" },
    ],
    writes:
      "Two things, both optional and both off until you grant their scope. A quote built "
      + "in PIE can be created in Zoho Books as an estimate: the write is checked rather "
      + "than trusted — PIE reads the reference back before it sends, so a request that "
      + "timed out and lost its response cannot put two estimates in front of one "
      + "customer. And a salesperson who adds a supply product to a quote can create that "
      + "item in your item master rather than leaving it for someone to key in later; a "
      + "write that fails leaves the line visibly in CREATE FAILED rather than stuck. "
      + "Nothing else is ever written: no invoice, no order, no payment, no edit to a "
      + "record that already exists.",
    setup:
      "The scopes are chosen when the Self Client grant is generated, and they are the "
      + "whole of the configuration. Reads: ZohoBooks.contacts.READ, "
      + "ZohoBooks.settings.READ (the item master, warehouses and per-location stock), "
      + "ZohoBooks.invoices.READ, ZohoBooks.creditnotes.READ, ZohoBooks.bills.READ, "
      + "ZohoBooks.vendorcredits.READ, ZohoBooks.customerpayments.READ, "
      + "ZohoBooks.vendorpayments.READ, ZohoBooks.salesorders.READ, "
      + "ZohoBooks.purchaseorders.READ, ZohoBooks.estimates.READ and "
      + "ZohoBooks.users.READ. Writing is two more and neither is required: "
      + "ZohoBooks.estimates.CREATE to send a quote, ZohoBooks.settings.CREATE to create "
      + "an item. Pressing Check probes each scope with one real call and reports it "
      + "granted, refused, or — where the call timed out or was throttled — unknown. An "
      + "endpoint that could not be reached is never reported as granted, so what you "
      + "read is what was actually answered rather than a pass assembled out of missing "
      + "evidence. A scope refused mid-sync is named on the report the same way: which "
      + "permission, and what to grant.",
    gaps: [
      "Draft and void invoices and bills are not read, deliberately: a cancelled invoice "
      + "must never count as revenue a customer stopped spending.",
      "History is bounded — two years by default. The detectors compare a recent window "
      + "against a prior one, so a decade of ledger costs API calls and buys nothing.",
      "Invoice and bill lines with no item — comment and charge rows, freight and "
      + "handling — are not product lines, so they are not costed and carry no margin.",
      "A salesperson is matched to a platform user by email, exactly. Anything else "
      + "leaves the account unassigned and says so on the sync report; a wrongly assigned "
      + "account would be invisible to the person who should act on it, which is worse.",
      "Where a product has no cost on any bill, margin is suppressed rather than "
      + "estimated. On a first sync that suppresses heavily, and that is the correct "
      + "answer rather than a failure — the platform does not assert a margin it cannot "
      + "stand behind.",
      "Only contacts Zoho types as customers or vendors are read; other contact types "
      + "are not.",
    ],
    fit: [
      {
        title: "Every screen has something to read",
        body:
          "This is the book PIE was built against, and it is the only connection where "
          + "no stage is missing. Payments come in, so collections and days-to-pay are "
          + "measured rather than absent. Estimates come in, so a win rate has a "
          + "denominator from the first sync. Per-location stock comes in, so what a line "
          + "returns on the cash it ties up is computable. On the ERP connectors some of "
          + "those screens stay empty and say why; here none of them do.",
      },
      {
        title: "Every quote line, against your own floor",
        body:
          "Your invoice and bill lines are what a floor is computed from: what you sold, "
          + "to whom, and what it cost. PIE checks each new line against the policy you "
          + "set and routes a breach for sign-off — the platform holds it, not the "
          + "salesperson, and the sign-off is on record. Cost and margin never reach a "
          + "salesperson's screen at all; they are absent from the response, not hidden "
          + "in it.",
      },
      {
        title: "Your books stay the system of record",
        body:
          "Everything PIE derives is rebuilt from a complete re-sync, so nothing "
          + "important lives only here — disconnect and your ledger is untouched and "
          + "whole. Every computed number is stamped with the version of the policy that "
          + "judged it, and opens into the rows it came from. The AI reads those numbers "
          + "and phrases them; it never produces one.",
      },
    ],
    evidence: "{{ZOHO_DISTRIBUTOR_EVIDENCE}}",
  },
];

/** One page by slug, or a loud failure.
 *
 *  The registry in `prerender.tsx` maps over `ERP_PAGES` directly and does not
 *  need this; it is here for a lookup by slug — `erp.test.ts` uses it — and it
 *  throws rather than returning undefined so a wrong slug is a named error at
 *  the call site instead of an empty page somewhere downstream. */
export function erpPage(slug: string): ErpPageData {
  const page = ERP_PAGES.find((p) => p.slug === slug);
  if (!page) throw new Error(`no ERP page for slug ${slug}`);
  return page;
}
