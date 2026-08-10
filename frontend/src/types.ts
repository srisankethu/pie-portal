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
   *  the rate it actually used rather than a rate it assumes. */
  taxRate: number;
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
export interface QuoteEstimate {
  number: string;
  lineCount: number | null;
  current: boolean;
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

export interface QuoteOutcome {
  quote_id: string;
  status: QuoteOutcomeStatus;
  note: string | null;
  sent_at: string | null;
  decided_at: string | null;
  allowed_next: QuoteOutcomeStatus[];
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
