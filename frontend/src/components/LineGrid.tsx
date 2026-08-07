/** The quote's lines, as a grid.
 *
 * This was a hand-written `<table class="grid">` with its own header row, its
 * own sort order (none), its own filtering (none) and its own alignment rules —
 * on a screen whose row count is set by the size of the RFQ, which is exactly
 * the line `platform/DataGrid.tsx` draws between a fact panel and a grid. A
 * forty-line tender was forty rows you scrolled past to find the one that was
 * unresolved. `docs/ui-standards.md` §3 has said "AG Grid Community, for all
 * tabular data" throughout; this table simply predated the rule and nothing
 * pointed at it.
 *
 * What the grid buys, beyond looking like the rest of the product: sort by
 * margin to bring the thin lines together, or by status to bring the blocked
 * ones; real multi-row selection with a header select-all; and a rate cell that
 * is an editor rather than a text box that saves on blur — Enter commits, Tab
 * moves down the quote, Escape abandons.
 *
 * **No column filters here**, though every other grid in the product has them:
 * the toolbar above already searches across code, description and supply
 * product at once, and the state chips already narrow by exactly the states
 * that matter. A funnel on each header would be a second filtering UI for the
 * same job, on the screen with the least width to spare.
 *
 * Cell *content* is MUI: every state is a `Chip` carrying a word, so none of
 * them is colour alone.
 */
import { useMemo } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DeleteOutlineOutlined from "@mui/icons-material/DeleteOutlineOutlined";

import type { Line, LineIntelligence } from "../types";
import { relTone, statusTone } from "../rel";
import { Labelled } from "../Tip";
import { money } from "../money";
import { DataGrid, numeric, type ColDef } from "../platform/DataGrid";
import { EmptyState, StatusChip, type Tone } from "../platform/kit";

/** One line, joined to its assessment.
 *
 *  Joined into the row rather than looked up inside a cell renderer, because a
 *  column resolved in a renderer displays correctly and **sorts on nothing** —
 *  and "which lines are thinnest?" is the first thing anybody asks of a priced
 *  quote. */
type Row = Line & { intel?: LineIntelligence; margin: number | null };

/** The margin to show, and where it came from.
 *
 *  The platform's figure wins: it uses the purchase cost recorded as of today,
 *  net of bill-line discounts. The catalogue figure is the fallback for an item
 *  with no synced purchase cost yet, and says so — an indicative margin
 *  presented as a measured one is the kind of number somebody discounts
 *  against. */
function marginOf(line: Line, intel?: LineIntelligence): number | null {
  return intel?.economics?.margin ?? line.economics?.margin ?? null;
}

/** True when the margin shown came from recorded purchase cost rather than the
 *  catalogue. The tooltip says which, because they are different claims. */
function marginIsMeasured(intel?: LineIntelligence): boolean {
  return (intel?.economics?.margin ?? null) !== null;
}

/** A code and its description, stacked. The description is one line and
 *  elides — the full text is a tooltip rather than a row that grows. */
function CodeCell({
  code, desc, accent = false, children,
}: {
  code: React.ReactNode;
  desc?: string | null;
  /** The supply code when it is not the requested one — a substitution is the
   *  one thing on this row somebody must not read past. */
  accent?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Box sx={{ lineHeight: 1.35, py: 0.5, minWidth: 0 }}>
      <Typography
        component="div"
        sx={{
          fontFamily: "ui-monospace, monospace", fontSize: 12.5, fontWeight: 700,
          color: accent ? "var(--color-accent-800)" : "text.primary",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {code}
      </Typography>
      {desc ? (
        <Tooltip title={desc}>
          <Typography
            component="div"
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {desc}
          </Typography>
        </Tooltip>
      ) : null}
      {children}
    </Box>
  );
}

/** The state pills under a requested item. Chips, so each carries a word.
 *
 *  Only the flags that are not already a column of their own: shortage,
 *  availability and the active substitution each have a cell that states them,
 *  and the old table printed all seven here as well — three lines of pills
 *  restating what the row already said, on every row. */
function Flags({ line }: { line: Line }) {
  const flags: [string, Tone][] = [
    line.flags.unresolved ? ["unresolved", "bad" as Tone] : null,
    line.flags.procurement ? ["procurement", "warn" as Tone] : null,
    line.flags.missingBooks ? ["no Zoho item", "warn" as Tone] : null,
    line.flags.manualReview ? ["manual review", "warn" as Tone] : null,
  ].filter(Boolean) as [string, Tone][];
  if (!flags.length) return null;
  return (
    <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "nowrap", mt: 0.25, overflow: "hidden" }}>
      {flags.map(([label, tone]) => (
        <StatusChip key={label} label={label} tone={tone} dense />
      ))}
    </Stack>
  );
}

