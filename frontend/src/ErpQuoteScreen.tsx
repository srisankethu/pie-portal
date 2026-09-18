/** One quote the ERP raised, as a page. Read-only, for everybody.
 *
 * **A drawer was the wrong container and this is the correction.** The first
 * version of this put the document in a 520px right-hand panel with its lines
 * in a fact table, which is a shape for a detail popover — a few labelled
 * values about a row you are still looking at. A quote is not that. It is the
 * document, and a person opening one wants it the way they get a draft: full
 * width, the lines in a real grid, the total under them. So this is a route
 * rather than an overlay, and it is shaped after the Quote Builder because the
 * Quote Builder is what a quote looks like here.
 *
 * **Read-only for every role, and not by hiding controls from some of them.**
 * There is no edit affordance on this page for anybody, because the role that
 * could act on it does not exist: `upsert_quote_document` rewrites every column
 * of `erp_quotes` from the payload on every sync, so a value typed here would
 * survive exactly until the next pull. That is the same reasoning the model's
 * docstring gives for the table having no `loss_reason`, no `lost_to` and no
 * `note` column, and no endpoint that writes it. Why a quote was lost is a
 * human fact; it lives on `quote_outcomes`, which no sync opens.
 *
 * **An empty line grid is never rendered as an empty quote.** The breakdown is
 * pulled per quote and a resumed sync refreshes headers without re-reading it,
 * so "no lines here" and "this quote had no lines" are two different facts that
 * an empty array cannot tell apart. `lines_held` is the server saying which,
 * and the page prints its sentence rather than an empty table.
 *
 * **Nothing here is cost or margin.** `value`, `rate` and `amount` are what was
 * offered to the customer; `erp_quotes` and `erp_quote_lines` carry no buy-side
 * column, which is why this opens for every role rather than behind a
 * management gate. A salesperson is narrowed to their own accounts by the
 * endpoints that feed it, not by anything on this page.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "./api";
import { money } from "./money";
import { DataGrid, numeric, text } from "./platform/DataGrid";
import type { ColDef } from "./platform/DataGrid";
import {
  EmptyState, ErrorState, FactTable, FieldLabel, IdentityStrip, LoadingState,
  Meta, Section, SectionHeader, Stat, StatusChip, TOUCH, type Tone,
} from "./platform/kit";
import { pathFor } from "./platform/route";
import { formatDate, formatDateTime } from "./when";
import { QuoteDiagnosisPanel, useDismissReasons } from "./components/QuoteDiagnosisPanel";
import { useErpQuoteDiagnosis } from "./useQuoteDiagnosis";
import type { QuoteDiagnosisState } from "./useQuoteDiagnosis";
import type { PlatformSession } from "./platform/types";
import type { ErpQuote, ErpQuoteLine, ErpQuoteLines } from "./types";

/** How the sync classified the ERP's own status word.
 *
 *  UNRECORDED is `neutral`, deliberately, and it is the common case rather than
 *  a fault: the classifier refuses to read silence as a loss, because "nobody
 *  worked it", "the customer never answered" and "we lost it to a competitor"
 *  all look identical in an ERP and only the last is a loss. Colouring it
 *  `warn` would put a verdict on screen the server went out of its way not to
 *  reach.
 */
export const OUTCOME: Record<string, { label: string; tone: Tone; tip: string }> = {
  WON: {
    label: "Won", tone: "good",
    tip: "The ERP recorded this quote as accepted, with the date it happened.",
  },
  LOST: {
    label: "Lost", tone: "bad",
    tip: "The ERP recorded this quote as declined, with the date it happened.",
  },
  UNRECORDED: {
    label: "No outcome", tone: "neutral",
    tip: "Nobody wrote down how this ended. That is not a loss — silence covers "
      + "a quote nobody worked, one the customer never answered, and one lost "
      + "to a competitor, and only the last is a loss.",
  },
};

export function outcomeOf(code: string) {
  return OUTCOME[code] ?? { label: code, tone: "neutral" as Tone, tip: "" };
}

/** The organization's own field names, as a person reads them.
 *
 *  A key the ERP set that nobody has a label for is shown under its own name
 *  rather than dropped: it is a fact somebody typed, and hiding it because this
 *  file has not heard of it would be this screen's own bug repeated. */
