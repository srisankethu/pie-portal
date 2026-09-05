// The quotes nobody wrote an outcome on — and the one place to say why.
//
// Roughly three quarters of the estimates this book raises end in no recorded
// way at all: ~215 on the live SLS book. `ingestion.normalize` refuses to read
// silence as a loss, which is correct and which leaves a pile nobody can be
// asked about all at once; `commercial/insight/unrecorded` ranks that pile;
// `quote_service.set_outcome` has accepted an ERP quote's own reference for a
// while. **Nothing asked the question.** Six losses are on record across the
// whole book, and no screen existed on which a seventh could be recorded, so
// the win/loss analysis beside this one is reading a sample of six.
//
// This is that screen, and the loss reason is the whole point of it. A win rate
// built from quotes people bothered to answer is a rate about diligence; the
// reason behind a loss is the only thing that separates a pricing problem from
// a stock problem, and the person who knows it is the one looking at this row.
//
// **Every number here was computed by the server, and none of them is cost.**
// `value` is the quote's own selling total — the figure that went to the
// customer — which is why a salesperson may read this list at all, scoped by
// the server to their own accounts. No margin, no cost, and no count that
// answers a margin question.
//
// **Two absences are rendered as absences, never as zero.** A quote with no
// expiry date has an *unanswerable* age, not an age of nothing, and a quote
// with no total is real quoting activity whose ranking key is missing rather
// than a quote worth ₹0. `unrecorded.py` refuses to fold either, `types.ts`
// types both as `| null` so `?? 0` cannot slip in, and `ageLabel`/`valueLabel`
// below are where that refusal becomes words on a screen. Both are exported
// and both are tested, because the grid cell and the narrow card must say the
// same thing and a helper is the only way to guarantee they do.
//
// **The group order is the server's.** `group_order` is published by
// `/insight/unrecorded-quotes` precisely so a reader can recompute the ranking
// rather than infer it; iterating it here rather than writing the three names
// out is what stops the screen and the module disagreeing the day the ordering
// argument changes.

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useMemo, useState } from "react";

import { intelligence } from "../intelligence";
import { money } from "../money";
import { formatDate } from "../when";
import { papi } from "./api";
import { actionColumn, DataGrid, numeric, text } from "./DataGrid";
import type { ColDef } from "./DataGrid";
import {
  CurrencyValue, EmptyState, ErrorState, FilterChip, FilterPanel, LoadingState,
  MetricCard, SectionHeader, StatusChip, TileGrid, TOUCH, type Tone,
} from "./kit";
import { RecordOutcomeDialog } from "./RecordOutcomeDialog";
import type {
  PlatformSession, UnrecordedQuote, UnrecordedQuoteGroup,
} from "./types";
import { useInsight } from "./viz/useInsight";

/** How much of the pile comes back in one request.
 *
 *  The server caps this at 500 and takes its counts over the whole list either
 *  way, so raising it shows more rows and never moves the headline. A hundred
 *  is a morning's worth of asking rather than an afternoon's, and the headline
 *  says what it is not showing — which is the point of `build()` returning
 *  everything and the router slicing it. */
const PAGE = 100;

/** The three piles, in this client's words. The codes are the server's.
 *
 *  Typed `Record<string, …>` rather than by `UnrecordedQuoteGroup`, and read
 *  with a fallback to the raw code: the server owns this vocabulary and may
 *  publish a fourth group before this file learns its name, and a screen that
 *  renders `undefined` for it would be worse than one that renders the code. */
const GROUP_LABEL: Record<string, string> = {
  PAST_EXPIRY: "Lapsed",
  EXPIRY_NOT_RECORDED: "No expiry recorded",
  STILL_OPEN: "Still open",
};

const GROUP_TONE: Record<string, Tone> = {
  PAST_EXPIRY: "bad",
  EXPIRY_NOT_RECORDED: "warn",
  STILL_OPEN: "neutral",
};

