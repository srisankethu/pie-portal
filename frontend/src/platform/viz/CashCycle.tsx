// How long a rupee is tied up, per legal entity, month by month.
//
// **The chart is a decomposition, not four lines.** DIO and DSO both tie money
// up and DPO releases it, so the cycle is drawn the way it is defined: the two
// that lengthen it stacked above the rule, the one that shortens it below, and
// the cycle itself as the line between them. Four separate trend lines would be
// the same numbers with the identity removed — the reader would have to do
// `DIO + DSO − DPO` in their head to check that the chart is telling the truth,
// which is exactly the arithmetic a chart exists to save.
//
// **A month the platform cannot state is a gap, and the line breaks across it.**
// Inventory history only exists from the day this platform started writing stock
// snapshots down, so early months genuinely have no cycle — and a polyline drawn
// straight through them would assert a value for every month it crossed. The
// line is therefore drawn as segments between consecutive stated months, the
// unstated ones carry a hatched slot rather than an empty one, and the reason
// the server gave is on the row in the table below.
//
// **One panel per entity, and no total.** Three books are three balance sheets;
// the server returns no pooled series and this screen builds none. The entities
// sit one under another with a shared axis convention so they can be read
// against each other, which is the comparison somebody actually wants — "why is
// 4U's cycle twenty days longer than SLS's" is answered by the decomposition,
// not by a fourth line on one chart.

import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import { scaleBand, scaleLinear } from "d3-scale";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { ChartTip, MetricCard, StatusChip, VarianceIndicator } from "../kit";
import { DataGrid, numeric } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import type { PlatformSession } from "../types";
import { extentOf, statedRuns } from "./cycle-layout";
import { Figure, Panel, ValueAxis, stateOf } from "./Panel";
import { useInsight } from "./useInsight";
import { useMeasure } from "./useMeasure";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);
/** A day count the server may legitimately have withheld. `Number(null)` is 0,
 *  which is a cycle of zero days — a real and very different claim. */
const days = (v: unknown): number | null =>
  v === null || v === undefined ? null : Number(v);

const HORIZONS: [string, string][] = [
  ["12", "12 months"], ["18", "18 months"], ["24", "24 months"],
];

/** The three legs, in the order the identity reads, with which way each pushes.
 *
 *  `sign` is what the chart draws and what the legend explains, so a reader
 *  never has to remember that a longer DPO is the good one. */
const LEGS: { key: string; label: string; sign: 1 | -1; meaning: string }[] = [
  { key: "dio", label: "Stock days", sign: 1,
    meaning: "How long stock sits before it is sold. Ties money up." },
  { key: "dso", label: "Collection days", sign: 1,
    meaning: "How long a customer takes to pay after being invoiced. Ties money up." },
  { key: "dpo", label: "Supplier days", sign: -1,
    meaning: "How long we take to pay a supplier. Releases money — the one leg where longer is better for cash." },
];

const d1 = (v: number | null): string => (v == null ? "—" : `${v.toFixed(1)}d`);

