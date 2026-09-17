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
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { money } from "../money";
import { DataGrid, numeric, text } from "../platform/DataGrid";
import type { ColDef } from "../platform/DataGrid";
import { EmptyState, Meta, StatusChip, type Tone } from "../platform/kit";
import type { ErpQuote } from "../types";

/** How the sync classified the ERP's own status word.
 *
 *  UNRECORDED is `neutral`, deliberately, and it is the common case rather than
 *  a fault: the classifier refuses to read silence as a loss, because "nobody
 *  worked it", "the customer never answered" and "we lost it to a competitor"
 *  all look identical in an ERP and only the last is a loss. Colouring it
 *  `warn` would put a verdict on the screen that the server went out of its way
 *  not to reach.
 */
const OUTCOME: Record<string, { label: string; tone: Tone; tip: string }> = {
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

/** The ERP's own word for a status, shown beside the verdict rather than
 *  replaced by it — a reader asking why a quote reads "No outcome" needs to see
 *  whether it was `sent` or `expired`. */
function sourceWord(status: string): string {
  return status ? status.replace(/_/g, " ") : "—";
}

function when(iso: string | null): string {
  return iso ?? "—";
}

export function ErpQuoteList({ quotes, emptyReason }: {
  quotes: ErpQuote[];
  /** The server's sentence for an empty book. Rendered rather than replaced:
   *  it is the one that distinguishes "no quotes synced yet" from "the quote
   *  stage was refused a permission", and a generic "Nothing here" is exactly
   *  what sent the original report. */
  emptyReason: string | null;
}) {
  const columns: ColDef<ErpQuote>[] = [
    text("number", "Quote", { minWidth: 150 }),
    text("customer_label", "Customer", { minWidth: 200 }),
    text("raised_on", "Raised", { width: 130, flex: 0 }),
    {
      field: "outcome", headerName: "Outcome", width: 150, flex: 0,
      cellRenderer: (p: { data?: ErpQuote }) => {
        if (!p.data) return null;
        const o = OUTCOME[p.data.outcome] ?? {
          label: p.data.outcome, tone: "neutral" as Tone, tip: "",
        };
        return <StatusChip label={o.label} tone={o.tone} tip={o.tip} />;
      },
    },
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
    <DataGrid<ErpQuote>
      rows={quotes}
      columns={columns}
      getRowId={(r) => r.quote_document_ref}
      ariaLabel="Quotes raised in your ERP"
      empty={
        <EmptyState
          title="No quotes from your ERP yet"
          reason={emptyReason
            ?? "Nothing has come through from the connected books."}
        />
      }
      renderNarrow={(q) => <ErpQuoteCard key={q.quote_document_ref} q={q} />}
    />
  );
}

/** The phone row. The grid's own narrow mode, not a second list — `renderNarrow`
 *  is what `platform/DataGrid` takes for exactly this. */
function ErpQuoteCard({ q }: { q: ErpQuote }) {
  const o = OUTCOME[q.outcome] ?? {
    label: q.outcome, tone: "neutral" as Tone, tip: "",
  };
  return (
    <Box sx={{ p: 2, borderBottom: 1, borderColor: "divider" }}>
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
        </Meta>
      </Stack>
    </Box>
  );
}
