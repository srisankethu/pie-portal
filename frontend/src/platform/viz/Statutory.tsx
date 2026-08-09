// Deadlines the tax code sets, as dates and amounts.
//
// Three panels over one question: which payments have a statutory clock on
// them, which suppliers we cannot answer that for, and which parties are about
// to cross the 194Q line.
//
// **This screen states facts and stops.** It shows a deadline, the amount that
// moves if the deadline passes, and the basis each date was computed on. It
// does not tell anybody what to pay or what it will cost them in tax — a wrong
// margin costs a deal, a wrong tax position is the owner's liability, and
// nobody here is their accountant. Every sentence on the screen comes from the
// server, which computes it deterministically; the client formats.
//
// Two things the layout is deliberately doing.
//
// The gap band is **as prominent as the confirmed one**. A supplier nobody has
// classified is the finding, not the absence of one, and a screen that tucked
// unknowns into a footnote would read as "you are fine" to somebody who has
// checked forty suppliers out of four hundred.
//
// The basis column is **always visible, never a tooltip**. Section 15 counts
// from acceptance and this platform holds bill dates, so every date here rests
// on a stated proxy. Hiding that behind a hover would make the number look more
// certain than it is.

import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import { useMemo } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { MetricCard, StatusChip } from "../kit";
import type { Tone } from "../kit";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import type { EntityOrigin, PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { useInsight } from "./useInsight";

type WatchRow = {
  vendor_id: string | null;
  vendor_name: string;
  bill_number: string | null;
  bill_date: string;
  deadline: string;
  deadline_explanation: string;
  days_remaining: number;
  already_past: boolean;
  balance: number;
  financial_year: string;
  scope: string;
  scope_label: string;
  classification: string;
  evidence: string;
  amount_at_risk: number;
  estimated_carry_cost: number | null;
  origin?: EntityOrigin;
};

type BacklogRow = {
  vendor_id: string;
  vendor_name: string;
  spend: number;
  settled_past_limit: number;
  settled_total: number;
  late_share: number;
  origin?: EntityOrigin;
};

type CrossingRow = {
  vendor_id: string;
  vendor_name: string;
  purchases: number;
  excess: number;
  crossed: boolean;
  crossed_on: string | null;
  origin?: EntityOrigin;
};

/** How urgent a deadline is, as a chip. Bands rather than a raw day count
 *  because the decision is the same anywhere inside one: past is a call to
 *  make today, inside a week is this week's payment run, beyond that is a note.
 */
function urgency(days: number): { tone: Tone; label: string } {
  if (days < 0) return { tone: "bad", label: `${Math.abs(days)}d past` };
  if (days <= 7) return { tone: "warn", label: `${days}d left` };
  return { tone: "neutral", label: `${days}d left` };
}

// ── the watchlist ───────────────────────────────────────────────────────────
function WatchlistPanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "msme-watchlist",
    () => papi.msmeWatchlist(session.token), [session.token]);

  const all = useMemo(
    () => (data?.rows as WatchRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const rows = filter.filtered;

  const confirmed = (data?.confirmed ?? {}) as Record<string, number>;
  const gaps = (data?.gaps ?? {}) as Record<string, unknown>;
  const taxRateSet = data?.tax_rate_set === true;

  const columns = useMemo<ColDef<WatchRow>[]>(() => [
    {
      field: "vendor_name", headerName: "Supplier", flex: 1.3, minWidth: 180,
      cellRenderer: (p: { data?: WatchRow }) => (p.data ? (
        <EntityName name={p.data.vendor_name} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    { field: "bill_number", headerName: "Bill", width: 130, flex: 0 },
    {
      field: "deadline", headerName: "Pay by", width: 130, flex: 0,
      valueFormatter: (p) => formatDate(p.value),
      headerTooltip: "The section 15 deadline for this bill, computed from the "
        + "date and basis shown alongside.",
    },
    {
      headerName: "Time left", width: 120, flex: 0,
      valueGetter: (p) => p.data?.days_remaining ?? 0,
      cellRenderer: (p: { data?: WatchRow }) => {
        if (!p.data) return null;
        const u = urgency(p.data.days_remaining);
        return <StatusChip tone={u.tone} label={u.label} />;
      },
    },
    {
      // Always a column, never a tooltip — see the header of this file.
      field: "deadline_explanation", headerName: "Basis", flex: 1.1,
      minWidth: 210,
      headerTooltip: "Why that date and not another. Section 15 runs from "
        + "acceptance, which is not in the books, so each row names the date it "
        + "counted from.",
    },
    numeric<WatchRow>("amount_at_risk", "At risk", (v) => money(v), {
      width: 130, flex: 0,
      headerTooltip: "The deduction that moves to next year if this is still "
        + "unpaid at the year end. The balance itself — a fact, not an estimate.",
    }),
    numeric<WatchRow>("estimated_carry_cost", "Est. cost",
                      (v) => (v == null ? "—" : money(v)), {
      width: 130, flex: 0,
      headerTooltip: taxRateSet
        ? "A year's financing cost on tax paid early — not the tax itself, "
          + "because the deduction returns in the year the supplier is paid."
        : "No effective tax rate is set, so no cost is estimated. Set one in "
          + "Settings, or work from the amount at risk.",
    }),
    {
      field: "financial_year", headerName: "Falls in", width: 120, flex: 0,
      headerTooltip: "Which year's return the disallowance would land in. A "
        + "deadline in March and one in April are a year apart in consequence.",
    },
    {
      field: "scope_label", headerName: "Status", flex: 0.9, minWidth: 150,
      cellRenderer: (p: { data?: WatchRow }) => (p.data ? (
        <StatusChip
          tone={p.data.scope === "IN_SCOPE" ? "info" : "warn"}
          label={p.data.scope === "IN_SCOPE"
            ? p.data.classification : "Not established"} />
      ) : null),
    },
  ], [filter.show, taxRateSet]);

  return (
    <Panel
      title="MSME payment deadlines"
      question="Which bills carry a statutory deadline, and when does each one pass?"
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
        <MetricCard
          label="At risk this year" value={money(confirmed.amount_at_risk_this_fy ?? 0)}
          sub={`${confirmed.bills ?? 0} bill(s), confirmed suppliers`}
          tip="Deductions that move to next year if these are unpaid at the year end." />
        <MetricCard
          label="Already past the deadline" value={String(confirmed.already_past ?? 0)}
          tip={"Past bills stay listed however old — a deadline that has gone "
            + "by does not stop mattering."} />
        {/* Beside the confirmed figures and never added to them. Summing the
            two would assert an exposure nobody has established. */}
        <MetricCard
          label="Unknown status — if protected"
          value={money(Number(gaps.amount_if_protected ?? 0))}
          sub={`${gaps.suppliers ?? 0} supplier(s) not established`}
          tip={"What would be at risk if these suppliers turn out to be "
            + "covered. Not counted in the figure on the left."} />
      </Stack>

      {Number(gaps.bills ?? 0) > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {String(gaps.note ?? "")}
        </Alert>
      )}

      {!taxRateSet && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No effective tax rate is set, so no cost is estimated. The deadline and
          the amount at risk are unaffected — they are read from your own records.
        </Alert>
      )}

      <DataGrid<WatchRow>
        rows={rows}
        columns={columns}
        getRowId={(r) => `${r.vendor_id ?? "none"}:${r.bill_number ?? r.deadline}`}
        ariaLabel="Bills approaching or past their MSME payment deadline"
      />

      <p className="viz-muted" style={{ marginTop: 12 }}>
        {String(data?.basis_note ?? "")}
      </p>
    </Panel>
  );
}

// ── the coverage backlog ────────────────────────────────────────────────────
function CapturePanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "msme-capture-backlog",
    () => papi.msmeCaptureBacklog(session.token), [session.token]);

  const all = useMemo(
    () => (data?.suppliers as BacklogRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const rows = filter.filtered;

  const columns = useMemo<ColDef<BacklogRow>[]>(() => [
    {
      field: "vendor_name", headerName: "Supplier", flex: 1.4, minWidth: 200,
      cellRenderer: (p: { data?: BacklogRow }) => (p.data ? (
        <EntityName name={p.data.vendor_name} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    numeric<BacklogRow>("spend", "Bought from them", (v) => money(v),
                        { width: 170, flex: 0 }),
    {
      headerName: "Paid past the limit", width: 180, flex: 0,
      valueGetter: (p) => p.data?.late_share ?? 0,
      valueFormatter: (p) => {
        const row = p.data as BacklogRow | undefined;
        if (!row || !row.settled_total) return "no settled bills yet";
        return `${row.settled_past_limit} of ${row.settled_total}`;
      },
      headerTooltip: "How often this supplier has already been paid later than "
        + "the default limit. Status matters most where behaviour already does.",
    },
  ], [filter.show]);

  return (
    <Panel
      title="Suppliers still to establish"
      question="Whose MSME status is worth finding out first?"
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
      <p className="viz-muted" style={{ marginBottom: 12 }}>
        Showing the top {String(data?.shown ?? 0)} of{" "}
        {String(data?.unestablished ?? 0)} suppliers with no status on record,
        together worth {money(Number(data?.unestablished_spend ?? 0))} of
        purchases. Ranked by spend weighted by how often that supplier is
        already paid past the limit, so the list is the handful worth asking
        about rather than the whole vendor master. Most registered suppliers
        print their Udyam number on the invoice, which is the cheapest place to
        read it from.
      </p>
      <DataGrid<BacklogRow>
        rows={rows}
        columns={columns}
        getRowId={(r) => r.vendor_id}
        ariaLabel="Suppliers whose MSME status has not been established"
      />
    </Panel>
  );
}

// ── 194Q ────────────────────────────────────────────────────────────────────
function WithholdingPanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "withholding-crossings",
    () => papi.msmeWithholding(session.token), [session.token]);

  const all = useMemo(
    () => (data?.crossings as CrossingRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const rows = filter.filtered;
  const gated = data?.gate_confirmed === false;

  const columns = useMemo<ColDef<CrossingRow>[]>(() => [
    {
      field: "vendor_name", headerName: "Supplier", flex: 1.4, minWidth: 200,
      cellRenderer: (p: { data?: CrossingRow }) => (p.data ? (
        <EntityName name={p.data.vendor_name} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    numeric<CrossingRow>("purchases", "Bought this year", (v) => money(v),
                         { width: 170, flex: 0 }),
    {
      headerName: "State", width: 150, flex: 0,
      valueGetter: (p) => (p.data?.crossed ? 1 : 0),
      cellRenderer: (p: { data?: CrossingRow }) => (p.data ? (
        <StatusChip tone={p.data.crossed ? "warn" : "neutral"}
                    label={p.data.crossed ? "Crossed" : "Approaching"} />
      ) : null),
    },
    {
      field: "crossed_on", headerName: "Crossed on", width: 140, flex: 0,
      valueFormatter: (p) => (p.value ? formatDate(p.value) : "—"),
    },
    numeric<CrossingRow>("excess", "Above the line", (v) => money(v),
                         { width: 150, flex: 0 }),
  ], [filter.show]);

  return (
    <Panel
      title="194Q threshold"
      question="Which suppliers have crossed the purchase threshold this year?"
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
      {gated ? (
        // The refusal is the content. An empty table would read as "nobody has
        // crossed", which is a different and unearned claim.
        <Alert severity="info">{String(data?.note ?? "")}</Alert>
      ) : (
        <>
          <DataGrid<CrossingRow>
            rows={rows}
            columns={columns}
            getRowId={(r) => r.vendor_id}
            ariaLabel="Suppliers at or near the 194Q purchase threshold"
          />
          <p className="viz-muted" style={{ marginTop: 12 }}>
            {String(data?.basis_note ?? "")}
          </p>
        </>
      )}
    </Panel>
  );
}

export function StatutoryScreen({ session }: { session: PlatformSession }) {
  return (
    <div className="screen-stack">
      {/* The worklist first: it is the only one of the three with a date on it.
          Coverage second, because it is what makes the first one trustworthy.
          194Q last — it fires a few times a year, not weekly. */}
      <WatchlistPanel session={session} />
      <CapturePanel session={session} />
      <WithholdingPanel session={session} />
    </div>
  );
}
