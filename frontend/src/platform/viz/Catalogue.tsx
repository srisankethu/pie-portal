// The last mile of category coverage.
//
// Three of the four sources place an item without anybody's help — an override,
// the catalogue's own category, the tariff code, the supplier's dominant line.
// Whatever is left needs a person, and this is where that person works.
//
// **This screen is a grid on purpose, and that is not a contradiction.** The
// other screens lead with a picture because an owner is reading a story out of
// them. This one is a work queue: somebody sits down and places items, one row
// at a time, and a picture of that is a picture of a to-do list. CLAUDE.md's
// rule points the same way — the row count is the size of the catalogue.
//
// **Ordered by the money running through the item.** A catalogue has thousands
// of items and nobody is going to place them all. What makes the work worth
// starting is that the first ten rows close most of the gap, and that is only
// true if revenue decides the order. Alphabetical would be a thousand rows of
// equal-looking work.
//
// **The headline is revenue, not item count.** "86% of items are placed" can
// look alarming while the unplaced ones sell nothing, and it can look fine
// while the one item a tenth of revenue runs through is missing. The share of
// *revenue* still unplaced is the number that says whether to bother.

import Button from "@mui/material/Button";
import { useState } from "react";
import { useSnackbar } from "notistack";
import { money } from "../../money";
import { papi } from "../api";
import { InlineLink, StatusChip } from "../kit";
import { DataGrid, numeric } from "../DataGrid";
import type { PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

/** How confident each source is. An item placed by hand and one placed by a
 *  guess from its supplier are both "placed", and somebody reviewing the
 *  catalogue needs to see which is which before trusting it. */
const SOURCE_TONE: Record<string, "good" | "warn" | "neutral"> = {
  OVERRIDE: "good",
  ZOHO: "good",
  HSN: "neutral",
  VENDOR: "warn",
  NONE: "warn",
};

export function CatalogueScreen({ session }: { session: PlatformSession }) {
  const { enqueueSnackbar } = useSnackbar();
  const [scope, setScope] = useState("unplaced");
  const { data, loading, error, reload } = useInsight(
    "catalogue",
    () => papi.catalogue(session.token, scope === "unplaced"),
    [session.token, scope]);

  const items = rows(data?.items);
  const lines = rows(data?.lines);
  const catalogue = (data?.catalogue as Row | undefined) ?? {};
  const unplacedShare = data?.unplaced_revenue_share as number | null | undefined;

  const labels = Object.fromEntries(
    lines.map((l) => [String(l.category), String(l.label)]));

  const place = async (row: Row, category: string) => {
    try {
      await papi.setItemLine(session.token, String(row.product_id), category);
      enqueueSnackbar(`${String(row.name)} → ${labels[category] ?? category}`,
                      { variant: "success" });
      reload();
    } catch (e) {
      enqueueSnackbar((e as Error).message, { variant: "error" });
    }
  };

  const clear = async (row: Row) => {
    try {
      await papi.clearItemLine(session.token, String(row.product_id));
      reload();
    } catch (e) {
      enqueueSnackbar((e as Error).message, { variant: "error" });
    }
  };

  return (
    <Panel
      title="Item lines"
      question="Which line of the business each item belongs to"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <div className="seg-controls">
          <Seg label="Show" value={scope} onChange={setScope}
               options={[["unplaced", "Needs placing"], ["all", "Everything"]]} />
        </div>
      }
    >
      <p className="viz-headline">
        <strong>{pct(catalogue.resolved_share as number, 0)}</strong> of{" "}
        {num(catalogue.products)} items are placed in a line
        {num(catalogue.uncategorised) > 0 && (
          <> · <strong>{num(catalogue.uncategorised)}</strong> are not</>
        )}
        {unplacedShare != null && unplacedShare > 0 && (
          <> · they carry <strong>{pct(unplacedShare, 1)}</strong> of revenue
            ({money(num(data?.unplaced_revenue))})</>
        )}.
      </p>

      <p className="bond-unscored">
        An item is placed by whichever source speaks first: something set here,
        then the catalogue's own category, then the HSN heading, then the line
        that principal mostly supplies. Setting one here beats all of them and
        survives a re-sync — the automatic sources are read fresh every time, so
        this is the only answer that is kept.
        {" "}The list leads with the items revenue actually runs through, so
        placing the first few closes most of the gap.
      </p>

      <div className="tier3-list">
        <DataGrid<Row>
          ariaLabel="Item lines"
          rows={items}
          pageSize={25}
          twoLineRows
          getRowId={(r) => String(r.product_id)}
          columns={[
            { field: "name", headerName: "Item", flex: 1, minWidth: 240,
              filter: "agTextColumnFilter" },
            { field: "hsn", headerName: "HSN", width: 130, flex: 0,
              valueFormatter: (p: { value: unknown }) =>
                p.value ? String(p.value) : "—" },
            {
              // Whose product it is, and on what evidence — a bill this book
              // paid, or the brand on the item master. Both read as a supplier
              // name, and they are not equally strong: a bill is a transaction,
              // a brand is an attribute somebody typed. Somebody reviewing a
              // catalogue has to be able to tell them apart before trusting
              // either, so the source travels with the name rather than being
              // available somewhere else.
              field: "supplier", headerName: "Supplier", width: 210, flex: 0,
              filter: "agTextColumnFilter",
              cellRenderer: (p: { data: Row }) => (
                p.data.supplier ? (
                  <span className="cat-attrib">
                    <span title={String(p.data.supplier)}>
                      {String(p.data.supplier)}
                    </span>
                    <StatusChip label={String(p.data.supplier_source_label)}
                                tone={p.data.supplier_source === "BILL"
                                      ? "good" : "neutral"}
                                dense />
                  </span>
                ) : <span className="viz-muted">—</span>
              ),
            },
            numeric<Row>("revenue", "Revenue", (v) => money(v),
                         { width: 150, flex: 0 }),
            {
              // A plain select in the cell rather than ag-grid's editor. The
              // editor wants a double-click to open and a second click to
              // choose; this is a work queue where somebody places item after
              // item, and halving the interactions is the difference between a
              // task that gets finished and one that gets abandoned. It is also
              // keyboard-navigable and screen-reader-labelled for free, which
              // the cell editor is not without extra work.
              field: "category", headerName: "Line", width: 230, flex: 0,
              cellRenderer: (p: { data: Row }) => (
                <select
                  className="cat-pick"
                  aria-label={`Line for ${String(p.data.name)}`}
                  value={String(p.data.category)}
                  onChange={(e) => place(p.data, e.target.value)}
                >
                  {/* Present only while unplaced, and never selectable — an
                      item cannot be *set* to "not placed"; the way back is to
                      reset the override, which the source column offers. */}
                  {String(p.data.category) === "UNCATEGORISED" && (
                    <option value="UNCATEGORISED" disabled>Not placed</option>
                  )}
                  {lines.map((l) => (
                    <option key={String(l.category)} value={String(l.category)}>
                      {String(l.label)}
                    </option>
                  ))}
                </select>
              ),
            },
            {
              field: "source", headerName: "From", width: 190, flex: 0,
              cellRenderer: (p: { data: Row }) => (
                <span className="cat-source">
                  <StatusChip label={String(p.data.source_label)}
                              tone={SOURCE_TONE[String(p.data.source)] ?? "neutral"}
                              dense />
                  {p.data.overridden === true && (
                    <InlineLink onClick={() => clear(p.data)}>reset</InlineLink>
                  )}
                </span>
              ),
            },
          ]}
          empty={
            <div className="viz-state">
              <p className="viz-state-title">Nothing needs placing</p>
              <p className="viz-muted">
                Every item that has traded resolved to a line on its own.
              </p>
              <Button type="button" size="small" variant="outlined"
                      onClick={() => setScope("all")}>
                Show everything anyway
              </Button>
            </div>
          }
        />
      </div>
    </Panel>
  );
}
