// What this book leans on, at both ends, facing each other.
//
// A distributor is exposed in two directions and they are almost never looked
// at together. Lose a principal and the customers buying their product are
// exposed with you; lose the customer who takes most of a principal's line and
// the target goes too. One screen, one encoding, mirrored — so the two halves
// are comparable at a glance rather than being two screens somebody remembers
// to open in sequence.
//
// **A principal's weight is the revenue riding on them, not the spend.** Both
// bars are drawn, and the gap between them is the finding: "12% of what we buy,
// 34% of what we sell" is the sentence somebody leaves the screen with. Bars
// rather than a pie for the usual reason — a pie asks a reader to compare
// angles, and the whole question here is "how much bigger is this one".
//
// **Targets sit on the vendor row, with pace beside achievement.** 60% of a
// number with 80% of the quarter gone is behind, and an achievement figure on
// its own hides that until the last week. Both are drawn on one track.
//
// The screen never says a customer depends on us — see the server's own
// `unavailable`, rendered at the bottom. We see what they buy here and nothing
// of what they buy elsewhere.

import Button from "@mui/material/Button";
import { useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { InlineLink, StatusChip } from "../kit";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { TargetEditor } from "./Targets";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

export function DependencyScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const { data, loading, error, reload } = useInsight(
    "dependency", () => papi.dependency(session.token), [session.token]);

  const customers = (data?.customers as Row | undefined) ?? {};
  const vendors = (data?.vendors as Row | null | undefined) ?? null;
  const supplierSide = Boolean(data?.supplier_side_visible) && vendors !== null;
  const attribution = (data?.attribution as Row | undefined) ?? {};
  const sourcesDiffer = Boolean(data?.sources_differ);

  const [editing, setEditing] = useState(false);

  const customerRows = rows(customers.rows);
  const vendorRows = rows(vendors?.rows);
  const company = useCompanyFilter([...customerRows, ...vendorRows] as Sourced[]);

  const attributed = attribution.share as number | null | undefined;

  return (
    <Panel
      title="Dependency"
      question="What this book leans on — at both ends"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={supplierSide ? (
        <Button type="button" size="small" variant="outlined"
                onClick={() => setEditing(true)}>
          Targets
        </Button>
      ) : undefined}
    >
      <CompanyFilter options={company.options} value={company.company}
                     onChange={company.setCompany} show={company.show} />

      {/* How much of revenue could be traced to a principal at all. A share of
          a fraction of the book, shown without saying so, is the single most
          misleading thing this screen could do. */}
      {supplierSide && attributed != null && attributed < 0.99 && (
        <p className="bond-unscored">
          <strong>{pct(attributed, 0)}</strong> of revenue could be traced to a
          principal ({money(num(attribution.revenue_total) - num(attribution.revenue_attributed))}{" "}
          could not). A sale is attributed through the bills that bought the
          item, so this fills in after a full sync — until then the supplier
          shares below are computed over the traced part only.
        </p>
      )}

      <div className="dep-halves">
        {supplierSide && (
          <Side
            title="Suppliers we lean on"
            question="Spend, and the revenue riding on their product"
            rows={company.apply(vendorRows as Sourced[]) as Row[]}
            concentration={(vendors?.concentration as Row) ?? {}}
            sourcesDiffer={sourcesDiffer}
            side="vendor"
            onOpen={() => undefined}
          />
        )}
        <Side
          title="Customers we lean on"
          question="Revenue, and how many principals their spend runs through"
          rows={company.apply(customerRows as Sourced[]) as Row[]}
          concentration={(customers.concentration as Row) ?? {}}
          sourcesDiffer={sourcesDiffer}
          side="customer"
          onOpen={(id) => onNavigate(`customer/${id}`)}
        />
      </div>

      <Unavailable items={rows(data?.unavailable)} />

      {editing && (
        <TargetEditor session={session} onClose={() => setEditing(false)}
                      onSaved={reload} />
      )}
    </Panel>
  );
}

