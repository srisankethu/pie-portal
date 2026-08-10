// The shelf, the suppliers and the cash.
//
// **Two of these three are lists, and that is the design.** "Which items are
// committed beyond stock" and "which orders should I chase" are ranks with
// names attached — a person reads the top of the list and picks up the phone.
// Drawing them as charts would make somebody estimate a bar length to recover
// a number that was going to be printed next to it anyway. The one genuine
// chart here is the days-to-pay histogram, because a *distribution* is the
// thing a shape shows better than a table.
//
// Every screen renders the server's `unavailable` list. These three views sit
// closest to data the platform does not hold — a demand forecast, a promised
// delivery date, stock value for a salesperson — and the discipline that keeps
// them trustworthy is naming the gap rather than filling it.

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { scaleBand, scaleLinear } from "d3-scale";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { abilityFor } from "../ability";
import { EntityName } from "../EntityName";
import { ChartTip, InlineLink, MetricCard, StatusChip, VarianceIndicator } from "../kit";
import type { Tone } from "../kit";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Figure, Panel, ValueAxis, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, pp, useInsight } from "./useInsight";
import { compactMoney, useMeasure } from "./useMeasure";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

/** What the server says it cannot show, and why. Never omitted. */
function Unavailable({ items }: { items: Row[] }) {
  if (!items.length) return null;
  return (
    <ul className="tl-unavailable">
      {items.map((u, i) => (
        <li key={i}>
          <strong>{String(u.series).replace(/_/g, " ")}</strong> — not shown.{" "}
          <span className="viz-muted">{String(u.reason)}</span>
        </li>
      ))}
    </ul>
  );
}

// ── Cash: what the committed book does next ─────────────────────────────────
//
// The one forward-looking view in the product, and it is forward-looking only
// in the sense that it reads *dates already written on documents*. Every rupee
// on this chart is an invoice raised or a bill received; nothing is a forecast
// of trade that has not happened, and nothing is weighted by how likely it is
// to be paid.
//
// **It says movement, never position.** PIE reads payments, not balances, so
// there is no opening figure to run a balance from. "The committed book moves
// cash by −₹4.2L over thirteen weeks, worst in week six" is answerable;
// "you run out on 12 October" is not, and the difference is the whole reason
// this panel is trustworthy.
//
// Four totals sit beside the chart rather than inside it — overdue, past the
// horizon, undated, and open orders. Each is real money that cannot honestly be
// drawn as a bar in a particular week, and a projection whose parts do not add
// up to the book is a projection people stop trusting.

// ── Self-funding: what we kept, against what the growth had to be paid for ──
//
// Owner only, and silent until confirmed. Half of this reading is retained
// profit after tax, which is not in this platform and is not derived from
// anything in it: PIE reads invoices and bills, not a ledger, so the nearest
// figure available is gross profit and gross profit is a different and much
// larger number. Substituting it would overstate what was kept by the entire
// cost of running the business, silently, in the direction that reads as good
// news — so the owner confirms each entity's figure in Settings and this panel
// says what is missing until they have.
//
// The verdict is deliberately one-sided and the panel says so out loud. Growth
// consumes working capital, a fraction of the revenue increase, and that
// fraction is not measured here — so retained profit above the whole increase
// is "covered", and below it is "undetermined", never "unfunded".

const VERDICT: Record<string, [string, Tone, string]> = {
  COVERED: ["Covered", "good",
            "Retained profit exceeds the whole revenue increase, so it covers "
            + "the growth whatever share of it turned into working capital."],
  UNDETERMINED: ["Undetermined", "neutral",
                 "Retained profit is smaller than the revenue increase. That is "
                 + "not a finding that growth was funded from outside — growth "
                 + "consumes working capital, a fraction of the increase, and "
                 + "that fraction is not measured here."],
  NOT_GROWING: ["Not growing", "info",
                "Revenue did not grow against the previous year, so there was "
                + "no growth to fund."],
  UNKNOWN: ["Unknown", "warn", "Not enough is confirmed to read this."],
};

function SelfFunding({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "self-funding", () => papi.selfFunding(session.token), [session.token]);

  const confirmed = Boolean(data?.confirmed);
  const missing = (data?.missing_entities as string[] | undefined) ?? [];
  // The server's reason, with the entities it is waiting on named. One string
  // so the shared empty state carries the whole answer, rather than a generic
  // sentence in the panel and the useful half somewhere else.
  const reason = confirmed || !data
    ? (data?.empty_reason as string | null | undefined)
    : [data.blocked_by as string,
       missing.length ? `Still to confirm: ${missing.join(", ")}.` : ""]
      .filter(Boolean).join(" ");

  const [verdictLabel, tone, verdictHelp] =
    VERDICT[String(data?.verdict ?? "UNKNOWN")] ?? VERDICT.UNKNOWN;
  const entities = rows(data?.entities);

  return (
    <Panel
      title="Self-funding growth"
      question="Is the book outgrowing the profit it keeps"
      state={stateOf(loading, error, reason)}
      error={error} emptyReason={reason} onRetry={reload}
    >
      <Stack spacing={2}>
        <Box sx={{
          display: "grid", gap: 2,
          gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)",
                                 md: "repeat(4, 1fr)" },
        }}>
          <MetricCard
            label="Retained after tax"
            value={money(num(data?.retained_pat))}
            sub={`${String(data?.financial_year ?? "")}, confirmed from each entity's accounts`}
            variance={<StatusChip label={verdictLabel} tone={tone} tip={verdictHelp} />}
          />
          <MetricCard
            label="Revenue growth"
            value={money(num(data?.revenue_growth))}
            sub={`${money(num(data?.previous_revenue))} → ${money(num(data?.revenue))}`}
          />
          <MetricCard
            label="Grew by"
            value={pct(num(data?.growth_ratio))}
            sub="Of the revenue the year started from."
          />
          <MetricCard
            label="Kept"
            value={pct(num(data?.retention_ratio))}
            // Both rates are over the revenue the year started from, which is
            // the only thing that makes their difference meaningful — and a
            // difference of two ratios is percentage points, never a percent.
            sub={`Same base as growth. Gap ${pp(num(data?.funding_gap_pp))}.`}
          />
        </Box>

        {/* A fact panel: one row per legal entity, a count set by the shape of
            the business rather than by its size. ui-standards §3's named
            exception, not a grid. */}
        <table className="facttable">
          <caption className="viz-muted">
            Retained profit after tax, {String(data?.financial_year ?? "")}
          </caption>
          <tbody>
            {entities.map((e) => (
              <tr key={String(e.entity)}>
                <td>{String(e.label)}</td>
                <td className="fv">{money(num(e.retained_pat))}</td>
              </tr>
            ))}
            <tr>
              <td><b>Together</b></td>
              <td className="fv">{money(num(data?.retained_pat))}</td>
            </tr>
          </tbody>
        </table>

        <p className="viz-muted viz-footnote">{String(data?.basis_note ?? "")}</p>
        <p className="viz-muted viz-footnote">{String(data?.limit_note ?? "")}</p>
      </Stack>
    </Panel>
  );
}

const HORIZONS: [string, string][] = [["13", "13 weeks"], ["26", "26 weeks"]];

