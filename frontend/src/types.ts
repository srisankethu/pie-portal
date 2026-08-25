/* There is no `Session` type here any more, and no `"sales" | "mgmt"` role
 * beside it. Both belonged to the Quote Builder's own login, which was a second
 * identity with a second role vocabulary — so "is this person management?" had
 * two answers in one browser, and the one the quote screen used came from a
 * demo account. The session is `platform/types.PlatformSession`, and the role
 * question is answered by `platform/ability.ts`. */

export interface Candidate {
  code: string;
  desc: string;
  rel: string;
  grade: string | null;
  brand: string | null;
  score: number | null;
  reason: string;
  attributes: Record<string, unknown>;
}

export interface Economics {
  cost: number | null;
  list_price: number | null;
  recommended: number | null;
  quoted: number | null;
  margin: number | null;
  below_floor: boolean;
}

export interface LineStatus {
  kind: "ready" | "technical" | "operational" | "commercial";
  label: string;
}

export interface LineFlags {
  attention: boolean;
  procurement: boolean;
  missingBooks: boolean;
  manualReview: boolean;
  unresolved: boolean;
  substituted: boolean;
}

export interface Line {
  id: string;
  raw: string;
  reqCode: string;
  reqDesc: string;
  reqQty: number;
  rel: string;
  relLabel: string;
  /** A model read this line out of the customer's prose; a person has not yet
   *  checked it. Blocks the estimate until confirmed, one line at a time. */
  proposed: boolean;
  /** What the reader had to interpret, where it did. Empty for a line taken
   *  straight off the text. */
  reading: string;
  supplyCode: string | null;
  supplyDesc: string;
  sel: "AUTO" | "USER" | "MANUAL";
  avail: number | null;
  availUnknown: boolean;
  inBooks: boolean | null;
  shortage: number | null;
  quoted: number | null;
  /** Whose number `quoted` is: `LIST` is the catalogue rate the line opened at,
   *  `USER` is one a person put there. Null when there is no price.
   *
   *  A resolved line arrives priced at list so a long tender is not a column of
   *  typing. Without this the default was indistinguishable from a considered
   *  price, so a quote nobody had looked at showed a Quotation total in the same
   *  weight as a finished one. */
  priceSource: "LIST" | "USER" | null;
  recommended: number | null;
  lineTotal: number | null;
  createPhase: string | null;
  service: string | null;
  incompatReason: string | null;
  status: LineStatus;
  flags: LineFlags;
  candidates: Candidate[];
  notes: string[];
  substituted: boolean;
  economics?: Economics;
}

export interface QuoteSummary {
  subtotal: number;
  /** Sales tax on the subtotal, in the organization's currency. */
  tax: number;
  /** What the jurisdiction calls it — "GST", "VAT", "Sales Tax". */
  taxLabel: string;
  /** The rate it was computed at, as a ratio (0.18), so the screen can state
   *  the rate it actually used rather than a rate it assumes.
   *
   *  `null` when the priced lines do not share one rate. The tax amount above
   *  is still exact — it is the sum of each line at its own rate — but there is
   *  no single rate to print, and printing one would state something false
   *  about a document the customer receives. */
  taxRate: number | null;
  /** How that tax was arrived at: how many priced lines carried a rate from the
   *  books, and how many fell back to the configured default. */
  taxBasis: { known: number; assumed: number; defaultRate: number };
  grand: number;
  total: number;
  /** Lines with no rate at all — they contribute nothing to the total above. */
  unpriced: number;
  /** Lines priced, but still at the catalogue rate nobody has agreed to. */
  atListPrice: number;
}

/** What this quote has already sent to Zoho.
 *
 *  `current` is false once the quote's products, quantities or rates have moved
 *  since — the estimate exists but no longer describes what is on screen. */
/** What became of a send, named in the vocabulary of the system it went to.
 *
 *  `systemLabel` and `documentTerm` come from the server rather than being
 *  chosen here: telling a Business Central user their "estimate" was created
 *  names a record type their own system does not have, and the screen has no
 *  way of knowing which system a given quote's books are. */
export interface EstimateResult {
  ok: boolean;
  documentNumber: string | null;
  lineCount: number | null;
  blockers: string[];
  message: string;
  /** The connector key, and that system's own names for itself and for the
   *  document a quote becomes there. Empty on a refusal that never reached a
   *  system — there is nothing to name. */
  system: string;
  systemLabel: string;
  documentTerm: string;
  /** True where the document was already there under this quote's reference.
   *  "Sent" and "was already sent" are different facts. */
  alreadyExisted: boolean;
}

