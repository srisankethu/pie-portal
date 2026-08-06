// One grid, for every list long enough that scrolling it is work.
//
// The tables in this app were plain `<table>` markup: fine at eight rows, and
// the reason nobody could use the accounts list at four hundred. What was
// missing is not decoration — it is sorting (which account has the most open
// decisions?), filtering (show me the ones with "Rane" in the name) and
// pagination (do not paint two thousand rows to find one).
//
// **Not every table becomes this.** A four-row fact panel with a label and a
// value is not a grid, and wrapping one in a grid adds a header, a filter row
// and a paginator to something somebody reads in one glance. The rule applied
// here: a table becomes a grid when its row count is driven by the size of the
// business rather than by the shape of the screen. `facttable` stays as it is.
//
// **Loaded on demand.** ag-grid is roughly the size of the rest of this
// application. Statically imported it would be in the bundle for a salesperson
// who only ever opens the negotiation desk, so the grid is a lazy chunk and the
// screens that use it get a skeleton for the moment it takes to arrive.

import { lazy, Suspense, useMemo } from "react";
import type { ColDef, GridOptions } from "ag-grid-community";
import { LoadingState } from "./kit";

export type { ColDef } from "ag-grid-community";

// `lazy` erases the generic, so the type is restored here. The cast is safe:
// the implementation's props are exactly `DataGridProps<T>`, and without it
// every call site would silently degrade to `unknown` columns.
const Grid = lazy(() => import("./DataGridImpl")) as unknown as
  <T>(props: DataGridProps<T>) => React.ReactElement;

export interface DataGridProps<T> {
  rows: T[] | null;
  columns: ColDef<T>[];
  /** Row click. Given only when clicking a row does something — the cursor and
   *  the hover state follow from this, so a decorative handler makes every row
   *  look actionable. */
  onRowClick?: (row: T) => void;
  /** Rows per page. 25 suits a screen somebody scans; 10 suits a panel that
   *  sits under something else and must not push it off the page. */
  pageSize?: number;
  /** Taller rows, for a grid whose name column carries a second line.
   *
   *  `EntityName` renders the record's connected company under its name, and a
   *  row sized for one line clips it — the source is in the DOM, invisible, and
   *  two customers called "Pitti Engineering" look identical again. Opt in
   *  rather than default: most grids are one line and would only lose density.
   */
  twoLineRows?: boolean;
  /** Shown in place of the grid when `rows` is an empty array. A grid that
   *  renders "No Rows To Show" in its own words has thrown away the one place
   *  this product explains *why* something is empty. */
  empty?: React.ReactNode;
  /** Height in pixels. Omitted means the grid sizes to its rows, up to the
   *  page size — which is what keeps a five-row list five rows tall instead of
   *  reserving a screenful of blank grid. */
  height?: number;
  /** Turns off the per-column filter row for narrow panels where it costs more
   *  vertical space than it earns. Sorting always stays on. */
  filters?: boolean;
  ariaLabel: string;
}

export function DataGrid<T>(props: DataGridProps<T>) {
  const { rows, empty, height, pageSize = 25 } = props;

  // A skeleton the height the grid will be, so the page does not jump when the
  // chunk lands.
  const placeholder = (
    <div className="ag-shell" style={{ height: height ?? 240 }}>
      <LoadingState rows={4} height={38} />
    </div>
  );

  if (rows === null) return placeholder;
  if (rows.length === 0 && empty) return <>{empty}</>;

  return (
    <Suspense fallback={placeholder}>
      <Grid {...props} pageSize={pageSize} />
    </Suspense>
  );
}

/* ── column helpers ────────────────────────────────────────────────────────
 * Written here rather than at each call site so two screens cannot end up
 * right-aligning money one way and another the other, and so "sortable by the
 * number, displayed as text" stays the default rather than something each
 * caller remembers. A column that sorts alphabetically on "₹1,20,000" is worse
 * than no sorting at all, because it looks like it worked.
 */

/** A numeric column: right-aligned, tabular figures, sorted on the raw value
 *  and rendered by `format`. */
export function numeric<T>(
  field: string, header: string, format: (v: number) => string,
  extra: Partial<ColDef<T>> = {},
): ColDef<T> {
  return {
    field: field as never,
    headerName: header,
    type: "numericColumn",
    filter: "agNumberColumnFilter",
    cellClass: "ag-num",
    valueFormatter: (p) =>
      p.value === null || p.value === undefined ? "—" : format(Number(p.value)),
    ...extra,
  };
}

/** A text column that flexes to fill spare width. */
export function text<T>(
  field: string, header: string, extra: Partial<ColDef<T>> = {},
): ColDef<T> {
  return {
    field: field as never,
    headerName: header,
    filter: "agTextColumnFilter",
    flex: 1,
    minWidth: 160,
    ...extra,
  };
}

export function useGridOptions<T>(o: Partial<GridOptions<T>>): GridOptions<T> {
  return useMemo(() => o, [o]);
}