/** The worst exception on a line, as a chip. Ordered by severity, so the chip
 *  always shows the thing that most needs a decision rather than the first rule
 *  that happened to fire.
 *
 *  The label is the *category*, not the exception's own sentence. Titles here
 *  run to "First time for this customer and item", which a column wide enough
 *  to print would be a column stealing width from the product codes — and it
 *  elided to "First time for this custome…", which reads as a truncation bug
 *  rather than a state. The sentences are all in the tooltip, in full, and in
 *  the drawer behind the row. */
function CommercialChip({ intel }: { intel?: LineIntelligence }) {
  if (!intel) return <Typography variant="caption" color="text.secondary">—</Typography>;
  const worst = intel.exceptions[0];
  if (!worst) return <StatusChip label="clear" tone="good" tip="Every deterministic check passed on this line. That is not a claim that the price is optimal." />;

  const tone: Tone =
    worst.severity === "CRITICAL" ? "bad" : worst.severity === "WARNING" ? "warn" : "info";
  const others = intel.exceptions.length - 1;
  const label = intel.requires_approval
    ? "approval"
    : worst.severity === "CRITICAL" ? "blocking"
      : worst.severity === "WARNING" ? "check price"
        : "context";

  return (
    <StatusChip
      tone={tone}
      label={
        <Labelled
          tip={
            <>
              {intel.requires_approval && (
                <div style={{ marginBottom: 4 }}>
                  <b>This line cannot be sent without an approval.</b>
                </div>
              )}
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {intel.exceptions.map((e) => (
                  <li key={e.code}>{e.title}</li>
                ))}
              </ul>
              <div style={{ marginTop: 4 }}>Open the line for the detail behind each one.</div>
            </>
          }
        >
          {label}
          {others > 0 ? ` +${others}` : ""}
        </Labelled>
      }
    />
  );
}

/** A column that must keep its width.
 *
 *  `sizeColumnsToFit` shrinks whatever it can when the grid is narrower than
 *  its columns, which is right for a description and wrong for a chip: a
 *  status column shaved to 60px shows a chip clipped mid-word. The two text
 *  columns flex; everything else is pinned and drops out entirely (via
 *  `context.minGridWidth`) when there is no room for it. */
function fixed(width: number): Partial<ColDef<Row>> {
  return { width, minWidth: width, maxWidth: width, flex: 0, suppressSizeToFit: true };
}