export interface QuoteEstimate {
  number: string;
  lineCount: number | null;
  current: boolean;
  /** The system holding it, and its own names for itself and the document.
   *  "Sent · SQ-1001" does not say where, and two connected systems can both
   *  answer to that number. */
  system: string;
  systemLabel: string;
  documentTerm: string;
}

/** How the lines in this response were produced. Sent only by `/intake`, so it
 *  is optional on `Quote` — the screen uses it to say "read from your message,
 *  check each line" rather than presenting a model's reading as though somebody
 *  had typed it. Declared because the server sends it: an undeclared field and
 *  a renamed one look identical from here, which is what
 *  `backend/tests/test_frontend_contract.py` exists to tell apart. */
export interface QuoteIntake {
  read_by: "ai" | "pattern";
  detail: string;
  /** Whether the enquiry text was captured into the inbound corpus. False is the
   *  ordinary case today — nobody stated how the enquiry arrived — and the screen
   *  needs to tell that apart from a capture that was attempted and refused. */
  captured: boolean;
}

export interface MarginFloor {
  count: number;
  worst: number;
  floor: number;
}

export interface Quote {
  id: string;
  customer: string;
  /** The platform's id for the customer, when one was picked rather than typed.
   *  Null on a quote started before the picker existed, or from a draft. */
  customerId: string | null;
  number: string;
  /** The key any Zoho estimate for this quote is written under. It is what
   *  makes sending twice return the first estimate rather than create a second,
   *  and what a person searches Zoho for when a send fails in a way the screen
   *  cannot resolve. */
  reference: string;
  savedAt: string | null;
  lines: Line[];
  summary: QuoteSummary;
  filterCounts: Record<string, number>;
  /** Manager and owner only, and *absent* rather than null for a salesperson —
   *  as is `filterCounts.MFLOOR`, for the reason `store._filter_counts` gives. */
  marginFloor?: MarginFloor | null;
  /** The Zoho estimate already created from this quote, if any. */
  estimate: QuoteEstimate | null;
  /** Present when the last action taught the system something durable — today
   *  that is a confirmed "this customer's code means that product". Server-
   *  written prose, shown as-is; the client does not compose it. */
  note?: string;
  /** Present only when creating the item in the books failed, and carrying why.
   *  The line already reads CREATE FAILED; this is the reason, so the screen
   *  does not have to say "something went wrong". */
  createItemError?: string;
  /** Only on an intake response. */
  intake?: QuoteIntake;
}

/* ── Quote intelligence (deterministic; app/commercial) ──────────────────────
 * Cost, margin and every figure derived from them are absent for a sales role
 * — the server never sends them, so there is nothing here to hide in the UI.
 */
export type DataClass = "OPERATIONAL" | "RESTRICTED";
export type Severity = "CRITICAL" | "WARNING" | "INFO";

export interface PriceReference {
  code: string;
  label: string;
  value: number;
  basis: string;
  data_class: DataClass;
  as_of: string | null;
  txn_count: number;
  qty_band: string | null;
}

export interface QuoteException {
  code: string;
  severity: Severity;
  title: string;
  detail: string;
  manager_detail?: string | null;
  impact_amount: number | null;
  impact_data_class: DataClass;
  reference_code: string | null;
  requires_approval: boolean;
  policy: boolean;
  inputs?: Record<string, unknown>;
}

export interface QuoteLineEconomics {
  unit_cost: number | null;
  quoted_unit_price: number | null;
  qty: number;
  line_revenue: number | null;
  cogs: number | null;
  gross_profit: number | null;
  margin: number | null;
}

export interface LineIntelligence {
  line_id: string;
  product_id: string | null;
  product_ref: string;
  resolved: boolean;
  qty: number;
  quantity_band: { index: number; low: number; high: number | null; label: string };
  as_of: string;
  references: PriceReference[];
  references_withheld: string[];
  exceptions: QuoteException[];
  worst_severity: Severity | null;
  requires_approval: boolean;
  blocking: boolean;
  data_quality: {
    data_sufficiency: "SUFFICIENT" | "PARTIAL" | "INSUFFICIENT";
    reasons: string[];
    transaction_count: number;
  };
  thresholds_version: string;
  engine_version: string;
  economics?: QuoteLineEconomics | null;
  position?: {
    current_margin: number | null;
    historical_margin: number | null;
    margin_change_pp: number | null;
    cost_change_pct: number | null;
    price_change_pct: number | null;
    erosion_kind: string;
    peer_count: number;
    transaction_count: number;
  } | null;
  drilldown?: { customer_id: string; product_id: string } | null;
}