const GROUP_MEANING: Record<string, string> = {
  PAST_EXPIRY:
    "An expiry date is on record and the day has passed. The customer has run "
    + "out of time to answer, so the answer exists and only a person has it.",
  EXPIRY_NOT_RECORDED:
    "No expiry date on record, so how long this has been sitting cannot be "
    + "computed at all. Not known to be lapsed, and not known to be live — "
    + "filed as neither rather than ranked as one.",
  STILL_OPEN:
    "An expiry date on record, still ahead. The offer is live and chasing it "
    + "is a different conversation.",
};

/** How old the lapse is, or why there is no such thing for this row.
 *
 *  Three answers, and the two that are not a number are the reason this is a
 *  function rather than a format string. A missing expiry date does not make a
 *  quote zero days lapsed — it makes the age unanswerable, and a `0` there
 *  would file the row with the freshest quotes in the book, which is the
 *  `sum(… or 0)` failure wearing a sort key. A future expiry is not a lapse
 *  either, and calling it one would invent a problem.
 *
 *  The server has already made both distinctions: `days_past_expiry` is `null`
 *  in both cases and `group` says which. This reads them rather than
 *  recomputing them from the dates, so the words and the ranking cannot
 *  disagree. */
export function ageLabel(row: UnrecordedQuote): string {
  if (row.days_past_expiry !== null) {
    return `${row.days_past_expiry} day${row.days_past_expiry === 1 ? "" : "s"}`;
  }
  return row.expires_on === null ? "No expiry set" : "Not lapsed";
}

/** The quote's own selling total, or the fact that the ERP gave none.
 *
 *  Not `money(0)`, and not a blank cell either: this row is real quoting
 *  activity with a real customer on the other end, counted in `count` and
 *  deliberately left out of `value_at_stake`. What is missing is the number,
 *  not the quote. */
export function valueLabel(row: UnrecordedQuote): string {
  return row.value === null ? "No total on the quote" : money(row.value);
}

/** The same three answers as a phrase, for prose rather than for a cell.
 *
 *  Two renderings of one fact and not two facts: the grid column is a column,
 *  so it says "No expiry set", and a sentence cannot say "No expiry set past
 *  expiry". Both read `days_past_expiry` and `expires_on` the same way, and
 *  neither invents a zero. */
export function agePhrase(row: UnrecordedQuote): string {
  if (row.days_past_expiry !== null) {
    return `lapsed ${ageLabel(row)} ago`;
  }
  return row.expires_on === null
    ? "no expiry date on record"
    : `not lapsed — expires ${formatDate(row.expires_on)}`;
}

/** What the ERP saw, stated as a fact or as the absence of one.
 *
 *  Never "the customer never opened it". `client_viewed_at` records an open the
 *  ERP happened to see; its absence spans "never sent", "tracking off" and
 *  "they read a forwarded PDF", and only the first is about the customer. */
function openedLabel(row: UnrecordedQuote): string {
  return row.opened_at === null
    ? "No open recorded"
    : `Opened ${formatDate(row.opened_at)}`;
}

