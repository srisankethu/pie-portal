import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import { useCallback, useEffect, useState } from "react";
import { DataGrid, numeric, text } from "./DataGrid";
import { formatDate } from "../when";
import { papi } from "./api";
import { LoadingState } from "./kit";
import type {
  CustomerItemDetail,
  CustomerItemRow,
  CustomerItemTxn,
  CustomerPortfolio,
  PeerRow,
  PlatformSession } from "./types";
import { Bp, Labelled } from "./ui";
import { money, count } from "../money";

/**
 * Customer × Item commercial intelligence.
 *
 * Two screens: the customer's items ranked by what the gap is worth, and the
 * drill-down that explains one relationship and shows the transactions behind
 * every claim on it.
 *
 * Presentation only — every number here was computed and persisted by the
 * backend. Nothing on this page calculates a margin, and nothing on it is
 * AI-generated: the diagnosis prose arrives as sentences already rendered from
 * the same values shown in the tables.
 */

// ── formatting ──────────────────────────────────────────────────────────────

/** A ratio (0.261) rendered as a percentage. */
function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** A percentage-POINT movement, signed. Never a percent change of a percent. */
function pp(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)} pp`;
}

function signedPct(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}%`;
}


function num(v: number | null | undefined): string {
  return count(v);
}

const when = formatDate;

const SIGNAL_LABEL: Record<string, string> = {
  CI_MARGIN_EROSION: "Margin eroding",
  CI_COST_NOT_PASSED: "Cost not passed on",
  CI_LOW_PEER_PRICING: "Below peers",
  CI_MARGIN_DECLINE_NO_VOLUME: "No volume gained",
  CI_MARGIN_DECLINE_WITH_VOLUME: "Volume traded for margin",
  CI_MATERIAL_MARGIN_GAP: "Material gap" };

const EROSION_LABEL: Record<string, string> = {
  COST_DRIVEN: "Cost rose, price didn't follow",
  PRICE_DRIVEN: "Price fell",
  MIXED: "Cost rose and price fell",
  NONE: "—" };

/** Data sufficiency, stated plainly. A conclusion drawn from thin data has to
 *  look different from one drawn from years of trading. */
function Sufficiency({ level, reasons }: { level: string; reasons?: string[] }) {
  if (level === "SUFFICIENT") return null;
  const label = level === "INSUFFICIENT" ? "Not enough data" : "Limited data";
  // The reasons used to live in a `title` attribute, which is invisible on a
  // touch device and to a keyboard — and "Not enough data" without the reason
  // is unactionable, since too few transactions and no purchase cost need
  // completely different fixes.
  return (
    <span className="ci-suff">
      <Labelled
        tip={
          <>
            {level === "INSUFFICIENT"
              ? "Too thin to draw a conclusion from, so no signal is raised — weak data must not produce confident output."
              : "Enough to show, not enough to be sure of."}
            {(reasons || []).length > 0 && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 16 }}>
                {(reasons || []).map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
          </>
        }
      >
        {label}
      </Labelled>
    </span>
  );
}

// ── customer portfolio ──────────────────────────────────────────────────────
type SortKey = "impact" | "deterioration" | "peer_gap" | "revenue" | "volume";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "impact", label: "Margin gap" },
  { key: "deterioration", label: "Margin deterioration" },
  { key: "peer_gap", label: "Peer benchmark gap" },
  { key: "revenue", label: "Revenue" },
  { key: "volume", label: "Volume change" },
];

function sortRows(rows: CustomerItemRow[], key: SortKey): CustomerItemRow[] {
  const v = (r: CustomerItemRow) => {
    switch (key) {
      case "deterioration": return -(r.margin_change_pp ?? 0);
      case "peer_gap": return r.peer_margin_gap ?? 0;
      case "revenue": return r.revenue_12m ?? 0;
      case "volume": return -(r.volume_change_pct ?? 0);
      default: return r.historical_margin_gap ?? 0;
    }
  };
  return [...rows].sort((a, b) => v(b) - v(a));
}