export function CashCycleScreen({ session }: { session: PlatformSession }) {
  const [months, setMonths] = useState("12");
  const { data, loading, error, reload } = useInsight(
    "cash-cycle",
    () => papi.cashCycle(session.token, Number(months)), [session.token, months]);

  const entities = rows(data?.entities);
  const definition = (data?.definition ?? {}) as Record<string, string>;
  const basis = (data?.basis ?? {}) as Record<string, string>;
  const unattributed = (data?.unattributed ?? {}) as Row;
  const state = stateOf(loading, error, data?.empty_reason as string);

  return (
    <div className="screen-stack">
      <Panel
        title="Cash conversion cycle"
        question="How long is a rupee tied up between paying for stock and being paid for it"
        state={state}
        error={error} emptyReason={data?.empty_reason as string} onRetry={reload}
        wide
        actions={
          <TextField
            select size="small" value={months}
            aria-label="How far back the trend runs"
            onChange={(e) => setMonths(e.target.value)}
            sx={{ minWidth: 150,
                  "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
          >
            {HORIZONS.map(([value, label]) => (
              <MenuItem key={value} value={value}>{label}</MenuItem>
            ))}
          </TextField>
        }
      >
        <p className="viz-headline">
          {definition.ccc}{" "}
          <span className="viz-muted">
            {definition.window} Each entity is its own balance sheet, so there is
            no combined figure — the same customer or supplier in two books is
            two relationships, and one cycle drawn across them would belong to no
            company that files anything.
          </span>
        </p>

        <Stack spacing={4}>
          {entities.map((entity) => (
            <EntityCycle key={String(entity.connection_id)} entity={entity} />
          ))}
        </Stack>

        <div className="tier3-list">
          <h4>What this measure is standing on</h4>
          <ul className="cash-aside">
            {Object.entries(basis).map(([key, why]) => (
              <li key={key}>
                <span className="cash-aside-head">
                  <strong>
                    {key === "tax" ? "Gross balances, net cost of sales"
                      : "Stock valued at last purchase rate"}
                  </strong>
                </span>
                <span className="viz-muted">{why}</span>
              </li>
            ))}
          </ul>
        </div>

        {(num(unattributed.invoices) > 0 || num(unattributed.bills) > 0) && (
          <p className="viz-muted viz-footnote">
            {num(unattributed.invoices)} invoice(s) and {num(unattributed.bills)}{" "}
            bill(s) name a customer or supplier with no connected company on
            record. They are counted out of every series above rather than filed
            under whichever book came first — a cycle that moved because a
            master record failed to resolve would be unreadable.
          </p>
        )}

        <Unavailable items={rows(data?.unavailable)} />
      </Panel>
    </div>
  );
}

/** One book: the headline, the decomposition, and the months behind it. */
function EntityCycle({ entity }: { entity: Row }) {
  const [ref, room] = useMeasure<HTMLDivElement>();
  const all = rows(entity.months);
  const latest = (entity.latest ?? null) as Row | null;
  const change = days(entity.change_days);
  const stated = num(entity.months_stated);
  const counts = (entity.counts ?? {}) as Row;
  const label = String(entity.label);

  const columns: ColDef<Row>[] = [
    { field: "month", headerName: "Month", width: 120, flex: 0 },
    numeric<Row>("ccc", "Cycle", (v) => `${v.toFixed(1)}d`, {
      width: 110, flex: 0,
      headerTooltip: "Stock days plus collection days less supplier days. "
        + "Blank where any one of the three could not be stated — never a sum "
        + "of the legs that survived.",
    }),
    ...LEGS.map((leg) => numeric<Row>(
      leg.key, leg.label, (v) => `${v.toFixed(1)}d`,
      { width: 130, flex: 0, headerTooltip: leg.meaning })),
    numeric<Row>("receivables", "Owed to us", (v) => money(v),
                 { width: 140, flex: 0 }),
    numeric<Row>("payables", "Owed by us", (v) => money(v),
                 { width: 140, flex: 0 }),
    numeric<Row>("inventory", "On the shelf", (v) => money(v), {
      width: 140, flex: 0,
      headerTooltip: "Valued at the item master's last purchase rate, on the "
        + "last day stock was observed inside that month.",
    }),
    {
      // The refusal, on the row that carries the blank it explains. A month
      // whose cycle is missing and whose reason lives in a footnote is a month
      // a reader files under "the chart is broken".
      headerName: "Why a leg is blank", flex: 1, minWidth: 260, sortable: false,
      valueGetter: (p) => {
        const missing = (p.data?.unknown as string[] | undefined) ?? [];
        const why = (p.data?.why ?? {}) as Record<string, string>;
        return missing.map((leg) => why[leg]).filter(Boolean).join(" ");
      },
      cellRenderer: (p: { value?: string }) => (
        p.value ? <Box component="span" className="viz-muted">{p.value}</Box> : null
      ),
    },
  ];

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 0.5 }}>{label}</Typography>

      <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap", mb: 1.5 }}>
        <MetricCard
          label="Cycle, latest stated month"
          value={latest ? d1(days(latest.ccc)) : "—"}
          variance={
            change == null ? undefined : (
              // Down is the good direction for a cycle: fewer days is less
              // money tied up. The arrow and the word both follow `invert`.
              <VarianceIndicator
                value={change} format="number" digits={1} invert
                // Named rather than called "last month": the comparison is
                // against the previous month the platform could *state*, which
                // with a gap in the stock history is not the month before.
                label={`against ${String(entity.previous_stated_month ?? "the previous stated month")}`} />
            )
          }
          sub={latest ? `Month ending ${formatDate(String(latest.ends_on))}`
            : "No month in this range has all three legs"}
          tip="Days between paying for stock and being paid for it. Negative means suppliers are funding the working capital."
        />
        {LEGS.map((leg) => (
          <MetricCard key={leg.key} label={leg.label}
                      value={latest ? d1(days(latest[leg.key])) : "—"}
                      tip={leg.meaning}
                      sub={leg.sign > 0 ? "adds to the cycle"
                        : "subtracts from the cycle"} />
        ))}
      </Stack>

      <div ref={ref}>
        <Figure
          caption={`Bars above the rule are the days money is tied up — stock, then collection. The bar below is the days a supplier funds instead. The line is the cycle itself, and it breaks across any month one of the three could not be stated rather than being drawn straight through it.`}
          summary={all.map((m) => {
            const ccc = days(m.ccc);
            return `${String(m.month)}: ${ccc == null
              ? "cycle not stated"
              : `cycle ${ccc.toFixed(1)} days, stock ${d1(days(m.dio))}, collection ${d1(days(m.dso))}, supplier ${d1(days(m.dpo))}`}`;
          }).join("; ")}
          table={
            <DataGrid<Row>
              ariaLabel={`Cash conversion cycle by month for ${label}`}
              rows={all}
              columns={columns}
              pageSize={24}
              filters={false}
              rowHeight={48}
            />
          }
        >
          {room.width > 0 && all.length > 0 && (
            <CycleChart months={all} width={room.width} />
          )}
        </Figure>
      </div>

      <p className="viz-muted viz-footnote">
        {stated} of {all.length} months in this range have all three legs.{" "}
        {Boolean(entity.stock_observed_from) ? (
          <>Stock was first observed on{" "}
            {formatDate(String(entity.stock_observed_from))}, across{" "}
            {num(counts.stock_observation_days)} observation day
            {num(counts.stock_observation_days) === 1 ? "" : "s"} in this range —
            months without one carry no stock leg and therefore no cycle.</>
        ) : (
          <>No stock has been observed in this range at all, so no month has a
            stock leg. Zoho holds no stock history; it accumulates from the day
            the platform starts looking.</>
        )}{" "}
        {Boolean(entity.receivables_reliable_from) && (
          <>Receivables can be reconstructed from{" "}
            {formatDate(String(entity.receivables_reliable_from))}
            {num(counts.receivable_applications_unmatched) > 0 && (
              <> — {num(counts.receivable_applications_unmatched)} receipt or
                credit note settles an invoice this platform never read, which is
                proof of a balance the replay cannot see</>
            )}.{" "}</>
        )}
        {Boolean(entity.payables_reliable_from) && (
          <>Payables from {formatDate(String(entity.payables_reliable_from))}.</>
        )}
      </p>
    </Box>
  );
}

