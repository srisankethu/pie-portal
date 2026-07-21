import type { CSSProperties } from "react";

// Relationship chip styling — mirrors the design's REL table.
export const REL_STYLE: Record<string, CSSProperties> = {
  EXACT: { background: "var(--color-accent-100)", color: "var(--color-accent-800)" },
  TECH: { border: "1px solid var(--color-accent)", color: "var(--color-accent-700)" },
  COMPAT: {
    background: "var(--color-neutral-100)",
    color: "var(--color-neutral-800)",
    border: "1px solid var(--color-divider)",
  },
  POSSIBLE: { border: "1px dashed var(--color-neutral-500)", color: "var(--color-neutral-700)" },
  INCOMPATIBLE: { background: "var(--danger-bg)", color: "var(--danger-fg)" },
  AMBIGUOUS: { background: "var(--caution-bg)", color: "var(--caution-fg)" },
  UNRESOLVED: { background: "var(--danger-bg)", color: "var(--danger-fg)" },
  PIE_DOWN: { border: "1px dashed var(--color-neutral-500)", color: "var(--color-neutral-700)" },
  NONE: { color: "color-mix(in srgb,var(--color-text) 40%,transparent)" },
};

export function statusColor(kind: string): string {
  return kind === "technical"
    ? "var(--danger-fg)"
    : kind === "operational"
      ? "var(--warn)"
      : kind === "commercial"
        ? "var(--color-accent)"
        : "var(--color-neutral-600)";
}

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}
