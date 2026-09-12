// The grid itself. Split from `DataGrid.tsx` so this file — and the ~1 MB of
// ag-grid it pulls in — is a lazy chunk rather than part of the main bundle.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "@mui/material/styles";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type CellKeyDownEvent,
  type ColDef,
  type GridApi,
  type IRowNode,
  type RowSelectionOptions,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { GridSizeChangedEvent } from "ag-grid-community";
import type { DataGridProps } from "./DataGrid";

ModuleRegistry.registerModules([AllCommunityModule]);

// The v33+ Theming API rather than a CSS import, so the grid takes this app's
// tokens instead of arriving with its own look.
//
// These used to be seven literal hex codes and `browserColorScheme: "light"`,
// with a comment saying the values were "read off styles.css deliberately".
// Reading them off is the problem: `theme.ts` says in its own opening paragraph
// that the look had two owners and that "two copies of a hex code diverge on the
// first tweak, and the failure is silent". This was the second copy, and it sat
// in the file every table in the product renders through — §3 routes them all
// here — so a palette change would have moved every surface except the grids,
// and a dark mode would have left them lit.
//
// Derived from the MUI theme now, which is itself built from `tokens`. Two
// deliberate choices survive as choices rather than as literals:
//
//   * **White, not `background.paper`.** The paper token is `neutral-100`, and a
//     grid nearly the same grey as the panel around it reads worse than one that
//     is either identical or clearly separate. `common.white` says that in the
//     theme's own vocabulary, so it still moves with the palette.
//   * **`divider` for the border**, which is text at 16% where this said 12% — a
//     hair stronger, and worth it to have one owner for the line between rows.
//
// Built inside the component rather than at module scope because it now depends
// on the theme; memoised on it, so a grid is not re-themed on every render.
function useGridTheme() {
  const mui = useTheme();
  return useMemo(() => themeQuartz.withParams({
    accentColor: mui.palette.primary.main,
    backgroundColor: mui.palette.common.white,
    borderColor: mui.palette.divider,
    browserColorScheme: mui.palette.mode,
    cellHorizontalPadding: 10,
    fontFamily: "inherit",
    fontSize: `${mui.typography.body2.fontSize}px`,
    foregroundColor: mui.palette.text.primary,
    headerBackgroundColor: mui.palette.common.white,
    // The uppercase small-caps header ramp, which is what a column header is.
    headerFontSize: `${mui.typography.overline.fontSize}px`,
    headerFontWeight: Number(mui.typography.overline.fontWeight),
    headerTextColor: mui.palette.text.secondary,
    headerVerticalPaddingScale: 0.8,
    oddRowBackgroundColor: "transparent",
    rowHoverColor: mui.palette.info.light,
    rowVerticalPaddingScale: 0.9,
    spacing: 6,
    wrapperBorderRadius: 0,
  }), [mui]);
}

/** Checkbox selection, click-to-select off.
 *
 *  Off because a row click already does something on every grid that offers
 *  selection here — it opens the record — and a click that both opened a
 *  drawer and changed what a bulk action would apply to is a click nobody can
 *  predict. The checkbox and the space bar select; nothing else does. */
const ROW_SELECTION: RowSelectionOptions = {
  mode: "multiRow",
  checkboxes: true,
  headerCheckbox: true,
  enableClickSelection: false,
  // A strip is not a row of the list. Without this the header checkbox selects
  // it too, and "3 selected" over a grid showing two ticked lines is the grid
  // counting its own annotations.
  isRowSelectable: (node) => !isDetail(node.data),
};

/** A strip's row: the row it belongs to, what to draw, and the parent's id.
 *
 *  The parent id is carried rather than recomputed because `postSortRows` has
 *  to re-pair the two after every sort, and asking `getRowId` again there would
 *  mean this file knowing how to identify a row it was only ever handed. */
