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

import { money } from "../../money";
import { papi } from "../api";
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
                    {money(num(c.total_settled))} settled
                    {c.late_share != null && Number(c.late_share) > 0 &&
                      ` · ${pct(Number(c.late_share), 0)} late`}
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
          invoices and no typical is reported.
        </p>
      </div>

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}

// ── The shelf ───────────────────────────────────────────────────────────────
export function StockScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    () => papi.stock(session.token), [session.token]);

  const groups = rows(data?.groups);
  const counts = (data?.counts as Record<string, number>) ?? {};
  const hasValue = counts.stock_value !== undefined;

  return (
    <Panel
      title="Stock"
      question="What is on the shelf, and which of it is a problem"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
    >
      <ul className="quad-legend">
        <li className="quad quad-bad">
          <span className="quad-count">{counts.oversold ?? 0}</span>
          <span className="quad-body"><strong>Committed beyond stock</strong>
            <span className="viz-muted">Promised more than is on the shelf.</span></span>
        </li>
        <li className="quad quad-warn">
          <span className="quad-count">{counts.below_reorder ?? 0}</span>
          <span className="quad-body"><strong>At the reorder point</strong>
            <span className="viz-muted">Of the items that have one set.</span></span>
        </li>
        <li className="quad quad-flat">
          <span className="quad-count">{counts.idle ?? 0}</span>
          <span className="quad-body"><strong>Sitting still</strong>
            <span className="viz-muted">
              On hand, nothing sold in {String(data?.idle_after_days)} days.
            </span></span>
        </li>
        <li className="quad quad-good">
          <span className="quad-count">{counts.tracked_items ?? 0}</span>
          <span className="quad-body"><strong>Stock-tracked items</strong>
            <span className="viz-muted">
              {hasValue
                ? `${money(counts.stock_value)} at last purchase price.`
                : "Services and non-stock lines are excluded."}
            </span></span>
        </li>
      </ul>

      {groups.map((g, i) => (
        <div className="tier3-list" key={i}>
          <h4>{String(g.label)} <span className="tier3-count">{String(g.count)}</span></h4>
          <p className="viz-muted">{String(g.meaning)}</p>
          {rows(g.items).length === 0 ? (
            // An empty group is a good outcome and reads as one. Hiding it
            // would leave a reader unsure whether it was checked.
            <p className="tier3-none">Nothing here. That is the good answer.</p>
          ) : (
            <ol className="cadence-rows">
              {rows(g.items).map((item, j) => (
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
                        {/* Nothing on the shelf has no value worth printing —
                            "₹0" beside "0 on hand" is noise restating the
                            column to its left. */}
                        {item.stock_value != null && num(item.stock_value) > 0 &&
                          ` · ${money(num(item.stock_value))}`}
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
