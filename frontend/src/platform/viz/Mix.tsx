// The mix grid: which lines each customer takes, and which they do not.
//
// The bond strip answers "who is close to this book". This answers the other
// half of the growth question, and it is a different shape on purpose — a
// distribution shows you where the mass is, and a grid with a name in every row
// is what somebody works down on a Monday.
//
// **Three cell states, and the third is the reason this is not a tick-box
// grid.** A line they used to buy and stopped is the strongest cell here: the
// product was approved, somebody was ordering it, and it stopped. That is a
// shorter conversation than a line they have never taken, and folding the two
// into one "no" throws the difference away.
//
// **The grid will not tell you a gap is money.** An empty cell means they do
// not buy that line *from us* — it cannot mean they do not buy it, or that they
// need it, and a shop with no measuring room has no metrology gap. So a gap
// carries the *affinity* beside it: how many comparable customers do take that
// line. The reader makes the call. Colouring blanks as opportunity would send
// people on wasted drives and the grid would not be trusted twice.
//
// **This is AG Grid, not a hand-written table.** The row count is the number of
// customers in the business, which is exactly the line CLAUDE.md draws — the
// only `<table>` on this screen is the accessible twin of the affinity figure.

import { useMemo, useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Panel, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

/** What each cell state looks like. Never colour alone: every state also
 *  carries a word, because a grid read in greyscale or by somebody with a
 *  colour-vision deficiency must say the same thing. */
const CELL: Record<string, { label: string; cls: string }> = {
  BUYS: { label: "buys", cls: "mix-buys" },
  LAPSED: { label: "lapsed", cls: "mix-lapsed" },
  NEVER: { label: "—", cls: "mix-never" },
};

export function MixScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (r: string) => void }) {
  const [months, setMonths] = useState("12");
  const { data, loading, error, reload } = useInsight(
    "mix", () => papi.mix(session.token, Number(months)),
    [session.token, months]);

  const columns = rows(data?.categories);
  const customers = rows(data?.customers);
  const affinity = rows(data?.affinity);
  const catalogue = (data?.catalogue as Row | undefined) ?? {};
  const counts = (data?.counts as Record<string, number>) ?? {};
  const sourcesDiffer = Boolean(data?.sources_differ);

  const company = useCompanyFilter(customers as Sourced[]);
  const shown = company.apply(customers as Sourced[]) as Row[];

  // Which line the reader is hunting whitespace in. Narrowing to one column
  // turns the grid from "everything about everyone" into a call list, which is
  // the thing somebody actually leaves the screen holding.
  const [gap, setGap] = useState<string>("");
  const worklist = useMemo(() => {
    if (!gap) return shown;
    return shown.filter((c) =>
      rows(c.cells).some((cell) => cell.category === gap
                                   && cell.state !== "BUYS"));
  }, [shown, gap]);

  const resolved = catalogue.resolved_share as number | null | undefined;

  return (
    <Panel
      title="Product mix"
      question="Who takes which lines of the business — and who takes only one"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="Window" value={months} onChange={setMonths}
               options={[["12", "1y"], ["24", "2y"], ["36", "3y"]]} />
        </div>
      }
    >
      <p className="viz-headline">
        <strong>{counts.single_line ?? 0}</strong> of {shown.length} customers
        buy only one line
        {(counts.full_coverage ?? 0) > 0 && (
          <> · <strong>{counts.full_coverage}</strong> take everything</>
        )}
        {(counts.lapsed_cells ?? 0) > 0 && (
          <> · <strong>{counts.lapsed_cells}</strong> line
            {counts.lapsed_cells === 1 ? " has" : "s have"} lapsed</>
        )}.
      </p>

      {/* How much of the catalogue could actually be placed. A grid built on a
          half-categorised catalogue has half-phantom whitespace, and that has
          to be visible before anybody acts on a blank cell. */}
      {resolved != null && resolved < 1 && (
        <p className="bond-unscored">
          <strong>{pct(resolved, 0)}</strong> of the catalogue is placed in a
          line ({num(catalogue.uncategorised)} item
          {num(catalogue.uncategorised) === 1 ? "" : "s"} unplaced). Trade in
          unplaced items is left out of this grid entirely rather than counted
          against anybody — so a gap here may be a gap in the catalogue.
        </p>
      )}

      <CompanyFilter options={company.options} value={company.company}
                     onChange={company.setCompany} show={company.show} />

      {/* The whitespace picker. Each button is a line and a count of who is
          missing it — the count is the point, because "43 customers do not buy
          coolant" is the sentence somebody acts on. */}
      <ul className="mix-lines">
        {columns.map((c) => {
          const key = String(c.category);
          const missing = shown.filter((cust) =>
            rows(cust.cells).some((cell) => cell.category === key
                                            && cell.state !== "BUYS")).length;
          const on = gap === key;
          return (
            <li key={key}>
              <button type="button" aria-pressed={on} disabled={missing === 0}
                      className={`mix-line${on ? " mix-line-on" : ""}`}
                      onClick={() => setGap(on ? "" : key)}>
                <span className="mix-line-count">{missing}</span>
                <span className="mix-line-body">
                  <strong>{String(c.label)}</strong>
                  <span className="viz-muted">
                    {missing === 0 ? "everyone takes this" : "do not take this"}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {gap && (
        <p className="quad-focus">
          Showing the <strong>{worklist.length}</strong> customers who do not
          currently buy{" "}
          {String(columns.find((c) => c.category === gap)?.label ?? gap)}.{" "}
          <AffinityNote affinity={affinity} target={gap} />
        </p>
      )}

      <div className="tier3-list">
        <DataGrid<Row>
          ariaLabel="Product mix by customer"
          rows={worklist}
          pageSize={20}
          twoLineRows
          getRowId={(r) => String(r.customer_id)}
          onRowClick={(r) => onNavigate(`customer/${String(r.customer_id)}`)}
          columns={[
            {
              field: "label", headerName: "Customer", flex: 1, minWidth: 220,
              filter: "agTextColumnFilter",
              cellRenderer: (p: { data: Row }) => (
                <EntityName name={String(p.data.label)}
                            origin={p.data.origin as EntityOrigin | undefined}
                            show={sourcesDiffer} strong={false} />
              ),
            },
            ...columns.map((c) => ({
              // The cell is the state, and the state is a word as well as a
              // colour. `valueGetter` so the column still sorts and filters on
              // something meaningful rather than on a React element.
              colId: String(c.category),
              headerName: String(c.label),
              width: 150,
              flex: 0,
              // ag-grid can call this for a group or footer row, where there
              // is no data at all — hence the optional. Sorting and filtering
              // then work on the state word rather than on a React element.
              valueGetter: (p: { data?: Row }) =>
                (p.data ? String(cellOf(p.data, String(c.category))?.state
                                 ?? "NEVER") : "NEVER"),
              cellRenderer: (p: { data: Row }) => {
                const cell = cellOf(p.data, String(c.category));
                const state = String(cell?.state ?? "NEVER");
                const meta = CELL[state] ?? CELL.NEVER;
                return (
                  <span className={`mix-cell ${meta.cls}`}
                        title={state === "LAPSED" && cell?.last_traded
                          ? `Last bought ${formatDate(String(cell.last_traded))}`
                          : undefined}>
                    {state === "BUYS" && num(cell?.revenue) > 0
                      ? money(num(cell?.revenue))
                      : meta.label}
                  </span>
                );
              },
            })),
            numeric<Row>("lines_held", "Lines",
                         (v) => `${v} of ${columns.length}`,
                         { width: 110, flex: 0 }),
            numeric<Row>("revenue", "Revenue", (v) => money(v),
                         { width: 140, flex: 0 }),
          ]}
          empty={<p className="viz-muted">No customer matches this filter.</p>}
        />
      </div>

      <Affinity rows={affinity} minPeers={num(data?.min_peers)} />
      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}

function cellOf(customer: Row, category: string): Row | undefined {
  return rows(customer.cells).find((c) => c.category === category);
}

/** The strongest reason to look at this gap, in one sentence. */
function AffinityNote({ affinity, target }: { affinity: Row[]; target: string }) {
  const best = affinity
    .filter((a) => a.to === target && a.share != null)
    .sort((a, b) => num(b.share) - num(a.share))[0];
  if (!best) {
    return (
      <span className="viz-muted">
        Too few customers buy this line to say how it travels with the others.
      </span>
    );
  }
  return (
    <span className="viz-muted">
      {pct(num(best.share), 0)} of your {String(best.from_label).toLowerCase()}{" "}
      customers also take it. That is a reason to look, not evidence these ones
      need it.
    </span>
  );
}

/** Which lines travel together, both directions.
 *
 *  A `<table>` and not a grid: it is a fixed n×n of the business's own lines —
 *  five columns will not become five hundred because the business grew — which
 *  is the fact-panel case `ui-standards` §3 names. The customer grid above is
 *  the one whose row count is the size of the business. */
function Affinity({ rows: pairs, minPeers }: { rows: Row[]; minPeers: number }) {
  const estimable = pairs.filter((p) => p.share != null);
  if (!estimable.length) {
    return (
      <p className="bond-unscored">
        No line yet has the {minPeers} customers needed to say how it travels
        with the others. A share computed over fewer is a coincidence with a
        percentage sign on it.
      </p>
    );
  }
  return (
    <details className="viz-table-fallback mix-affinity">
      <summary>Which lines travel together</summary>
      <p className="viz-muted">
        Of the customers who buy the first line, the share who also buy the
        second. Read in one direction only — “most coolant buyers take cutting
        tools” and “few cutting-tool buyers take coolant” are both true and
        mean different things.
      </p>
      <table className="grid">
        <thead>
          <tr><th>Customers who buy</th><th>also buy</th><th>Share</th><th>Of</th></tr>
        </thead>
        <tbody>
          {estimable
            .slice()
            .sort((a, b) => num(b.share) - num(a.share))
            .map((p, i) => (
              <tr key={i}>
                <td>{String(p.from_label)}</td>
                <td>{String(p.to_label)}</td>
                <td>{pct(num(p.share), 0)}</td>
                <td>{num(p.peers)} customers</td>
              </tr>
            ))}
        </tbody>
      </table>
    </details>
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