/** The decomposition: stacked days above and below a zero rule, cycle over it.
 *
 *  Two stacked bars up and one down rather than one net bar, for the reason the
 *  cash chart gives about inflow and outflow: a book with 60 days of stock and
 *  60 days of supplier credit is not the same book as one with neither, and a
 *  net bar draws them identically. */
// The vertical axis is days, so — unlike every other chart in this package —
// it needs no currency: `money()` inside the tooltip already reads the session's.
function CycleChart({ months, width }: { months: Row[]; width: number }) {
  const H = 300;
  const PAD = { top: 16, right: 10, bottom: 48, left: 58 };

  const stated = months.map((m) => days(m.ccc));
  const up = months.map((m) => (days(m.dio) ?? 0) + (days(m.dso) ?? 0));
  const down = months.map((m) => days(m.dpo) ?? 0);
  const y = scaleLinear()
    .domain(extentOf(up, down, stated))
    .range([H - PAD.bottom, PAD.top])
    .nice();
  const band = scaleBand<number>()
    .domain(months.map((_, i) => i))
    .range([PAD.left, Math.max(PAD.left + 1, width - PAD.right)])
    .paddingInner(0.34);
  const barW = Math.max(2, band.bandwidth());
  const zero = y(0);
  const mid = (i: number) => (band(i) ?? PAD.left) + band.bandwidth() / 2;

  // Segments between consecutive *stated* months. A single polyline would
  // interpolate through every month the server refused to state, drawing a
  // value for it — the one thing this screen must not do. `cycle-layout.ts`
  // owns the run-splitting because a fixture can show a line spanning a gap and
  // an eye cannot.
  const segments = statedRuns(stated).filter((run) => run.length > 1);

  return (
    <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`}
         className="viz-svg" role="presentation">
      <defs>
        {/* The hatch marks an *absence*, so it is deliberately not a colour
            from the series palette: an unstated month must not read as a
            fourth category with a small value. */}
        <pattern id="cycle-gap" width="6" height="6" patternUnits="userSpaceOnUse"
                 patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="var(--viz-rule)"
                strokeWidth="2" />
        </pattern>
      </defs>

      <ValueAxis scale={y} x0={PAD.left} x1={width - PAD.right}
                 format={(v) => `${Math.round(v)}d`} />

      {months.map((m, i) => {
        const left = band(i) ?? PAD.left;
        const dio = days(m.dio);
        const dso = days(m.dso);
        const dpo = days(m.dpo);
        const ccc = days(m.ccc);
        const missing = (m.unknown as string[] | undefined) ?? [];
        const why = (m.why ?? {}) as Record<string, string>;
        const tip = (
          <>
            <strong>{String(m.month)}</strong>
            <br />
            {ccc == null ? "Cycle not stated" : `Cycle ${ccc.toFixed(1)} days`}
            <br />
            Stock {d1(dio)} · Collection {d1(dso)} · Supplier {d1(dpo)}
            <br />
            <span style={{ opacity: 0.85 }}>
              Owed to us {money(num(m.receivables))} · owed by us{" "}
              {money(num(m.payables))}
              {m.inventory != null && <> · on the shelf {money(num(m.inventory))}</>}
            </span>
            {missing.length > 0 && (
              <>
                <br />
                <span style={{ opacity: 0.9 }}>
                  {missing.map((leg) => why[leg]).filter(Boolean).join(" ")}
                </span>
              </>
            )}
          </>
        );

        // Stock sits on the rule, collection stacks on top of it: the order is
        // the order money moves through the business, so the stack reads as a
        // journey rather than as two arbitrary bands.
        const stockTop = dio == null ? zero : y(dio);
        const collectTop = dso == null ? stockTop
          : y((dio ?? 0) + dso);
        return (
          <g key={i}>
            {ccc == null && (
              <rect x={left} y={PAD.top} width={barW}
                    height={Math.max(1, H - PAD.bottom - PAD.top)}
                    fill="url(#cycle-gap)" opacity="0.5" />
            )}
            {dio != null && dio > 0 && (
              <rect x={left} y={stockTop} width={barW}
                    height={Math.max(1, zero - stockTop)}
                    className="cycle-bar cycle-bar-stock" rx="1.5" />
            )}
            {dso != null && dso > 0 && (
              <rect x={left} y={collectTop} width={barW}
                    height={Math.max(1, stockTop - collectTop)}
                    className="cycle-bar cycle-bar-collect" rx="1.5" />
            )}
            {dpo != null && dpo > 0 && (
              <rect x={left} y={zero} width={barW}
                    height={Math.max(1, y(-dpo) - zero)}
                    className="cycle-bar cycle-bar-supplier" rx="1.5" />
            )}
            <ChartTip title={tip}>
              <rect x={left} y={PAD.top} width={band.bandwidth()}
                    height={Math.max(1, H - 28 - PAD.top)} className="cash-hit" />
            </ChartTip>
            {/* Every third month carries a label; twenty-four of them at this
                width overlap into a smear, and a label nobody can read still
                costs the space. */}
            {i % 3 === 0 && (
              <text x={mid(i)} y={H - 28} textAnchor="middle" className="viz-axis">
                {String(m.month)}
              </text>
            )}
          </g>
        );
      })}

      <line x1={PAD.left} x2={width - PAD.right} y1={zero} y2={zero}
            stroke="var(--viz-rule)" strokeWidth="1" />
      {segments.map((run, i) => (
        <polyline key={i} className="cycle-line"
                  points={run.map((j) => `${mid(j)},${y(stated[j] ?? 0)}`)
                    .join(" ")} />
      ))}
      {/* A stated month standing alone between two gaps has no segment to sit
          on, and a lone point is a real reading rather than a rendering
          accident — so it is drawn. */}
      {stated.map((value, i) => (value == null ? null : (
        <circle key={i} cx={mid(i)} cy={y(value)} r="2.5" className="cycle-dot" />
      )))}
      <text x={PAD.left} y={H - 8} className="viz-axis-note">
        above the rule: days money is tied up · below: days a supplier funds it ·
        line: the cycle · hatched: a month one leg could not be stated for
      </text>
    </svg>
  );
}

/** What the server says it cannot show, and why. Never omitted.
 *
 *  Deliberately the same shape as `TheBook`'s, because it is the same list from
 *  the same builder — but it is not imported across, because that file is a
 *  lazy chunk of its own and pulling a helper out of it would drag the stock,
 *  supplier and payments screens into this route's bundle. */
function Unavailable({ items }: { items: Row[] }) {
  if (!items.length) return null;
  return (
    <ul className="tl-unavailable">
      {items.map((u, i) => (
        <li key={i}>
          <strong>{String(u.series).replace(/_/g, " ")}</strong> — not shown.{" "}
          <StatusChip label={String(u.kind).toLowerCase()} tone="neutral" dense />{" "}
          <span className="viz-muted">{String(u.reason)}</span>
        </li>
      ))}
    </ul>
  );
}
