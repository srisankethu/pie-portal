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
// **The heatmap leads and the grid follows, and that order was wrong first.**
// This screen shipped as an AG Grid of 197 rows by 5 columns. An analyst scans
// that happily; an owner wants to look once and know *the right half of my book
// is empty and it is coolant*. The grid is still here and still AG Grid — the
// row count is the size of the business, which is exactly the line CLAUDE.md
// draws — but it now sits under the picture rather than in front of it, which
// is the `Figure` pattern the rest of this package already uses.
//
// The heatmap is the same three states in the same colours. What it adds is
// *shape*: rows sorted by how many lines they hold, so whitespace collects into
// a block you can see the size of instead of a count you have to trust.

import { useMemo, useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { CompanyScope } from "../CompanyFilter";
import type { CompanyScopeOption } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import type { EntityOrigin, PlatformSession } from "../types";
import { Figure, Panel, stateOf } from "./Panel";
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
  // Lines of the business, or principals. The same grid asked of a different
  // key — see mix.py. An authorised distributor needs both: "who has never
  // bought coolant" and "who has never bought a single Sandvik item".
  const [by, setBy] = useState("category");
  // Which connected company the grid is for. Server-side, unlike the row
  // filter every other list uses — see the endpoint. The totals *are* this
  // screen, so scoping has to recompute them rather than hide rows underneath
  // a headline that still describes all three books.
  const [scope, setScope] = useState("");
  const { data, loading, error, reload } = useInsight(
    "mix", () => papi.mix(session.token, Number(months), by, scope || undefined),
    [session.token, months, by, scope]);

  const columns = rows(data?.categories);
  const customers = rows(data?.customers);
  const affinity = rows(data?.affinity);
  const catalogue = (data?.catalogue as Row | undefined) ?? {};
  const attribution = (data?.principals as Row | undefined) ?? {};
  const counts = (data?.counts as Record<string, number>) ?? {};
  const sourcesDiffer = Boolean(data?.sources_differ);

  // Offered from the connection list the server returns, not from the rows'
  // provenance. A picker built from row origins disappears exactly when it is
  // most needed: a book synced before connections were stamped leaves every
  // origin null and the control silently never renders.
  const companies = rows(data?.companies);
  const shown = customers as Row[];

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
  const attributed = attribution.attributed_share as number | null | undefined;
  const makerOnly = attribution.maker_only_share as number | null | undefined;

  return (
    <Panel
      title="Product mix"
      question={by === "vendor"
        ? "Which principals each customer buys — and which they have never taken"
        : "Who takes which lines of the business — and who takes only one"}
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="Columns" value={by} onChange={setBy}
               options={[["category", "By line"], ["vendor", "By supplier"]]} />
          {/* A quarter, a half and a year — the horizons this trade actually
              plans in. "Lapsed" scales with the window (see mix.py), so a 3m
              view means "bought this line before, nothing in the last quarter",
              which is the question a salesperson is asking. */}
          <Seg label="Window" value={months} onChange={setMonths}
               options={[["3", "3m"], ["6", "6m"], ["12", "1y"]]} />
        </div>
      }
    >
      <p className="viz-headline">
        <strong>{counts.single_line ?? 0}</strong> of {shown.length} customers
        buy from only one {by === "vendor" ? "supplier" : "line"}
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
      {by === "category" && resolved != null && resolved < 1 && (
        <p className="bond-unscored">
          <strong>{pct(resolved, 0)}</strong> of the catalogue is placed in a
          line ({num(catalogue.uncategorised)} item
          {num(catalogue.uncategorised) === 1 ? "" : "s"} unplaced). Trade in
          unplaced items is left out of this grid entirely rather than counted
          against anybody — so a gap here may be a gap in the catalogue.
        </p>
      )}

      {/* The same caveat for the supplier pivot, where the gap has two
          different causes and they are worth separating. An item is attributed
          to a principal by a purchase bill where one exists, and otherwise by
          the manufacturer on the item master — which is how stock bought before
          the sync window still knows whose it is. What neither can reach is a
          column nobody has failed to sell. */}
      {by === "vendor" && attributed != null && (
        <p className="bond-unscored">
          <strong>{pct(attributed, 0)}</strong> of trade is attributed to a
          principal
          {makerOnly != null && makerOnly > 0 && (
            <> — <strong>{pct(makerOnly, 0)}</strong> of it from the item's
              manufacturer rather than a purchase bill, which is how stock
              bought before the sync window is still counted</>
          )}
          . The rest is items with neither, and it is left out of this grid
          rather than counted against anybody.
        </p>
      )}

      {/* Server-scoped: every figure above and below is recomputed for the
          company chosen. See CompanyScope for why this is not the row filter
          the directories use. */}
      <CompanyScope options={companies as unknown as CompanyScopeOption[]}
                    value={scope} onChange={setScope} />

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

      <Figure
        caption={`Each row is a customer, each column a ${by === "vendor" ? "principal" : "line of the business"}. Filled means they buy it, amber means they used to and stopped, blank means never. Rows are sorted by how much they take, so the whitespace collects.`}
        summary={`${counts.single_line ?? 0} of ${shown.length} customers buy from only one ${by === "vendor" ? "supplier" : "line"}.`}
        tableLabel="View as a grid"
      >
        <Heatmap rows={worklist} columns={columns} />
      </Figure>

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

/** The shape of the whitespace, at a glance.
 *
 *  One cell per customer per column, sorted so the customers holding most sit
 *  at the top — which turns a scattered grid into a wedge, and the size of the
 *  empty part into something you see rather than count. The same three states
 *  and the same colours as the grid below, so the two cannot tell different
 *  stories about one book.
 *
 *  Deliberately not a `<table>`: at two hundred rows this is a picture of a
 *  distribution, and the accessible version of it is the AG Grid immediately
 *  underneath. */
function Heatmap({ rows: list, columns }: { rows: Row[]; columns: Row[] }) {
  const ordered = useMemo(
    () => [...list].sort((a, b) => (num(b.lines_held) - num(a.lines_held))
                                   || (num(b.revenue) - num(a.revenue))),
    [list]);
  if (!ordered.length || !columns.length) return null;

  // Rows thin out as the book grows so the whole thing stays on one screen —
  // the point of this view is the shape, and a heatmap you scroll has none.
  const h = ordered.length > 300 ? 2 : ordered.length > 120 ? 3 : 6;
  const tracks = `repeat(${columns.length}, 1fr)`;

  return (
    <>
      {/* The columns have to be named on the picture itself. The whitespace
          picker above carries the same words, but a reader looking at a block
          of colour should not have to count across to work out which line a
          gap is in. */}
      <div className="heat-head" style={{ gridTemplateColumns: tracks }}>
        {columns.map((col) => (
          <span key={String(col.category)}>{String(col.label)}</span>
        ))}
      </div>
      {/* No explicit cell width: the grid track *is* the width. Setting both
          made every cell a fifth of its own column and turned a dense block
          into five thin strips with gaps between them — which is a picture of
          nothing. */}
      <div className="heat" style={{ gridTemplateColumns: tracks }}>
        {ordered.map((c) =>
          columns.map((col) => {
            const cell = cellOf(c, String(col.category));
            const state = String(cell?.state ?? "NEVER");
            return (
              <span key={`${c.customer_id}-${col.category}`}
                    className={`heat-cell heat-${state.toLowerCase()}`}
                    style={{ height: h }}
                    title={`${String(c.label)} — ${String(col.label)}: ${CELL[state]?.label ?? state}`} />
            );
          }))}
      </div>
    </>
  );
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