export type QuoteOutcomeStatus = "DRAFT" | "SENT" | "WON" | "LOST";

/** Why a quote was lost — the customer's reason, as heard.
 *
 *  PRICE, DELIVERY and COMPETITOR all mean somebody else supplied it, so the
 *  spend is evidence about what this customer buys elsewhere.
 *  CUSTOMER_CANCELLED means nobody supplied it. NO_DECISION means it is still
 *  nobody's and may yet move.
 *
 *  NOT_RECORDED is only ever read, never sent: it marks a loss decided before
 *  the vocabulary existed, which is not the same as somebody answering
 *  "no decision". The server's `loss_reasons` list cannot contain it. */
export type QuoteLossReason =
  | "PRICE"
  | "DELIVERY"
  | "COMPETITOR"
  | "CUSTOMER_CANCELLED"
  | "NO_DECISION";

/** Which quote an outcome is about. Two id spaces, never one bare `string`.
 *
 *  `quoteId` names a quote this platform priced and holds lines for.
 *  `documentRef` names one the ERP raised itself, and carries that system's own
 *  id — the value in `quote_documents.external_ref`, never the surrogate a
 *  re-sync re-mints. The second is most of the book: roughly three quarters of
 *  the estimates on it were never seen by the platform at all, and until the
 *  server made both keys optional there was no request body that could name one.
 *
 *  A union rather than two optional fields, because `set_outcome` refuses an
 *  outcome that is about no document at all and this is that refusal moved to
 *  compile time. Both keys together stays legal, deliberately: it is the
 *  *normal* shape for a platform quote pushed to the ERP, and a stricter type
 *  would make the ordinary case the one needing a cast.
 *
 *  Two named fields rather than a positional `string` for the reason the server
 *  keeps the columns apart. Both are strings and neither means anything in the
 *  other's space, so a single parameter lets a Zoho estimate id be passed where
 *  a platform quote id belongs with no error at any layer — writing an outcome
 *  row keyed on a quote this platform never priced, which is a row no later
 *  lookup finds. */
export type QuoteOutcomeSubject =
  | { quoteId: string; documentRef?: string }
  | { quoteId?: string; documentRef: string };

export interface QuoteOutcome {
  /** Null on an outcome recorded against a quote the ERP raised: the platform
   *  never priced it and holds no id of its own for it. This was a plain
   *  `string`, which is the same defect a plain `number` over a nullable figure
   *  is — the type asserting a guarantee the server does not make, so the one
   *  shape that needs handling is the one that type-checks silently. */
  quote_id: string | null;
  /** The source ERP's own id for the quote this outcome is about. Null on a
   *  quote the platform priced and never pushed. Both non-null is normal and
   *  means one estimate, priced here and raised there. */
  quote_document_ref: string | null;
  status: QuoteOutcomeStatus;
  note: string | null;
  /** Null is the NOT_RECORDED bucket: a loss decided before the vocabulary
   *  existed. Not the same as a recorded NO_DECISION, and a screen should
   *  render the two differently. */
  loss_reason: QuoteLossReason | null;
  lost_to: string | null;
  sent_at: string | null;
  decided_at: string | null;
  allowed_next: QuoteOutcomeStatus[];
  /** What a person may choose. Served rather than hardcoded here, so the form
   *  and the rule cannot drift — UNKNOWN is deliberately absent from it. */
  loss_reasons: QuoteLossReason[];
}

export interface QuoteIntelligence {
  customer: { customer_id: string | null; label: string | null; resolved: boolean; ref: string };
  as_of: string;
  lines: LineIntelligence[];
  summary: {
    lines_assessed: number;
    lines_unresolved: number;
    exceptions_total: number;
    insufficient_data: number;
    // Absent for a salesperson, not zero: both counts are derived from cost, and
    // a count over lines the caller priced locates the floor faster than the
    // per-line flag does. Optional here because the server omits them — a
    // required field would be the client asserting a guarantee the server does
    // not make.
    critical?: number;
    requires_approval?: number;
  };
  outcome: QuoteOutcome | null;
  thresholds_version: string;
}
