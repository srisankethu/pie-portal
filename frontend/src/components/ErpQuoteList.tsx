/** Every quote the connected ERP raised, as a list the desk can read.
 *
 * **Why this exists.** A sync ran, read the book's quotes, and the person who
 * ran it saw two platform drafts on this screen and reported the sync as
 * broken. It was not: `erp_quotes` had exactly one reader on the server, the
 * unanswered-quotes worklist, which filters to the quotes nobody has recorded
 * an outcome for. So every quote the ERP had already marked accepted or
 * invoiced was synced, stored and shown nowhere — and a book that converts most
 * of what it quotes, which is a good book, was the book that looked empty.
 *
 * **Read-only, and a separate list from the drafts beside it.** These are
 * issued documents in somebody else's system. The desk cannot price one, send
 * one, or delete one, and the readiness lifecycle the drafts grid shows — needs
 * work, awaiting approval, ready to send — has no meaning here. That is why
 * this is its own tab and its own row type rather than extra rows in the draft
 * grid with half their columns blank: one list with two kinds of row in it is
 * how somebody ends up offered "Send" on a quote that went out in March.
 *
 * **Nothing here is cost.** `value` is the quote's own selling total, the
 * figure that went to the customer. There is no cost column on this table to
 * withhold, which is why every role sees this list — narrowed, for a
 * salesperson, to the accounts they hold.
 */
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { money } from "../money";
import { DataGrid, numeric, text } from "../platform/DataGrid";
import type { ColDef } from "../platform/DataGrid";
import { EmptyState, Meta, StatusChip } from "../platform/kit";
import { erpQuotePath, pathFor } from "../platform/route";
import Button from "@mui/material/Button";
import { TOUCH } from "../platform/kit";
import { erpOutcome, outcomeOfRecord } from "../ErpQuoteScreen";
import type { ErpQuote } from "../types";

/** The chip readings live with the drawer and are imported here.
 *
 *  One row and its opened form must not disagree about whether a quote was
 *  won — which they would the first time somebody edited one of two copies.
 *  UNRECORDED is `neutral` there, deliberately: the classifier refuses to read
 *  silence as a loss, and colouring it `warn` would put a verdict on screen
 *  that the server went out of its way not to reach.
 */

/** The ERP's own word for a status, shown beside the verdict rather than
 *  replaced by it — a reader asking why a quote reads "No outcome" needs to see
 *  whether it was `sent` or `expired`. */
function sourceWord(status: string): string {
  return status ? status.replace(/_/g, " ") : "—";
}

function when(iso: string | null): string {
  return iso ?? "—";
}

/** Whether a row still wants a person's answer: nobody here has recorded
 *  one and the ERP has not already recorded a win. A decline the ERP holds
 *  still wants a reason, which the ERP cannot hold. */
export function wantsOutcome(q: ErpQuote): boolean {
  return !q.recorded && erpOutcome(q) !== "WON";
}