const FIELD_LABEL: Record<string, string> = {
  cf_quote_type: "Quote type",
  cf_pricing_type: "Pricing type",
  cf_procurement_type: "Procurement type",
  branch_id: "Branch",
  branch_name: "Branch",
};

/** Keys whose only reader is another machine, dropped once a readable field
 *  says the same thing.
 *
 *  `branch_id` was rendered under the heading "Branch" as
 *  `2263307000000033035`, which is not a branch to anybody — it is the join
 *  key the ERP files one under. The id is still pulled and still stored,
 *  because that is what a second system matches on; it is simply not the half
 *  worth a row on a page somebody reads. Where the source gave no name the id
 *  is shown anyway: a branch nobody can name is still a fact about the quote,
 *  and blanking it would hide that the ERP set one. */
const SUPERSEDED_BY: Record<string, string> = { branch_id: "branch_name" };

/** The page's own name, used by all four of its states so the heading does not
 *  change under a reader while the same page loads, fails, or resolves. */
const TITLE = "Quote from your ERP";

/** What this screen is, in the shape the Quote Builder's own sub-heading takes.
 *
 *  It says read-only up front rather than only in the small print at the foot,
 *  because "why can I not edit this" is the question the page was opened with
 *  the first three times it was looked at. */
const SUB =
  "A quote your ERP raised, as it was issued — the lines, what they came to, "
  + "and how the ERP recorded the outcome. Read-only for everybody: this "
  + "document lives in another system, and a change typed here would be "
  + "overwritten by the next sync.";

function dash(value: string | null | undefined): string {
  return value || "—";
}

/** The ERP's own word for a status, shown beside the verdict rather than
 *  replaced by it — a reader asking why a quote reads "No outcome" needs to see
 *  whether it was `sent` or `expired`. */
function sourceWord(status: string): string {
  return status ? status.replace(/_/g, " ") : "—";
}

