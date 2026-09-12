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
 *
 * **Five columns, and the problems annotate their own row.** It had eleven —
 * requested item, supply product, stock, rate, recommended, line total, margin,
 * commercial, what-was-read, status, remove — and four of them were columns of
 * chips describing states. Nine columns of that kind have one cost that does
 * not show up in a screenshot: cost, margin, availability and shortfall matter
 * on four rows out of fourteen, and rendering them on all fourteen buys the
 * reader a wall to scan. For a salesperson the two economics columns were a
 * column of em-dashes, because the server sends no cost at all.
 *
 * So: what every line has is a column (#, item, qty, rate, line total), the
 * economics are a toggle for whoever has them, and everything that is true of
 * *some* lines is a strip under the line it is true of, carrying the fix as a
 * button. `lineProblems.ts` decides what a problem is; this draws it.
 */
import { useCallback, useMemo, useRef } from "react";
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
import { problemsFor, type Fix, type LineProblem, type ProblemTone } from "./lineProblems";
import { relTone } from "../rel";
import { money } from "../money";
import { DataGrid, numeric, type ColDef } from "../platform/DataGrid";
import { EmptyState, StatusChip, TOUCH, type Tone } from "../platform/kit";

/** One line, joined to its assessment.
 *
 *  Joined into the row rather than looked up inside a cell renderer, because a
 *  column resolved in a renderer displays correctly and **sorts on nothing** —
 *  and "which lines are thinnest?" is the first thing anybody asks of a priced
 *  quote. */
type Row = Line & {
  /** The line's place in the RFQ as pasted, counted over the lines alone.
   *
   *  Not ag-grid's `rowIndex`: a strip is a row too, so a quote whose second
   *  line has a problem numbered its lines 1, 2, 4. */
  n: number;
  intel?: LineIntelligence;
  margin: number | null;
  /** Lifted out of `economics` so the column can sort on it. A value read
   *  inside a cell renderer displays and sorts on nothing — the same reason
   *  `margin` is here. Absent for a role the server sends no cost to. */
  cost: number | null;
  problems: LineProblem[];
};

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
function Flags({ line, wrap, systemShort }:
               { line: Line; wrap?: boolean; systemShort: string }) {
  const flags: [string, Tone][] = [
    line.flags.unresolved ? ["unresolved", "bad" as Tone] : null,
    line.flags.procurement ? ["procurement", "warn" as Tone] : null,
    // Named for the system this quote's books are in, not for Zoho. The chip
    // is dense and shares a 66px row, so it takes the short name.
    line.flags.missingBooks ? [`not in ${systemShort}`, "warn" as Tone] : null,
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
  line, intel, problems, mgmt, econ, readOnly = false, systemShort,
  selected, onToggle, onOpen, onSetPrice, onDeleteLine, onFix,
}: {
  line: Line;
  intel?: LineIntelligence;
  /** The same problems the grid draws under the row, drawn inside the card.
   *  One model, two renderings — a phone that disagreed with a laptop about
   *  what is wrong with a line would be the worst of both. */
  problems: LineProblem[];
  mgmt: boolean;
  econ: boolean;
  /** The reader may not change this quote — see `Quote.canEdit`. The rate
   *  field and the remove control are withheld, because a control that only
   *  ever answers 403 is worse than none. */
  readOnly?: boolean;
  systemShort: string;
  selected: boolean;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  onFix: (line: Line, fix: Fix) => void;
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
      sx={{ p: 1.5, bgcolor: tone ? CARD_TINT[tone] : undefined }}
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
            <CodeCell
              code={line.supplyCode ?? line.reqCode}
              desc={line.supplyCode ? line.supplyDesc : line.reqDesc}
              accent={line.substituted}
              wrap
            >
              <Stack direction="row" spacing={0.5} useFlexGap
                     sx={{ flexWrap: "wrap", alignItems: "center", mt: 0.5 }}>
                {line.supplyCode && line.supplyCode !== line.reqCode && (
                  <StatusChip label={line.relLabel} tone={relTone(line.rel)} dense />
                )}
                {(line.sel === "USER" || line.sel === "MANUAL") && (
                  <StatusChip label={line.sel === "MANUAL" ? "manual" : "user set"}
                              tone={line.sel === "MANUAL" ? "warn" : "neutral"} dense />
                )}
                <Flags line={line} wrap systemShort={systemShort} />
              </Stack>
            </CodeCell>
          </Box>
          {line.supplyCode && line.supplyCode !== line.reqCode && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              asked for {line.reqCode}
            </Typography>
          )}
        </Box>
        {!readOnly && (
          <IconButton
            aria-label={`Remove ${line.reqCode} from this quote`}
            onClick={() => onDeleteLine(line.id)}
            sx={TOUCH}
          >
            <DeleteOutlineOutlined fontSize="small" />
          </IconButton>
        )}
      </Stack>

      <Stack direction="row" spacing={1.5}
             sx={{ mt: 1.5, alignItems: "flex-end", flexWrap: "wrap", rowGap: 1 }}>
        <Field label="Qty" inline>
          <Typography variant="body2">{line.reqQty}</Typography>
        </Field>
        <TextField
          // Says whose number is in the field, in the label, because the card
          // has no header row to carry it and no tooltip a thumb can reach.
          label={line.priceSource === "LIST" ? "Rate (list)" : "Rate"}
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
          disabled={readOnly}
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
        {mgmt && econ && margin !== null && (
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

      {/* What is wrong with this line, and what fixes it — the card's copy of
          the strip the grid draws under the row. Wrapping rather than one
          line: a phone has the height and not the width. */}
      {problems.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 1.5 }}>
          {problems.map((problem) => (
            <ProblemStrip
              key={problem.key}
              problem={problem}
              dense
              onFix={(fix) => onFix(line, fix)}
            />
          ))}
        </Stack>
      )}
    </Card>
  );
}