function CashProjection({ session }: { session: PlatformSession }) {
  const [weeks, setWeeks] = useState("13");
  const { data, loading, error, reload } = useInsight(
    "cashflow",
    () => papi.cashflow(session.token, Number(weeks)), [session.token, weeks]);
  const [ref, room] = useMeasure<HTMLDivElement>();

  const buckets = rows(data?.buckets);
  const overdue = (data?.overdue ?? {}) as Row;
  const undated = (data?.undated ?? {}) as Row;
  const beyond = (data?.beyond_horizon ?? {}) as Row;
  const unscheduled = (data?.unscheduled ?? {}) as Row;
  const unattributed = (data?.unattributed ?? {}) as Row;
  const net = num(data?.net_over_horizon);
  const lowest = num(data?.lowest_cumulative);
  // The band, named for what each corner costs the week rather than for how
  // fast anybody is: `worst` is customers at their slowest *and* us paying at
  // our fastest, which is the genuinely deepest trough now that both sides can
  // move. `requirement` is still taken from the server, which minimises across
  // all three rather than leaving the client to assume which one wins.
  const scenarios = (data?.scenarios ?? {}) as Record<string, Row>;
  const basis = (data?.basis ?? {}) as Row;
  const requirement = num(data?.requirement);
  const best = rows(scenarios.best?.buckets);
  const expected = rows(scenarios.expected?.buckets);
  const worst = rows(scenarios.worst?.buckets);
  const measuredShare = num(basis.share_measured);
  const outflowShare = num(basis.outflow_share_measured);
  const outflowShifted = Boolean(basis.outflow_shifted);
  const banded = expected.length > 0 && (measuredShare > 0 || outflowShare > 0);

  /** Named beside the chart, never drawn on it. Zero rows are dropped rather
   *  than shown as "₹0" — an empty row teaches a reader to skip the list. */
  const aside = [
    { key: "overdue", label: "Already due, not settled",
      why: "Real, and not week-one movement — being overdue is what disproves that.",
      inflow: num(overdue.inflow), outflow: num(overdue.outflow) },
    { key: "beyond", label: `Dated past ${formatDate(data?.horizon_ends_on as string)}`,
      why: "Counted so the parts still add up to the book.",
      inflow: num(beyond.inflow), outflow: num(beyond.outflow) },
    { key: "undated", label: "No terms on record",
      why: "Owed, with no due date to place it. Defaulting one would invent terms nobody gave.",
      inflow: num(undated.inflow), outflow: num(undated.outflow) },
    { key: "orders", label: "Open orders",
      why: "Committed, and carrying no due date — only the invoice or bill that follows has one.",
      inflow: num(unscheduled.open_sales_value),
      outflow: num(unscheduled.open_purchase_value) },
  ].filter((r) => r.inflow > 0 || r.outflow > 0);

  return (
    <Panel
      title="Cash from the committed book"
      question="What does what we have already promised do to cash"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="Horizon" value={weeks} onChange={setWeeks} options={HORIZONS} />
        </div>
      }
    >
      <p className="viz-headline">
        Over {String(data?.weeks ?? "")} weeks the committed book moves cash by{" "}
        <strong>{net >= 0 ? "+" : "−"}{money(Math.abs(net))}</strong>
        {lowest < 0 && Boolean(data?.lowest_week_starts_on) && (
          <> · deepest at <strong>−{money(Math.abs(lowest))}</strong> in the week
          of {formatDate(String(data?.lowest_week_starts_on))} if everyone pays
          to terms</>
        )}.{" "}
        {/* The number somebody funding a week actually wants. Due dates assume
            every party moves on the day; the headline says what the worst
            measured timing needs, and the band shows the space between. */}
        {banded && requirement < lowest && (
          <>
            At the speed money has actually moved, the deepest point is{" "}
            <strong>−{money(Math.abs(requirement))}</strong>
            {Boolean(scenarios.worst?.lowest_week_starts_on) && (
              <> in the week of{" "}
              {formatDate(String(scenarios.worst?.lowest_week_starts_on))}</>
            )}.{" "}
          </>
        )}
        <span className="viz-muted">
          Movement, not a balance — the platform reads payments, never bank
          balances, so there is no position to run this from.
          {banded && (
            <>
              {" "}The band covers {pct(measuredShare)} of the money coming in
              {outflowShifted && <> and {pct(outflowShare)} of the money going
              out</>}: the rest has too little settled history to measure and is
              left on its due date rather than given a made-up one.{" "}
              {outflowShifted
                ? "The worst case is the corner, not the slow line — customers "
                  + "at their slowest and us paying at our fastest, because "
                  + "money leaving later is what the week needs less of."
                : "Bills do not move: nothing has settled often enough to "
                  + "measure how this book pays its suppliers, so the outflow "
                  + "sits on its due dates and the best case is conservative."}
            </>
          )}
        </span>
      </p>

      <div ref={ref}>
        <Figure
          caption={banded
            ? "Bars are invoices and bills falling due, in the week their own document names. The shaded band is the running total between the best and worst case for the week: best is customers at their fastest and us paying at our slowest, worst is the reverse. The line inside it is everybody at their own median. Nothing here is a forecast of trade that has not happened — it is the same committed money, on the dates the payers have used."
            : "Bars are invoices and bills falling due, in the week their own document names. The line is the running total of those bars, from zero. Nothing here is a forecast of trade that has not happened."}
          summary={buckets.map((b) =>
            `Week of ${formatDate(b.starts_on as string)}: in ${money(num(b.inflow))}, out ${money(num(b.outflow))}, running ${money(num(b.cumulative))}`,
          ).join("; ")}
          table={
            <DataGrid<Row>
              ariaLabel="Committed cash by week"
              pageSize={26}
              filters={false}
              rows={buckets}
              columns={[
                {
                  field: "starts_on", headerName: "Week of", width: 150, flex: 0,
                  valueFormatter: (p) => (p.value ? formatDate(String(p.value)) : "—"),
                },
                numeric<Row>("inflow", "Money in", (v) => money(v),
                             { width: 160, flex: 0 }),
                numeric<Row>("outflow", "Money out", (v) => money(v),
                             { width: 160, flex: 0 }),
                numeric<Row>("net", "Net", (v) =>
                  `${v >= 0 ? "+" : "−"}${money(Math.abs(v))}`,
                  { width: 160, flex: 0 }),
                numeric<Row>("cumulative", "Running total", (v) => money(v), {
                  width: 180, flex: 0,
                  headerTooltip: "The bars added up from zero, week by week. "
                    + "Movement, not a balance — the platform reads payments, "
                    + "never bank balances.",
                }),
              ]}
            />
          }
        >
          {room.width > 0 && buckets.length > 0 && (
            <CashChart buckets={buckets} width={room.width}
                       currency={String(data?.currency ?? "INR")}
                       best={banded ? best : []}
                       expected={banded ? expected : []}
                       worst={banded ? worst : []} />
          )}
        </Figure>
      </div>

      {aside.length > 0 && (
        <div className="tier3-list">
          <h4>Not on the timeline, and why</h4>
          <ul className="cash-aside">
            {aside.map((r) => (
              <li key={r.key}>
                <span className="cash-aside-head">
                  <strong>{r.label}</strong>
                  {/* "in" and "out" stay as words — they are the direction,
                      and an arrow alone would leave the reader to work out
                      which way is which. The colour now comes from the theme. */}
                  <span className="cash-aside-figures">
                    {r.inflow > 0 && <VarianceIndicator value={r.inflow} label="in" />}
                    {r.outflow > 0 && <VarianceIndicator value={-r.outflow} label="out" />}
                  </span>
                </span>
                <span className="viz-muted">{r.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The one reconciliation worth stating out loud: the schedule counts an
          obligation whether or not its party resolved, and the receivables and
          supplier screens cannot. Saying so is what stops two screens with
          different totals looking like a bug in one of them. */}
      {(num(unattributed.inflow) > 0 || num(unattributed.outflow) > 0) && (
        <p className="viz-muted viz-footnote">
          {money(num(unattributed.inflow) + num(unattributed.outflow))} of this
          belongs to a customer or supplier the contact pull did not return. It
          is real money and it is counted here; it simply has no name to file it
          under, which is why the Customers and Suppliers screens show less.
        </p>
      )}
    </Panel>
  );
}

/** Weekly in-and-out against a zero rule, with the running total over it.
 *
 *  Two bars per week rather than one net bar: a week that takes ₹5L in and
 *  pays ₹5L out is not the same week as one where nothing happens, and a net
 *  bar draws them identically. */
function CashChart({
  buckets, width, currency, best = [], expected = [], worst = [],
}: {
  buckets: Row[]; width: number; currency: string;
  /** The same committed book at the two corners and the middle: `best` is
   *  customers at their fastest and us paying at our slowest, `worst` the
   *  reverse. Empty when nothing is measured, in which case the chart is
   *  exactly what it was: bars and one running line. */
  best?: Row[]; expected?: Row[]; worst?: Row[];
}) {
  const H = 260;
  const PAD = { top: 14, right: 10, bottom: 46, left: 62 };

  const flows = buckets.flatMap((b) => [num(b.inflow), -num(b.outflow)]);
  const running = buckets.map((b) => num(b.cumulative));
  const bestRun = best.map((b) => num(b.cumulative));
  const expectedRun = expected.map((b) => num(b.cumulative));
  const worstRun = worst.map((b) => num(b.cumulative));
  const banded = expectedRun.length === buckets.length
    && bestRun.length === buckets.length && worstRun.length === buckets.length;
  // The scale has to hold the whole band, or the worst case — the one number
  // this chart exists to show — is drawn off the bottom of its own axis.
  const y = scaleLinear()
    .domain([Math.min(...flows, ...running, ...worstRun, ...bestRun, 0),
             Math.max(...flows, ...running, ...worstRun, ...bestRun, 0)])
    .range([H - PAD.bottom, PAD.top])
    .nice();
  const band = scaleBand<number>()
    .domain(buckets.map((_, i) => i))
    .range([PAD.left, Math.max(PAD.left + 1, width - PAD.right)])
    .paddingInner(0.34);
  // Two bars share a band, so each gets half of it minus a hairline gap.
  const barW = Math.max(2, band.bandwidth() / 2 - 1);
  const zero = y(0);

  return (
    <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`}
         className="viz-svg" role="presentation">
      <ValueAxis scale={y} x0={PAD.left} x1={width - PAD.right}
                 format={(v) => compactMoney(v, currency)} />

      {buckets.map((b, i) => {
        const left = band(i) ?? PAD.left;
        const inflow = num(b.inflow);
        const outflow = num(b.outflow);
        const label = String(b.starts_on ?? "");
        const net = num(b.net);
        // One tooltip for the whole week, on a transparent strip covering the
        // band. Per-bar tooltips would mean hovering a 6px rectangle to learn
        // what a week does, and the out-bar of a week with no outflow does not
        // exist to hover at all.
        const tip = (
          <>
            <strong>Week of {formatDate(label)}</strong>
            <br />
            Money in {money(inflow)}
            <br />
            Money out {money(outflow)}
            <br />
            Net {net >= 0 ? "+" : "−"}{money(Math.abs(net))} this week
            <br />
            <span style={{ opacity: 0.85 }}>
              Running total from zero: {money(num(b.cumulative))}
            </span>
            <br />
            <span style={{ opacity: 0.8 }}>
              Invoices and bills already raised — not a forecast
            </span>
          </>
        );
        return (
          <g key={i}>
            {inflow > 0 && (
              <rect x={left} y={y(inflow)} width={barW}
                    height={Math.max(1, zero - y(inflow))}
                    className="cash-bar cash-bar-in" rx="1.5" />
            )}
            {outflow > 0 && (
              <rect x={left + barW + 2} y={zero} width={barW}
                    height={Math.max(1, y(-outflow) - zero)}
                    className="cash-bar cash-bar-out" rx="1.5" />
            )}
            <ChartTip title={tip}>
              <rect x={left} y={PAD.top} width={band.bandwidth()}
                    height={Math.max(1, H - 26 - PAD.top)}
                    className="cash-hit" />
            </ChartTip>
            {/* Every fourth week carries a date. Thirteen dates at this width
                overlap into a grey smear, and a label nobody can read is worse
                than none because it still costs the space. */}
            {i % 4 === 0 && (
              <text x={left + band.bandwidth() / 2} y={H - 26}
                    textAnchor="middle" className="viz-axis">
                {formatDate(label)}
              </text>
            )}
          </g>
        );
      })}

      <line x1={PAD.left} x2={width - PAD.right} y1={zero} y2={zero}
            stroke="var(--viz-rule)" strokeWidth="1" />
      {/* The band between the two corners. Drawn under the lines so neither is
          obscured, and filled rather than outlined because its *width* is the
          message: a narrow band is a book that can be planned, a wide one is
          not. */}
      {banded && (
        <path
          className="cash-band"
          d={[
            ...bestRun.map((v, i) =>
              `${i === 0 ? "M" : "L"}${(band(i) ?? PAD.left) + band.bandwidth() / 2},${y(v)}`),
            // Back along the worst edge, so the two lines close into one shape.
            ...worstRun.map((_, j) => {
              const i = worstRun.length - 1 - j;
              return `L${(band(i) ?? PAD.left) + band.bandwidth() / 2},${y(worstRun[i])}`;
            }),
            "Z",
          ].join(" ")}
        />
      )}
      {/* The running total. A line rather than a third bar: it is a level at a
          point in time, not a quantity arriving in that week.

          On terms — every document settled on the day it says — kept as the
          reference line because it is the only one that asserts nothing beyond
          what the documents say. Note it is no longer the best case: a book
          that habitually pays its suppliers late does better than its terms. */}
      <polyline
        className="cash-running"
        points={buckets.map((_, i) =>
          `${(band(i) ?? PAD.left) + band.bandwidth() / 2},${y(running[i])}`).join(" ")}
      />
      {banded && (
        <polyline
          className="cash-running cash-running-expected"
          points={expectedRun.map((v, i) =>
            `${(band(i) ?? PAD.left) + band.bandwidth() / 2},${y(v)}`).join(" ")}
        />
      )}
      <text x={PAD.left} y={H - 8} className="viz-axis-note">
        {banded
          ? "running total from zero · solid: on terms · dashed: how money actually moves · band: best case to worst"
          : "running total, from zero"}
      </text>
    </svg>
  );
}

// ── Cash: how long settlement actually takes, on either side of the ledger ──
//
// One panel, both directions, for the same reason `insight/payments.py` is one
// module: the figures are identical measurements with the parties swapped, and
// a second copy of this markup would be the place the two screens started
// disagreeing about what a spread means. What differs is the wording and where
// a row navigates to, so those arrive as props.

/** Everything about a side that the panel below cannot compute for itself. */
type LedgerSide = {
  /** Response key holding the party rows, and the id field on each of them. */
  parties: "customers" | "vendors";
  partyId: "customer_id" | "vendor_id";
  title: string;
  question: string;
  heading: string;
  /** "invoice" / "bill", already lower-case and singular. */
  document: string;
  /** Where a row goes when clicked, or null when there is nowhere to go. */
  href: ((id: string) => string) | null;
};

function SettlementPanel({
  data, loading, error, reload, side, onNavigate,
}: {
  data: Record<string, unknown> | null;
  loading: boolean; error: string | null; reload: () => void;
  side: LedgerSide;
  onNavigate: (r: string) => void;
}) {
  const parties = rows(data?.[side.parties]);
  const distribution = rows(data?.distribution);
  const peak = Math.max(...distribution.map((d) => num(d.count)), 1);
  const median = data?.median_days_to_pay as number | null | undefined;
  const lateShare = data?.late_share as number | null | undefined;
  const minSettlements = num(data?.min_settlements);
  const datable = num(data?.settlements) - num(data?.undatable_count);
  const patterns = (data?.patterns as Record<string, Record<string, string>>) ?? {};
  const trends = (data?.trends as Record<string, string>) ?? {};
  const patternCounts = (data?.pattern_counts as Record<string, number>) ?? {};
  const sourcesDiffer = Boolean(data?.sources_differ);
  const unattributed = num(data?.unattributed);
  const docs = `${side.document}s`;

  return (
    <Panel
      title={side.title}
      question={side.question}
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <p className="viz-headline">
        Half of all {docs} are settled within{" "}
        <strong>{median == null ? "—" : `${median} days`}</strong>
        {lateShare != null && (
          // The denominator travels with the share. "100% were late" over one
          // document is true and useless; "1 of 1" is true and self-limiting.
          <> · <strong>{Math.round(lateShare * datable)} of {datable}</strong>{" "}
          {docs} with terms on record were paid late</>
        )}
        {num(data?.advances) > 0 && (
          <> · {String(data?.advances)} advance
          {num(data?.advances) === 1 ? "" : "s"} held
          {num(data?.unapplied_total) > 0 &&
            ` (${money(num(data?.unapplied_total))} unapplied)`}</>
        )}.
      </p>

      {/* A distribution really is better as a shape: the question "are terms
          being met" is answered by where the mass sits, not by any one
          bucket's count. */}
      <Figure
        caption={`Each bar is one ${side.document} settled, bucketed by how many days it took. Buckets are fixed rather than quantile — terms are absolute, and a moving band would stop 30 days meaning 30 days.`}
        summary={distribution.map((d) => `${d.label}: ${d.count}`).join(", ")}
        table={
          <table className="viz-table">
            <thead><tr><th scope="col">Days to pay</th><th scope="col">Documents</th></tr></thead>
            <tbody>
              {distribution.map((d, i) => (
                <tr key={i}><th scope="row">{String(d.label)}</th><td>{String(d.count)}</td></tr>
              ))}
            </tbody>
          </table>
        }
      >
        <ul className="dist">
          {distribution.map((d, i) => (
            <li className="dist-row" key={i}>
              <span className="dist-label">{String(d.label)}</span>
              <span className="dist-track">
                <span className="dist-fill"
                      style={{ width: `${(num(d.count) / peak) * 100}%` }} />
              </span>
              <span className="dist-count">{String(d.count)}</span>
            </li>
          ))}
        </ul>
      </Figure>

      {/* Slow and unpredictable are different problems, so they are different
          rows. A party that always takes 45 days can be planned around; one
          that takes 5 or 95 cannot, whatever their average says. */}
      <ul className="quad-legend">
        {["PROMPT", "PREDICTABLY_LATE", "ERRATIC", "TOO_FEW"].map((key) => (
          <li key={key} className={`quad quad-${
            key === "ERRATIC" ? "bad" : key === "PREDICTABLY_LATE" ? "warn"
              : key === "PROMPT" ? "good" : "flat"}`}>
            <span className="quad-count">{patternCounts[key] ?? 0}</span>
            <span className="quad-body">
              <strong>{patterns[key]?.label ?? key}</strong>
              <span className="viz-muted">{patterns[key]?.meaning}</span>
            </span>
          </li>
        ))}
      </ul>

      <div className="tier3-list">
        <h4>{side.heading}</h4>
        <ol className="cadence-rows">
          {parties.slice(0, 15).map((c, i) => {
            const id = String(c[side.partyId]);
            const body = (
              <>
                {/* Two accounts sharing a name across two books are two
                    different relationships with two different people. */}
                <span className="cadence-name">
                  <EntityName
                    name={String(c.label)}
                    origin={c.origin as EntityOrigin | undefined}
                    show={sourcesDiffer}
                    strong={false}
                  />
                </span>
                <span className="cadence-figures">
                  <span>
                    {/* Below the floor there is no rhythm to report, and the
                        row says so instead of printing a median of two. */}
                    {c.estimable
                      ? `${c.median_days_to_pay} days typical`
                      : `only ${c.settlements} settled — no typical yet`}
                    {/* Promised against actual, where both halves exist. A
                        gap of zero is a real statement; a missing one is not
                        the same thing and is left off entirely. */}
                    {c.terms_gap_days != null && (
                      <span className="viz-muted">
                        {" · "}terms say {String(c.agreed_terms_days)}d,{" "}
                        {Number(c.terms_gap_days) > 0
                          ? `${c.terms_gap_days}d over`
                          : Number(c.terms_gap_days) < 0
                            ? `${Math.abs(Number(c.terms_gap_days))}d inside`
                            : "met"}
                      </span>
                    )}
                  </span>
                  {/* Past their line, said on the row that already says how
                      slowly they pay. Absent on the payable side, where a
                      supplier has no credit limit with us — the field simply
                      is not there, rather than being rendered as zero. */}
                  {c.credit_status === "OVER" && (
                    <span>
                      <StatusChip label={`${money(num(c.over_by))} over limit`}
                                  tone="bad" dense
                                  tip={(data?.credit_statuses as Record<string,
                                        Record<string, string>>
                                        )?.OVER?.meaning} />
                    </span>
                  )}
                  <span className="viz-muted">
                    {patterns[String(c.pattern)]?.label ?? ""}
                    {c.spread_days != null && Number(c.spread_days) > 0 &&
                      ` ±${c.spread_days}d`}
                    {c.trend !== "STEADY" && c.trend !== "UNKNOWN" &&
                      ` · ${trends[String(c.trend)]?.toLowerCase()}`}
                    {" · "}{money(num(c.total_settled))} settled
                  </span>
                </span>
                {Number(c.worst_days_to_pay) > 90 ? (
                  <span className="cadence-flag">{String(c.worst_days_to_pay)}d worst</span>
                ) : null}
              </>
            );
            return (
              <li key={i} className="cadence-row">
                {side.href ? (
                  <button type="button" className="cadence-hit"
                          onClick={() => onNavigate(side.href!(id))}>
                    {body}
                  </button>
                ) : (
                  // No supplier screen to open yet. A button that navigates
                  // nowhere is worse than a row that does not pretend to.
                  <span className="cadence-hit">{body}</span>
                )}
              </li>
            );
          })}
        </ol>
        <p className="viz-muted viz-footnote">
          Measured per {side.document} settled, not per payment — one transfer
          clearing ten {docs} is ten observations. Fewer than {minSettlements}{" "}
          settled {docs} and no pattern is asserted. The spread is a median
          absolute deviation, so one {side.document} paid nine months late stays
          a story about that {side.document} rather than redefining the account.
          {unattributed > 0 && (
            <> {unattributed} settlement{unattributed === 1 ? "" : "s"} could not
            be attributed to anyone the contact pull returned, and {unattributed === 1
              ? "is" : "are"} left out of the rows above rather than filed under
            a placeholder.</>
          )}
        </p>
      </div>

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}

const RECEIVABLE_SIDE: LedgerSide = {
  parties: "customers", partyId: "customer_id",
  title: "Cash collection",
  question: "How long does the money take to arrive, and from whom",
  heading: "Slowest payers first",
  document: "invoice",
  href: (id) => `customer/${id}`,
};

const PAYABLE_SIDE: LedgerSide = {
  parties: "vendors", partyId: "vendor_id",
  title: "How we pay",
  question: "How long do we take to settle, and with whom",
  heading: "Suppliers we take longest with",
  document: "bill",
  // Suppliers have no per-account screen the way customers do, so a row here
  // is a fact to read rather than a door.
  href: null,
};

// ── The line we gave them, and whose book the account is in ────────────────
//
// "Rane Madras takes 69 days to pay" is an observation. "Rane Madras is ₹8 lakh
// past the limit we gave them, and it is Rahul's account" is a decision, and
// this panel is the difference between the two. It sits on the collections
// screen beside the payment behaviour for exactly that reason — the two facts
// are read together or neither is worth much.
//
// **Absent is never rendered as zero.** An account with no limit recorded shows
// "none recorded", not "₹0" and not a full bar: nobody has decided, which is a
// different thing from having decided nothing. The server keeps the two apart
// with `has_limit`; this reads that flag rather than testing the number.
//
// A grid, because the row count is the number of customers — the size of the
// business, which is the rule in `platform/DataGrid.tsx`. Sorting by how far
// over the line an account is *is* the collections queue.

type CreditRow = Sourced & {
  customer_id: string; label: string;
  has_limit: boolean;
  limit: number | null;
  outstanding: number;
  overdue: number;
  headroom: number | null;
  over_by: number | null;
  status: string;
  note: string | null;
  set_by: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  owner_source: string | null;
  open_invoices: number;
  overdue_invoices: number;
};

type Person = { user_id: string; name: string; role: string };

/** Which book to look at. "Anyone" for a manager scanning the org, a person for
 *  "what is Rahul sitting on", and the accounts nobody owns — which is its own
 *  answer, because an unowned overdue account is one nobody is chasing. */
const ANYONE = "";
const NOBODY = "__unassigned__";

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad"> = {
  OVER: "bad", NEAR: "warn", WITHIN: "good", NO_LIMIT: "neutral",
};

function CreditPanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "credit", () => papi.credit(session.token), [session.token]);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [owner, setOwner] = useState<string>(ANYONE);

  const maySet = Boolean(data?.may_set);
  const people = useMemo(
    () => (data?.people as Person[] | undefined) ?? [], [data]);
  const statuses = (data?.statuses as Record<string, Record<string, string>>) ?? {};
  const all = useMemo(
    () => (data?.accounts as CreditRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const accounts = useMemo(
    () => filter.filtered.filter((r) =>
      owner === ANYONE ? true
        : owner === NOBODY ? r.owner_user_id == null
          : r.owner_user_id === owner),
    [filter.filtered, owner]);

  async function save(row: CreditRow, amount: number | null) {
    setBusy(row.customer_id);
    setFailed(null);
    try {
      if (amount === null) await papi.clearCreditLimit(session.token, row.customer_id);
      else await papi.setCreditLimit(session.token, row.customer_id, amount, row.note);
      // Refetched rather than patched in place: the status and the headroom are
      // read against a balance the browser does not hold.
      reload();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : "Could not save that limit");
    } finally {
      setBusy(null);
    }
  }

  async function assign(row: CreditRow, userId: string) {
    setBusy(row.customer_id);
    setFailed(null);
    try {
      if (userId === "") await papi.clearAccountOwner(session.token, row.customer_id);
      else await papi.setAccountOwner(session.token, row.customer_id, userId);
      reload();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : "Could not assign that account");
    } finally {
      setBusy(null);
    }
  }

  const columns = useMemo<ColDef<CreditRow>[]>(() => [
    {
      field: "label", headerName: "Account", flex: 1.3, minWidth: 190,
      cellRenderer: (p: { data?: CreditRow }) => (p.data ? (
        <EntityName name={p.data.label} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    {
      headerName: "Owner", width: 190, flex: 0,
      valueGetter: (p) => p.data?.owner_name ?? "",
      cellRenderer: (p: { data?: CreditRow }) => {
        const row = p.data;
        if (!row) return null;
        if (!maySet) {
          return row.owner_name ? (
            <Box component="span">{row.owner_name}</Box>
          ) : <Box component="span" className="viz-muted">unassigned</Box>;
        }
        return (
          <TextField
            select size="small" value={row.owner_user_id ?? ""}
            disabled={busy === row.customer_id}
            aria-label={`Who owns ${row.label}`}
            onChange={(e) => void assign(row, e.target.value)}
            sx={{ width: 168, "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
          >
            <MenuItem value="">
              {/* Withdrawing an assignment falls back to Zoho's salesperson,
                  which is not the same as picking nobody. */}
              <span className="viz-muted">unassigned</span>
            </MenuItem>
            {people.map((u) => (
              <MenuItem key={u.user_id} value={u.user_id}>{u.name}</MenuItem>
            ))}
          </TextField>
        );
      },
    },
    numeric<CreditRow>("outstanding", "Owed now", (v) => money(v), {
      width: 130, flex: 0,
      headerTooltip: "What Zoho says is still owed on their open invoices. Read "
        + "from the receivables fold, never recomputed here.",
    }),
    numeric<CreditRow>("overdue", "Of it, overdue", (v) => money(v), {
      width: 140, flex: 0,
      headerTooltip: "The part past its due date. Over the limit with none of it "
        + "late and over the limit with all of it late are two different calls.",
    }),
    {
      headerName: "Credit limit", width: 180, flex: 0,
      valueGetter: (p) => p.data?.limit ?? null,
      cellRenderer: (p: { data?: CreditRow }) => {
        const row = p.data;
        if (!row) return null;
        if (!maySet) {
          return row.has_limit ? (
            <Box component="span">{money(row.limit ?? 0)}</Box>
          ) : <Box component="span" className="viz-muted">none recorded</Box>;
        }
        return (
          <TextField
            size="small" type="number"
            defaultValue={row.has_limit ? String(row.limit) : ""}
            placeholder="none recorded"
            disabled={busy === row.customer_id}
            aria-label={`Credit limit for ${row.label}`}
            // Commits when the field is finished with, never per keystroke —
            // the same rule the supplier terms grid follows.
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            onBlur={(e) => {
              const raw = e.target.value.trim();
              const next = raw === "" ? null : Number(raw);
              if (next !== null && (!Number.isFinite(next) || next < 0)) return;
              if (next === null && !row.has_limit) return;
              if (next !== null && row.has_limit && next === row.limit) return;
              void save(row, next);
            }}
            sx={{ width: 132, "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
          />
        );
      },
    },
    {
      headerName: "Against the limit", flex: 1, minWidth: 220,
      // Sorted on how far past the line they are, which is the collections
      // queue. Accounts with no limit sort last rather than as zero.
      valueGetter: (p) => (p.data?.has_limit ? (p.data?.headroom ?? 0) : null),
      cellRenderer: (p: { data?: CreditRow }) => {
        const row = p.data;
        if (!row) return null;
        const meaning = statuses[row.status]?.meaning;
        if (!row.has_limit) {
          return (
            <StatusChip label="no limit recorded" tone="neutral" dense
                        tip={meaning} />
          );
        }
        return (
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
            <StatusChip label={statuses[row.status]?.label ?? row.status}
                        tone={STATUS_TONE[row.status] ?? "neutral"} dense
                        tip={meaning} />
            <Box component="span">
              {row.status === "OVER"
                ? `${money(row.over_by ?? 0)} over`
                : `${money(row.headroom ?? 0)} left`}
              {row.overdue_invoices > 0 && (
                <Box component="span" className="viz-muted">
                  {" "}· {row.overdue_invoices} invoice
                  {row.overdue_invoices === 1 ? "" : "s"} past due
                </Box>
              )}
            </Box>
          </Stack>
        );
      },
    },
    {
      headerName: "", width: 96, flex: 0, sortable: false, filter: false,
      cellRenderer: (p: { data?: CreditRow }) => (
        maySet && p.data && p.data.has_limit ? (
          <Button size="small" color="inherit"
                  disabled={busy === p.data.customer_id}
                  onClick={() => void save(p.data!, null)}>
            Clear
          </Button>
        ) : null
      ),
    },
  ], [busy, filter.show, maySet, people, statuses]);

  const over = num(data?.over_limit_count);
  const recorded = num(data?.limits_recorded);

  return (
    <Panel
      title="Credit and exposure"
      question="Who is past the line we gave them, and whose account is it"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          {maySet && people.length > 0 && (
            <TextField
              select size="small" value={owner}
              aria-label="Show one person's book"
              onChange={(e) => setOwner(e.target.value)}
              sx={{ minWidth: 160,
                    "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
            >
              <MenuItem value={ANYONE}>Everyone's accounts</MenuItem>
              {people.map((u) => (
                <MenuItem key={u.user_id} value={u.user_id}>{u.name}'s book</MenuItem>
              ))}
              <MenuItem value={NOBODY}>Unassigned</MenuItem>
            </TextField>
          )}
          <CompanyFilter options={filter.options} value={filter.company}
                         onChange={filter.setCompany} show={filter.show} />
        </Stack>
      }
    >
      <p className="viz-headline">
        {over > 0 ? (
          <>
            <strong>{over} account{over === 1 ? " is" : "s are"}</strong> past the
            credit limit they were given, by{" "}
            <strong>{money(num(data?.over_limit_total))}</strong> in total.
          </>
        ) : (
          <>No account is past its credit limit.</>
        )}{" "}
        <span className="viz-muted">
          {recorded} of {rows(data?.accounts).length} accounts have a limit on
          record at all — the rest are not unlimited, they are undecided, and
          nothing here treats a missing limit as one.
        </span>
      </p>
      {failed && <p className="viz-muted" role="alert">{failed}</p>}
      <DataGrid<CreditRow>
        ariaLabel="Credit limits and exposure by account"
        rows={accounts}
        columns={columns}
        pageSize={20}
        rowHeight={54}
        getRowId={(r) => r.customer_id}
      />
      <p className="viz-muted viz-footnote">
        The balance is what Zoho says is still owed, read from the receivables
        fold rather than recomputed — deriving it as invoiced minus received
        would be wrong the moment a credit note lands, in the direction that gets
        a customer chased for money they do not owe. An account's owner is
        whoever it was assigned to here; where nobody has assigned one it falls
        back to the salesperson on their latest Zoho invoice, and Zoho's own
        value is never overwritten.
      </p>
    </Panel>
  );
}

export function PaymentsScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    "payments",
    () => papi.payments(session.token), [session.token]);

  // The projection is manager-and-above because half of it is what we owe
  // suppliers. Omitted rather than rendered and then 403'd — a panel that
  // always fails teaches people the product is broken.
  const ability = abilityFor(session);
  const mayReadCommitments = ability.can("read", "supply");
  // Owner only, mirroring `require_owner` on `/insight/self-funding`. Gated on
  // the manage-policy rule rather than a new subject: an owner is exactly the
  // person who confirms the retained figure this panel reads, and this
  // vocabulary is meant to stay small enough to hold in your head. Omitted
  // rather than rendered and 403'd, for the reason above.
  const mayReadEntityEconomics = ability.can("manage", "policy");

  return (
    <div className="screen-stack">
      {/* What is coming, then how they actually pay. The projection places
          money at its due date; the panel below is the measured evidence
          about whether that date is honoured, which is the right order to
          read them in. */}
      {mayReadCommitments && <CashProjection session={session} />}
      {/* Thirteen weeks of committed movement, then the year behind it. The
          projection says what the book does next; this says whether last
          year's growth was paid for out of what the book kept. */}
      {mayReadEntityEconomics && <SelfFunding session={session} />}
      <SettlementPanel data={data} loading={loading} error={error}
                       reload={reload} side={RECEIVABLE_SIDE}
                       onNavigate={onNavigate} />
      {/* Who is past their line, and whose account it is. Below the measured
          behaviour rather than above it, for the same reason the supplier terms
          sit below the payables panel: somebody arrives asking "how do they
          pay", and what to do about it is the answer to that, not the
          question. */}
      <CreditPanel session={session} />
    </div>
  );
}

// ── The term we actually agreed, beside the one the ERP could express ───────
//
// Zoho's payment terms are a fixed dropdown, so a real agreement of "net 37" or
// "45 days from month end" gets filed under the nearest entry — and every due
// date derived from it is wrong by days, in a direction nobody chose. This is
// where the agreement itself is recorded.
//
// Both numbers are always shown. A screen that displayed only our figure would
// be a schedule quietly disagreeing with the books it was drawn from, with no
// way to see where the disagreement came from.
//
// A grid rather than a list: the row count is the number of suppliers, which is
// set by the size of the business — `platform/DataGrid.tsx`, per the standing
// rule. Sorting by what is owed is the whole point, because a term worth
// recording is one with money behind it.

type TermRow = Sourced & {
  vendor_id: string; label: string;
  zoho_terms_days: number | null;
  agreed_days: number | null;
  basis: string;
  note: string | null;
  shift_days: number | null;
  shift_exact: boolean | null;
  open_bills: number;
  open_value: number;
};

function VendorTermsPanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "vendor-terms",
    () => papi.vendorTerms(session.token), [session.token]);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const bases = (data?.bases as Record<string, string>) ?? {};
  const all = useMemo(
    () => (data?.terms as TermRow[] | undefined) ?? [], [data]);
  const filter = useCompanyFilter(all);
  const terms = filter.filtered;

  async function save(row: TermRow, days: number | null, basis: string) {
    setBusy(row.vendor_id);
    setFailed(null);
    try {
      if (days === null) await papi.clearVendorTerm(session.token, row.vendor_id);
      else await papi.setVendorTerm(session.token, row.vendor_id, days, basis,
                                    row.note);
      // Refetched rather than patched in place: recording a term changes the
      // *shift* the schedule will apply, which is computed from this
      // supplier's open bills and is not derivable in the browser.
      reload();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : "Could not save that term");
    } finally {
      setBusy(null);
    }
  }

  const columns = useMemo<ColDef<TermRow>[]>(() => [
    {
      field: "label", headerName: "Supplier", flex: 1.4, minWidth: 190,
      cellRenderer: (p: { data?: TermRow }) => (p.data ? (
        <EntityName name={p.data.label} origin={p.data.origin}
                    show={filter.show} strong={false} />
      ) : null),
    },
    numeric<TermRow>("open_value", "Owed now", (v) => money(v), {
      width: 140, flex: 0,
      headerTooltip: "The balance on their open bills. Sorted on it, because a "
        + "term worth recording is one with money behind it.",
    }),
    {
      field: "zoho_terms_days", headerName: "Zoho says", width: 120, flex: 0,
      type: "numericColumn",
      valueFormatter: (p) => (p.value == null ? "—" : `${p.value}d`),
      headerTooltip: "What the ERP holds. Never overwritten — the gap between "
        + "this and the agreed term is the thing worth seeing.",
    },
    {
      headerName: "Agreed term", width: 230, flex: 0,
      valueGetter: (p) => p.data?.agreed_days ?? null,
      cellRenderer: (p: { data?: TermRow }) => {
        const row = p.data;
        if (!row) return null;
        return (
          <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", py: 0.25 }}>
            <TextField
              size="small" type="number"
              defaultValue={row.agreed_days ?? ""}
              placeholder={row.zoho_terms_days == null ? "—" : String(row.zoho_terms_days)}
              disabled={busy === row.vendor_id}
              aria-label={`Agreed payment term in days for ${row.label}`}
              // Commit on blur and on Enter. A term is a value being typed, not
              // an authority being granted, so an explicit save button per row
              // would be ceremony — but it commits only when the field is
              // finished with, never per keystroke.
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
              onBlur={(e) => {
                const raw = e.target.value.trim();
                const next = raw === "" ? null : Number(raw);
                if (next !== null && (!Number.isFinite(next) || next < 0)) return;
                if (next === row.agreed_days) return;
                void save(row, next, row.basis);
              }}
              sx={{ width: 84, "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
            />
            <TextField
              select size="small" value={row.basis}
              disabled={busy === row.vendor_id || row.agreed_days == null}
              aria-label={`What the days are counted from, for ${row.label}`}
              onChange={(e) => {
                if (row.agreed_days == null) return;
                void save(row, row.agreed_days, e.target.value);
              }}
              sx={{ width: 128, "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
            >
              {Object.keys(bases).map((key) => (
                <MenuItem key={key} value={key}>
                  {key === "NET" ? "from bill date" : "from month end"}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        );
      },
    },
    {
      headerName: "Effect on the schedule", flex: 1, minWidth: 210,
      valueGetter: (p) => p.data?.shift_days ?? null,
      cellRenderer: (p: { data?: TermRow }) => {
        const row = p.data;
        if (!row) return null;
        if (row.agreed_days == null) {
          return <Box component="span" className="viz-muted">on the ERP's dates</Box>;
        }
        if (row.shift_days == null) {
          // A term is exactly what an undated bill was missing, but there is
          // nothing to measure a displacement from.
          return (
            <StatusChip label="no dated bills" tone="neutral" dense
                        tip="None of this supplier's open bills carries a due date to move from, so their money stays where the schedule put it." />
          );
        }
        const d = row.shift_days;
        return (
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
            <Box component="span">
              {d === 0 ? "no change"
                : `${Math.abs(d)}d ${d > 0 ? "later" : "earlier"}`}
              <Box component="span" className="viz-muted">
                {" "}· {row.open_bills} bill{row.open_bills === 1 ? "" : "s"}
              </Box>
            </Box>
            {row.shift_exact === false && (
              <StatusChip label="summary" tone="warn" dense
                          tip="Zoho holds more than one term for this supplier, so a single shift cannot be right for every bill. The figure is a median, not an exact correction." />
            )}
          </Stack>
        );
      },
    },
    {
      headerName: "", width: 96, flex: 0, sortable: false, filter: false,
      cellRenderer: (p: { data?: TermRow }) => (
        p.data && p.data.agreed_days != null ? (
          <Button size="small" color="inherit"
                  disabled={busy === p.data.vendor_id}
                  onClick={() => void save(p.data!, null, p.data!.basis)}>
            Clear
          </Button>
        ) : null
      ),
    },
  ], [bases, busy, filter.show]);

  return (
    <Panel
      title="Payment terms on record"
      question="What did we actually agree to pay each supplier in"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <CompanyFilter options={filter.options} value={filter.company}
                       onChange={filter.setCompany} show={filter.show} />
      }
    >
      <p className="viz-headline viz-muted">
        Zoho's payment terms are a fixed list, so an agreement of net-37 gets
        filed under the nearest entry and every due date from it is wrong by
        days. Recording the real term re-dates that supplier's bills on the cash
        chart. What the ERP holds is never overwritten — both are shown, because
        a schedule that quietly disagreed with the books would be one nobody
        could reconcile.
      </p>
      {failed && <p className="viz-muted" role="alert">{failed}</p>}
      <DataGrid<TermRow>
        ariaLabel="Supplier payment terms"
        rows={terms}
        columns={columns}
        pageSize={20}
        rowHeight={54}
        getRowId={(r) => r.vendor_id}
      />
    </Panel>
  );
}

/** The same measurement, from the other end. Manager and above — see the
 *  endpoint's docstring for why this is scoped like Suppliers and not like
 *  Cash collection. */
export function PayablesScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    "payables",
    () => papi.payables(session.token), [session.token]);

  return (
    <div className="screen-stack">
      <SettlementPanel data={data} loading={loading} error={error}
                       reload={reload} side={PAYABLE_SIDE}
                       onNavigate={onNavigate} />
      {/* Measured behaviour first, then the terms it is judged against. A
          person arrives here asking "are we paying late", and the answer
          depends on which term is on record — so the evidence comes first and
          the lever second. */}
      <VendorTermsPanel session={session} />
    </div>
  );
}

// ── The shelf ───────────────────────────────────────────────────────────────
/** The quick filters, as the server defines them. Applied client-side because
 *  the grid already holds every row: a round trip to hide rows the browser has
 *  is a round trip somebody waits for. The *definitions* stay server-side so a
 *  label and its predicate cannot drift apart. */
type StockFilter = {
  key: string; label: string; field: string; op: string; value: unknown;
};

function passes(row: Row, f: StockFilter, decile: Record<string, number>): boolean {
  const v = row[f.field];
  switch (f.op) {
    case "eq": return v === f.value;
    case "is_true": return v === true;
    case "is_null": return v == null;
    case "gte_or_null": return v == null || Number(v) >= Number(f.value);
    // "High" is the top tenth of *this book*, not a fixed rupee amount: the
    // same cutoff cannot be right for a ₹19 crore book and a ₹37 lakh one.
    case "top_decile": return v != null && Number(v) >= (decile[f.field] ?? Infinity);
    default: return true;
  }
}

function decileOf(rowsIn: Row[], field: string): number {
  const values = rowsIn.map((r) => r[field]).filter((v) => v != null).map(Number)
    .sort((a, b) => a - b);
  if (values.length === 0) return Infinity;
  return values[Math.floor(values.length * 0.9)] ?? values[values.length - 1];
}

const HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "Moving", SLOW: "Slow", DEAD: "Quiet",
  // Never sold, and not on the books long enough for that to mean anything.
  // Its own word rather than a shade of "Quiet": these rows used to be counted
  // as dead stock, and on the live book they were the majority of it.
  UNKNOWN: "Too new to say",
};

export function StockScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "stock",
    () => papi.stock(session.token), [session.token]);
  const [active, setActive] = useState<string[]>([]);
  // `?item=` narrows the shelf to one product. The landscape sends items here
  // because there is no product screen and Stock is the closest thing to one —
  // arriving at a 3,000-row grid and being told to find it yourself is not a
  // drill-down. Read from the URL so the view is linkable and the back button
  // returns to the whole shelf.
  const [params, setParams] = useSearchParams();
  const focusItem = params.get("item");

  const items = rows(data?.items);
  const counts = (data?.counts as Record<string, number>) ?? {};
  const kpis = rows(data?.kpis);
  const filters = (rows(data?.filters) as unknown as StockFilter[]);
  // A manager or owner gets the value behind the drain; a salesperson does
  // not, and the presence of the field is how the screen knows which it is.
  const seesValue = items.some((r) => "inventory_value" in r);
  // Below two connected companies every badge says the same thing, and a
  // column of identical badges is width spent on decoration. The server
  // decides, because it is the only side that knows how many books there are.
  const sourcesDiffer = Boolean(data?.sources_differ);
  // An item master is per company, so "show me only the 4U shelf" is a real
  // question. Options come from every item, not from the health filters above,
  // so narrowing by band never hides a company from the dropdown.
  const company = useCompanyFilter(items as Sourced[]);

  const deciles = useMemo(() => ({
    monthly_holding_cost: decileOf(items, "monthly_holding_cost"),
    on_hand: decileOf(items, "on_hand"),
  }), [items]);

  // Filters combine, and they combine as AND: picking "Quiet" and "Costliest
  // to hold" means the rows that are both, which is the question somebody
  // actually has. Two chips that widened the list would be a search that gets
  // longer the more you ask of it.
  const banded = useMemo(() => {
    const chosen = filters.filter((f) => active.includes(f.key));
    return chosen.length === 0
      ? items
      : items.filter((r) => chosen.every((f) => passes(r, f, deciles)));
  }, [items, filters, active, deciles]);
  // Company last, so the band chips keep counting the whole shelf.
  const byCompany = company.apply(banded as Sourced[]) as Row[];
  // The `?item=` narrowing is applied after everything else and is *not* a
  // filter chip: it came from a link somebody followed, so it is announced and
  // dismissible rather than hidden among the controls. An unknown id shows
  // nothing and says so, instead of silently falling back to the whole shelf —
  // a stale link that quietly returns 3,000 rows reads as the link having
  // worked.
  const shown = focusItem
    ? byCompany.filter((r) => String(r.product_id) === focusItem)
    : byCompany;
  const focusLabel = focusItem
    ? String(items.find((r) => String(r.product_id) === focusItem)?.label ?? "")
    : "";

  const toggle = (key: string) =>
    setActive((a) => a.includes(key) ? a.filter((k) => k !== key) : [...a, key]);

  const worst = shown.reduce(
    (m, r) => Math.max(m, num(r.monthly_holding_cost)), 0);

  return (
    <Panel
      title="Stock"
      question="What is on the shelf, what it costs to keep, and what to do about it"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      {focusItem && (
        <p className="quad-focus">
          {shown.length > 0 ? (
            <>Showing one item — <strong>{focusLabel || focusItem}</strong>.</>
          ) : (
            <>No item on this shelf matches <strong>{focusItem}</strong>. It may
            have been removed from the books since that link was made.</>
          )}{" "}
          <InlineLink onClick={() => { params.delete("item"); setParams(params); }}>
            Show the whole shelf
          </InlineLink>
        </p>
      )}

      {/* The summary, each card a way into the rows behind it. A headline a
          person cannot drill into is one they have to take on trust. */}
      <ul className="kpi-row">
        {kpis.map((k, i) => {
          const filterKey = k.filter as string | null;
          const on = filterKey != null && active.includes(filterKey);
          return (
            <li key={i}>
              <button
                type="button"
                className={`kpi-card${on ? " on" : ""}${filterKey ? " clickable" : ""}`}
                disabled={!filterKey}
                aria-pressed={filterKey ? on : undefined}
                onClick={() => filterKey && toggle(filterKey)}
              >
                <span className="kpi-label">{String(k.label)}</span>
                <span className="kpi-value">
                  {k.unit === "money" ? money(num(k.value))
                    : k.unit === "ratio" ? pct(k.value as number, 0)
                      : num(k.value)}
                </span>
                <span className="viz-muted">{String(k.note)}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="stock-filters">
        <span className="viz-muted">Narrow to</span>
        {filters.map((f) => (
          <button
            key={f.key} type="button"
            className={`chip${active.includes(f.key) ? " on" : ""}`}
            aria-pressed={active.includes(f.key)}
            onClick={() => toggle(f.key)}
          >
            {f.label}
          </button>
        ))}
        {active.length > 0 && (
          <Button type="button" variant="text" size="small"
                  onClick={() => setActive([])}>
            Clear
          </Button>
        )}
        <CompanyFilter options={company.options} value={company.company}
                       onChange={company.setCompany} show={company.show} />
      </div>

      <p className="viz-muted">
        {shown.length === items.length
          ? `${items.length} lines on the shelf`
          : `${shown.length} of ${items.length} lines`}
        {" "}· ranked by what each costs to keep for a month
        {counts.value_unpriced_items ? (
          <> · {counts.value_unpriced_items} have no purchase cost recorded, so
            their drain is unknown rather than zero</>
        ) : null}
      </p>

      <DataGrid<Row>
        ariaLabel="Stock"
        twoLineRows
        pageSize={25}
        rows={shown}
        empty={
          <p className="tier3-none">
            Nothing matches those filters. That may be the good answer.
          </p>
        }
        columns={[
          {
            field: "label", headerName: "Item", flex: 1.5, minWidth: 220,
            filter: "agTextColumnFilter",
            // An item master is per connected company: the same part number is
            // a different row in each book, with its own stock and its own
            // purchase rate. A shelf pooled across three companies has to say
            // which shelf, or two rows with one name read as a duplicate.
            cellRenderer: (p: { data?: Row }) => (
              <span className="stock-item">
                <span className={`stock-dot ${String(p.data?.health ?? "")}`} />
                <EntityName
                  name={String(p.data?.label ?? "")}
                  origin={p.data?.origin as EntityOrigin | undefined}
                  show={sourcesDiffer}
                />
              </span>
            ),
          },
          numeric<Row>("on_hand", "On hand", (v) => num(v).toLocaleString("en-IN"),
                            { width: 120, flex: 0 }),
          {
            field: "idle_days", headerName: "Age", width: 120, flex: 0,
            type: "numericColumn", cellClass: "ag-num",
            filter: "agNumberColumnFilter",
            headerTooltip: "Days since this item last sold. Blank means it has "
              + "never sold at all, which sorts as the oldest.",
            valueFormatter: (p) =>
              p.value == null ? "never sold" : `${p.value} d`,
          },
          {
            // Quantity only, so it is on every role's grid — there is no cost
            // term in it to pair with the drain beside it. The server decides
            // that, as always; this renders what arrives.
            field: "days_of_cover", headerName: "Cover", width: 140, flex: 0,
            type: "numericColumn", cellClass: "ag-num",
            filter: "agNumberColumnFilter",
            // The wording is the whole point of the column. "At the rate it has
            // moved" is a measurement over the past; "will last" would be a
            // forecast, which this data cannot support and the response still
            // refuses by name.
            headerTooltip: "How much you hold, in days, at the rate this item "
              + "has actually moved since it first sold. A measurement, not a "
              + "forecast — it does not say how long the stock will last. Blank "
              + "means the item has never sold, so there is no rate to divide by.",
            valueFormatter: (p) =>
              p.value == null ? "never sold"
                : `${Number(p.value).toLocaleString("en-IN")} d`,
          },
          {
            field: "monthly_holding_cost",
            // Named for the reader. A manager is deciding where to put the
            // team; a salesperson is being told what their shelf costs them,
            // and "holding cost" is an accountant's word for it.
            headerName: seesValue ? "Costs per month" : "Monthly cash drain",
            width: 190, flex: 0, sort: "desc",
            type: "numericColumn", cellClass: "ag-num",
            filter: "agNumberColumnFilter",
            headerTooltip: "What keeping this line costs for a month at the "
              + "organization's carrying rate. The bar is relative to the "
              + "costliest line in view.",
            // A bar, not just a figure: the point of this column is that a
            // handful of lines carry most of the drain, and a column of
            // right-aligned numbers hides that until somebody adds them up.
            cellRenderer: (p: { data?: Row; value?: unknown }) => {
              if (p.value == null) return <span className="viz-muted">unknown</span>;
              const share = worst > 0 ? Number(p.value) / worst : 0;
              return (
                <span className="drain">
                  <span className="drain-bar" aria-hidden="true">
                    <span className={`drain-fill ${String(p.data?.health ?? "")}`}
                          style={{ width: `${Math.max(2, share * 100)}%` }} />
                  </span>
                  {money(Number(p.value))}
                </span>
              );
            },
          },
          {
            field: "health", headerName: "State", width: 120, flex: 0,
            filter: "agTextColumnFilter",
            valueFormatter: (p) => HEALTH_LABEL[String(p.value)] ?? String(p.value),
          },
          {
            field: "action_label", headerName: "What to do", flex: 1,
            minWidth: 190, filter: "agTextColumnFilter",
          },
          {
            headerName: "Who buys it", flex: 1.2, minWidth: 200,
            sortable: false, filter: false, autoHeight: true, wrapText: true,
            context: { minGridWidth: 1000 },
            headerTooltip: "Customers who have bought this item before, most "
              + "recent first. The answer to 'who do I call about this'.",
            valueGetter: (p) => ((p.data?.buyers as string[]) ?? []).join(", "),
            valueFormatter: (p) => p.value || "nobody yet",
          },
          // Cost-zone columns. Absent from a salesperson's payload entirely,
          // so these simply do not exist for them.
          ...(seesValue ? [
            numeric<Row>("inventory_value", "On the shelf", money,
                              { width: 155, flex: 0, context: { minGridWidth: 1180 } }),
            numeric<Row>("annual_holding_cost", "Per year", money,
                              { width: 145, flex: 0, context: { minGridWidth: 1320 } }),
          ] : []),
        ]}
      />

      {/* The three operational groups keep their place under the grid: they
          answer different questions from "what is this costing us", and
          folding them into filters would lose the sentence that says what each
          one means. */}
      {rows(data?.groups).map((g, i) => (
        <div className="tier3-list" key={i}>
          <h4>{String(g.label)} <span className="tier3-count">{String(g.count)}</span></h4>
          <p className="viz-muted">{String(g.meaning)}</p>
          {rows(g.items).length === 0 ? (
            <p className="tier3-none">Nothing here. That is the good answer.</p>
          ) : (
            <ol className="cadence-rows">
              {rows(g.items).slice(0, 8).map((item, j) => (
                <li key={j} className="cadence-row">
                  <span className="cadence-hit as-row">
                    <span className="cadence-name">{String(item.label)}</span>
                    <span className="cadence-figures">
                      <span>{num(item.on_hand)} on hand</span>
                      <span className="viz-muted">
                        {g.key === "OVERSOLD"
                          ? `${num(item.actual_available)} after commitments`
                          : g.key === "BELOW_REORDER"
                            ? `reorder at ${num(item.reorder_level)}`
                            : item.last_sold
                              ? `last sold ${String(item.last_sold)}`
                              : "never sold"}
                      </span>
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      ))}

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}

// ── Suppliers ───────────────────────────────────────────────────────────────
export function SupplyScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "supply",
    () => papi.supply(session.token), [session.token]);

  const suppliers = rows(data?.suppliers);
  const vendorSourcesDiffer = Boolean(data?.sources_differ);
  const vendorCompany = useCompanyFilter(suppliers as Sourced[]);
  // The share each bar draws is the server's, computed over the whole book —
  // narrowing the list does not renormalise it to 100%, because that would be
  // this screen inventing a concentration figure the server never computed.
  const shownSuppliers = vendorCompany.apply(suppliers as Sourced[]) as Row[];
  const open = rows(data?.open_orders);
  const counts = (data?.counts as Record<string, number>) ?? {};
  const topShare = data?.top_supplier_share as number | null | undefined;
  const staleAfter = num(data?.stale_after_days);

  return (
    <Panel
      title="Suppliers"
      question="Who does this book depend on, and what is still outstanding"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <p className="viz-headline">
        {counts.suppliers ?? 0} suppliers, {money(num(data?.total_spend))} ordered
        {topShare != null && (
          <> · the largest is <strong>{pct(topShare, 0)}</strong> of it</>
        )}
        {" "}· <strong>{counts.open_orders ?? 0}</strong> order{(counts.open_orders ?? 0) === 1 ? "" : "s"} still open
        {(counts.stale_open_orders ?? 0) > 0 && (
          <>, {counts.stale_open_orders} of them over {staleAfter} days old</>
        )}.
      </p>

      <div className="tier3-list">
        <h4>Where the spend goes</h4>
        {/* The tail is deliberately not folded — see supply.py. Every name on a
            supplier list is somebody with a phone number. */}
        <CompanyFilter options={vendorCompany.options} value={vendorCompany.company}
                       onChange={vendorCompany.setCompany} show={vendorCompany.show} />
        <ul className="dist">
          {shownSuppliers.map((s, i) => (
            <li className="dist-row" key={i}>
              {/* A supplier is per connected company too: the same vendor
                  invoicing two of the books is two rows, and a concentration
                  figure read across them without saying so looks like one
                  dependency where there are two relationships. */}
              <span className="dist-label">
                <EntityName
                  name={String(s.label)}
                  origin={s.origin as EntityOrigin | undefined}
                  show={vendorSourcesDiffer}
                  strong={false}
                />
              </span>
              <span className="dist-track">
                <span className="dist-fill"
                      style={{ width: `${num(s.share) * 100}%` }} />
              </span>
              <span className="dist-count">{pct(num(s.share), 0)}</span>
            </li>
          ))}
        </ul>
        <p className="viz-muted viz-footnote">
          Typical lead time is shown only where enough orders were actually
          marked received:{" "}
          {shownSuppliers
            .filter((s) => s.typical_lead_time_days != null)
            .map((s) => `${s.label} ${s.typical_lead_time_days}d`)
            .join(" · ") || "no supplier has enough logged receipts yet."}
        </p>
      </div>

      <div className="tier3-list">
        <h4>Open orders, oldest first <span className="tier3-count">{open.length}</span></h4>
        {open.length === 0 ? (
          <p className="tier3-none">Nothing outstanding.</p>
        ) : (
          <>
            {/* A grid rather than the hand-rolled `<ol>` this used to be. The
                list could not be sorted by value or age, filtered to one
                supplier, or copied out — and "which of these should I chase"
                is a question you answer by re-sorting. The standard says AG
                Grid for tabular data; this was tabular data wearing a list. */}
            <DataGrid<Row>
              ariaLabel="Open purchase orders"
              twoLineRows
              pageSize={25}
              rows={open}
              columns={[
                {
                  field: "number", headerName: "Order", flex: 1.4, minWidth: 240,
                  filter: "agTextColumnFilter",
                  cellRenderer: (p: { data?: Row }) => (
                    <EntityName
                      name={String(p.data?.number || "—")}
                      sub={String(p.data?.vendor_label ?? "")}
                      origin={p.data?.origin as EntityOrigin | undefined}
                      show={vendorSourcesDiffer}
                    />
                  ),
                },
                {
                  // "Placed", not "Ordered": the quantity column two along is
                  // also an "ordered", and two columns under one word is a
                  // table you have to decode rather than read.
                  field: "ordered_on", headerName: "Placed", width: 130, flex: 0,
                  filter: "agDateColumnFilter",
                  valueFormatter: (p) => (p.value ? formatDate(String(p.value)) : "—"),
                },
                numeric<Row>("age_days", "Age", (v) => `${v} d`, {
                  width: 110, flex: 0, sort: "desc",
                  headerTooltip: "Days since the order was placed. With no "
                    + "promised date on most of this book's orders, age is the "
                    + "only thing that ranks what to chase.",
                  // The ageing flag as a cell state rather than a separate
                  // column: it is derived from this number, so it belongs on it.
                  cellClass: (p) =>
                    `ag-num${num(p.value) >= staleAfter ? " po-ageing" : ""}`,
                }),
                // Ordered then outstanding, left to right, so the pair reads
                // as the order shrinking rather than as two unrelated counts.
                numeric<Row>("ordered_qty", "Ordered qty", (v) => v.toLocaleString("en-IN"),
                             { width: 140, flex: 0 }),
                numeric<Row>("pending_qty", "Still to come", (v) => v.toLocaleString("en-IN"),
                             { width: 150, flex: 0 }),
                // The whole order's value. Already on the payload and simply
                // never rendered. Safe here because `/supply` is
                // manager-or-owner at the door — purchase cost never reaches a
                // salesperson because they cannot reach this endpoint at all.
                numeric<Row>("total", "Order value", (v) => money(v), {
                  width: 160, flex: 0,
                  headerTooltip: "What the whole order is worth. The platform "
                    + "stores purchase orders at header grain, with no line "
                    + "rates, so the part already received and the part still "
                    + "to come cannot be valued separately — see the note "
                    + "below the table.",
                }),
              ]}
            />
            {/* Naming the gap rather than filling it, which is the rule this
                screen is built on. Splitting the value by quantity would need
                every line on the order to carry the same rate; on an order
                reading "460 of 500 to come" across a dozen different tools it
                would be a number nobody could reproduce from the book. */}
            <p className="viz-muted viz-footnote">
              {money(open.reduce((t, o) => t + num(o.total), 0))} of open orders
              on this page. The value dispatched and the value still to come are
              not shown because purchase orders are held at header grain — the
              line rates that would split the total are not ingested, and
              apportioning it by quantity would assume every line on an order
              costs the same.
            </p>
          </>
        )}
      </div>

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}
