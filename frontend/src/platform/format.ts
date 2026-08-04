// Presentation formatting only — NO business logic / calculations (those live in
// the backend). These map already-computed values to human-readable strings.
import type { Fact } from "./types";
import { money } from "../money";

export const TYPE_LABEL: Record<string, string> = {
  CUSTOMER_DECLINE: "Customer decline",
  CUSTOMER_DORMANCY: "Customer dormancy",
  MARGIN_DETERIORATION: "Margin deterioration",
  COST_PASS_THROUGH: "Cost pass-through",
  QUOTE_CONTEXT: "Quote context" };

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
