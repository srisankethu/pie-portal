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
// who only ever opens the Quote Builder, so the grid is a lazy chunk and the
// screens that use it get a skeleton for the moment it takes to arrive.
//
// **Below `NARROW_BREAKPOINT` a grid is not a grid.** A phone is narrower than
// the columns of any list worth sorting, so the wrapper offers `renderNarrow`:
// one card per row instead of a row of cells. This is the wrapper's job rather
// than each screen's, for the reason `docs/ui-standards.md` §3 gives — a second
// answer to "what does this list look like on a phone" is how two screens end
// up disagreeing about it. It also means a phone never fetches the ag-grid
// chunk at all.

import { Fragment, lazy, Suspense, useMemo, useState } from "react";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import useMediaQuery from "@mui/material/useMediaQuery";
import type { ColDef, GridOptions } from "ag-grid-community";
import { LoadingState } from "./kit";

export type { ColDef } from "ag-grid-community";

/** The width below which a grid that offers `renderNarrow` draws cards.
 *
 *  A literal rather than a theme breakpoint, because it is not one: MUI's `sm`
 *  is 600 and `md` is 900, and the question here is "is there room for six
 *  columns and a price field", whose answer sits between them. Stated once,
 *  exported, so a test can assert against the same number the component reads.
 */
export const NARROW_BREAKPOINT = 700;

/** The narrowest a column is worth rendering at, on a grid too narrow to hold
 *  them all: a money figure with its symbol and separators — "₹2,94,000" —
 *  plus the cell padding and the sort arrow. */
const READABLE_COLUMN = 120;

/** What `sizeColumnsToFit` may do to the columns at this grid width.
 *
 *  Fitting divides whatever space there is between the columns and stops only
 *  at each column's own minimum, which ag-grid defaults to 50px. At 390px the
 *  Money screen's credit grid drew six columns of about 34px: headers reading
 *  "O…", "O…", "C…" over cells reading "₹…". That is not a grid somebody can
 *  scroll sideways — it is a grid with every value hidden and nothing on screen
 *  saying so.
 *
 *  So below `NARROW_BREAKPOINT` a floor goes in and the columns that cannot fit
 *  take it, which leaves the grid scrolling inside its own box: the narrow
 *  fallback this file documents above, and readable. Above it, nothing —
 *  ag-grid raises a column's own `minWidth` to this floor rather than only
 *  filling one in where none was declared, so applying it at every width would
 *  quietly re-lay-out every wide grid whose columns declare less.
 *
 *  Here rather than in the implementation because it is the same question as
 *  `NARROW_BREAKPOINT` — what a grid does when there is not enough width — and
 *  because this module is the one a test can import without loading ag-grid. */
export function fitParamsAt(gridWidth: number): { defaultMinWidth: number } | undefined {
  return gridWidth < NARROW_BREAKPOINT ? { defaultMinWidth: READABLE_COLUMN } : undefined;
}

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
   *  look actionable.
   *
   *  A column that carries its own control — an editable rate, a delete button,
   *  the selection checkbox — sets `context.noRowClick` so clicking it does not
   *  also fire this. Editing a price should not open a drawer over the field
   *  being typed into. */
  onRowClick?: (row: T) => void;
  /** Enter pressed on a row: the keyboard twin of `onRowClick`.
   *
   *  Separate because ag-grid already owns ↑↓ and Enter-to-edit inside the
   *  grid; a window-level key handler beside it means two things listening to
   *  one keystroke, which is how Enter ends up opening a drawer *and* entering
   *  a cell editor. */
  onRowActivate?: (row: T) => void;
  /** This row's identity, stable across refetches.
   *
   *  Without it ag-grid treats every new array as new rows: it rebuilds the
   *  body, and the cell somebody was editing loses focus mid-edit. Anything
   *  whose rows are replaced by a server response after each change — a quote
   *  is the extreme case, since setting one price returns the whole quote —
   *  needs this. Required for `selection`, which is keyed by it. */
  getRowId?: (row: T) => string;
  /** A class per row, for rows that carry a state worth seeing at a glance.
   *
   *  Tinting a row is a *second* cue by design, never the only one: the state
   *  is also a chip in the row, because colour alone is unreadable to a
   *  substantial minority of people and gone in greyscale. */
  rowClass?: (row: T) => string | undefined;
  /** Row height in pixels, where `twoLineRows` is not the shape needed. Rows
   *  that carry a code, a description and a row of state pills need three. */
  rowHeight?: number;
  /** Multi-row selection with checkboxes, controlled by the caller.
   *
   *  The ids stay upstream rather than inside the grid because the toolbar acts
   *  on them too — "select visible", "clear", and a bulk discount over exactly
   *  what is ticked. Requires `getRowId`. */
  selection?: {
    selectedIds: string[];
    onChange: (ids: string[]) => void;
  };
  /** An editable cell was committed. Only fires for columns marked
   *  `editable`; `value` has already been through the column's `valueParser`,
   *  so it arrives in the type the caller declared rather than as the string
   *  somebody typed. */
  onCellValueChanged?: (row: T, field: string, value: unknown) => void;
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
  /** A strip drawn under the row it belongs to, the full width of the grid.
   *
   *  For what a row has to say that its columns cannot hold: what is wrong with
   *  it, and the button that fixes it. The alternative is a column of status
   *  chips plus a banner elsewhere on the screen counting how many rows are in
   *  which state — which is how the Quote Builder came to report its problems
   *  at the moment of sending, a screen away from the row each one was about.
   *
   *  Returning nothing leaves the row alone, so a grid where no row has one is
   *  exactly the grid it was before. **Sorting keeps each strip with its own
   *  row**; per-column filters do not, because a strip carries no fields to
   *  filter on — a grid using this passes `filters={false}` and filters above
   *  itself, which a screen with its own filter chips does anyway. */
  renderRowDetail?: (row: T) => React.ReactNode;
  /** How tall that strip is, in pixels — a function where it depends on how
   *  much the row has to say. Given rather than measured: a height the grid has
   *  to discover is a strip that jumps on first paint. */
  rowDetailHeight?: number | ((row: T) => number);
  /** One card per row, for a viewport narrower than `NARROW_BREAKPOINT`.
   *
   *  A grid narrower than its columns scrolls sideways inside its own box,
   *  which is correct on a laptop and useless on a phone: at 412px the quote
   *  grid put its 822px of columns in a 383px box, so the rate field — the one
   *  thing a salesperson in a machine shop is there to fill in — sat at x=486
   *  with nothing on screen to say it existed.
   *
   *  Given, the wrapper draws these instead of the grid below that width. The
   *  card owns its own controls and its own tap targets: none of `onRowClick`,
   *  `selection` or `onCellValueChanged` applies to it, because a card is not a
   *  row of cells and pretending otherwise would put a second, half-working
   *  copy of each behaviour here.
   *
   *  Omitted, a narrow screen gets the sideways-scrolling grid, unchanged. */
  renderNarrow?: (row: T, index: number) => React.ReactNode;
  ariaLabel: string;
}

