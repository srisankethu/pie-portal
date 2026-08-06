// The grid itself. Split from `DataGrid.tsx` so this file — and the ~1 MB of
// ag-grid it pulls in — is a lazy chunk rather than part of the main bundle.

import { useCallback, useMemo } from "react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
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

export default function DataGridImpl<T>({
  rows, columns, onRowClick, pageSize = 25, height, filters = true, ariaLabel,
  twoLineRows = false,
}: DataGridProps<T>) {
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

  // Grid height: tall enough for the page it is showing, never taller. A grid
  // fixed at 600px under a five-row list is a screenful of ruled blank space.
  const rowsThisPage = Math.min(rows?.length ?? 0, pageSize);
  const rowHeight = twoLineRows ? 50 : 34;
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
        suppressCellFocus={!onRowClick}
        rowClass={onRowClick ? "ag-row-clickable" : undefined}
        onRowClicked={onRowClick ? (e) => e.data && onRowClick(e.data) : undefined}
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