export default function ErpQuoteScreen({ session }: { session: PlatformSession }) {
  const { ref = "" } = useParams();
  const navigate = useNavigate();
  const [quote, setQuote] = useState<ErpQuote | null | undefined>(undefined);
  const [lines, setLines] = useState<ErpQuoteLines | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  /* Both reads, on arrival. The header comes from the book rather than from a
     second endpoint, because the book already holds every field this page shows
     above the grid — a detail endpoint for them would be a second shape saying
     the same thing, and the one that drifts. */
  useEffect(() => {
    let live = true;
    setError(null);
    setQuote(undefined);
    setLines(undefined);

    Promise.all([
      api.listErpQuotes(session.token, 1000),
      api.erpQuoteLines(session.token, ref),
    ])
      .then(([book, got]) => {
        if (!live) return;
        setQuote(book.quotes_listed.find((q) => q.quote_document_ref === ref)
                 ?? null);
        setLines(got);
      })
      .catch((e) => {
        // Never left on the loading state. A page that cannot fetch has to say
        // so: a skeleton that never resolves is indistinguishable from a hang,
        // and it is the failure this screen's first version actually shipped.
        if (!live) return;
        setError((e as Error).message);
        setQuote(null);
        setLines(undefined);
      });
    return () => { live = false; };
  }, [ref, session.token]);

  const back = (
    <Button size="small" startIcon={<ArrowBackOutlined />} sx={TOUCH}
            onClick={() => navigate(pathFor("quotes"))}>
      All quotes
    </Button>
  );

  if (error) {
    return (
      <Box>
        <SectionHeader title={TITLE} actions={back} />
        <ErrorState error={error} onRetry={() => navigate(0)} />
      </Box>
    );
  }
  if (quote === undefined) {
    return (
      <Box>
        <SectionHeader title={TITLE} actions={back} />
        <LoadingState rows={4} label="Reading the quote…" />
      </Box>
    );
  }
  if (quote === null) {
    return (
      <Box>
        <SectionHeader title={TITLE} actions={back} />
        <EmptyState
          title="No such quote"
          reason="This reference does not name a quote on your list. A quote
                  raised for an account you do not hold is read by whoever
                  holds it."
        />
      </Box>
    );
  }

  const o = outcomeOf(quote.outcome);
  const attributes = quote.attributes ?? {};
  const fields = Object.entries(attributes).filter(
    ([key]) => !(SUPERSEDED_BY[key] && attributes[SUPERSEDED_BY[key]]));
  const heading = (t: string) => (
    <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
      {t}
    </Typography>
  );

  return (
    <Box>
      {/* The page is titled for what it is, not for which quote it is — the
          Builder's own arrangement, and the reason for it here is that the
          strip below already says the number. Titling this "QT FY27-018"
          printed the number twice, ten millimetres apart, and left this screen
          the only one of its four states with a different heading. */}
      <SectionHeader title={TITLE} sub={SUB} actions={back} />

      {/* The same strip the Quote Builder opens with, and the reason it is a
          `kit` component rather than markup in one file: a person reading a
          quote should not have to re-learn where its number, its customer and
          its standing are because this one came out of the ERP instead of the
          desk. The Builder's third field is the owner and this one's is the
          book — an ERP quote carries `salesperson_external_id` and this
          platform holds no name for it, so the honest third fact is which set
          of books raised the document. */}
      <IdentityStrip
        fields={[
          { label: "Quote", value: heading(dash(quote.number)) },
          { label: "Customer", value: heading(quote.customer_label) },
          { label: "Book", value: heading(quote.company) },
        ]}
        aside={
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <StatusChip label={o.label} tone={o.tone} tip={o.tip} />
            <Meta>{`ERP status: ${sourceWord(quote.source_status)}`}</Meta>
          </Stack>
        }
      />

      {/* What the Builder bands as QUOTE DETAILS. Not collapsible, which is the
          one place this deliberately departs from it: there the band holds a
          form somebody fills in and folding it away is how you get past it;
          here it is five facts, and a page whose whole promise is that there is
          nothing to press should not open with something to press. */}
      <Section title="Quote details" level="widget" dense>
        {/* A label and a value, five rows — the case `ui-standards` §3 keeps a
            fact panel for. Its row count is fixed by the document, not by the
            size of the business. `Value` has moved to the summary at the foot,
            where the Builder puts a total and where it can be read against
            what the lines come to. */}
        <FactTable
          label={`Quote ${dash(quote.number)}`}
          rows={[
            // Through `when.ts`, the one owner of how this platform writes a
            // date, rather than passed through as the ERP's wire format. Four
            // rows read "2026-07-11" and the fifth read
            // "2026-07-12T09:00:00" — the same screen in two formats, one of
            // them a machine's. `formatDate` and `formatDateTime` already
            // return an em dash for a missing value, so `dash` is not needed
            // over them.
            ["Raised", formatDate(quote.raised_on)],
            ["Expires", formatDate(quote.expires_on)],
            ["Decided", formatDate(quote.decided_on)],
            // The one row with a time worth keeping: when a customer opened a
            // quote is an instant, and "the 12th" loses what the hour says
            // about how it was read.
            ["Customer opened", formatDateTime(quote.opened_at)],
          ]}
        />

        {/* Out of the table above on purpose. It is not a fact about the quote
            the way its dates are — it is the handle the source system files
            this document under, useful for looking it up there or quoting to
            support, and unreadable as anything else. Sitting in the fact panel
            it had the same weight as "Raised", so a reader met a twenty-digit
            number where every other row told them something. */}
        <Box sx={{ mt: 2 }}>
          <Meta>
            {`Your ERP files this document under ${quote.quote_document_ref} — `}
            {"quote that reference if you are looking it up there."}
          </Meta>
        </Box>

        {fields.length > 0 && (
          <Box sx={{ mt: 3 }}>
            <FieldLabel>Your fields on this quote</FieldLabel>
            <Box sx={{ mt: 0.5 }}>
              <FactTable
                prose
                label="Fields this organization set on the quote"
                rows={fields.map(([k, v]) => [FIELD_LABEL[k] ?? k, v])}
              />
            </Box>
          </Box>
        )}
      </Section>

      <Box sx={{ mt: 3 }}>
        <FieldLabel>What was quoted</FieldLabel>
        <Box sx={{ mt: 1 }}>
          <ErpQuoteLineGrid lines={lines} />
        </Box>
      </Box>

      {/* The diagnosis, against what this customer had paid *by the day this
          quote went out* — not against today. It is the same engine and the
          same endpoint the Quote Builder asks; what differs is that this
          document has already been answered, so the cards read as "what the
          evidence said at the time" beside an outcome the ERP recorded.

          Dismissal and "review the price" are both absent, and neither is an
          oversight: nothing here was recorded, so there is no diagnosis to
          dismiss, and an issued document cannot be re-priced from this screen. */}
      <ErpQuoteDiagnosis quote={quote} lines={lines} token={session.token} />

      <ErpQuoteSummary quote={quote} lines={lines} />
    </Box>
  );
}

