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
import { useCallback, useMemo } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import Checkbox from "@mui/material/Checkbox";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DeleteOutlineOutlined from "@mui/icons-material/DeleteOutlineOutlined";

import type { Line, LineIntelligence } from "../types";
import { relTone, statusTone } from "../rel";
import { Labelled } from "../Tip";
import { money } from "../money";
import { DataGrid, numeric, type ColDef } from "../platform/DataGrid";
import { EmptyState, StatusChip, TOUCH, TOUCH_TARGET, type Tone } from "../platform/kit";

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
  code, desc, accent = false, wrap = false, children,
}: {
  code: React.ReactNode;
  desc?: string | null;
  /** The supply code when it is not the requested one — a substitution is the
   *  one thing on this row somebody must not read past. */
  accent?: boolean;
  /** Let both lines wrap instead of eliding. For the card rendering, where the
   *  constraint is the opposite of a grid's: height is free and there is no
   *  column beside this one to steal width from, so a truncated tool name is a
   *  loss with nothing bought by it — and a tooltip is not reachable by touch. */
  wrap?: boolean;
  children?: React.ReactNode;
}) {
  const clamp = wrap
    ? { overflowWrap: "anywhere" as const }
    : { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const };
  const description = desc ? (
    <Typography
      component="div"
      variant="caption"
      color="text.secondary"
      sx={{ display: "block", ...clamp }}
    >
      {desc}
    </Typography>
  ) : null;
  return (
    <Box sx={{ lineHeight: 1.35, py: 0.5, minWidth: 0 }}>
      <Typography
        component="div"
        sx={{
          fontFamily: "ui-monospace, monospace", fontSize: 12.5, fontWeight: 700,
          color: accent ? "var(--color-accent-800)" : "text.primary",
          ...clamp,
        }}
      >
        {code}
      </Typography>
      {description && !wrap ? (
        <Tooltip title={desc}>{description}</Tooltip>
      ) : description}
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
function Flags({ line, wrap }: { line: Line; wrap?: boolean }) {
  const flags: [string, Tone][] = [
    line.flags.unresolved ? ["unresolved", "bad" as Tone] : null,
    line.flags.procurement ? ["procurement", "warn" as Tone] : null,
    line.flags.missingBooks ? ["no Zoho item", "warn" as Tone] : null,
    line.flags.manualReview ? ["manual review", "warn" as Tone] : null,
  ].filter(Boolean) as [string, Tone][];
  if (!flags.length) return null;
  // The grid cell is a fixed 66px row, so there it stays one clipped line; the
  // card is the phone view and has the height to spare, so there the chips wrap
  // rather than silently dropping the third and fourth flag off the right edge.
  return (
    <Stack direction="row" spacing={0.5} useFlexGap
           sx={{ flexWrap: wrap ? "wrap" : "nowrap", mt: 0.25, ...(wrap ? {} : { overflow: "hidden" }) }}>
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

/** Whether this line is holding the quote up, wants a look, or neither.
 *
 *  One function because two renderings ask it: the grid turns it into a row
 *  class and the card into a background. Written twice, the two would drift and
 *  a line would be tinted on a laptop and plain on a phone.
 *
 *  Always a *second* cue. The same fact is a `Chip` in both renderings, so
 *  nothing here is colour alone. */
function lineTone(line: Line): "blocked" | "attention" | undefined {
  if (line.flags.unresolved || line.status.kind === "technical") return "blocked";
  if (line.substituted || line.flags.procurement || line.flags.attention) return "attention";
  return undefined;
}

/** The card's tint for each tone, matching `.qb-row-blocked`/`.qb-row-attention`
 *  in `styles.css` — same tokens, same mix. */
const CARD_TINT: Record<"blocked" | "attention", string> = {
  blocked: "color-mix(in srgb, var(--danger-bg) 55%, transparent)",
  attention: "color-mix(in srgb, var(--caution-bg) 45%, transparent)",
};

/** One line, as a card, for a screen too narrow to be a grid.
 *
 *  A `Card` rather than a `Paper` under `docs/ui-standards.md` §2: a quote line
 *  is a business entity with an identity — it has a requested item, a chosen
 *  supply product, a price somebody set, and it is the thing the drawer opens
 *  and the delete button removes.
 *
 *  It carries the six things the brief names — requested item, supply, qty,
 *  price, availability, status — and nothing else. The columns the grid hides
 *  on a laptop are hidden here too: `#` identifies nothing the code beside it
 *  does not, and shortage is availability restated as a subtraction.
 *
 *  **The price is a real field, not a cell that becomes one.** ag-grid's
 *  editable cell is the right control with a keyboard and the wrong one with a
 *  thumb: it needs a second tap to enter edit mode, and Escape and Tab — the
 *  two things that make it good on a desktop — have no touch equivalent. So
 *  this is a `TextField`, always open, committing on blur and on Enter.
 */
function LineCard({
  line, intel, mgmt, selected, onToggle, onOpen, onSetPrice,
  onDeleteLine, onCreateItem, onConfirmReading,
}: {
  line: Line;
  intel?: LineIntelligence;
  mgmt: boolean;
  selected: boolean;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  onCreateItem: (id: string) => void;
  onConfirmReading: (id: string) => void;
}) {
  const margin = marginOf(line, intel);
  const tone = lineTone(line);
  const commit = (raw: string) => {
    const v = raw.replace(/[^0-9.]/g, "");
    const next = v === "" ? null : Number.parseFloat(v);
    if (next !== line.quoted) onSetPrice(line.id, Number.isNaN(next!) ? null : next);
  };

  return (
    <Card
      variant="outlined"
      role="listitem"
      data-line-card={line.id}
      sx={{
        p: 1.5,
        bgcolor: tone ? CARD_TINT[tone] : undefined,
      }}
    >
      <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
        <Checkbox
          checked={selected}
          onChange={() => onToggle(line.id)}
          slotProps={{ input: { "aria-label": `Select ${line.reqCode}` } }}
          sx={{ ...TOUCH, mt: -0.5 }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {/* The whole heading is the way into the line's supply options — the
              card's equivalent of the grid's row click. A button, so it is
              reachable by keyboard and announces itself as one. */}
          <Box
            component="button"
            type="button"
            onClick={() => onOpen(line.id)}
            aria-label={`Open supply options for ${line.reqCode}`}
            sx={{
              ...TOUCH, display: "block", width: "100%", textAlign: "left",
              background: "none", border: 0, p: 0, cursor: "pointer", font: "inherit",
            }}
          >
            <CodeCell code={line.reqCode} desc={line.reqDesc} wrap>
              <Flags line={line} wrap />
            </CodeCell>
          </Box>
        </Box>
        <IconButton
          aria-label={`Remove ${line.reqCode} from this quote`}
          onClick={() => onDeleteLine(line.id)}
          sx={TOUCH}
        >
          <DeleteOutlineOutlined fontSize="small" />
        </IconButton>
      </Stack>

      <Field label="Supply product">
        {line.supplyCode ? (
          <CodeCell code={line.supplyCode} desc={line.supplyDesc}
                    accent={line.substituted} wrap>
            <Stack direction="row" spacing={0.5} useFlexGap
                   sx={{ flexWrap: "wrap", alignItems: "center", mt: 0.5 }}>
              <StatusChip label={line.relLabel} tone={relTone(line.rel)} dense />
              {(line.sel === "USER" || line.sel === "MANUAL") && (
                <StatusChip label={line.sel === "MANUAL" ? "manual" : "user set"}
                            tone={line.sel === "MANUAL" ? "warn" : "neutral"} dense />
              )}
              {line.inBooks === false && (
                <Button size="small" sx={TOUCH} onClick={() => onCreateItem(line.id)}>
                  + Create in Zoho
                </Button>
              )}
            </Stack>
          </CodeCell>
        ) : (
          <Typography variant="body2" color="text.secondary">
            {line.rel === "PIE_DOWN" ? "awaiting PIE"
              : line.rel === "AMBIGUOUS" ? "select product" : "not resolved"}
          </Typography>
        )}
      </Field>

      {/* Qty and availability read together — "twenty asked for, forty on the
          shelf" is one fact — so they share a row rather than stacking. */}
      <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
        <Field label="Qty" inline>
          <Typography variant="body2">{line.reqQty}</Typography>
        </Field>
        <Field label="Available" inline>
          <Typography variant="body2">
            {!line.supplyCode ? "—" : line.availUnknown ? "?" : line.avail}
            {line.shortage !== null && line.shortage > 0
              ? ` · short ${line.shortage}` : ""}
          </Typography>
        </Field>
      </Stack>

      <Stack direction="row" spacing={1.5}
             sx={{ mt: 1.5, alignItems: "flex-end", flexWrap: "wrap", rowGap: 1 }}>
        <TextField
          // Says whose number is in the field, in the label, because the card
          // has no header row to carry it and no tooltip a thumb can reach.
          label={line.priceSource === "LIST" ? "Quoted ₹ (list)" : "Quoted ₹"}
          size="small"
          defaultValue={line.quoted ?? ""}
          // Remounts when the server sends a different price back, which it does
          // after a bulk discount. Without it the field keeps the old number.
          key={`${line.id}-${line.quoted}`}
          placeholder="—"
          slotProps={{
            htmlInput: {
              inputMode: "decimal",
              "aria-label": `Quoted rate for ${line.reqCode}`,
              "data-line-price": line.id,
            },
          }}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          sx={{ width: 130, "& .MuiInputBase-root": TOUCH }}
        />
        <Box sx={{ pb: 0.75 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            Line total
          </Typography>
          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {money(line.lineTotal)}
          </Typography>
        </Box>
        {mgmt && margin !== null && (
          <Box sx={{ pb: 0.75 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              Margin
            </Typography>
            <Typography
              variant="body2"
              color={intel?.blocking || line.economics?.below_floor
                ? "error.main" : undefined}
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {(margin * 100).toFixed(1)}%
            </Typography>
          </Box>
        )}
      </Stack>

      <Stack direction="row" spacing={0.5} useFlexGap
             sx={{ mt: 1.5, flexWrap: "wrap", alignItems: "center" }}>
        <StatusChip label={line.status.label} tone={statusTone(line.status.kind)} />
        <CommercialChip intel={intel} />
      </Stack>

      {line.proposed && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="body2" sx={{ fontStyle: "italic" }}>
            “{line.raw}”
          </Typography>
          {line.reading ? (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              interpreted: {line.reading}
            </Typography>
          ) : null}
          <Button size="small" variant="outlined" sx={{ ...TOUCH, mt: 0.5 }}
                  onClick={() => onConfirmReading(line.id)}>
            Accept
          </Button>
        </Box>
      )}
    </Card>
  );
}

/** A label above its value, inside a card. The card's answer to a column
 *  header — without it a bare product code has nothing saying which of the two
 *  codes on the card it is. */
function Field({
  label, inline = false, children,
}: { label: string; inline?: boolean; children: React.ReactNode }) {
  return (
    <Box sx={{ mt: inline ? 0 : 1.5, minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary"
                  sx={{ display: "block", textTransform: "uppercase",
                        letterSpacing: "0.06em" }}>
        {label}
      </Typography>
      {children}
    </Box>
  );
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
  onCreateItem,
  onConfirmReading }: {
  lines: Line[];
  mgmt: boolean;
  intel: Record<string, LineIntelligence>;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  onCreateItem: (id: string) => void;
  /** Accept one line's reading. One at a time by design — see
   *  store.confirm_reading. */
  onConfirmReading: (id: string) => void;
}) {
  // Toggling one line's selection, for the card rendering. The grid speaks
  // "here is the whole selected set"; a card has one checkbox and knows only
  // about itself, so the set is edited here rather than reconstructed in the
  // card from a list it would have to be handed.
  const toggle = useCallback((id: string) => {
    onSelectionChange(selectedIds.includes(id)
      ? selectedIds.filter((x) => x !== id)
      : [...selectedIds, id]);
  }, [selectedIds, onSelectionChange]);

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
                  variant="text" size="small"
                  // The 66px row has the height for a full tap target; the
                  // width is the scarce thing, so `minWidth` is the one part of
                  // `TOUCH` this cannot take.
                  sx={{ minHeight: TOUCH_TARGET, minWidth: 0, px: 0.75, fontSize: 11 }}
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
      field: "quoted", headerName: "Quoted ₹", ...fixed(124),
      type: "numericColumn",
      // The one editable cell on the screen, and the whole point of it: the
      // price is the human's to set. Enter commits, Tab moves down the quote,
      // Escape abandons — none of which the blur-only text input it replaces
      // could do.
      editable: true,
      cellClass: "ag-num qb-editable",
      context: { noRowClick: true },
      headerTooltip: "The rate you are quoting. A resolved line opens at the "
        + "catalogue rate so a long tender is not a column of typing — that is "
        + "a starting point, marked “list”, not a recommendation. Type over it "
        + "and the mark goes.",
      valueParser: (p) => {
        const raw = String(p.newValue ?? "").replace(/[^0-9.]/g, "");
        return raw === "" ? null : Number.parseFloat(raw);
      },
      // A rate nobody has agreed to, marked as such. It reads exactly like a
      // considered price otherwise, and on a fresh RFQ every line is one: four
      // lines arrived priced, the summary bar showed a Quotation total, and the
      // screen's own subtitle said "nothing is priced for you".
      cellRenderer: (p: { data?: Row; value?: number | null }) => {
        if (p.value == null) return "—";
        return (
          <Stack direction="row" spacing={0.5}
                 sx={{ alignItems: "center", justifyContent: "flex-end" }}>
            <span>{money(Number(p.value))}</span>
            {p.data?.priceSource === "LIST" && (
              <StatusChip
                label="list" tone="neutral" dense
                tip="The catalogue rate this line opened at. Nobody has priced it yet."
              />
            )}
          </Stack>
        );
      },
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
    // Only while something is unconfirmed. A permanently empty column is width
    // spent on a state the quote is usually not in.
    ...(lines.some((l) => l.proposed) ? [{
      headerName: "Read from the message",
      minWidth: 300, flex: 1, sortable: false, filter: false,
      context: { noRowClick: true },
      headerTooltip: "What the customer actually wrote, beside what was read "
        + "from it. Check the two match — a grade suffix is the difference "
        + "between two different tools — then accept the line.",
      cellRenderer: (p: { data?: Row }) => {
        if (!p.data?.proposed) return null;
        return (
          <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 0.5 }}>
            <Stack sx={{ minWidth: 0 }}>
              {/* The customer's words, not the tidied version. Confirming
                  against the reading would be confirming against itself. */}
              <Typography variant="body2" sx={{ fontStyle: "italic" }} noWrap
                          title={p.data.raw}>
                “{p.data.raw}”
              </Typography>
              {p.data.reading ? (
                <Typography variant="caption" color="text.secondary" noWrap
                            title={p.data.reading}>
                  interpreted: {p.data.reading}
                </Typography>
              ) : null}
            </Stack>
            <Button size="small" variant="outlined"
                    onClick={() => onConfirmReading(p.data!.id)}>
              Accept
            </Button>
          </Stack>
        );
      },
    } as ColDef<Row>] : []),
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
              sx={TOUCH}
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
      rowClass={(r) => {
        const tone = lineTone(r);
        return tone ? `qb-row-${tone}` : undefined;
      }}
      selection={{ selectedIds, onChange: onSelectionChange }}
      // Below 700px the columns above do not fit and the rate field lands off
      // screen — see DataGridProps.renderNarrow. The grid is untouched above it.
      renderNarrow={(r) => (
        <LineCard
          key={r.id}
          line={r}
          intel={r.intel}
          mgmt={mgmt}
          selected={selectedIds.includes(r.id)}
          onToggle={toggle}
          onOpen={onOpen}
          onSetPrice={onSetPrice}
          onDeleteLine={onDeleteLine}
          onCreateItem={onCreateItem}
          onConfirmReading={onConfirmReading}
        />
      )}
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
