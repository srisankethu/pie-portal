// Presentation formatting only — NO business logic / calculations (those live in
// the backend). These map already-computed values to human-readable strings.
import type { Fact } from "./types";
import { money } from "../money";

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
  CASH_PAYABLE_OVERDUE: "Payables past due" };

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
  units_sold_to_date: "units sold to date" };

export function stateFieldLabel(name: string): string {
  return STATE_FIELD_LABEL[name] || name.replace(/_/g, " ");
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
