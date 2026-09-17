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
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "./api";
import { money } from "./money";
import { DataGrid, numeric, text } from "./platform/DataGrid";
import type { ColDef } from "./platform/DataGrid";
import {
  EmptyState, ErrorState, FactTable, FieldLabel, LoadingState, Meta,
  SectionHeader, StatusChip, TOUCH, type Tone,
} from "./platform/kit";
import { pathFor } from "./platform/route";
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
};

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
        <SectionHeader title="Quote from your ERP" actions={back} />
        <ErrorState error={error} onRetry={() => navigate(0)} />
      </Box>
    );
  }
  if (quote === undefined) {
    return (
      <Box>
        <SectionHeader title="Quote from your ERP" actions={back} />
        <LoadingState rows={4} label="Reading the quote…" />
      </Box>
    );
  }
  if (quote === null) {
    return (
      <Box>
        <SectionHeader title="Quote from your ERP" actions={back} />
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
  const fields = Object.entries(quote.attributes ?? {});

  return (
    <Box>
      <SectionHeader
        title={dash(quote.number)}
        sub={quote.customer_label}
        actions={back}
      />

      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 2 }}>
        <StatusChip label={o.label} tone={o.tone} tip={o.tip} />
        <Meta>{`ERP status: ${sourceWord(quote.source_status)}`}</Meta>
      </Stack>

      {/* A label and a value, seven rows — the case `ui-standards` §3 keeps a
          fact panel for. Its row count is fixed by the document, not by the
          size of the business. */}
      <FactTable
        label={`Quote ${dash(quote.number)}`}
        rows={[
          ["Value", quote.value === null ? "—" : money(quote.value)],
          ["Raised", quote.raised_on],
          ["Expires", dash(quote.expires_on)],
          ["Decided", dash(quote.decided_on)],
          ["Customer opened", dash(quote.opened_at)],
          ["Book", quote.company],
          ["ERP reference", quote.quote_document_ref],
        ]}
      />

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

      <Box sx={{ mt: 3 }}>
        <FieldLabel>What was quoted</FieldLabel>
        <Box sx={{ mt: 1 }}>
          <ErpQuoteLineGrid lines={lines} />
        </Box>
      </Box>

      <Box sx={{ mt: 3 }}>
        <Meta>
          Nothing on this page can be edited: a change typed here would be
          overwritten by the next sync.
        </Meta>
      </Box>
    </Box>
  );
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
    text("item_code", "Item", {
      minWidth: 180,
      valueGetter: (p) => p.data?.item_code || "—",
    }),
    text("description", "Description", { minWidth: 260 }),
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
          <Box sx={{ fontWeight: 600 }}>{ln.item_code || "—"}</Box>
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