/** The diagnosis cards for a quote the ERP already issued.
 *
 *  A component rather than three lines inline because the hook must not run
 *  until the quote is loaded — `ErpQuoteScreen` returns early on four states
 *  before it has one, and a hook cannot live behind an early return. */
function ErpQuoteDiagnosis({ quote, lines, token }: {
  quote: ErpQuote; lines?: ErpQuoteLines; token: string;
}) {
  const held = lines?.lines ?? [];
  const diagnosis = useErpQuoteDiagnosis(
    quote.quote_document_ref, held.length > 0, token);
  const dismissReasons = useDismissReasons(token);

  // Nothing to ask about. The breakdown has not been read, or no line on it
  // names a product and a price — and "the check found nothing" would be a
  // claim about evidence that was never put to it.
  if (held.length === 0) return null;

  const lineIds = held.map((l) => String(l.line_number));

  return (
    <QuoteDiagnosisPanel
      lineIds={lineIds}
      diagnosis={diagnosis}
      dismissReasons={dismissReasons}
      title="How this was priced against the customer's own history"
      // Formatted here rather than inside `settled`, which composes a
      // sentence and should not also be deciding how this platform writes
      // a date — `when.ts` owns that for every screen.
      coverage={settled(lineIds, diagnosis, formatDate(quote.raised_on))}
    />
  );
}

/** What to say when the engine flagged nothing.
 *
 *  **"Nothing stood out" is only one of the things that sentence used to
 *  cover,** and the other one is not good news. A line renders no card when it
 *  sat inside the supported range, when the deviation was too small to be worth
 *  interrupting anybody over, *or* when there was no comparable history to
 *  judge it against at all. Reporting the third as a clean bill is the
 *  `absence of evidence is not a pass` rule broken on screen, over an engine
 *  that is careful about it — `INSUFFICIENT_EVIDENCE` is a first-class answer
 *  there, described in its own source as valid, expected and frequent.
 *
 *  So this counts. `comparable` is the engine's own answer to "could I say
 *  anything about this line", carried as a field rather than inferred from the
 *  word in `evidence`, which is chosen for display and would be a guess about
 *  what the producer meant.
 *
 *  **And it counts what was flagged, rather than assuming nothing was.** "Nothing
 *  stood out" was written into the sentence as a constant, at a time when no
 *  quote on this screen surfaced a card and the claim was therefore true by
 *  accident. It is not the salesperson who catches that when it stops being
 *  true — the panel draws their cards and never reaches this sentence. It is
 *  the manager, who was served the owner payload no card was built on, saw no
 *  cards at all, and got told a quote was clean while their own projection said
 *  two of its lines were above the customer's history. An asserted clean bill
 *  that nothing checked is the `absence of evidence is not a pass` rule again,
 *  one layer up from the engine that is careful about it.
 *
 *  A manager has `OwnerDiagnosisCard` now and both projections publish
 *  `renders`, so this sentence and the cards above it are finally reading the
 *  same field. It still counts rather than assuming, because the reason it was
 *  wrong was the assuming, not the missing card.
 *
 *  **And one sentence used to cover three different situations.** "No line
 *  could be compared" was printed when the engine answered about nothing, when
 *  it answered and found no history, and when it found history it was not
 *  allowed to use — and it blamed the customer's history in all three. The
 *  third is the one that matters: `EVIDENCE_WITHHELD` means rows were located
 *  and dropped because their visibility could not be established, which is a
 *  fixable data problem rather than a quiet account. It was reported on a real
 *  book as "this customer had no purchase history" over sixteen excluded
 *  transactions, which sent somebody looking for the wrong thing.
 */
