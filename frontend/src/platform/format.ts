// Presentation formatting only — NO business logic / calculations (those live in
// the backend). These map already-computed values to human-readable strings.
import type { Fact } from "./types";
import { money } from "../money";

/** What a role is called on screen.
 *
 *  `Record<string, string>` rather than `Record<Role, string>` because callers
 *  index it with a role that arrived over the wire — `decision.assigned_role` is
 *  a string, and a build that trusted it to be a known `Role` would render
 *  `undefined` for one the server added first. Callers fall back to the raw
 *  value. Lived privately in `AdminScreens` until a second screen needed it. */
export const ROLE_LABEL: Record<string, string> = {
  SALESPERSON: "Salesperson",
  SALES_MANAGER: "Sales manager",
  OWNER: "Owner",
};

export const TYPE_LABEL: Record<string, string> = {
  CUSTOMER_DECLINE: "Customer decline",
  CUSTOMER_DORMANCY: "Customer dormancy",
  MARGIN_DETERIORATION: "Margin deterioration",
  COST_PASS_THROUGH: "Cost pass-through",
  QUOTE_CONTEXT: "Quote context",

  // Derived from Business State. Named for the *situation*, not the metric:
  // somebody scanning a queue is deciding what to open, and "dead stock" tells
  // them what they are about to look at where "INV_DEAD_STOCK" does not.
  INV_DEAD_STOCK: "Dead stock",
  INV_SLOW_MOVING: "Slow-moving stock",
  INV_EXCESS_COVER: "Overstocked against its own offtake",
  INV_BELOW_REORDER: "Below the reorder point",
  INV_OVERSOLD: "Committed beyond stock",
  SUP_OPEN_COMMITMENT: "Open supplier commitment",
  CASH_PAYABLE_OVERDUE: "Payables past due",
  CASH_RECEIVABLE_OVERDUE: "Receivables past due",
  CASH_CREDIT_EXPOSURE: "Credit exposure",
  SUP_SPEND_CONCENTRATION: "Supplier concentration",
  SUP_SOLE_SOURCE: "Only source for these items",

  // Commercial-intelligence signals, folded in from a second map that lived in
  // `CommercialScreens.tsx`. One concept had two owners: these six were complete
  // there and absent here, so the decision queue — which reads this one through
  // `ui.typeLabel` — rendered them as raw enums like `CI_MARGIN_DECLINE_NO_VOLUME`
  // while the commercial screen three clicks away said "No volume gained". Adding
  // the missing rows to the incomplete map would have made a third copy.
  CI_MARGIN_EROSION: "Margin eroding",
  CI_COST_NOT_PASSED: "Cost not passed on",
  CI_LOW_PEER_PRICING: "Below peers",
  CI_MARGIN_DECLINE_NO_VOLUME: "No volume gained",
  CI_MARGIN_DECLINE_WITH_VOLUME: "Volume traded for margin",
  CI_MATERIAL_MARGIN_GAP: "Material gap" };

/** The state fields a card shows, in the words a person reads.
 *
 *  Only the ones that appear in evidence. A field with no entry here is
 *  rendered from its own key rather than hidden — a missing label is a gap in
 *  this table, and silently dropping the row would hide the number instead of
 *  the omission. */