export function UnrecordedQuotesScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "unrecorded-quotes", () => papi.unrecordedQuotes(session.token, PAGE),
    [session.token]);

  const { enqueueSnackbar } = useSnackbar();
  const [group, setGroup] = useState<UnrecordedQuoteGroup | null>(null);
  const [recording, setRecording] = useState<UnrecordedQuote | null>(null);

  const quotes = useMemo(
    () => (data?.quotes ?? []).filter((q) => group === null || q.group === group),
    [data, group]);

  const columns = useMemo<ColDef<UnrecordedQuote>[]>(() => [
    {
      ...text<UnrecordedQuote>("customer_label", "Customer", { minWidth: 200 }),
    },
    {
      ...text<UnrecordedQuote>("number", "Quote", { flex: 0.5, minWidth: 130 }),
      valueFormatter: (p: { value?: unknown }) => String(p.value ?? "—"),
    },
    {
      field: "group" as never, headerName: "Pile", width: 190,
      filter: "agTextColumnFilter",
      headerTooltip: "Which of the server's three piles this sits in. The order "
        + "of the list is the server's too — see the chips above it.",
      cellRenderer: (p: { data?: UnrecordedQuote }) => (p.data ? (
        <StatusChip
          label={GROUP_LABEL[p.data.group] ?? p.data.group}
          tone={GROUP_TONE[p.data.group] ?? "neutral"}
          tip={GROUP_MEANING[p.data.group]}
          dense
        />
      ) : null),
    },
    // The two ranking keys sit next to the pile they rank inside, before the
    // ERP's own word for the quote. The order used to put both chips first and
    // the age and the value fifth and sixth, so the columns the server sorts on
    // — the two the headline is about — were the ones a reader met last.
    {
      ...numeric<UnrecordedQuote>("days_past_expiry", "Lapsed for",
                                  (v) => String(v), { width: 150 }),
      headerTooltip: "Days since the offer lapsed. Blank is never zero: a quote "
        + "with no expiry date on record has no such age, and one whose expiry "
        + "is still ahead has not lapsed at all.",
      cellRenderer: (p: { data?: UnrecordedQuote }) =>
        p.data ? ageLabel(p.data) : null,
    },
    {
      ...numeric<UnrecordedQuote>("value", "Quoted", money, { width: 160 }),
      headerTooltip: "The quote's own selling total — what went to the customer. "
        + "A quote the ERP gave no total for says so; it is not worth nothing.",
      cellRenderer: (p: { data?: UnrecordedQuote }) =>
        p.data ? valueLabel(p.data) : null,
    },
    {
      field: "source_status" as never, headerName: "ERP says", width: 130,
      filter: "agTextColumnFilter",
      headerTooltip: "The source system's own word for this quote, verbatim. "
        + "None of these words is a win or a loss, which is why the row is here.",
      cellRenderer: (p: { data?: UnrecordedQuote }) => (p.data ? (
        <StatusChip label={p.data.source_status} tone="info" dense />
      ) : null),
    },
    {
      ...text<UnrecordedQuote>("raised_on", "Raised", { flex: 0.5, minWidth: 120 }),
      valueFormatter: (p: { value?: unknown }) => formatDate(String(p.value ?? "")),
    },
    // Through `actionColumn` rather than written out: no sort, no filter, and
    // `context.noRowClick` so pressing the button does not also fire the row
    // handler. This column is why that helper exists — it had two of the three
    // and a comment claiming the third, and because both handlers happened to
    // open the same dialog nothing looked wrong. The next control put here
    // would have inherited a silent second press, so the trio is one call now.
    actionColumn<UnrecordedQuote>(
      (p: { data?: UnrecordedQuote }) => (p.data ? (
        <Button size="small" onClick={() => setRecording(p.data!)}>
          Record…
        </Button>
      ) : null),
      { width: 150 },
    ),
  ], []);

  if (loading) {
    return <LoadingState rows={5} height={72} label="Reading the unanswered pile…" />;
  }
  if (error) {
    return <ErrorState title="The unanswered quotes did not load"
                       error={error} onRetry={reload} />;
  }

  const total = data?.count ?? 0;
  const listed = data?.listed ?? 0;
  const groups = data?.group_order ?? [];

  return (
    <Stack spacing={3}>
      <SectionHeader
        title="Quotes nobody answered"
        sub={"Every quote this book raised that has neither a win nor a loss "
             + "against it — silence, which is never read as a loss. Ranked by "
             + "how long each has been lapsed and how much was on it. Recording "
             + "why one was lost is the only way that fact ever enters the "
             + "numbers."}
        actions={<Button size="small" onClick={reload}>Refresh</Button>}
      />

      {/* `kit.TileGrid`, which is this row's own `Grid container` promoted to
          the kit after six screens had written it out. It emits the same
          breakpoints — the tiles used to be flex-1 boxes that went to a row at
          `sm`, which put four figures and four explanatory lines across 600px,
          and the fix is now one component rather than six copies of it. */}
      <TileGrid>
        <MetricCard
          label="Unanswered"
          value={total}
          // The whole reason `build()` returns the pile and the router slices
          // it: a headline that agreed with the visible rows would make the
          // pile look the size of the page.
          sub={listed < total
            ? `Showing the top ${listed}. The other ${total - listed} are real `
              + "and not on this page."
            : `All ${listed} are on this page.`}
          tip="Quotes with no recorded outcome. A quote the customer never
               answered is not a loss, and nothing here treats it as one."
        />
        <MetricCard
          label="Value at stake"
          value={<CurrencyValue value={data?.value_at_stake ?? null} />}
          sub={(data?.quotes_without_a_value ?? 0) > 0
            ? `${data?.quotes_without_a_value} of these carry no total and are `
              + "not in this figure"
            : "Every quote on the list carries a total"}
          tip="The sum of the quotes that carry a selling total, and of no
               others. Their own price to the customer — never cost."
        />
        <MetricCard
          label="Longest lapse"
          // `== null` catches the server's null and the not-yet-loaded
          // undefined in one test, which is the same two-line check the `sub`
          // below already made. Still an em dash and never a zero: nothing on
          // the list having an expiry date to have passed is not a lapse of
          // no days.
          value={data?.longest_lapse_days == null
            ? "—"
            : `${data.longest_lapse_days} days`}
          sub={data?.longest_lapse_days == null
            ? "Nothing on this list has an expiry date to have passed"
            : "Since the oldest offer on this list ran out"}
          tip="Measured over the whole pile, not over this page — the counts
               above it are too, which is why they can disagree with the rows."
        />
        <MetricCard
          label="Opened by the customer"
          value={data?.opened ?? 0}
          sub={`${data?.opening_not_recorded ?? 0} with no open recorded`}
          tip="An open the ERP saw. No open on record is not the same as the
               customer never opening it — the quote may never have been sent."
        />
      </TileGrid>

      {total === 0 ? (
        <EmptyState
          title="Nothing is waiting to be answered"
          // The server's own sentence: it knows whether the book holds no
          // quotes, or holds only quotes the ERP already decided, and a screen
          // that guesses between those two is a screen nobody trusts.
          reason={data?.empty_reason
            ?? "No quote on this book is missing an outcome."}
        />
      ) : (
        <>
          <FilterPanel>
            <Typography variant="overline" color="text.secondary">Pile</Typography>
            {/* Iterated from `group_order`, never written out here. The server
                publishes the order so the ranking can be recomputed rather than
                inferred, and a second copy of it in this file is one that
                disagrees the first time the ordering argument changes. */}
            <Stack direction="row" spacing={1} useFlexGap
                   role="group" aria-label="Which pile"
                   sx={{ flexWrap: "wrap", rowGap: 1 }}>
              <FilterChip
                label="All" count={total} selected={group === null}
                onClick={() => setGroup(null)}
              />
              {groups.map((g) => (
                <FilterChip
                  key={g}
                  label={GROUP_LABEL[g] ?? g}
                  count={data?.by_group?.[g] ?? 0}
                  selected={group === g}
                  onClick={() => setGroup(g)}
                />
              ))}
            </Stack>
          </FilterPanel>

          <DataGrid<UnrecordedQuote>
            ariaLabel="Quotes with no recorded outcome"
            rows={quotes}
            columns={columns}
            getRowId={(r) => r.quote_document_ref}
            pageSize={25}
            onRowClick={(r) => setRecording(r)}
            onRowActivate={(r) => setRecording(r)}
            renderNarrow={(r) => (
              <QuoteCard key={r.quote_document_ref} row={r}
                         onRecord={() => setRecording(r)} />
            )}
            // Always given. A grid that falls back to ag-grid's own "No Rows
            // To Show" has thrown away the one place this product explains why
            // something is empty — `DataGridProps.empty` says so.
            empty={
              <EmptyState
                title={group === null
                  ? "Nothing on this page"
                  : "Nothing in this pile"}
                reason={group === null
                  ? "The pile is not empty — the headline above counts it. "
                    + "Reload if this persists."
                  : `No unanswered quote on this page is filed under `
                    + `"${GROUP_LABEL[group] ?? group}". `
                    + (GROUP_MEANING[group] ?? "")}
              />
            }
          />
        </>
      )}

      {/* What every age on this page is measured against. "Lapsed for 310 days"
          is only readable next to the day it was counted from, and the server
          measures it in the organization's own timezone rather than the
          browser's — which is the whole argument `when.ts` opens with. */}
      {data && (
        <Typography variant="caption" color="text.secondary">
          Ages measured against {formatDate(data.as_of)}, in this business's own
          timezone. Silence is never read as a loss: a quote stays on this list
          until somebody says what happened to it.
        </Typography>
      )}

      <RecordOutcomeDialog
        open={recording !== null}
        title={recording
          ? `What happened to ${recording.number ?? recording.quote_document_ref}?`
          : "What happened to this quote?"}
        summary={recording ? (
          <>
            {recording.customer_label} · {valueLabel(recording)} · raised{" "}
            {formatDate(recording.raised_on)} · {agePhrase(recording)} ·{" "}
            {openedLabel(recording).toLowerCase()}.
          </>
        ) : undefined}
        // Lifted out of the end of that sentence, where it was the sixth clause
        // of a line somebody reads to identify the row. It is the one fact here
        // that cannot be taken back.
        caution="Recording an outcome is final: a decided quote cannot be
                 reopened, because the analysis that reads it has already
                 counted it."
        onClose={() => setRecording(null)}
        onRecord={async (status, lossReason, note) => {
          if (!recording) return;
          // The ERP raised this quote and the platform never priced it, so it is
          // named by the source system's own reference rather than by a
          // `quote_id` that does not exist. `documentOutcome` is the writer for
          // that id space; the refusals it raises reach the dialog as an Error
          // carrying the server's own sentence, which the dialog shows verbatim.
          await intelligence.documentOutcome(
            session.token, recording.quote_document_ref, status,
            recording.customer_label, note, lossReason);
          enqueueSnackbar(
            `${recording.number ?? recording.quote_document_ref} recorded as `
            + status.toLowerCase(),
            { variant: "success" });
          setRecording(null);
          reload();
        }}
      />
    </Stack>
  );
}