export function CustomerCommercial({
  session,
  customerId,
  onOpenItem }: {
  session: PlatformSession;
  customerId: string;
  onOpenItem: (productId: string) => void;
}) {
  const [data, setData] = useState<CustomerPortfolio | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("impact");
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await papi.customerPortfolio(session.token, customerId));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token, customerId]);

  useEffect(() => { load(); }, [load]);

  if (error) {
    return (
      <div className="state-panel">
        <div className="state-mark">Commercial analysis could not be loaded</div>
        <p style={{ margin: 0, fontSize: 13.5 }}>{error}</p>
      </div>
    );
  }
  if (!data) return <LoadingState rows={1} height={120} />;

  const s = data.summary;
  if (s.active_items === 0) {
    return (
      <div className="dp-empty">
        No item-level history for this account yet. Once invoices are synced, this
        is where the items driving its margin appear.
      </div>
    );
  }

  const rows = showAll ? data.all_items : data.items_requiring_attention;
  // The backend already ranks by materiality; re-sorting is an explicit user act.
  const shown = sort === "impact" && !showAll ? rows : sortRows(rows, sort);

  return (
    <div>
      <div className="section-h">Commercial summary · last 12 months</div>
      <div className="ci-kpis">
        <Kpi label="Revenue" value={money(s.revenue_12m)} />
        <Kpi label="Gross profit" value={money(s.gross_profit_12m)} />
        <Kpi label="Gross margin" value={pct(s.gross_margin_12m)}
             tip="Total gross profit ÷ total revenue across the account — not the average of the per-item margins, which would let a trivial line count as much as one a thousand times its size." />
        <Kpi label="Active items" value={num(s.active_items)} />
        <Kpi label="Items eroding" value={num(s.items_with_margin_erosion)}
             tip="Items whose margin has fallen by more than the erosion threshold between the recent window and the one before it, and where there is enough evidence to say so. Set the threshold in Settings → Margin policy."
             tone={s.items_with_margin_erosion ? "warn" : undefined} />
        <Kpi label="Material gaps" value={num(s.material_gap_items)}
             tip="Items where the gap is worth more in rupees than the material-gap floor. Ranking by percentage instead is how a team ends up working trivial accounts first."
             tone={s.material_gap_items ? "warn" : undefined} />
        <Kpi label="Historical margin gap" value={money(s.historical_margin_gap)}
             sub="estimate, not recoverable profit"
             tip="What recent volume would have earned at the historical margin, minus what it actually earned. An arithmetic gap, not money anyone can go and collect — costs may have risen for reasons no price can undo."
             tone={s.historical_margin_gap ? "warn" : undefined} />
      </div>

      {/* When *every* item is uncostable the whole screen is revenue-only, and
          the flagged table below will be empty. Said plainly here, because
          "nothing is flagged" would otherwise read as "all is well" when the
          truth is "we cannot tell". */}
      {s.items_without_cost > 0 && s.items_without_cost === s.active_items ? (
        <p className="ci-note warn">
          No purchase cost is recorded for any item on this account, so margin
          cannot be computed and nothing can be flagged. This is almost always
          because bills have not been synced yet — invoices alone say what was
          sold, never what it cost. Sync bills from Data &amp; connection, then
          recompute.
        </p>
      ) : s.items_without_cost > 0 ? (
        <p className="ci-note">
          {s.items_without_cost} of {s.active_items} items have no reliable purchase
          cost recorded, so no margin is shown for them. That is missing data, not a
          zero margin.
        </p>
      ) : null}

      <div className="section-h" style={{ marginTop: 18 }}>
        {showAll ? "All items" : "Items requiring attention"}
      </div>
      <div className="ci-controls">
        <TextField
          select
          size="small"
          label="Sort by"
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          sx={{ minWidth: 210 }}
        >
          {SORTS.map((o) => <MenuItem key={o.key} value={o.key}>{o.label}</MenuItem>)}
        </TextField>
        <Button size="small" onClick={() => setShowAll(!showAll)}>
          {showAll ? "Only items needing attention" : `Show all ${s.active_items} items`}
        </Button>
      </div>

      {shown.length === 0 ? (
        <div className="dp-empty">
          {s.items_without_cost === s.active_items && s.active_items > 0
            ? "Nothing can be flagged without a purchase cost to compare against — see above."
            : "Nothing on this account is flagged. That is a fact about the data, not a judgement about the relationship."}
        </div>
      ) : (
        <DataGrid<CustomerItemRow>
          ariaLabel="Items on this account"
          pageSize={25}
          rows={shown}
          onRowClick={(r) => onOpenItem(r.product_id)}
          columns={[
            {
              field: "item_name", headerName: "Item", flex: 1.4, minWidth: 220,
              filter: "agTextColumnFilter",
              cellRenderer: (p: { data?: CustomerItemRow }) => (
                <div>
                  <div style={{ fontWeight: 600 }}>{p.data?.item_name}</div>
                  {p.data?.item_code && <div className="fsrc">{p.data.item_code}</div>}
                </div>
              ),
            },
            numeric<CustomerItemRow>("revenue_12m", "Revenue 12M", money,
                                     { width: 140, flex: 0 }),
            numeric<CustomerItemRow>("current_margin", "Current", (v) => pct(v), {
              width: 115, flex: 0,
              headerTooltip: "Margin over the recent window — the period treated "
                + "as 'now'. Its length is fixed in configuration.",
            }),
            numeric<CustomerItemRow>("historical_margin", "Historical", (v) => pct(v), {
              width: 125, flex: 0,
              headerTooltip: "The longer lookback 'current' is compared against. "
                + "Too little history shows nothing rather than a number built "
                + "from two invoices.",
            }),
            {
              field: "peer_median_margin", headerName: "Peers", width: 120, flex: 0,
              type: "numericColumn", cellClass: "ag-num",
              filter: "agNumberColumnFilter",
              headerTooltip: "The median margin other customers got on this item "
                + "recently. Withheld below the minimum peer count — fewer than "
                + "that is one customer's price wearing the word 'median'.",
              cellRenderer: (p: { data?: CustomerItemRow }) =>
                (p.data?.peer_count ?? 0) > 0 ? (
                  <div>
                    {pct(p.data?.peer_median_margin)}
                    <div className="fsrc">{p.data?.peer_count} peers</div>
                  </div>
                ) : "—",
            },
            numeric<CustomerItemRow>("margin_change_pp", "Change", (v) => pp(v), {
              width: 115, flex: 0,
              headerTooltip: "Current minus historical, in percentage POINTS. "
                + "24% to 20% is −4 pp, not −17%.",
              cellClassRules: { "ci-bad": (p) => Number(p.value ?? 0) < 0 },
            }),
            numeric<CustomerItemRow>("volume_change_pct", "Volume",
                                     (v) => signedPct(v), { width: 115, flex: 0 }),
            numeric<CustomerItemRow>("historical_margin_gap", "Margin gap", money, {
              width: 145, flex: 0,
              headerTooltip: "The margin gap in money, not points — a 9-point "
                + "slide on a small item matters less than 2 points on a large one.",
              cellStyle: { fontWeight: 600 },
            }),
            {
              headerName: "Reason", flex: 1.2, minWidth: 200, sortable: false,
              filter: false, autoHeight: true, wrapText: true,
              cellRenderer: (p: { data?: CustomerItemRow }) => p.data ? (
                <div style={{ padding: "4px 0" }}>
                  <div className="ci-tags">
                    {p.data.signals.map((sig) => (
                      <span key={sig} className="ci-tag">{SIGNAL_LABEL[sig] || sig}</span>
                    ))}
                  </div>
                  {p.data.erosion_kind && p.data.erosion_kind !== "NONE" && (
                    <div className="fsrc">{EROSION_LABEL[p.data.erosion_kind]}</div>
                  )}
                  <Sufficiency level={p.data.data_sufficiency}
                               reasons={p.data.sufficiency_reasons} />
                </div>
              ) : null,
            },
          ]}
        />
      )}
    </div>
  );
}