interface DetailRow<T> {
  __detailFor: T;
  __parentId: string;
  __node: React.ReactNode;
}

function isDetail<T>(data: unknown): data is DetailRow<T> {
  return Boolean(data && typeof data === "object" && "__detailFor" in data);
}

export default function DataGridImpl<T>({
  rows, columns, onRowClick, onRowActivate, pageSize = 25, height, filters = true,
  ariaLabel, twoLineRows = false, rowHeight: rowHeightProp, getRowId,
  rowClass: rowClassFor, selection, onCellValueChanged,
  renderRowDetail, rowDetailHeight = 64,
}: DataGridProps<T>) {
  const [api, setApi] = useState<GridApi<T | DetailRow<T>> | null>(null);
  const gridTheme = useGridTheme();
  const defaultColDef = useMemo<ColDef<T>>(() => ({
    sortable: true,
    resizable: true,
    // The floating filter row is what makes a long list usable without a
    // separate search box per screen. Suppressed where a panel cannot spare
    // the height.
    filter: filters,
    floatingFilter: filters,
    // Long tool names are the norm here, so a header that cannot show its own
    // text is the common case rather than the exception.
    headerTooltip: undefined,
  }), [filters]);

  // Responsive columns. A grid narrower than its columns scrolls sideways
  // inside its own box — correct, and the page never scrolls with it — but on
  // a phone that leaves two columns visible and no sign there are five more.
  // A column may declare the narrowest grid it is worth showing in, via
  // `context.minGridWidth`; below that it is hidden rather than pushed off.
  const onGridSizeChanged = useCallback((e: GridSizeChangedEvent) => {
    const width = e.clientWidth;
    const hide: string[] = [];
    const show: string[] = [];
    for (const col of e.api.getColumns() ?? []) {
      const need = Number(col.getColDef().context?.minGridWidth ?? 0);
      (need && width < need ? hide : show).push(col.getColId());
    }
    e.api.setColumnsVisible(hide, false);
    e.api.setColumnsVisible(show, true);
    e.api.sizeColumnsToFit();
  }, []);

  // ── selection, controlled from upstream ───────────────────────────────────
  // The grid reports what the user did; the caller owns the list and pushes it
  // back. `source` is what keeps the two from chasing each other: a change this
  // effect made arrives back as `'api'` and is ignored, so a toolbar "select
  // visible" cannot loop against the grid's own event.
  const selectedIds = selection?.selectedIds;
  const wanted = useMemo(() => new Set(selectedIds ?? []), [selectedIds]);
  const onChange = selection?.onChange;
  // Read inside the callback rather than closed over, so the handler does not
  // have to be rebuilt — and cannot fire with a stale `getRowId`.
  const idOf = useRef(getRowId);
  idOf.current = getRowId;

  useEffect(() => {
    const id = idOf.current;
    if (!api || !selection || !id) return;
    const select: IRowNode<T | DetailRow<T>>[] = [];
    const deselect: IRowNode<T | DetailRow<T>>[] = [];
    // Every node, not only the rendered ones: a selection made from the toolbar
    // must not depend on where the paginator happens to be. A strip is not one
    // of them — it has no identity of its own to be selected by.
    api.forEachNode((node) => {
      if (!node.data || isDetail<T>(node.data)) return;
      const should = wanted.has(id(node.data as T));
      if (should !== node.isSelected()) (should ? select : deselect).push(node);
    });
    if (select.length) api.setNodesSelected({ nodes: select, newValue: true, source: "api" });
    if (deselect.length) api.setNodesSelected({ nodes: deselect, newValue: false, source: "api" });
  }, [api, wanted, selection, rows]);

  const onCellKeyDown = useCallback((e: CellKeyDownEvent<T | DetailRow<T>>) => {
    if (!onRowActivate || !e.data || isDetail<T>(e.data)) return;
    const key = (e.event as KeyboardEvent | undefined)?.key;
    // Not while a cell is being edited: there, Enter commits the value, and
    // opening a drawer on top of it would discard the keystroke that saved it.
    if (key !== "Enter" || e.colDef.editable || e.api.getEditingCells().length) return;
    onRowActivate(e.data as T);
  }, [onRowActivate]);

  // Grid height: tall enough for the page it is showing, never taller. A grid
  // fixed at 600px under a five-row list is a screenful of ruled blank space.
  const rowsThisPage = Math.min(rows?.length ?? 0, pageSize);

  /* Pagination only once there is a second page to go to.
   *
   * "Page Size 50 · 1 to 1 of 1 · |< < Page 1 of 1 > >|" under a single quote
   * line is 44px of controls, every one of which is disabled or a no-op — a
   * page-size selector on a four-line RFQ cannot change what is on screen. It
   * returns the moment the rows outgrow a page, which is the only time any of
   * it does something. */
  const paginated = (rows?.length ?? 0) > pageSize;
  const rowHeight = rowHeightProp ?? (twoLineRows ? 50 : 34);

  /** The rows, with each strip interleaved after the row it belongs to.
   *
   *  The strip is drawn once here rather than on every grid render: ag-grid
   *  asks its full-width renderer for a node repeatedly, and a `renderRowDetail`
   *  that builds React elements is cheaper to call per row than per paint. */
  const data = useMemo<(T | DetailRow<T>)[]>(() => {
    if (!rows) return [];
    if (!renderRowDetail || !getRowId) return rows;
    const out: (T | DetailRow<T>)[] = [];
    for (const row of rows) {
      out.push(row);
      const node = renderRowDetail(row);
      if (node) out.push({ __detailFor: row, __parentId: getRowId(row), __node: node });
    }
    return out;
  }, [rows, renderRowDetail, getRowId]);

  /** Whether this grid draws strips at all. Everything below keys off it. */
  const strips = Boolean(renderRowDetail && getRowId);

  const detailHeightOf = useCallback((row: T) =>
    typeof rowDetailHeight === "function" ? rowDetailHeight(row) : rowDetailHeight,
  [rowDetailHeight]);

  // Tall enough for the page it is showing, strips included: a grid sized for
  // its lines alone clips the last one's strip, which is the half of the row
  // that says what to do about it. A strip is an extra row rather than a taller
  // one, so its whole height is added — `rowsThisPage` counts only the lines.
  const stripHeight = data
    .slice(0, Math.min(data.length, pageSize))
    .reduce((sum, r) => sum + (isDetail<T>(r) ? detailHeightOf(r.__detailFor) : 0), 0);
  const auto = 32 + (filters ? 32 : 0) + rowsThisPage * rowHeight + stripHeight + 48;

  return (
    <div className="ag-shell" style={{ height: height ?? Math.min(auto, 720) }}>
      <AgGridReact<T | DetailRow<T>>
        theme={gridTheme}
        rowData={data}
        // The columns are the caller's, written against its own row type. A
        // strip renders no cells at all, so the widened type is a fact about
        // this file's plumbing rather than about anything a column can meet.
        columnDefs={columns as unknown as ColDef<T | DetailRow<T>>[]}
        // A strip is one cell the width of the grid, and it is not a row of the
        // list: it cannot be selected, sorted into somewhere else, or clicked
        // as though it were the record above it.
        {...(strips ? {
          // Only where a caller draws strips. Handed to ag-grid unconditionally
          // — even returning false for every row — a full-width renderer changes
          // how it builds rows on grids that have none, and the first symptom is
          // a button inside a cell that stops firing its click. A grid with no
          // strips is byte-for-byte the grid it was before.
          isFullWidthRow: (p: { rowNode: { data?: T | DetailRow<T> } }) =>
            isDetail<T>(p.rowNode.data),
          fullWidthCellRenderer: (p: { data?: DetailRow<T> }) => <>{p.data?.__node}</>,
          getRowHeight: (p: { data?: T | DetailRow<T> }) =>
            (isDetail<T>(p.data) ? detailHeightOf(p.data.__detailFor) : rowHeight),
        } : {})}
        // Sorting moves the rows; this puts each strip back under its own.
        // Without it a sort by rate leaves every problem attached to whichever
        // line happens to land above it — the one failure that would make this
        // worse than the column it replaced.
        postSortRows={renderRowDetail && getRowId ? (p) => {
          const strips = new Map<string, IRowNode<T | DetailRow<T>>>();
          const parents: IRowNode<T | DetailRow<T>>[] = [];
          for (const node of p.nodes) {
            if (isDetail<T>(node.data)) strips.set(node.data.__parentId, node);
            else parents.push(node);
          }
          const ordered: IRowNode<T | DetailRow<T>>[] = [];
          for (const parent of parents) {
            ordered.push(parent);
            const strip = parent.data && strips.get(getRowId(parent.data as T));
            if (strip) ordered.push(strip);
          }
          p.nodes.length = 0;
          p.nodes.push(...ordered);
        } : undefined}
        defaultColDef={defaultColDef as unknown as ColDef<T | DetailRow<T>>}
        pagination={paginated}
        rowHeight={rowHeight}
        paginationPageSize={pageSize}
        paginationPageSizeSelector={[10, 25, 50, 100]}
        getRowId={getRowId
          ? (p) => (isDetail<T>(p.data) ? `${p.data.__parentId}:strip` : getRowId(p.data as T))
          : undefined}
        getRowClass={(p) => (isDetail<T>(p.data)
          ? "ag-row-strip"
          : (rowClassFor && p.data ? rowClassFor(p.data as T) : undefined))}
        rowSelection={selection ? ROW_SELECTION : undefined}
        // Pinned to its width. `sizeColumnsToFit` shaves whatever it is allowed
        // to, and with nothing holding this column it collapsed to nothing on a
        // laptop — the rows were still selectable by keyboard, and there was no
        // checkbox on screen to say so.
        selectionColumnDef={{ width: 44, minWidth: 44, maxWidth: 44,
                              resizable: false, suppressMovable: true,
                              suppressSizeToFit: true,
                              context: { noRowClick: true } }}
        onSelectionChanged={
          selection && getRowId
            ? (e) => {
                if (e.source === "api") return;   // our own effect, echoing back
                onChange?.(e.api.getSelectedRows()
                  .filter((r): r is T => !isDetail<T>(r)).map(getRowId));
              }
            : undefined
        }
        onGridReady={(e) => setApi(e.api)}
        suppressCellFocus={!onRowClick && !onRowActivate}
        rowClass={onRowClick ? "ag-row-clickable" : undefined}
        // Per cell rather than per row, so a column carrying its own control
        // can opt out — see `context.noRowClick` in DataGrid.tsx.
        onCellClicked={
          onRowClick
            ? (e) => {
                if (e.column.getColDef().context?.noRowClick) return;
                if (e.data && !isDetail<T>(e.data)) onRowClick(e.data as T);
              }
            : undefined
        }
        onCellKeyDown={onRowActivate ? onCellKeyDown : undefined}
        onCellValueChanged={
          onCellValueChanged
            ? (e) => {
                const field = e.colDef.field ?? e.column.getColId();
                if (e.data && !isDetail<T>(e.data)) {
                  onCellValueChanged(e.data as T, field, e.newValue);
                }
              }
            : undefined
        }
        // ag-grid's own "No Rows To Show" throws away the one place this
        // product explains *why* a screen is empty. Callers pass `empty`; this
        // is only reached when they did not.
        overlayNoRowsTemplate={
          '<span class="ag-empty">Nothing to show.</span>'}
        onGridSizeChanged={onGridSizeChanged}
        domLayout="normal"
        aria-label={ariaLabel}
      />
    </div>
  );
}
