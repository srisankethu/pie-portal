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
      + "only thing PIE ever creates anywhere. The write is idempotent by construction: it "
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
      + "of type QT — and that is the only thing PIE ever creates anywhere. Acumatica's PUT "
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
          "Of these three systems, Acumatica is the one that gives PIE quantity on hand "
          + "and available as well as line-level history, so what a line returns on the cash "
          + "it ties up is computable rather than absent — and every figure carries the "
          + "policy version that judged it.",
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
