// How long an order takes to become cash, and whose wait each half is.
//
// One question, and the layout exists to answer it in one glance: the two
// stages sit side by side, each labelled with whose delay it is, so "we are
// slow to collect" and "we are slow to *bill*" stop being the same sentence.
//
// Three things this screen deliberately does.
//
// **It states the measurement rule, above the numbers.** A cycle time has
// several defensible definitions and this one picks the earliest linked order
// on each invoice. A reader who cannot see which rule produced a figure argues
// with the figure instead of acting on it, so the rule and what it drops are
// rendered, not hidden in a tooltip.
//
// **An unmeasurable invoice is shown as unmeasurable, with its reason.** Stock
// sold across the counter has no order date, so it has no order-to-invoice lag
// — never a zero. The unknown count sits on the same tile as the median rather
// than under it, because a median drawn from eleven invoices out of four
// hundred reads as a fact about all four hundred otherwise.
//
// **The invoice list is a `DataGrid`.** Its row count is the size of the book,
// which is the test `docs/ui-standards.md` §3 states — and "which invoices sat
// longest before we billed them" is a sort question.
//
// Dates and day counts only. No money reaches this screen, which is why every
// role can open it.

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useMemo } from "react";
import { formatDate } from "../../when";
import { papi } from "../api";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import { EntityName } from "../EntityName";
import { MetricCard, SectionHeader, StatusChip } from "../kit";
import type { Tone } from "../kit";
import type { EntityOrigin, PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { useInsight } from "./useInsight";

type Stage = {
  key: string;
  label: string;
  owner: string;
  owner_label: string;
  question: string;
  measured: number;
  unknown: number;
  unknown_by_reason: Record<string, number>;
  median_days: number | null;
  slow_days: number | null;
  worst_days: number | null;
  before_start: number;
  estimable: boolean;
  min_observations: number;
};

type Split = {
  invoices: number;
  estimable: boolean;
  ours_median_days: number | null;
  theirs_median_days: number | null;
  total_median_days: number | null;
  ours_share: number | null;
};

type Coverage = {
  invoices: number;
  with_order: number;
  without_order: number;
  consolidated: number;
  order_not_held: number;
};

type CycleRow = {
  invoice_ref: string;
  invoice_number: string | null;
  customer_id: string | null;
  customer_label: string;
  invoice_date: string;
  order_date: string | null;
  order_numbers: string[];
  orders_linked: number;
  consolidated: boolean;
  order_to_invoice_days: number | null;
  invoice_to_cash_days: number | null;
  order_to_cash_days: number | null;
  order_unknown_reason: string | null;
  cash_unknown_reason: string | null;
  origin?: EntityOrigin;
};

/** Whose delay this is, as a chip rather than a colour.
 *
 *  `info` and `warn` carry a word each, so the distinction survives greyscale
 *  and forced-colours mode — §6 of the UI standards. Neither tone means "bad":
 *  a long stage is not a failure until somebody has looked at why. */
const OWNER_TONE: Record<string, Tone> = {
  US: "warn",
  CUSTOMER: "info",
  BOTH: "neutral",
};

/** A day count, or an em dash. Never "0 days" for something unmeasured — the
 *  whole point of the server's unknown reasons is that those are different. */
function days(v: number | null | undefined): string {
  return v == null ? "—" : `${v} day${Math.abs(v) === 1 ? "" : "s"}`;
}

/** Short forms of the server's reason codes, for a tile or a chip. The full
 *  sentence the server wrote travels with each one as its tooltip rather than
 *  being reworded here — two explanations of one refusal is one too many. */
const LABEL: Record<string, string> = {
  NO_ORDER: "with no order",
  ORDER_NOT_HELD: "whose order is not held",
  STILL_OWED: "still owed",
  NO_RECEIPT_ON_RECORD: "cleared without a receipt",
  BALANCE_UNKNOWN: "with no balance stated",
};

// ── the stages ──────────────────────────────────────────────────────────────
function StageTile({ stage }: { stage: Stage }) {
  const reasons = Object.entries(stage.unknown_by_reason);
  return (
    <MetricCard
      label={
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
          <span>{stage.label}</span>
          <StatusChip tone={OWNER_TONE[stage.owner] ?? "neutral"}
                      label={stage.owner_label} dense />
        </Stack>
      }
      value={stage.estimable ? days(stage.median_days) : "Not enough yet"}
      tip={stage.question}
      sub={
        stage.estimable
          ? `typical · slow tenth ${days(stage.slow_days)} · `
            + `measured on ${stage.measured} invoice(s)`
          : `${stage.measured} measured, and this needs `
            + `${stage.min_observations} before a typical figure is asserted`
      }
      variance={
        // The unknowns belong beside the figure, not beneath it. `variance` is
        // MetricCard's slot directly under the value, which is where a caveat
        // on that value has to sit to be read with it.
        stage.unknown > 0 || stage.before_start > 0 ? (
          <Typography variant="caption" color="text.secondary">
            {stage.unknown > 0 && (
              <>
                {stage.unknown} invoice(s) unmeasurable
                {reasons.length ? ` — ${reasons.map(([k, n]) => `${n} ${LABEL[k] ?? k}`)
                  .join(", ")}` : ""}
              </>
            )}
            {/* A negative leg is a data-entry finding, so it is said out loud
                rather than left for somebody to spot in the grid. */}
            {stage.before_start > 0 && (
              <>
                {stage.unknown > 0 ? " · " : ""}
                {stage.before_start} counted backwards, dated before the step
                before them
              </>
            )}
          </Typography>
        ) : undefined
      }
    />
  );
}

export function OrderToCashScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "order-to-cash",
    () => papi.orderToCash(session.token), [session.token]);

  const stages = (data?.stages as Stage[] | undefined) ?? [];
  const split = data?.split as Split | undefined;
  const coverage = data?.coverage as Coverage | undefined;
  const rule = (data?.rule ?? {}) as Record<string, string>;
  const reasons = (data?.unknown_reasons ?? {}) as Record<string, string>;

  const all = useMemo(
    () => (data?.invoices as CycleRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const rows = filter.filtered;

  const columns = useMemo<ColDef<CycleRow>[]>(() => [
    {
      field: "customer_label", headerName: "Customer", flex: 1.2, minWidth: 170,
      cellRenderer: (p: { data?: CycleRow }) => (p.data ? (
        <EntityName name={p.data.customer_label} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    { field: "invoice_number", headerName: "Invoice", width: 140, flex: 0 },
    {
      headerName: "Order", flex: 0.9, minWidth: 150,
      valueGetter: (p) => p.data?.order_numbers.join(", ") ?? "",
      valueFormatter: (p) => (p.value ? String(p.value) : "no order"),
      headerTooltip: "Every order this invoice bills against. More than one "
        + "means the gap below is measured from the earliest of them.",
    },
    {
      field: "order_date", headerName: "Ordered", width: 130, flex: 0,
      valueFormatter: (p) => formatDate(p.value),
    },
    {
      field: "invoice_date", headerName: "Invoiced", width: 130, flex: 0,
      valueFormatter: (p) => formatDate(p.value),
    },
    numeric<CycleRow>("order_to_invoice_days", "Ours", (v) => String(v), {
      width: 110, flex: 0,
      headerTooltip: "Days from the earliest order to this invoice. Blank where "
        + "there is no order date to measure from — never zero.",
    }),
    numeric<CycleRow>("invoice_to_cash_days", "Theirs", (v) => String(v), {
      width: 115, flex: 0,
      headerTooltip: "Days from this invoice to the payment that settled it. "
        + "Blank while it is still owed.",
    }),
    numeric<CycleRow>("order_to_cash_days", "Whole cycle", (v) => String(v), {
      width: 140, flex: 0,
      headerTooltip: "This invoice's own order date to its own payment date.",
    }),
    {
      headerName: "Not measured", flex: 1, minWidth: 190,
      valueGetter: (p) =>
        p.data?.order_unknown_reason ?? p.data?.cash_unknown_reason ?? "",
      valueFormatter: (p) => (p.value ? (LABEL[String(p.value)] ?? String(p.value)) : ""),
      cellRenderer: (p: { data?: CycleRow; value?: string }) => {
        const code = p.value;
        if (!code) return null;
        return <StatusChip tone="neutral" label={LABEL[code] ?? code}
                           tip={reasons[code]} dense />;
      },
      headerTooltip: "Why one leg of this invoice's cycle has no figure. Each "
        + "of these is a different fact, not a shared blank.",
    },
  ], [filter.show, reasons]);

  return (
    <div className="screen-stack">
      <SectionHeader
        title="Order to cash"
        sub={"How long a customer's order takes to become money in the bank, "
          + "split into the half we control and the half they do."} />

      <Panel
        title="The cycle, by stage"
        question="Where does the time actually go — our billing, or their paying?"
        state={stateOf(loading, error, data?.empty_reason as string | null)}
        error={error}
        emptyReason={data?.empty_reason as string | null}
        onRetry={reload}
        actions={
          <CompanyFilter options={filter.options} value={filter.company}
                         onChange={filter.setCompany} show={filter.show} />
        }
        wide
      >
        <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap", mb: 2 }}>
          {stages.map((stage) => (
            <Box key={stage.key} sx={{ flex: "1 1 240px", minWidth: 240 }}>
              <StageTile stage={stage} />
            </Box>
          ))}
        </Stack>

        {/* The answer to the screen's question, in a sentence, and only where
            there is enough evidence to say it. */}
        {split?.estimable && split.ours_share != null && (
          <Alert severity="info" sx={{ mb: 2 }}>
            Across the {split.invoices} invoice(s) where both halves are known,{" "}
            <strong>{Math.round(split.ours_share * 100)}%</strong> of the total
            wait is ours — {days(split.ours_median_days)} typically from order to
            invoice against {days(split.theirs_median_days)} from invoice to
            payment. The share totals the days on both sides rather than
            averaging each invoice's own split, so a long invoice weighs what it
            actually cost.
          </Alert>
        )}

        {/* Stated above the grid, not tucked into a tooltip — see the header of
            this file. */}
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          <Typography variant="subtitle2">
            Measured per {rule.grain ?? "invoice"} · rule {rule.code ?? "—"}
          </Typography>
          <Typography variant="body2">{rule.statement}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            What this drops: {rule.drops}
          </Typography>
        </Alert>

        {coverage && (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {coverage.with_order} of {coverage.invoices} invoice(s) name a sales
            order; {coverage.consolidated} bill against more than one, and{" "}
            {coverage.order_not_held} name an order this platform does not hold.
            The remaining {coverage.without_order} have no order behind them at
            all — counter sales, which have no order-to-invoice gap rather than
            one of zero days.
          </Typography>
        )}

        <DataGrid<CycleRow>
          rows={rows}
          columns={columns}
          getRowId={(r) => r.invoice_ref}
          ariaLabel="Invoices, with the order behind each one and how long each stage took"
        />
      </Panel>
    </div>
  );
}