/** How tall one strip is. Fixed rather than measured — see
 *  `DataGridProps.rowDetailHeight` — and enough for a title, a sentence and a
 *  row of buttons at full tap height. */
const STRIP_HEIGHT = 64;

const STRIP_TINT: Record<ProblemTone, string> = {
  bad: "var(--danger-bg)",
  warn: "var(--caution-bg)",
  info: "var(--color-neutral-200)",
};

/** One problem, under the line it is about, with what would fix it.
 *
 *  The buttons are the whole point: a strip that only said "below cost on 400
 *  pieces" would be the `Commercial` chip it replaces, moved. The fix is the
 *  specific thing — take the recommended rate, ask for the approval, choose
 *  this candidate — and it acts on the line it is drawn under. */
function ProblemStrip({
  problem, onFix, dense = false,
}: {
  problem: LineProblem;
  onFix: (fix: Fix) => void;
  /** Inside a card rather than under a grid row: the strip wraps instead of
   *  holding one line, because a phone has no width to hold it. */
  dense?: boolean;
}) {
  return (
    <Box
      sx={{
        display: "flex", alignItems: "center", gap: 1.5, rowGap: 0.5,
        flexWrap: "wrap",
        height: dense ? "auto" : STRIP_HEIGHT,
        px: dense ? 1 : 2, py: dense ? 1 : 0,
        bgcolor: STRIP_TINT[problem.tone],
        borderLeft: "2px solid",
        borderColor: problem.tone === "bad" ? "error.main"
                   : problem.tone === "warn" ? "warning.main" : "divider",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 220 }}>
        <Typography variant="subtitle2" sx={{ lineHeight: 1.3 }}>{problem.title}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap={!dense}
                    sx={{ display: "block" }} title={problem.detail}>
          {problem.detail}
        </Typography>
      </Box>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
        {problem.fixes.map((f) => (
          <Button
            key={f.label}
            size="small"
            variant={f.primary ? "contained" : "outlined"}
            sx={{ ...TOUCH, minHeight: 36 }}
            onClick={() => onFix(f.fix)}
          >
            {f.label}
          </Button>
        ))}
      </Stack>
    </Box>
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
  econ = true,
  readOnly = false,
  systemShort,
  intel,
  approvalPendingFor,
  selectedIds,
  onSelectionChange,
  onOpen,
  onSetPrice,
  onDeleteLine,
  onFix }: {
  lines: Line[];
  mgmt: boolean;
  /** Whether the two economics columns are showing. Only ever consulted where
   *  `mgmt` already holds — a salesperson has no cost to toggle. */
  econ?: boolean;
  /** The reader may not change this quote — see `Quote.canEdit`. The rate
   *  cell stops being editable and the per-line controls are withheld. */
  readOnly?: boolean;
  /** What this quote's books are called at the width a grid cell has for it —
   *  see `Quote.systemShort`. The grid never decides what to call somebody's
   *  ERP. */
  systemShort: string;
  intel: Record<string, LineIntelligence>;
  /** Whether an approval has already been asked for on this line, so the strip
   *  says it is waiting instead of offering to ask a second time. */
  approvalPendingFor?: (lineId: string) => boolean;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onSetPrice: (id: string, price: number | null) => void;
  onDeleteLine: (id: string) => void;
  /** A fix pressed on one line's strip. The screen owns what each one does —
   *  see `lineProblems.Fix` — because every one of them is an existing action
   *  of the builder's, reached from the row instead of from a drawer. */
  onFix: (line: Line, fix: Fix) => void;
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

  // Read inside the callback rather than closed over — the same treatment
  // `DataGridImpl` gives `getRowId`, and for a sharper reason here: the strip
  // renderer is part of the grid's row data, so an identity that changed on
  // every parent render would rebuild the rows under somebody typing a rate.
  const fixRef = useRef(onFix);
  fixRef.current = onFix;

  const renderRowDetail = useCallback((r: Row) => (r.problems.length ? (
    <Stack>
      {r.problems.map((problem) => (
        <ProblemStrip
          key={problem.key}
          problem={problem}
          onFix={(fix) => fixRef.current(r, fix)}
        />
      ))}
    </Stack>
  ) : null), []);

  const rowDetailHeight = useCallback(
    (r: Row) => r.problems.length * STRIP_HEIGHT, []);

  const rows: Row[] = useMemo(
    () => lines.map((l, i) => ({
      ...l,
      n: i + 1,
      intel: intel[l.id],
      margin: marginOf(l, intel[l.id]),
      cost: intel[l.id]?.economics?.unit_cost ?? l.economics?.cost ?? null,
      problems: problemsFor(l, intel[l.id], {
        mgmt, systemShort, readOnly,
        approvalPending: approvalPendingFor?.(l.id) ?? false,
      }),
    })),
    [lines, intel, mgmt, systemShort, readOnly, approvalPendingFor],
  );

  const columns = useMemo<ColDef<Row>[]>(() => [
    {
      // The line's place in the RFQ as pasted. Not sortable: a position that
      // reorders with the sort is not a position.
      headerName: "#", ...fixed(46), sortable: false, filter: false,
      context: { minGridWidth: 900 },
      cellClass: "ag-num", cellStyle: { color: "var(--color-neutral-600)" },
      field: "n",
    },
    {
      /* What is being quoted, and what was asked for.
       *
       * Two columns until now — `Requested item` and `Supply product` — side by
       * side and identical on most rows, because most lines resolve to the
       * thing the customer named. They are one cell: the product that would go
       * out, with the request under it where the two differ, and the
       * relationship chip beside it where it is not an exact match. A reader
       * scanning for "what am I quoting" reads one column; a reader checking a
       * substitution still has both codes in front of them. */
      field: "supplyCode", headerName: "Item", flex: 1.6, minWidth: 240,
      context: { noRowClick: true },
      cellRenderer: (p: { data?: Row }) => {
        const l = p.data;
        if (!l) return null;
        const differs = Boolean(l.supplyCode) && l.supplyCode !== l.reqCode;
        return (
          <CodeCell
            code={l.supplyCode ?? l.reqCode}
            desc={l.supplyCode ? l.supplyDesc : l.reqDesc}
            accent={l.substituted}
          >
            <Stack direction="row" spacing={0.5} useFlexGap
                   sx={{ flexWrap: "nowrap", alignItems: "center",
                         overflow: "hidden", mt: 0.25 }}>
              {differs && (
                <StatusChip
                  label={l.relLabel}
                  tone={relTone(l.rel)}
                  dense
                  tip={`Asked for ${l.reqCode}. How the supply product relates to it — identical, an equivalent from another maker, or a substitute that differs in some dimension. It is not a judgement about whether to offer it.`}
                />
              )}
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
              <Flags line={l} systemShort={systemShort} />
            </Stack>
          </CodeCell>
        );
      },
    },
    numeric<Row>("reqQty", "Qty", (n) => String(n), {
      ...fixed(70), filter: false,
    }),
    {
      field: "quoted", headerName: "Rate", ...fixed(132),
      type: "numericColumn",
      // The one editable cell on the screen, and the whole point of it: the
      // price is the human's to set. Enter commits, Tab moves down the quote,
      // Escape abandons.
      editable: !readOnly,
      cellClass: readOnly ? "ag-num" : "ag-num qb-editable",
      context: { noRowClick: true },
      headerTooltip: "The rate you are quoting. A resolved line opens at the "
        + "catalogue rate so a long tender is not a column of typing — that is "
        + "a starting point, marked “list”, not a recommendation. Type over it "
        + "and the mark goes.",
      valueParser: (p) => {
        const raw = String(p.newValue ?? "").replace(/[^0-9.]/g, "");
        return raw === "" ? null : Number.parseFloat(raw);
      },
      cellRenderer: (p: { data?: Row; value?: number | null }) => {
        const l = p.data;
        /* What the platform would price this line at, under the rate rather
         * than in a column of its own.
         *
         * It was a column, and it was the right thing to add — a salesperson
         * has no floor, no cost and no margin by design, so without it they
         * price against nothing but this customer's history. It is still the
         * answer to "am I under on this line", and that question is asked *of
         * the rate*, so it belongs against the rate and only where the answer
         * is yes. On the lines that are already at or above it, it is a column
         * of numbers nobody reads. */
        const under = l && l.recommended !== null
          && (p.value == null || Number(p.value) < l.recommended);
        return (
          <Stack sx={{ alignItems: "flex-end", lineHeight: 1.2 }}>
            <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
              <span>{p.value == null ? "—" : money(Number(p.value))}</span>
              {l?.priceSource === "LIST" && (
                <StatusChip
                  label="list" tone="neutral" dense
                  tip="The catalogue rate this line opened at. Nobody has priced it yet."
                />
              )}
            </Stack>
            {under && (
              <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1.2 }}
                          title="What this line would be priced at from this customer's history and the organisation's policy. Guidance — the rate that goes out is the one you set.">
                rec {money(l!.recommended)}
              </Typography>
            )}
          </Stack>
        );
      },
    },
    numeric<Row>("lineTotal", "Line total", money, {
      ...fixed(120), filter: false,
    }),
    /* The economics, for a reader who has them and has asked to see them.
     *
     * Behind a toggle rather than always on. They are the two columns a manager
     * wants while pricing and nobody wants while reading a quote back, and a
     * salesperson does not have them at all — the server sends no cost, so for
     * that role these were two columns of em-dashes explaining nothing. */
    ...(mgmt && econ
      ? [
          numeric<Row>("cost", "Cost", money, {
            ...fixed(104), filter: false,
            headerTooltip: "The landed cost this line's margin is measured "
              + "against — the recorded purchase cost where one has synced, the "
              + "catalogue cost otherwise.",
          }),
          numeric<Row>("margin", "Margin", (n) => `${(n * 100).toFixed(1)}%`, {
            ...fixed(96), filter: false,
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
          }),
        ] as ColDef<Row>[]
      : []),
    {
      headerName: "", ...fixed(52), sortable: false, filter: false,
      resizable: false, context: { noRowClick: true },
      cellRenderer: (p: { data?: Row }) =>
        p.data && !readOnly ? (
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
  ], [mgmt, econ, readOnly, systemShort, onDeleteLine]);

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
      /* What is wrong with this line, under this line. See the header: this is
         the half of the screen that used to be four columns of chips and a
         refusal at the end of the journey. */
      renderRowDetail={renderRowDetail}
      rowDetailHeight={rowDetailHeight}
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
          problems={r.problems}
          mgmt={mgmt}
          econ={econ}
          readOnly={readOnly}
          systemShort={systemShort}
          selected={selectedIds.includes(r.id)}
          onToggle={toggle}
          onOpen={onOpen}
          onSetPrice={onSetPrice}
          onDeleteLine={onDeleteLine}
          onFix={onFix}
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