function settled(lineIds: string[], diagnosis: QuoteDiagnosisState,
                 raisedOn: string): string {
  const seen = lineIds.map((id) => diagnosis.byLineId[id]).filter(Boolean);
  const answered = seen.length;
  const compared = seen.filter((d) => d.comparable).length;
  const flagged = seen.filter((d) => d.renders).length;
  // Rows the engine found and could not use, because it cannot tell when they
  // became visible. Distinct from having found nothing, and the distinction is
  // the whole point of saying it out loud.
  const withheld = seen.some((d) => d.context?.includes("EVIDENCE_WITHHELD"));
  // Lines the engine was never asked about: a line with no item code or no
  // price is not something it can be put a question about, and counting those
  // as "not comparable" would blame the customer's history for a blank field.
  const unasked = lineIds.length - answered;
  const alsoUnasked = unasked === 0 ? ""
    : unasked === 1
      ? " One more line carried no item code or no price, so it was not checked."
      : ` ${unasked} more lines carried no item code or no price, so they were `
        + "not checked.";

  if (answered === 0) {
    return `No line on this quote could be checked: none of them carried both `
      + `an item code and a price. That is a gap in the document, not a `
      + `verdict on the pricing.`;
  }
  if (compared === 0) {
    return withheld
      ? `No line on this quote could be compared — but this customer's history `
        + `is not empty. The engine found past transactions for these items and `
        + `could not use them, because it cannot tell when each one became `
        + `visible to the business. Re-read the documents already held from `
        + `Data & connection; until then nothing here is a verdict on the `
        + `pricing.${alsoUnasked}`
      : `No line on this quote could be compared: this customer had no `
        + `purchase history on record for these items by ${raisedOn}. That is `
        + `an absence of evidence, not a verdict on the pricing.${alsoUnasked}`;
  }
  const lead = compared < answered
    ? `${compared} of ${answered} lines were compared against what this `
      + `customer had paid by ${raisedOn}`
    : `All ${answered} lines were compared against what this customer had paid `
      + `by ${raisedOn}`;
  const rest = compared < answered
    ? ` The other ${answered - compared} had no comparable history to judge.`
    : "";
  const body = flagged > 0
    ? `${lead}, and ${flagged} of them ${flagged === 1 ? "sits" : "sit"} `
      + `outside it.${rest}`
    : `${lead}, and nothing on those stood out.${rest}`;
  return `${body}${alsoUnasked}`;
}

/** What the lines come to, beside what the ERP says the document came to.
 *
 *  The screen had no total at all: fourteen priced lines and nowhere on the
 *  page saying what they add up to, which is the first thing anybody reads a
 *  quote for. This is the Builder's summary strip, with the two figures an
 *  issued document actually has.
 *
 *  **They are two different numbers and the difference is not an error.** The
 *  lines are pre-tax; the quotation total is the ERP's own figure for the whole
 *  document and includes tax and anything charged against the quote rather than
 *  against a line. The gap is deliberately *not* computed and labelled "tax" —
 *  this pull holds no tax row, and naming a subtraction after the thing it is
 *  usually made of is how a screen states something it does not know. */
function ErpQuoteSummary({ quote, lines }: {
  quote: ErpQuote; lines?: ErpQuoteLines;
}) {
  const held = lines !== undefined && lines.lines_held;
  const sum = sumOfLines(lines);

  return (
    <Paper
      variant="outlined"
      sx={{
        mt: 3, p: 1.5,
        display: "flex", alignItems: "center", flexWrap: "wrap",
        gap: 3, rowGap: 1.5,
        bgcolor: "var(--color-neutral-100)",
      }}
    >
      <Stat
        label="Lines total"
        value={sum.total}
        note={held ? sum.note : "the breakdown has not been read yet"}
      />
      <Stat label="Quotation total" value={quote.value} strong />
      <Box sx={{ flex: 1 }} />
      <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "52ch" }}>
        The quotation total is the ERP&rsquo;s own figure for the whole document,
        including tax and anything charged against the quote rather than against
        a line. Nothing on this page can be edited: a change typed here would be
        overwritten by the next sync.
      </Typography>
    </Paper>
  );
}

/** Σ of the line amounts, and what the sum left out.
 *
 *  A line the ERP never priced is excluded and counted, never added as zero —
 *  the `sum(… or 0)` tell CLAUDE.md §1 names, which would put a total on screen
 *  that looks complete and is not. Where nothing was priced, or the breakdown
 *  was never read, there is no total: `null`, which `CurrencyValue` renders as
 *  an em dash. A quote whose lines this platform has not read is not a quote
 *  worth nothing. */
export function sumOfLines(lines?: ErpQuoteLines):
    { total: number | null; note: string | null } {
  if (lines === undefined || lines.lines.length === 0) {
    return { total: null, note: null };
  }
  let total = 0;
  let priced = 0;
  let unpriced = 0;
  for (const ln of lines.lines) {
    if (ln.amount === null || ln.amount === undefined) unpriced += 1;
    else { total += ln.amount; priced += 1; }
  }
  return {
    total: priced === 0 ? null : total,
    note: unpriced === 0 ? null
      : `${unpriced} line${unpriced === 1 ? "" : "s"} the ERP did not price`,
  };
}