export function DataGrid<T>(props: DataGridProps<T>) {
  const { rows, empty, height, pageSize = 25, renderNarrow, ariaLabel } = props;
  // `- 0.02` for the same reason MUI's own `down()` does it: at exactly 700 the
  // two queries must not both match.
  const narrow = useMediaQuery(`(max-width:${NARROW_BREAKPOINT - 0.02}px)`);

  // A skeleton the height the grid will be, so the page does not jump when the
  // chunk lands.
  const placeholder = (
    <div className="ag-shell" style={{ height: height ?? 240 }}>
      <LoadingState rows={4} height={38} />
    </div>
  );

  if (rows === null) return placeholder;
  if (rows.length === 0 && empty) return <>{empty}</>;

  if (narrow && renderNarrow) {
    return (
      <NarrowRows rows={rows} render={renderNarrow} pageSize={pageSize}
                  ariaLabel={ariaLabel} getRowId={props.getRowId} />
    );
  }

  return (
    <Suspense fallback={placeholder}>
      <Grid {...props} pageSize={pageSize} />
    </Suspense>
  );
}

/** The narrow rendering: cards, stacked, one page at a time.
 *
 *  Paged rather than unbounded because the grid it stands in for is paged, and
 *  a list whose length is the size of the business would otherwise mount four
 *  hundred cards on the device least able to draw them. The button says how
 *  many are left rather than "more", so the count is never a surprise. */
function NarrowRows<T>({
  rows, render, pageSize, ariaLabel, getRowId,
}: {
  rows: T[];
  render: (row: T, index: number) => React.ReactNode;
  pageSize: number;
  ariaLabel: string;
  getRowId?: (row: T) => string;
}) {
  const [shown, setShown] = useState(pageSize);
  const rest = rows.length - shown;
  return (
    <Stack spacing={1.5} role="list" aria-label={ariaLabel}>
      {/* Keyed here rather than by each caller. The wrapper owns the mapping,
          so a caller cannot key it — every `renderNarrow` in the app was
          logging React's missing-key warning, and the fix belonging to one
          place is the same reason §3 says to extend this wrapper rather than
          open `AgGridReact` beside it. A Fragment so no element is added
          between the Stack and the card the caller drew. */}
      {rows.slice(0, shown).map((row, i) => (
        <Fragment key={getRowId ? getRowId(row) : i}>{render(row, i)}</Fragment>
      ))}
      {rest > 0 && (
        <Button variant="outlined" onClick={() => setShown(shown + pageSize)}>
          Show {Math.min(rest, pageSize)} more of {rows.length}
        </Button>
      )}
    </Stack>
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

/** A trailing column holding a control rather than a value.
 *
 *  Three settings always travel together — no sort, no filter, and
 *  `context.noRowClick` so pressing the control does not also fire the row
 *  click. `UnrecordedQuotes` had two of the three and a comment claiming the
 *  third; both handlers happened to do the same thing, so the missing flag was
 *  invisible until somebody put a *different* control there. A helper makes the
 *  trio impossible to half-remember.
 */
export function actionColumn<T>(
  render: NonNullable<ColDef<T>["cellRenderer"]>,
  extra: Partial<ColDef<T>> = {},
): ColDef<T> {
  return {
    headerName: "",
    sortable: false,
    filter: false,
    cellRenderer: render,
    ...extra,
    // After `...extra`, and merged rather than replaced: a caller passing its
    // own `context` would otherwise overwrite `noRowClick` and silently undo
    // the one guarantee this helper exists to make.
    context: { noRowClick: true, ...(extra.context as object | undefined) },
  };
}

export function useGridOptions<T>(o: Partial<GridOptions<T>>): GridOptions<T> {
  return useMemo(() => o, [o]);
}
