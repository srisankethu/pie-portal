// What each line returns on the cash it ties up.
//
// Margin says whether a line is priced well. Turns say whether it moves. Neither
// answers the question an owner is actually asking about a shelf — *is this item
// worth the money it is sitting on* — and GMROI is the single figure that does:
// gross profit over a window ÷ average inventory at cost over the same window.
// ₹2.40 means every rupee tied up in that line returned ₹2.40 of gross profit
// while it sat there.
//
// **The window is the headline, not a footnote.** Stock history only exists from
// the day the platform started writing it down — Zoho keeps none — so a figure
// labelled "12 months" over eight weeks of readings is roughly six times the
// truth, and no column on the row would say so. The window therefore leads the
// screen, above the grid, as a sentence rather than as a caption; the server
// states the span it actually covered and this renders that verbatim.
//
// **Nothing is annualised, so nothing is formatted as if it were.** The figure
// is a multiple over the stated window and reads `2.40×`, never a percentage —
// `pct()` would render 2.4 as "240%", which is a different claim about a
// different quantity.
//
// **Two grains, one payload.** A brand's figure is Σ profit ÷ Σ shelf across its
// own items, so the items are computed anyway; a `Seg` switches which grain is
// on screen rather than which request is made. The unattributed line stays in
// the brand list, last and labelled, because a third of this item master has no
// manufacturer tagged and folding that into "Other" would make it read like a
// small brand nobody has to think about.
//
// **A row with no figure is not a row with a zero.** A measured zero — held all
// window, sold nothing — is the finding this screen exists for. An unknown
// numerator or an absent denominator is missing evidence, and the two are given
// different words and a chip apiece, because the first is news and the second is
// a job.

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { money } from "../../money";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { CompanyFilter, useCompanyFilter } from "../CompanyFilter";
import { DataGrid, numeric } from "../DataGrid";
import { MetricCard, StatusChip, Unavailable } from "../kit";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Panel, stateOf } from "./Panel";
import { MonthPicker, Seg } from "./Seg";
import { pct, useInsight } from "./useInsight";
import { useState } from "react";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

/** GMROI as a multiple, which is what it is.
 *
 *  Deliberately not `pct()`: a GMROI of 2.4 is "₹2.40 back per rupee held", and
 *  rendering it as "240%" states a ratio of the same units as a share of one
 *  quantity by another. The `×` carries the reading. */
function multiple(v: unknown): string {
  return v == null ? "—" : `${Number(v).toFixed(2)}×`;
}

/** How each no-answer reads, and how much of a job it is.
 *
 *  Four reasons, four different responses — which is the whole point of the
 *  server distinguishing them. A missing purchase rate is a clerical fix; a
 *  service has no shelf and never will; a missing reading resolves itself as
 *  snapshots accumulate; an uncosted sale is a bill somebody has not entered. A
 *  single "no data" chip would have collapsed all four into one shrug. */
const REASON: Record<string, { label: string; tone: "warn" | "neutral" }> = {
  NO_INVENTORY_OBSERVED: { label: "Never seen on the shelf", tone: "neutral" },
  NOT_STOCKED: { label: "Not stocked", tone: "neutral" },
  INVENTORY_NOT_COSTED: { label: "Shelf not priced", tone: "warn" },
  NO_INVENTORY_HELD: { label: "No stock held", tone: "neutral" },
  NO_COSTED_SALE: { label: "Sales not costed", tone: "warn" },
};

/** The two money columns both grains carry, defined once.
 *
 *  They are the numerator and the denominator of the figure in the column beside
 *  them, so an item grid and a brand grid showing different widths or different
 *  words for them would make switching grain look like switching subject. Small
 *  enough to inline twice, and that is exactly how the two would drift. */
function ratioColumns() {
  return [
    numeric<Row>("gross_profit", "Gross profit", (v) => money(v),
                 { width: 160, flex: 0 }),
    numeric<Row>("avg_inventory_at_cost", "Average shelf", (v) => money(v),
                 { width: 160, flex: 0 }),
  ];
}

const BY_SKU = "sku";
const BY_BRAND = "brand";