/** The lines, in a grid — the shape a draft's lines are in, because this is the
 *  same kind of thing being read rather than written.
 *
 *  A quote's line count is set by the enquiry behind it, not by the size of the
 *  business, so this could defensibly be a fact panel. It is a `DataGrid`
 *  anyway: a forty-line tender is ordinary here, and a reader who can sort by
 *  value or filter to one item code on a draft should not lose that because
 *  the document came from the ERP instead. */
export function ErpQuoteLineGrid({ lines }: { lines?: ErpQuoteLines }) {
  if (lines === undefined) return <Skeleton variant="rectangular" height={160} />;

  if (lines.lines.length === 0) {
    // The server's own sentence, which is the only thing that distinguishes
    // "this quote had no lines" from "nobody has read them yet". An empty grid
    // would assert the first.
    return (
      <Alert severity="info">
        {lines.empty_reason ?? "This quote has no lines on record."}
      </Alert>
    );
  }

  const columns: ColDef<ErpQuoteLine>[] = [
    // The position the ERP wrote the line at, counted from one as the document
    // itself does. `line_number` is zero-based on the wire because it is an
    // index; a reader comparing this against the PDF in their other hand is
    // not reading indices.
    text("line_number", "#", {
      width: 70, flex: 0,
      valueGetter: (p) => String((p.data?.line_number ?? 0) + 1),
    }),
    // Name first, then the code. Somebody reading a quote knows the tool, not
    // the eight digits the ERP files it under — the SKU is what they check
    // against a PO, which is a second act rather than the first. Two columns
    // rather than one stacked cell: each is separately sortable and filterable,
    // and the desk filters on the code as often as it scans the names.
    text("item_name", "Item", {
      minWidth: 240,
      // Empty where the line resolved to no catalogue item. The code below
      // still names it, so this says "not in the master" rather than going
      // blank as though the line were nameless.
      valueGetter: (p) => p.data?.item_name || "—",
    }),
    text("item_code", "SKU", {
      minWidth: 150,
      valueGetter: (p) => p.data?.item_code || "—",
    }),
    text("description", "Description", { minWidth: 220 }),
    numeric("qty", "Qty", (v) => (v === null || v === undefined ? "—" : String(v)),
            { width: 110, flex: 0 }),
    text("unit", "Unit", { width: 90, flex: 0 }),
    // `null` is not zero on either of these: a line the ERP never priced is a
    // line nobody priced, which is a different fact from a line priced at
    // nothing — and only the second is something a reader would act on.
    numeric("rate", "Rate", (v) => (v === null || v === undefined ? "—" : money(v)),
            { width: 130, flex: 0 }),
    numeric("amount", "Amount",
            (v) => (v === null || v === undefined ? "—" : money(v)),
            { width: 140, flex: 0 }),
  ];

  return (
    <DataGrid<ErpQuoteLine>
      rows={lines.lines}
      columns={columns}
      getRowId={(r) => String(r.line_number)}
      ariaLabel="Lines on this quote"
      renderNarrow={(ln) => (
        <Box key={ln.line_number}
             sx={{ p: 2, borderBottom: 1, borderColor: "divider" }}>
          <Box sx={{ fontWeight: 600 }}>{ln.item_name || ln.item_code || "—"}</Box>
          {/* The code under the name on a phone rather than beside it: two
              columns of a grid become two lines of a card, and the name is
              the one somebody scans a list by. Dropped when it would only
              repeat the line above, which is the unresolved case. */}
          {ln.item_name && ln.item_code && <Meta>{ln.item_code}</Meta>}
          <Box sx={{ color: "text.secondary", fontSize: 13 }}>
            {ln.description}
          </Box>
          <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
            <Meta>
              {ln.qty === null ? "—" : `${ln.qty}${ln.unit ? ` ${ln.unit}` : ""}`}
            </Meta>
            <Meta>{ln.rate === null ? "—" : money(ln.rate)}</Meta>
            <Box sx={{ ml: "auto", fontWeight: 600 }}>
              {ln.amount === null ? "—" : money(ln.amount)}
            </Box>
          </Stack>
        </Box>
      )}
    />
  );
}