export function ErpQuoteList({ quotes, emptyReason, showCompany = false, onRecord }: {
  quotes: ErpQuote[];
  /** Offer "Record…" on rows that want an answer, opening the caller's
   *  outcome form. Absent where the list is read-only. */
  onRecord?: (q: ErpQuote) => void;
  /** The server's sentence for an empty book. Rendered rather than replaced:
   *  it is the one that distinguishes "no quotes synced yet" from "the quote
   *  stage was refused a permission", and a generic "Nothing here" is exactly
   *  what sent the original report. */
  emptyReason: string | null;
  /** Name the book on every row. Only worth the width where the rows come
   *  from more than one company — the caller decides, from the same rule the
   *  source badges follow: one company means one word repeated down a column. */
  showCompany?: boolean;
}) {
  const navigate = useNavigate();
  /* Opening a quote is a route change, not an overlay. It is the document, and
     a person opening one wants it the way they get a draft — full width, the
     lines in a grid — which a 520px drawer cannot be. */
  const open = (q: ErpQuote) => navigate(erpQuotePath(q.quote_document_ref));
  /* A document this platform wrote is still an ERP quote — it is listed, it
     is counted, and it opens like the rest. What it gains is its draft, one
     click away, so the same quote is never two unrelated rows on two tabs. */
  const fromPie = quotes.some((q) => q.platform_quote);

  const columns: ColDef<ErpQuote>[] = [
    text("number", "Quote", { minWidth: 150 }),
    text("customer_label", "Customer", { minWidth: 200 }),
    ...(showCompany
      ? [text<ErpQuote>("company", "Book", { minWidth: 150, flex: 0, width: 170 })]
      : []),
    ...(fromPie ? [{
      field: "platform_quote", headerName: "Built in PIE", width: 130, flex: 0,
      sortable: false, filter: false,
      // Its own control, so a click on it must not also open the ERP page.
      context: { noRowClick: true },
      valueGetter: (p: { data?: ErpQuote }) => p.data?.platform_quote?.number ?? "",
      cellRenderer: (p: { data?: ErpQuote }) =>
        p.data?.platform_quote ? (
          <Chip
            size="small"
            variant="outlined"
            clickable
            label={p.data.platform_quote.number || "Open draft"}
            title="Written from this draft in the Quote Builder — open it"
            onClick={(e) => {
              e.stopPropagation();
              navigate(pathFor("quotes", p.data!.platform_quote!.quote_id));
            }}
          />
        ) : null,
    } as ColDef<ErpQuote>] : []),
    text("raised_on", "Raised", { width: 130, flex: 0 }),
    {
      // The outcome of record — a person's decision first, the ERP's word
      // where nobody here has said — with the tip naming which.
      field: "outcome", headerName: "Outcome", width: 150, flex: 0,
      valueGetter: (p: { data?: ErpQuote }) => (p.data ? erpOutcome(p.data) : ""),
      cellRenderer: (p: { data?: ErpQuote }) => {
        if (!p.data) return null;
        const o = outcomeOfRecord(p.data);
        return <StatusChip label={o.label} tone={o.tone} tip={o.tip} />;
      },
    },
    ...(onRecord ? [{
      field: "recorded", headerName: "", width: 110, flex: 0, sortable: false,
      // The grid opens the row on a cell click, and a React stopPropagation
      // does not reach it; `noRowClick` is how a column opts out — see
      // `DataGrid.tsx`.
      context: { noRowClick: true },
      cellRenderer: (p: { data?: ErpQuote }) =>
        p.data && wantsOutcome(p.data) ? (
          <Button size="small" sx={TOUCH}
                  onClick={(e) => { e.stopPropagation(); onRecord(p.data!); }}>
            Record…
          </Button>
        ) : null,
    } as ColDef<ErpQuote>] : []),
    text("source_status", "ERP status", {
      width: 140, flex: 0,
      valueGetter: (p) => sourceWord(p.data?.source_status ?? ""),
    }),
    text("expires_on", "Expires", {
      width: 130, flex: 0,
      valueGetter: (p) => when(p.data?.expires_on ?? null),
    }),
    // `null` is not zero: a quote the ERP gave no total for is real quoting
    // activity with a missing figure, and rendering it as ₹0 would put a number
    // on screen that nothing in the book supports.
    numeric("value", "Value", (v) => (v === null || v === undefined ? "—" : money(v)),
            { width: 140, flex: 0 }),
  ];

  return (
    <>
    <DataGrid<ErpQuote>
      rows={quotes}
      columns={columns}
      getRowId={(r) => r.quote_document_ref}
      // Opening one is a read, so both the click and the keyboard activation
      // land on the same handler — a row somebody can reach with the keyboard
      // and not open is a row a screen-reader user cannot read at all.
      onRowClick={open}
      onRowActivate={open}
      ariaLabel="Quotes raised in your ERP"
      empty={
        <EmptyState
          title="No quotes from your ERP yet"
          reason={emptyReason
            ?? "Nothing has come through from the connected books."}
        />
      }
      renderNarrow={(q) => (
        <ErpQuoteCard key={q.quote_document_ref} q={q} showCompany={showCompany}
                      onOpen={() => open(q)}
                      onRecord={onRecord && wantsOutcome(q) ? () => onRecord(q) : undefined} />
      )}
    />
    </>
  );
}

/** The phone row. The grid's own narrow mode, not a second list — `renderNarrow`
 *  is what `platform/DataGrid` takes for exactly this.
 *
 *  A `button`, not a `div` with an `onClick`: the whole card opens the quote, so
 *  it has to be reachable by keyboard and announced as something that can be
 *  pressed. The wide grid gets that from `onRowActivate`; the narrow path has to
 *  say it itself. */
function ErpQuoteCard({ q, onOpen, showCompany, onRecord }: {
  q: ErpQuote; onOpen: () => void; showCompany: boolean; onRecord?: () => void;
}) {
  const o = outcomeOfRecord(q);
  return (
    <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
    {/* The record control sits beside the card, not inside it: the card is
        itself a button, and a button inside a button is not a control a
        screen reader can name. */}
    {onRecord && (
      <Box sx={{ px: 2, pt: 1, display: "flex", justifyContent: "flex-end" }}>
        <Button size="small" sx={TOUCH} onClick={onRecord}>Record…</Button>
      </Box>
    )}
    <Box
      component="button"
      type="button"
      onClick={onOpen}
      sx={{
        display: "block", width: "100%", textAlign: "left", font: "inherit",
        color: "inherit", background: "none", border: 0, cursor: "pointer",
        p: 2,
      }}
    >
      <Stack direction="row" spacing={1}
             sx={{ alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="subtitle2">{q.number ?? q.quote_document_ref}</Typography>
        <StatusChip label={o.label} tone={o.tone} tip={o.tip} />
      </Stack>
      <Typography variant="body2" sx={{ mt: 0.5 }}>{q.customer_label}</Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: "baseline" }}>
        <Typography variant="body2">
          {q.value === null ? "—" : money(q.value)}
        </Typography>
        <Meta>
          raised {q.raised_on} · {sourceWord(q.source_status)}
          {showCompany ? ` · ${q.company}` : ""}
          {q.platform_quote ? ` · built in PIE as ${q.platform_quote.number || "a draft"}` : ""}
        </Meta>
      </Stack>
    </Box>
    </Box>
  );
}