/** One quote, on a screen too narrow for a row of cells.
 *
 *  A `Card` rather than a `Paper`, per `docs/ui-standards.md` §2: a quote is a
 *  self-contained business entity with an identity you could open and act on,
 *  which is the one case the standard reserves a Card for. The Quote Builder's
 *  narrow line card is the other. */
function QuoteCard({ row, onRecord }: {
  row: UnrecordedQuote;
  onRecord: () => void;
}) {
  return (
    <Card variant="outlined" role="listitem" sx={{ p: 1.5 }}>
      <Stack spacing={1}>
        <Box>
          <Typography variant="subtitle2">{row.customer_label}</Typography>
          <Typography variant="caption" color="text.secondary">
            {row.number ?? row.quote_document_ref} · raised{" "}
            {formatDate(row.raised_on)}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
          <StatusChip
            label={GROUP_LABEL[row.group] ?? row.group}
            tone={GROUP_TONE[row.group] ?? "neutral"}
            tip={GROUP_MEANING[row.group]}
            dense
          />
          <StatusChip label={row.source_status} tone="info" dense />
        </Stack>
        <Typography variant="body2">
          {valueLabel(row)} · {agePhrase(row)}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {openedLabel(row)}
        </Typography>
        <Button variant="outlined" size="small" sx={TOUCH} onClick={onRecord}>
          Record what happened
        </Button>
      </Stack>
    </Card>
  );
}