export function GmroiScreen({ session }: { session: PlatformSession }) {
  const [months, setMonths] = useState(12);
  const [grain, setGrain] = useState(BY_SKU);
  const { data, loading, error, reload } = useInsight(
    "gmroi", () => papi.gmroi(session.token, months), [session.token, months]);

  const covered = (data?.window as Row | undefined) ?? {};
  const totals = (data?.totals as Row | undefined) ?? {};
  const counts = (data?.counts as Record<string, number>) ?? {};
  const skus = rows(data?.skus);
  const brands = rows(data?.brands);
  const measurable = data?.measurable === true;
  // An item master is per connected company, so the same part number is a
  // different row with its own shelf in each book. Below two books every badge
  // says the same thing, and the server is the only side that knows how many
  // there are.
  const sourcesDiffer = Boolean(data?.sources_differ);
  const company = useCompanyFilter(skus as Sourced[]);
  const shown = company.apply(skus as Sourced[]) as Row[];

  return (
    <Panel
      title="GMROI"
      question="What each line returns on the cash it ties up"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <Stack direction="row" spacing={2}
               sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <Seg label="By" value={grain} onChange={setGrain}
               options={[[BY_SKU, "Item"], [BY_BRAND, "Brand"]]} />
          <MonthPicker id="gmroi-months" value={months} onChange={setMonths}
                       options={[1, 3, 6, 12, 24]} />
        </Stack>
      }
    >
      {/* The window, first and in words. Every figure below is over this span
          and only this span, and a reader who takes the ratio without the
          sentence has taken a number that means something else. */}
      <Typography variant="body2" sx={{ mb: 1 }}>
        {String(covered.basis ?? "")}
      </Typography>
      {covered.shortfall ? (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {String(covered.shortfall)}
        </Typography>
      ) : null}

      {measurable && (
        <>
          <Box
            sx={{
              display: "grid", gap: 2, mb: 2,
              gridTemplateColumns: {
                xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(4, 1fr)",
              },
            }}
          >
            <MetricCard
              label="The book"
              value={multiple(totals.gmroi)}
              tip={"Total gross profit over the window ÷ total average "
                   + "inventory at cost over it. Not the average of the item "
                   + "figures — a small line would weigh as much as a large one."}
              sub={`${money(num(totals.gross_profit))} earned on `
                   + `${money(num(totals.avg_inventory_at_cost))} held`}
            />
            <MetricCard
              label="Items measured"
              value={`${counts.skus_measured ?? 0} of ${counts.skus ?? 0}`}
              sub={"The rest have no shelf on record, no purchase rate on it, "
                   + "or no bill behind their sales."}
            />
            <MetricCard
              label="Brands"
              value={num(counts.brands)}
              tip={"Attributed through the bill that bought the item first, "
                   + "and the item master's manufacturer second."}
              sub={counts.unattributed_skus
                ? `${counts.unattributed_skus} item(s) could not be attributed `
                  + "and are their own line"
                : "Every item attributed"}
            />
            <MetricCard
              label="Understated"
              value={num(counts.partial_cost_skus)}
              tip={"Items whose sales in the window are only partly covered "
                   + "by a bill. Their gross profit is the covered part, so "
                   + "their GMROI is low by an amount nobody can compute."}
              sub="Items with sales no bill covers"
            />
          </Box>

          {grain === BY_SKU ? (
            <>
              <Stack direction="row" spacing={2}
                     sx={{ mb: 1, alignItems: "center", flexWrap: "wrap" }}>
                <Typography variant="body2" color="text.secondary">
                  {shown.length === skus.length
                    ? `${skus.length} items`
                    : `${shown.length} of ${skus.length} items`}
                  {" "}· earners first, unmeasurable last rather than as zero
                </Typography>
                <CompanyFilter options={company.options} value={company.company}
                               onChange={company.setCompany} show={company.show} />
              </Stack>
              <DataGrid<Row>
                ariaLabel="GMROI by item"
                rows={shown}
                pageSize={25}
                twoLineRows
                getRowId={(r) => String(r.product_id)}
                columns={[
                  {
                    field: "label", headerName: "Item", flex: 1.5, minWidth: 220,
                    filter: "agTextColumnFilter",
                    cellRenderer: (p: { data?: Row }) => (
                      <EntityName
                        name={String(p.data?.label ?? "")}
                        origin={p.data?.origin as EntityOrigin | undefined}
                        show={sourcesDiffer}
                      />
                    ),
                  },
                  {
                    field: "gmroi", headerName: "GMROI", width: 150, flex: 0,
                    sort: "desc",
                    type: "numericColumn", cellClass: "ag-num",
                    filter: "agNumberColumnFilter",
                    headerTooltip: "Gross profit over the window ÷ average "
                      + "inventory at cost over it. A multiple, not a "
                      + "percentage, and not annualised.",
                    // A blank cell would read as a zero in a column of numbers,
                    // and the difference between "earned nothing" and "we cannot
                    // tell" is the whole reason the reason column exists.
                    cellRenderer: (p: { data?: Row; value?: unknown }) => {
                      if (p.value != null) return multiple(p.value);
                      const reason = REASON[String(p.data?.reason ?? "")];
                      return (
                        <StatusChip label={reason?.label ?? "Not measurable"}
                                    tone={reason?.tone ?? "neutral"}
                                    tip={String(p.data?.reason_meaning ?? "")}
                                    dense />
                      );
                    },
                  },
                  ...ratioColumns(),
                  {
                    field: "inventory_days", headerName: "Readings", width: 130,
                    flex: 0, type: "numericColumn", cellClass: "ag-num",
                    filter: "agNumberColumnFilter",
                    headerTooltip: "Days of stock reading behind this item's "
                      + "average. A thin count is a thin average, and it says "
                      + "so rather than hiding inside the figure.",
                    valueFormatter: (p) =>
                      p.value == null ? "—" : `${p.value} d`,
                  },
                  {
                    field: "confidence", headerName: "Basis", width: 170,
                    flex: 0, filter: "agTextColumnFilter",
                    headerTooltip: "PARTIAL_COST means only some of this item's "
                      + "sales in the window had a bill behind them, so the "
                      + "gross profit — and the GMROI — is an understatement.",
                    cellRenderer: (p: { data?: Row }) => (
                      p.data?.confidence === "PARTIAL_COST" ? (
                        <StatusChip
                          label={`${pct(p.data?.cost_coverage as number, 0)} costed`}
                          tone="warn" dense
                          tip={"Gross profit covers only the sale lines a "
                               + "bill backs, so this figure is low by an "
                               + "unknown amount."}
                        />
                      ) : <Box component="span" className="viz-muted">—</Box>
                    ),
                  },
                  numeric<Row>("revenue", "Revenue", (v) => money(v),
                               { width: 150, flex: 0,
                                 context: { minGridWidth: 1100 } }),
                ]}
                empty={
                  <Typography variant="body2" color="text.secondary">
                    Nothing on the shelf has both a valued reading and a costed
                    sale inside this window.
                  </Typography>
                }
              />
            </>
          ) : (
            <>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Σ gross profit ÷ Σ average shelf across each principal's items —
                never the average of their GMROIs, which would let a ₹900 line
                weigh as much as a ₹9 lakh one. Items nothing could attribute
                keep a line of their own, last.
              </Typography>
              <DataGrid<Row>
                ariaLabel="GMROI by brand"
                rows={brands}
                pageSize={25}
                twoLineRows
                getRowId={(r) => String(r.principal_id)}
                columns={[
                  {
                    field: "label", headerName: "Principal", flex: 1.4,
                    minWidth: 220, filter: "agTextColumnFilter",
                    cellRenderer: (p: { data?: Row }) => (
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                        <span>{String(p.data?.label ?? "")}</span>
                        {/* Which fact placed the brand. A bill this book paid is
                            a transaction; a manufacturer is an attribute
                            somebody typed, and they are not equally strong. */}
                        <StatusChip
                          label={String(p.data?.source_label ?? "")}
                          tone={p.data?.source === "BILL" ? "good"
                                : p.data?.attributed === false ? "warn"
                                  : "neutral"}
                          dense
                        />
                      </Stack>
                    ),
                  },
                  {
                    field: "gmroi", headerName: "GMROI", width: 150, flex: 0,
                    type: "numericColumn", cellClass: "ag-num",
                    filter: "agNumberColumnFilter",
                    valueFormatter: (p) => multiple(p.value),
                  },
                  ...ratioColumns(),
                  {
                    headerName: "Items", width: 140, flex: 0, sortable: false,
                    filter: false,
                    headerTooltip: "How many of the principal's items the ratio "
                      + "could include, out of how many it has.",
                    valueGetter: (p) =>
                      `${num(p.data?.skus_measured)} of ${num(p.data?.skus)}`,
                  },
                  {
                    field: "gross_profit_excluded", headerName: "Profit left out",
                    width: 180, flex: 0, type: "numericColumn",
                    cellClass: "ag-num", filter: "agNumberColumnFilter",
                    headerTooltip: "Gross profit on this principal's items that "
                      + "the ratio could not include — earnings off a shelf "
                      + "nothing valued. Shown so the omission has a size "
                      + "rather than quietly depressing the figure.",
                    valueFormatter: (p) =>
                      Number(p.value) > 0 ? money(Number(p.value)) : "—",
                  },
                ]}
                empty={
                  <Typography variant="body2" color="text.secondary">
                    No principal has an item with both a valued shelf and a
                    costed sale inside this window.
                  </Typography>
                }
              />
            </>
          )}
        </>
      )}

      <Unavailable items={rows(data?.unavailable)} />
    </Panel>
  );
}
