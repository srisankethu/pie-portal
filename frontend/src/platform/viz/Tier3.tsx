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

import { useMemo, useState } from "react";
import { money } from "../../money";
import { papi } from "../api";
import { DataGrid, numeric } from "../DataGrid";
import type { PlatformSession } from "../types";
import { Figure, Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";

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

// ── Cash: how long customers take to pay ────────────────────────────────────
export function PaymentsScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    () => papi.payments(session.token), [session.token]);

  const customers = rows(data?.customers);
  const distribution = rows(data?.distribution);
  const peak = Math.max(...distribution.map((d) => num(d.count)), 1);
  const median = data?.median_days_to_pay as number | null | undefined;
  const lateShare = data?.late_share as number | null | undefined;
  const minSettlements = num(data?.min_settlements);
  const datable = num(data?.settlements) - num(data?.undatable_count);
  const patterns = (data?.patterns as Record<string, Record<string, string>>) ?? {};
  const trends = (data?.trends as Record<string, string>) ?? {};
  const patternCounts = (data?.pattern_counts as Record<string, number>) ?? {};

  return (
    <Panel
      title="Cash collection"
      question="How long does the money take to arrive, and from whom"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <p className="viz-headline">
        Half of all invoices are settled within{" "}
        <strong>{median == null ? "—" : `${median} days`}</strong>
        {lateShare != null && (
          // The denominator travels with the share. "100% were late" over one
          // invoice is true and useless; "1 of 1" is true and self-limiting.
          <> · <strong>{Math.round(lateShare * datable)} of {datable}</strong>{" "}
          invoices with terms on record were paid late</>
        )}
        {num(data?.advances) > 0 && (
          <> · {String(data?.advances)} advance
          {num(data?.advances) === 1 ? "" : "s"} held
          {num(data?.unapplied_total) > 0 &&
            ` (${money(num(data?.unapplied_total))} unapplied)`}</>
        )}.
      </p>

      {/* A distribution really is better as a shape: the question "are we
          being paid to terms" is answered by where the mass sits, not by any
          one bucket's count. */}
      <Figure
        caption="Each bar is one invoice settled, bucketed by how many days it took. Buckets are fixed rather than quantile — terms are absolute, and a moving band would stop 30 days meaning 30 days."
        summary={distribution.map((d) => `${d.label}: ${d.count}`).join(", ")}
        table={
          <table className="viz-table">
            <thead><tr><th scope="col">Days to pay</th><th scope="col">Invoices</th></tr></thead>
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
          rows. A customer who always takes 45 days can be planned around; one
          who takes 5 or 95 cannot, whatever their average says. */}
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
        <h4>Slowest payers first</h4>
        <ol className="cadence-rows">
          {customers.slice(0, 15).map((c, i) => (
            <li key={i} className="cadence-row">
              <button type="button" className="cadence-hit"
                      onClick={() => onNavigate(`customer/${String(c.customer_id)}`)}>
                <span className="cadence-name">{String(c.label)}</span>
                <span className="cadence-figures">
                  <span>
                    {/* Below the floor there is no rhythm to report, and the
                        row says so instead of printing a median of two. */}
                    {c.estimable
                      ? `${c.median_days_to_pay} days typical`
                      : `only ${c.settlements} settled — no typical yet`}
                  </span>
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
              </button>
            </li>
          ))}
        </ol>
        <p className="viz-muted viz-footnote">
          Measured per invoice settled, not per payment — one transfer clearing
          ten invoices is ten observations. Fewer than {minSettlements} settled
          invoices and no pattern is asserted. The spread is a median absolute
          deviation, so one invoice paid nine months late stays a story about
          that invoice rather than redefining the customer.
        </p>
      </div>

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
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
};

export function StockScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    () => papi.stock(session.token), [session.token]);
  const [active, setActive] = useState<string[]>([]);

  const items = rows(data?.items);
  const counts = (data?.counts as Record<string, number>) ?? {};
  const kpis = rows(data?.kpis);
  const filters = (rows(data?.filters) as unknown as StockFilter[]);
  // A manager or owner gets the value behind the drain; a salesperson does
  // not, and the presence of the field is how the screen knows which it is.
  const seesValue = items.some((r) => "inventory_value" in r);

  const deciles = useMemo(() => ({
    monthly_holding_cost: decileOf(items, "monthly_holding_cost"),
    on_hand: decileOf(items, "on_hand"),
  }), [items]);

  // Filters combine, and they combine as AND: picking "Quiet" and "Costliest
  // to hold" means the rows that are both, which is the question somebody
  // actually has. Two chips that widened the list would be a search that gets
  // longer the more you ask of it.
  const shown = useMemo(() => {
    const chosen = filters.filter((f) => active.includes(f.key));
    return chosen.length === 0
      ? items
      : items.filter((r) => chosen.every((f) => passes(r, f, deciles)));
  }, [items, filters, active, deciles]);

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
          <button type="button" className="btn btn-ghost btn-sm"
                  onClick={() => setActive([])}>
            Clear
          </button>
        )}
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
            cellRenderer: (p: { data?: Row }) => (
              <span>
                <span className={`stock-dot ${String(p.data?.health ?? "")}`} />
                {String(p.data?.label ?? "")}
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
    () => papi.supply(session.token), [session.token]);

  const suppliers = rows(data?.suppliers);
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
        <ul className="dist">
          {suppliers.map((s, i) => (
            <li className="dist-row" key={i}>
              <span className="dist-label">{String(s.label)}</span>
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
          {suppliers
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
          <ol className="cadence-rows">
            {open.map((o, i) => (
              <li key={i}
                  className={num(o.age_days) >= staleAfter ? "cadence-row late" : "cadence-row"}>
                <span className="cadence-hit as-row">
                  <span className="cadence-name">
                    {String(o.number || "—")}
                    <span className="viz-muted"> · {String(o.vendor_label)}</span>
                  </span>
                  <span className="cadence-figures">
                    <span>{num(o.pending_qty)} of {num(o.ordered_qty)} to come</span>
                    <span className="viz-muted">
                      ordered {String(o.ordered_on)} · {String(o.age_days)} days ago
                    </span>
                  </span>
                  {num(o.age_days) >= staleAfter ? (
                    <span className="cadence-flag">ageing</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}