function Side({
  title, question, rows: list, concentration, sourcesDiffer, side, onOpen,
}: {
  title: string;
  question: string;
  rows: Row[];
  concentration: Row;
  sourcesDiffer: boolean;
  side: "vendor" | "customer";
  onOpen: (id: string) => void;
}) {
  const isVendor = side === "vendor";
  // Bars are drawn against the largest row *in view*, which is a comparison
  // between the rows on screen and not a claim about the book. The share
  // figures beside them are the server's, computed over the whole total — the
  // rule `CompanyFilter` states: narrowing a list hides rows, it does not
  // restate a total.
  const widest = Math.max(
    ...list.map((r) => (isVendor ? num(r.downstream_revenue) : num(r.money))), 1);

  return (
    <section className="dep-side">
      <h4>{title}</h4>
      <p className="viz-question">{question}</p>

      {concentration.top_share != null && (
        <p className="viz-headline">
          <strong>{String(concentration.top_label)}</strong> is{" "}
          {pct(num(concentration.top_share), 0)}
          {" "}· the largest five are {pct(num(concentration.top_n_share), 0)} of{" "}
          {num(concentration.count)}.
        </p>
      )}

      <ul className="dep-rows">
        {list.slice(0, 12).map((r) => {
          const money_ = isVendor ? num(r.downstream_revenue) : num(r.money);
          const target = r.target as Row | null | undefined;
          return (
            <li key={String(r.entity_id)} className="dep-row">
              <span className="dep-name">
                <EntityName name={String(r.label)}
                            origin={r.origin as EntityOrigin | undefined}
                            show={sourcesDiffer} strong={false} />
                {num(r.sole_source_items) > 0 && (
                  <StatusChip label={`only source for ${num(r.sole_source_items)}`}
                              tone="warn" dense
                              tip="No other supplier has ever sold us these items. That is not the same as no other supplier existing." />
                )}
              </span>

              <span className="dep-track">
                <span className="dep-fill"
                      style={{ width: `${(money_ / widest) * 100}%` }} />
                {/* The second bar, vendor side only: what they cost against
                    what rides on them. Two bars on one track rather than two
                    columns of numbers, because the gap is the point. */}
                {isVendor && (
                  <span className="dep-fill dep-spend"
                        style={{ width: `${(num(r.money) / widest) * 100}%` }} />
                )}
              </span>

              <span className="dep-figures">
                <strong>{money(money_)}</strong>
                <span className="viz-muted">
                  {isVendor
                    ? `${pct(r.downstream_share as number, 0)} of revenue · ${pct(r.share as number, 0)} of spend`
                    : `${pct(r.share as number, 0)} of revenue · ${num(r.counterparties)} principal${num(r.counterparties) === 1 ? "" : "s"}`}
                </span>
              </span>

              {target && <TargetTrack target={target} />}

              {!isVendor && (
                <InlineLink onClick={() => onOpen(String(r.entity_id))}>
                  Open
                </InlineLink>
              )}
            </li>
          );
        })}
      </ul>

      {list.length > 12 && (
        <div className="tier3-list">
          <DataGrid<Row>
            ariaLabel={title}
            rows={list}
            pageSize={15}
            twoLineRows
            getRowId={(r) => String(r.entity_id)}
            onRowClick={isVendor ? undefined
                                 : (r) => onOpen(String(r.entity_id))}
            columns={[
              {
                field: "label", headerName: isVendor ? "Supplier" : "Customer",
                flex: 1, minWidth: 200, filter: "agTextColumnFilter",
                cellRenderer: (p: { data: Row }) => (
                  <EntityName name={String(p.data.label)}
                              origin={p.data.origin as EntityOrigin | undefined}
                              show={sourcesDiffer} strong={false} />
                ),
              },
              ...(isVendor
                ? [numeric<Row>("downstream_revenue", "Revenue on them",
                                (v) => money(v), { width: 170, flex: 0 }),
                   numeric<Row>("money", "Spend", (v) => money(v),
                                { width: 140, flex: 0 })]
                : [numeric<Row>("money", "Revenue", (v) => money(v),
                                { width: 150, flex: 0 })]),
              numeric<Row>("counterparties",
                           isVendor ? "Customers" : "Principals",
                           (v) => String(v), { width: 130, flex: 0 }),
            ]}
            empty={<p className="viz-muted">Nothing here yet.</p>}
          />
        </div>
      )}
    </section>
  );
}

/** Achievement and elapsed period on one track.
 *
 *  Two marks, not one: an achievement bar alone says 60% and sounds fine, and
 *  the thing that decides whether it is fine is how much of the quarter is
 *  gone. The pace marker is where the bar would have to reach to be on time. */
function TargetTrack({ target }: { target: Row }) {
  const achieved = num(target.achieved);
  const elapsed = num(target.period_elapsed);
  const onPace = target.on_pace === true;
  return (
    <span className="dep-target">
      <span className="dep-target-track">
        <span className={`dep-target-fill${onPace ? " ok" : " behind"}`}
              style={{ width: `${Math.min(100, achieved * 100)}%` }} />
        <span className="dep-target-pace"
              style={{ left: `${Math.min(100, elapsed * 100)}%` }}
              title={`${pct(elapsed, 0)} of the period gone`} />
      </span>
      <span className="viz-muted">
        {pct(achieved, 0)} of {money(num(target.amount))}{" "}
        {String(target.basis_label)} · {pct(elapsed, 0)} of the period gone
        {num(target.days_left) > 0 && target.required_run_rate != null && (
          <> · {money(num(target.required_run_rate))}/day to close it</>
        )}
        {" ("}
        {formatDate(String(target.period_start))}–{formatDate(String(target.period_end))}
        {")"}
      </span>
    </span>
  );
}

function Unavailable({ items }: { items: Row[] }) {
  if (!items.length) return null;
  return (
    <ul className="tl-unavailable said-plain">
      {items.map((u, i) => (
        <li key={i}>
          <strong>{String(u.what)}</strong> — not claimed.{" "}
          <span className="viz-muted">{String(u.why)}</span>
        </li>
      ))}
    </ul>
  );
}
