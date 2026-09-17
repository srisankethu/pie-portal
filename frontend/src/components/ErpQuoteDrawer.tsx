/** One quote the ERP raised, opened. Read-only, for everybody.
 *
 * **There is no edit affordance here for any role, and that is the design.**
 * This is an issued document in somebody else's system: the desk did not write
 * it here and cannot change it here. `upsert_quote_document` rewrites every
 * column of `erp_quotes` from the payload on every sync, so a value typed into
 * this panel would survive exactly until the next pull — which is why there is
 * no endpoint that writes that table and why this drawer offers nothing to
 * press. Why a quote was lost is a human fact and lives on `quote_outcomes`,
 * a different table that no sync opens.
 *
 * **It says what it does not hold.** `erp_quotes` is header grain — the
 * line-level split would cost one API call per quote, and the model's docstring
 * says so. A reader who opens a quote expecting to see the lines and finds a
 * panel that simply does not mention them learns the same lesson that produced
 * the report this whole screen came from: something is missing and nothing says
 * what. So the absence is printed, with where the lines actually are.
 *
 * **Nothing here is cost or margin.** `value` is the quote's own selling total.
 * There is no cost column on this table, which is why this opens for every role
 * rather than behind a management gate — a salesperson is narrowed to their own
 * accounts by the list that fed this, and not by anything in here.
 */
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CloseOutlined from "@mui/icons-material/CloseOutlined";

import { money } from "../money";
import { FactTable, FieldLabel, Meta, StatusChip, TOUCH, type Tone } from "../platform/kit";
import type { ErpQuote } from "../types";

/** The same three readings the grid uses, kept in one place with it. */
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

export function ErpQuoteDrawer({ quote, showCompany, onClose }: {
  quote: ErpQuote;
  /** Naming the book is information with two connected companies and noise
   *  with one, which is the server's `companies` count to decide, not this
   *  component's. */
  showCompany: boolean;
  onClose: () => void;
}) {
  const o = outcomeOf(quote.outcome);
  const fields = Object.entries(quote.attributes ?? {});

  return (
    /* A real `Drawer`: focus stays inside, Escape closes, and focus returns to
       the grid row that opened it — the same reasoning `SupplyDrawer` states. */
    <Drawer
      anchor="right"
      open
      onClose={onClose}
      slotProps={{ paper: { sx: { width: "min(520px, 92vw)" } } }}
    >
      <Box sx={{ height: "100%", overflow: "auto", p: 2.5 }}>
        <Stack direction="row"
               sx={{ alignItems: "flex-start", justifyContent: "space-between" }}>
          <Box>
            <FieldLabel>Quote from your ERP</FieldLabel>
            <Typography variant="h6" sx={{ mt: 0.5 }}>
              {dash(quote.number)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {quote.customer_label}
            </Typography>
          </Box>
          <IconButton onClick={onClose} aria-label="Close" sx={{ ...TOUCH }}>
            <CloseOutlined />
          </IconButton>
        </Stack>

        <Box sx={{ mt: 2 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <StatusChip label={o.label} tone={o.tone} tip={o.tip} />
            {/* The ERP's own word beside the verdict, never instead of it: the
                classification collapses the source vocabulary into three
                values, and `expired` and `sent` both land on "No outcome". */}
            <Meta>{`ERP status: ${dash(quote.source_status)}`}</Meta>
          </Stack>
        </Box>

        <Box sx={{ mt: 2.5 }}>
          <FactTable
            label={`Quote ${dash(quote.number)}`}
            rows={[
              ["Value", quote.value === null ? "—" : money(quote.value)],
              ["Raised", quote.raised_on],
              ["Expires", dash(quote.expires_on)],
              ["Decided", dash(quote.decided_on)],
              ["Customer opened", dash(quote.opened_at)],
              ...(showCompany ? [["Book", quote.company]] : []),
              ["ERP reference", quote.quote_document_ref],
            ]}
          />
        </Box>

        {fields.length > 0 && (
          <Box sx={{ mt: 2.5 }}>
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

        {/* Printed on every quote, without exception. The platform reads these
            documents at header grain, and a panel that simply omitted the lines
            would leave a reader with the same unanswered silence that made this
            screen necessary in the first place. */}
        <Box sx={{ mt: 3 }}>
          <Meta>
            The platform reads these quotes at header level, so the lines that
            were on this one are not held here — open {dash(quote.number)} in
            your ERP to see them. Nothing on this panel can be edited: a change
            typed here would be overwritten by the next sync.
          </Meta>
        </Box>
      </Box>
    </Drawer>
  );
}
