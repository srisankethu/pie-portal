// The grid itself. Split from `DataGrid.tsx` so this file — and the ~1 MB of
// ag-grid it pulls in — is a lazy chunk rather than part of the main bundle.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
// tokens instead of arriving with its own look. The values below are read off
// styles.css deliberately — a grid that is nearly the same grey as everything
// around it reads worse than one that is either identical or clearly separate.
const theme = themeQuartz.withParams({
  accentColor: "#5980a6",
  backgroundColor: "#ffffff",
  borderColor: "color-mix(in srgb, #1d1f20 12%, transparent)",
  browserColorScheme: "light",
  cellHorizontalPadding: 10,
  fontFamily: "inherit",
  fontSize: "13px",
  foregroundColor: "#1d1f20",
  headerBackgroundColor: "#ffffff",
  headerFontSize: "11px",
  headerFontWeight: 600,
  headerTextColor: "#6b6f76",
  headerVerticalPaddingScale: 0.8,
  oddRowBackgroundColor: "transparent",
  rowHoverColor: "#eef6ff",
  rowVerticalPaddingScale: 0.9,
  spacing: 6,
  wrapperBorderRadius: 0,
});

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
};

export default function DataGridImpl<T>({
  rows, columns, onRowClick, onRowActivate, pageSize = 25, height, filters = true,
  ariaLabel, twoLineRows = false, rowHeight: rowHeightProp, getRowId,
  rowClass: rowClassFor, selection, onCellValueChanged,
}: DataGridProps<T>) {
  const [api, setApi] = useState<GridApi<T> | null>(null);
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
    const select: IRowNode<T>[] = [];
    const deselect: IRowNode<T>[] = [];
    // Every node, not only the rendered ones: a selection made from the toolbar
    // must not depend on where the paginator happens to be.
    api.forEachNode((node) => {
      if (!node.data) return;
      const should = wanted.has(id(node.data));
      if (should !== node.isSelected()) (should ? select : deselect).push(node);
    });
    if (select.length) api.setNodesSelected({ nodes: select, newValue: true, source: "api" });
    if (deselect.length) api.setNodesSelected({ nodes: deselect, newValue: false, source: "api" });
  }, [api, wanted, selection, rows]);

  const onCellKeyDown = useCallback((e: CellKeyDownEvent<T>) => {
    if (!onRowActivate || !e.data) return;
    const key = (e.event as KeyboardEvent | undefined)?.key;
    // Not while a cell is being edited: there, Enter commits the value, and
    // opening a drawer on top of it would discard the keystroke that saved it.
    if (key !== "Enter" || e.colDef.editable || e.api.getEditingCells().length) return;
    onRowActivate(e.data);
  }, [onRowActivate]);

  // Grid height: tall enough for the page it is showing, never taller. A grid
  // fixed at 600px under a five-row list is a screenful of ruled blank space.
  const rowsThisPage = Math.min(rows?.length ?? 0, pageSize);
  const rowHeight = rowHeightProp ?? (twoLineRows ? 50 : 34);
  const auto = 32 + (filters ? 32 : 0) + rowsThisPage * rowHeight + 48;

  return (
    <div className="ag-shell" style={{ height: height ?? Math.min(auto, 720) }}>
      <AgGridReact<T>
        theme={theme}
        rowData={rows ?? []}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        pagination
        rowHeight={rowHeight}
        paginationPageSize={pageSize}
        paginationPageSizeSelector={[10, 25, 50, 100]}
        getRowId={getRowId ? (p) => getRowId(p.data) : undefined}
        getRowClass={rowClassFor ? (p) => (p.data ? rowClassFor(p.data) : undefined) : undefined}
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
                onChange?.(e.api.getSelectedRows().map(getRowId));
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
                if (e.data) onRowClick(e.data);
              }
            : undefined
        }
        onCellKeyDown={onRowActivate ? onCellKeyDown : undefined}
        onCellValueChanged={
          onCellValueChanged
            ? (e) => {
                const field = e.colDef.field ?? e.column.getColId();
                if (e.data) onCellValueChanged(e.data, field, e.newValue);
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