function Kpi({ label, value, sub, tone, tip }: {
  label: string; value: string; sub?: string; tone?: "warn"; tip?: React.ReactNode;
}) {
  return (
    <div className={`ci-kpi${tone ? ` ci-kpi-${tone}` : ""}`}>
      <div className="ci-kpi-label">
        {tip ? <Labelled tip={tip}>{label}</Labelled> : label}
      </div>
      <div className="ci-kpi-value">{value}</div>
      {sub && <div className="fsrc">{sub}</div>}
    </div>
  );
}

// ── Customer × Item drill-down ──────────────────────────────────────────────
export function CustomerItemScreen({
  session,
  customerId,
  productId,
  onBack,
  onOpenCustomer }: {
  session: PlatformSession;
  customerId: string;
  productId: string;
  onBack: () => void;
  /** Open another account from the peer comparison. Optional: a caller that
   *  cannot navigate gets a non-clickable grid rather than a dead row. */
  onOpenCustomer?: (customerId: string) => void;
}) {
  const [data, setData] = useState<CustomerItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    papi
      .customerItemDetail(session.token, customerId, productId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => { cancelled = true; };
  }, [session.token, customerId, productId]);

  if (error) {
    return (
      <div>
        <button className="btn btn-ghost btn-sm" onClick={onBack}>← Back</button>
        <div className="state-panel" style={{ marginTop: 10 }}>
          <div className="state-mark">This item view could not be loaded</div>
          <p style={{ margin: 0, fontSize: 13.5 }}>{error}</p>
        </div>
      </div>
    );
  }
  if (!data) return <LoadingState rows={1} height={200} />;

  const h = data.headline;
  const q = data.data_quality;

  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ marginBottom: 10 }}>
        ← {data.customer.name}
      </button>
      <div className="dp-head">
        <h1>{data.item.name}</h1>
        <p>
          {data.customer.name}
          {data.item.code && <> · <span className="mono">{data.item.code}</span></>}
          {" "}· as at {when(data.as_of)}
        </p>
      </div>

      <div className="ci-kpis">
        <Kpi label="Revenue (recent)" value={money(h.revenue_recent)} />
        <Kpi label="Gross profit" value={money(h.gross_profit_recent)} />
        <Kpi label="Current margin" value={pct(h.current_margin)} />
        <Kpi label="Historical margin" value={pct(h.historical_margin)} />
        <Kpi label="Change" value={pp(h.margin_change_pp)}
             tip="Percentage points, not percent. A move from 24% to 20% is −4 pp."
             tone={(h.margin_change_pp ?? 0) < 0 ? "warn" : undefined} />
        <Kpi label="Net selling price" value={money(h.current_sell_price)} sub="per unit"
             tip="What the customer actually paid per unit — the invoice rate after line discounts, not the list price." />
        <Kpi label="Effective cost" value={money(h.current_effective_cost)} sub="per unit"
             tip="Purchase cost per unit from the bills, after landed costs and supplier discounts. Where no bill covers a sale, the margin is absent rather than assumed." />
        <Kpi label="Historical margin gap" value={money(h.historical_margin_gap)}
             sub={h.annualized_historical_margin_gap
               ? `${money(h.annualized_historical_margin_gap)} annualized`
               : "not enough history to annualize"}
             tone={h.historical_margin_gap ? "warn" : undefined} />
      </div>

      {/* A. deterministic diagnosis — every number computed, none AI-generated */}
      <div className="section-h">What the data says</div>
      <Bp style={{ padding: 16 }}>
        {data.diagnosis.map((line, i) => (
          <p key={i} className="ci-diagnosis">{line}</p>
        ))}
        {q.data_sufficiency !== "SUFFICIENT" && (
          <p className="ci-note" style={{ marginTop: 10 }}>
            Based on {q.transaction_count} transaction{q.transaction_count === 1 ? "" : "s"}
            {" "}over {q.history_months} months
            {q.cost_missing_txns > 0 &&
              `, ${q.cost_missing_txns} of them without a reliable cost`}.
          </p>
        )}
      </Bp>

      {/* B + C. unit economics and margin through time */}
      <div className="section-h">Net selling price vs effective cost</div>
      <Bp style={{ padding: 16 }}>
        {data.series.length < 2 ? (
          <p className="ci-note">
            Only {data.series.length} transaction{data.series.length === 1 ? "" : "s"} —
            too few to draw a trend from.
          </p>
        ) : (
          <PriceCostChart series={data.series} />
        )}
        <div className="ci-periods">
          {([["Current", data.margin_periods.current], ["3M", data.margin_periods.m3],
             ["6M", data.margin_periods.m6], ["12M", data.margin_periods.m12],
             ["Historical", data.margin_periods.historical]] as const).map(([label, v]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{pct(v)}</dd>
            </div>
          ))}
        </div>
      </Bp>

      {/* D. same item across other customers — a benchmark, not a mandate */}
      <div className="section-h">Same item, other customers</div>
      <Bp style={{ padding: 2 }}>
        {!data.peers.is_reliable ? (
          <p className="ci-note" style={{ padding: 14 }}>
            {data.peers.peer_count === 0 ? (
              <>
                No other customer bought this item in the last{" "}
                {Math.round(data.peers.window_days / 30)} months, so there is nothing to
                compare this price against.
              </>
            ) : (
              <>
                Only {data.peers.peer_count} other customer
                {data.peers.peer_count === 1 ? "" : "s"} bought this item in the last{" "}
                {Math.round(data.peers.window_days / 30)} months — too few for a
                meaningful price comparison.
              </>
            )}
          </p>
        ) : (
          <>
            <div className="ci-benchmark">
              <div>
                <dt>
                  <Labelled tip="The median, not the mean — one customer who bought at a strange price cannot drag the benchmark on its own.">
                    Median selling price
                  </Labelled>
                </dt>
                <dd>{money(data.peers.median_price)}</dd>
              </div>
              <div><dt>Median margin</dt><dd>{pct(data.peers.median_margin)}</dd></div>
              <div>
                <dt>
                  <Labelled tip="How far this customer's price sits from the peer median, as a percentage of it. Negative means they pay less than the others.">
                    This customer's price
                  </Labelled>
                </dt>
                <dd>{signedPct(data.peers.price_deviation_pct)}</dd>
              </div>
              <div>
                <dt>
                  <Labelled tip="Distance from the peer median margin in percentage points — a difference between two percentages, not a percentage change.">
                    This customer's margin
                  </Labelled>
                </dt>
                <dd>{pp(data.peers.margin_deviation_pp)}</dd>
              </div>
              <div><dt>Peer customers</dt><dd>{data.peers.peer_count}</dd></div>
            </div>
            <p className="ci-note" style={{ padding: "0 14px 12px" }}>
              A benchmark, not a target — volume, freight and payment terms differ
              between accounts, and none of that is in this data.
            </p>
          </>
        )}
        {/* A grid rather than markup: the row count here is "how many other
            accounts buy this item", which is driven by the size of the book.
            Sorting it by margin is the question the panel exists to answer, and
            a plain table cannot be asked. Rows open the account. */}
        <DataGrid<PeerRow>
          ariaLabel="This item's price across accounts"
          rows={[data.peers.subject, ...data.peers.rows]
            .filter((p): p is PeerRow => p != null)}
          onRowClick={(p) => onOpenCustomer?.(p.customer_id)}
          pageSize={10}
          filters={false}
          columns={[
            {
              ...text<PeerRow>("name", "Customer"),
              // The account being viewed is in this list on purpose — the
              // comparison is meaningless without it — so it is marked rather
              // than left for the reader to find by name.
              cellRenderer: (p: { data?: PeerRow }) =>
                p.data ? (
                  <span style={{ fontWeight: p.data.is_subject ? 700 : 400 }}>
                    {p.data.name}
                    {p.data.is_subject && <span className="ci-you"> this customer</span>}
                  </span>
                ) : null,
            },
            numeric<PeerRow>("net_sell_price", "Selling price", money),
            numeric<PeerRow>("margin", "Margin", (v) => pct(v)),
            numeric<PeerRow>("qty", "Volume", num),
            numeric<PeerRow>("txn_count", "Orders", (v) => String(v)),
            {
              ...text<PeerRow>("last_transaction_date", "Last bought"),
              minWidth: 130,
              valueFormatter: (p) => when(p.value),
            },
          ]}
        />
      </Bp>

      {/* E. did the lower margin buy anything */}
      <div className="section-h">Volume against margin</div>
      <Bp style={{ padding: 2 }}>
        <table className="dp-table ci-table">
          <thead>
            <tr>
              <th>Period</th><th className="num">Quantity</th>
              <th className="num">Revenue</th><th className="num">Margin</th>
            </tr>
          </thead>
          <tbody>
            {data.volume_vs_margin.map((p) => (
              <tr key={p.period_start}>
                <td>{when(p.period_start)} – {when(p.period_end)}</td>
                <td className="num">{num(p.qty)}</td>
                <td className="num">{money(p.revenue)}</td>
                <td className="num">{pct(p.margin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Bp>

      {/* F. the evidence every conclusion above rests on */}
      <div className="section-h">Transactions</div>
      <p className="ci-note">
        Every figure above is an aggregate of exactly these lines.
      </p>
      <Bp style={{ padding: 2 }}>
        {/* Every line this account ever bought of this item, so the row count
            is the length of the relationship. Sorting by margin or by date is
            how somebody finds the line that started an erosion, and a plain
            table could only be read top to bottom.

            No row click: a line's destination would be the invoice, and this
            platform does not have an invoice screen. A cursor that promised one
            would be worse than none. */}
        <DataGrid<CustomerItemTxn>
          ariaLabel="Every line of this relationship"
          rows={data.transactions}
          pageSize={25}
          columns={[
            { ...text<CustomerItemTxn>("date", "Date"), minWidth: 120,
              sort: "desc", valueFormatter: (p) => when(p.value) },
            { ...text<CustomerItemTxn>("invoice_id", "Invoice"),
              cellClass: "mono", minWidth: 130,
              valueFormatter: (p) => p.value || "—" },
            numeric<CustomerItemTxn>("qty", "Qty", num),
            numeric<CustomerItemTxn>("rate", "Rate", money),
            numeric<CustomerItemTxn>("discount_percent", "Disc",
                                     (v) => pct(v / 100)),
            numeric<CustomerItemTxn>("net_sell_price", "Net price", money),
            {
              ...numeric<CustomerItemTxn>("effective_cost", "Eff. cost", money),
              // "no cost" rather than an em dash: an uncosted line is a gap in
              // the bill history, not a missing value, and the two are acted on
              // differently.
              valueFormatter: (p) => (p.value == null ? "no cost" : money(Number(p.value))),
            },
            numeric<CustomerItemTxn>("gross_profit", "GP", money),
            numeric<CustomerItemTxn>("margin", "Margin", (v) => pct(v)),
          ]}
        />
      </Bp>
    </div>
  );
}

/**
 * Net selling price against effective cost, over time.
 *
 * Inline SVG rather than a charting dependency — two series and a shared scale
 * is not worth 40 kB. The point of the picture is one thing: whether the cost
 * line climbs while the price line stays flat.
 */
function PriceCostChart({ series }: { series: CustomerItemDetail["series"] }) {
  const W = 720, H = 200, PAD = 34;
  const points = series.filter((p) => p.net_sell_price != null);
  if (points.length < 2) return null;

  const values = points.flatMap((p) =>
    [p.net_sell_price, p.effective_cost].filter((v): v is number => v != null));
  const max = Math.max(...values) * 1.1;
  const min = Math.min(...values) * 0.9;
  const span = max - min || 1;

  const x = (i: number) => PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - PAD * 2);

  const path = (pick: (p: typeof points[number]) => number | null) =>
    points
      .map((p, i) => ({ v: pick(p), i }))
      .filter((d): d is { v: number; i: number } => d.v != null)
      .map((d, n) => `${n === 0 ? "M" : "L"}${x(d.i).toFixed(1)},${y(d.v).toFixed(1)}`)
      .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ci-chart" role="img"
         aria-label="Net selling price and effective cost per unit over time">
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="ci-axis" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} className="ci-axis" />
      <text x={PAD - 6} y={PAD + 4} className="ci-axis-label" textAnchor="end">
        {money(max)}
      </text>
      <text x={PAD - 6} y={H - PAD} className="ci-axis-label" textAnchor="end">
        {money(min)}
      </text>
      <path d={path((p) => p.net_sell_price)} className="ci-line ci-line-price" />
      <path d={path((p) => p.effective_cost)} className="ci-line ci-line-cost" />
      {points.map((p, i) =>
        p.net_sell_price == null ? null : (
          <circle key={`p${i}`} cx={x(i)} cy={y(p.net_sell_price)} r="2.5"
                  className="ci-dot ci-dot-price">
            <title>{`${when(p.date)} · price ${money(p.net_sell_price)}`}</title>
          </circle>
        ))}
      {points.map((p, i) =>
        p.effective_cost == null ? null : (
          <circle key={`c${i}`} cx={x(i)} cy={y(p.effective_cost)} r="2.5"
                  className="ci-dot ci-dot-cost">
            <title>{`${when(p.date)} · cost ${money(p.effective_cost)}`}</title>
          </circle>
        ))}
      <g className="ci-legend">
        <rect x={W - 168} y={8} width="10" height="3" className="ci-line-price" />
        <text x={W - 152} y={13}>Net selling price</text>
        <rect x={W - 168} y={24} width="10" height="3" className="ci-line-cost" />
        <text x={W - 152} y={29}>Effective cost</text>
      </g>
    </svg>
  );
}