export function LineGrid({
  lines,
  mgmt,
  intel,
  selectedIds,
  onSelectionChange,
  onOpen,
  onSetPrice,
  onDeleteLine,
  onCreateItem }: {
  lines: Line[];
  mgmt: boolean;
  intel: Record<string, LineIntelligence>;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  onCreateItem: (id: string) => void;
}) {
  const rows: Row[] = useMemo(
    () => lines.map((l) => ({
      ...l,
      intel: intel[l.id],
      margin: marginOf(l, intel[l.id]),
    })),
    [lines, intel],
  );

  const columns = useMemo<ColDef<Row>[]>(() => [
    {
      // The line's place in the RFQ as pasted. Not sortable: a position that
      // reorders with the sort is not a position. First to go when the grid is
      // narrow — it identifies nothing that the code beside it does not.
      headerName: "#", ...fixed(52), sortable: false, filter: false,
      context: { minGridWidth: 1280 },
      cellClass: "ag-num", cellStyle: { color: "var(--color-neutral-600)" },
      valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1,
    },
    {
      field: "reqCode", headerName: "Requested item", flex: 1.2, minWidth: 175,
      cellRenderer: (p: { data?: Row }) =>
        p.data ? (
          <CodeCell code={p.data.reqCode} desc={p.data.reqDesc}>
            <Flags line={p.data} />
          </CodeCell>
        ) : null,
    },
    numeric<Row>("reqQty", "Qty", (n) => String(n), {
      ...fixed(70), filter: false, context: { minGridWidth: 980 },
    }),
    {
      field: "supplyCode", headerName: "Supply product", flex: 1.3, minWidth: 195,
      // The button inside this cell is the reason: a click that created a Zoho
      // item should not also open the drawer over the confirmation.
      context: { noRowClick: true },
      cellRenderer: (p: { data?: Row }) => {
        const l = p.data;
        if (!l) return null;
        if (!l.supplyCode) {
          return (
            <Typography variant="caption" color="text.secondary">
              {l.rel === "PIE_DOWN" ? "awaiting PIE"
                : l.rel === "AMBIGUOUS" ? "select product" : "not resolved"}
            </Typography>
          );
        }
        return (
          <CodeCell code={l.supplyCode} desc={l.supplyDesc} accent={l.substituted}>
            <Stack direction="row" spacing={0.5} useFlexGap
                   sx={{ flexWrap: "nowrap", alignItems: "center", overflow: "hidden", mt: 0.25 }}>
              {/* The relationship belongs beside the product it qualifies, not
                  in a column of its own two cells away — and a column that has
                  to hide on a laptop is a column that is not there when it
                  matters. */}
              <StatusChip
                label={l.relLabel}
                tone={relTone(l.rel)}
                dense
                tip="How the supply product relates to what the customer asked for — identical, an equivalent from another maker, or a substitute that differs in some dimension. It is not a judgement about whether to offer it."
              />
              {(l.sel === "USER" || l.sel === "MANUAL") && (
                <StatusChip
                  label={l.sel === "MANUAL" ? "manual" : "user set"}
                  tone={l.sel === "MANUAL" ? "warn" : "neutral"}
                  dense
                  tip={l.sel === "MANUAL"
                    ? "Somebody typed this product in rather than choosing a resolved candidate."
                    : "Chosen from the resolved candidates rather than taken automatically."}
                />
              )}
              {l.inBooks === false && (
                <Button
                  variant="text" size="small" sx={{ minWidth: 0, px: 0.75, fontSize: 11 }}
                  title={`Create ${l.reqCode} in Zoho Books`}
                  onClick={() => onCreateItem(l.id)}
                >
                  + Zoho
                </Button>
              )}
            </Stack>
          </CodeCell>
        );
      },
    },
    numeric<Row>("avail", "Avail.", (n) => String(n), {
      ...fixed(84), filter: false, context: { minGridWidth: 1420 },
      valueGetter: (p) => (p.data?.supplyCode && !p.data.availUnknown ? p.data.avail : null),
      valueFormatter: (p) =>
        p.data?.supplyCode ? (p.data.availUnknown ? "?" : String(p.value ?? 0)) : "—",
    }),
    numeric<Row>("shortage", "Short.", (n) => String(n), {
      ...fixed(84), filter: false, context: { minGridWidth: 1520 },
      cellClassRules: { "qb-thin": (p) => Number(p.value) > 0 },
      valueFormatter: (p) => (Number(p.value) > 0 ? String(p.value) : "—"),
    }),
    {
      field: "quoted", headerName: "Quoted ₹", ...fixed(112),
      type: "numericColumn",
      // The one editable cell on the screen, and the whole point of it: the
      // price is the human's to set. Enter commits, Tab moves down the quote,
      // Escape abandons — none of which the blur-only text input it replaces
      // could do.
      editable: true,
      cellClass: "ag-num qb-editable",
      context: { noRowClick: true },
      headerTooltip: "The rate you are quoting. Nothing pre-fills it — the "
        + "engine resolves the product and computes the context; the number is "
        + "yours.",
      valueParser: (p) => {
        const raw = String(p.newValue ?? "").replace(/[^0-9.]/g, "");
        return raw === "" ? null : Number.parseFloat(raw);
      },
      valueFormatter: (p) => (p.value == null ? "—" : money(Number(p.value))),
    },
    numeric<Row>("lineTotal", "Line total", money, {
      ...fixed(120), filter: false, context: { minGridWidth: 1180 },
    }),
    ...(mgmt
      ? [numeric<Row>("margin", "Margin", (n) => `${(n * 100).toFixed(1)}%`, {
          ...fixed(104), filter: false,
          headerTooltip: "Gross profit ÷ line revenue at the quoted rate. Shown "
            + "to managers and owners only — a salesperson's response from the "
            + "server contains no cost and no margin at all.",
          cellClassRules: {
            "qb-thin": (p) => Boolean(p.data?.intel?.blocking || p.data?.economics?.below_floor),
          },
          tooltipValueGetter: (p) =>
            p.data && marginIsMeasured(p.data.intel)
              ? "From this item's recorded purchase cost — bill lines actually synced from the books."
              : "Derived from the catalogue cost, because no purchase cost has been synced for this item yet. Indicative only.",
        })] as ColDef<Row>[]
      : []),
    {
      headerName: "Commercial", ...fixed(128), sortable: false, filter: false,
      headerTooltip: "The most serious thing the deterministic checks found on "
        + "this line. “clear” means every check passed, not that the price is "
        + "optimal.",
      cellRenderer: (p: { data?: Row }) => <CommercialChip intel={p.data?.intel} />,
    },
    {
      headerName: "Status", ...fixed(116),
      valueGetter: (p) => p.data?.status.label ?? "",
      cellRenderer: (p: { data?: Row }) =>
        p.data ? <StatusChip label={p.data.status.label} tone={statusTone(p.data.status.kind)} /> : null,
    },
    {
      headerName: "", ...fixed(52), sortable: false, filter: false,
      resizable: false, context: { noRowClick: true },
      cellRenderer: (p: { data?: Row }) =>
        p.data ? (
          <Tooltip title={`Remove ${p.data.reqCode} from this quote`}>
            <IconButton
              size="small"
              aria-label={`Remove ${p.data.reqCode} from this quote`}
              onClick={() => onDeleteLine(p.data!.id)}
            >
              <DeleteOutlineOutlined fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : null,
    },
  ], [mgmt, onCreateItem, onDeleteLine]);

  return (
    <DataGrid<Row>
      ariaLabel="Quote lines"
      rows={rows}
      columns={columns}
      pageSize={50}
      rowHeight={66}
      // No floating filter row. The toolbar above already has the search box
      // and the state chips, and a second filter per column would cost a band
      // of height on a screen whose whole job is the rows.
      filters={false}
      getRowId={(r) => r.id}
      // A tint behind the rows that are holding the quote up. Second cue only:
      // the same fact is a chip in the Status column of the same row.
      rowClass={(r) =>
        r.flags.unresolved || r.status.kind === "technical" ? "qb-row-blocked"
          : r.substituted || r.flags.procurement || r.flags.attention ? "qb-row-attention"
            : undefined}
      selection={{ selectedIds, onChange: onSelectionChange }}
      onRowClick={(r) => onOpen(r.id)}
      onRowActivate={(r) => onOpen(r.id)}
      onCellValueChanged={(row, field, value) =>
        field === "quoted" && onSetPrice(row.id, value as number | null)}
      empty={
        <EmptyState
          title="No lines match this view"
          reason="Clear the filter, or paste an RFQ."
        />
      }
    />
  );
}