export const STATE_FIELD_LABEL: Record<string, string> = {
  on_hand: "On hand",
  available: "Available",
  actual_available: "Available after commitments",
  reorder_level: "Reorder point",
  purchase_rate: "What it cost, each",
  last_sold_on: "Last sold",
  first_sold_on: "First sold",
  last_purchased_on: "Last purchased",
  units_sold: "Units sold to date",
  revenue: "Revenue to date",
  observed_days: "Measured over",
  carrying_annual_pct: "Carrying rate a year",
  excess_cover_months: "Cover allowed",
  open_purchase_orders: "Open orders",
  open_purchase_value: "Committed, unreceived",
  oldest_open_purchase_on: "Oldest order placed",
  pending_qty: "Still to arrive",
  overdue_balance: "Past due",
  overdue_bills: "Bills past due",
  unpaid_bills: "Bills unpaid",
  earliest_due_on: "Earliest due date",
  unageable_bills: "Bills with no terms",
  payables_balance: "Owed in total",
  band: "Band",
  state: "State",
  key: "State key",
  // The remaining fold fields. Present so the drill-down reads as sentences
  // rather than as column names — somebody following a number down to its
  // source should not have to translate on the way.
  observed_on: "Stock counted",
  tracked: "Stock-tracked",
  units_purchased: "Units purchased to date",
  purchase_lines: "Bill lines",
  sale_lines: "Invoice lines",
  last_unit_cost: "Latest unit cost",
  spend: "Spent to date",
  direction: "Relationship",
  sales_orders: "Sales orders",
  open_sales_orders: "Open sales orders",
  open_sales_value: "Promised, uninvoiced",
  oldest_open_sale_on: "Oldest order taken",
  purchase_orders: "Purchase orders",
  bills: "Bills",
  idle_days: "days idle",
  short_by: "short by",
  months_of_cover: "months of cover",
  excess_units: "units beyond the policy",
  shortfall: "short of the point",
  open_orders: "open orders",
  oldest_open_on: "oldest placed",
  oldest_age_days: "days since the oldest",
  days_past_due: "days past due",
  units_sold_to_date: "units sold to date",
  // RECEIVABLES. Named from the customer's side of the ledger — "outstanding"
  // and "past due" mean the opposite thing on a payable, and one screen using
  // one word for both directions is how a reader ends up chasing a supplier.
  outstanding: "Owed in total",
  invoices: "Invoices",
  invoiced_value: "Invoiced to date",
  open_invoices: "Invoices unpaid",
  overdue_invoices: "Invoices past due",
  unageable_invoices: "Invoices with no terms",
  oldest_overdue_due_on: "Oldest overdue since",
  last_paid_on: "Last payment received",
  days_since_last_receipt: "days since the last payment",
  receipts: "Payments received",
  receivables_book: "Owed to the business in total",
  share_of_receivables: "Share of everything owed",
  share_of_receivables_pct: "% of everything owed",
  exposure_share_threshold: "Exposure threshold",
  // SUPPLIER.
  purchase_book: "Bought in total",
  share_of_spend: "Share of all purchasing",
  share_of_spend_pct: "% of all purchasing",
  items_supplied: "Items supplied",
  sole_sourced_items: "Items with no other source",
  supplier_share_threshold: "Concentration threshold",
  days_since_last_purchase: "days since the last purchase",
  first_purchased_on: "First purchased" };

export function stateFieldLabel(name: string): string {
  return STATE_FIELD_LABEL[name] || name.replace(/_/g, " ");
}

/** State fields denominated in the organization's currency.
 *
 * An explicit set, not a name test — the same choice, for the same reason, as
 * `policy.MONEY_FIELDS` on the server. "balance" and "value" appear in field
 * names that are counts, and `outstanding` contains neither word, so any
 * substring rule gets some of these wrong in both directions. Being wrong here
 * means a card prints `612300.00` where it means ₹6,12,300, or formats a
 * quantity as if it were rupees.
 */
export const MONEY_STATE_FIELDS = new Set([
  "purchase_rate", "revenue", "spend", "last_unit_cost",
  "open_purchase_value", "open_sales_value", "payables_balance",
  "overdue_balance", "outstanding", "invoiced_value", "receivables_book",
  "purchase_book",
]);

/** A state field's value, in the words and units a person reads.
 *
 * The companion to `stateFieldLabel`, and the reason it exists: the evidence
 * table and the impact strip both rendered every value with `String(v)`, so a
 * rupee figure arrived as the raw decimal the fold stored it as. Dates and
 * counts are already strings that read correctly; only money needed a rule.
 */
export function stateFieldValue(name: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (MONEY_STATE_FIELDS.has(name)) {
    const n = Number(value);
    if (Number.isFinite(n)) return money(n);
  }
  return String(value);
}

export const CONF_LABEL: Record<string, string> = {
  SUFFICIENT: "High",
  PARTIAL: "Medium",
  INSUFFICIENT: "Low" };


export function factLabel(label: string): string {
  return label
    .replace(/^drivers\./, "")
    .replace(/\[\d+\]/g, "")
    .replace(/_/g, " ")
    .replace(/\bpct\b/gi, "%")
    .replace(/^\w/, (c) => c.toUpperCase());
}

export function factValue(label: string, value: Fact["value"]): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    const l = label.toLowerCase();
    // percentage: only ratios/margins/*_pct — never a plain money "change"
    if (l.includes("pct") || l.includes("margin") || l.includes("ratio"))
      return (value * 100).toFixed(1) + "%";
    if (l.includes("price") || l.includes("cost") || l.includes("revenue") || l.includes("stake") || l.includes("change"))
      return money(value);
    if (l.includes("days") || l.includes("count") || l.includes("orders") || l.includes("interval"))
      return String(Math.round(value));
    return String(value);
  }
  return String(value);
}

/** Facts worth showing as top-level rows/chips — drop list-index expansions. */
export function isPrimaryFact(label: string): boolean {
  return !label.includes("[");
}

// AI status → a UI "state" the design treats specially.
export function aiState(status: string): "ok" | "degraded" | "failed" | "withheld" | "pending" {
  switch (status) {
    case "OK":
      return "ok";
    case "DEGRADED":
      return "degraded";
    case "FAILED":
      return "failed";
    case "SUPPRESSED":
      return "withheld";
    default:
      return "pending";
  }
}
