import * as React from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import type { BookItem, CatalogueRecord, ItemSearch as Results } from "../types";
import { EmptyState, LoadingState, Meta, PanelMark, StatusChip } from "../platform/kit";
import { api } from "../api";

/** Wait this long after the last keystroke before asking the server.
 *
 *  The catalogue side runs a nearest-neighbour pass over the whole decoded
 *  book, so a request per character would be a request per character of real
 *  work. Long enough that typing a part number is one search, short enough
 *  that it does not feel like a button. */
const DEBOUNCE_MS = 300;

/** Below this many characters, do not search at all.
 *
 *  Not a performance guard — the server caps its own read. "C" matches most of
 *  a cutting-tool master, and a list of twenty arbitrary rows is worse than no
 *  list: it invites a person to pick from it. */
const MIN_QUERY = 2;

/** Find an item by hand, in the catalogue and in this company's own books.
 *
 *  **Why this exists.** Everything above it in the drawer is the engine's
 *  answer — ranked candidates, each compared against the request and carrying
 *  a relationship. When the engine has no answer, that list is empty and the
 *  drawer used to end there: a line could not be pointed at an item at all,
 *  however plainly the person knew which one it was. The server has always
 *  accepted a code that was in no candidate list; this is the way to name one.
 *
 *  **The two lists are not merged, and that is the design.** A catalogue
 *  record and a book item are different claims — "this product exists, and
 *  here is what its designation decodes to" against "we already sell this,
 *  under this code" — and the same physical insert can be in one and not the
 *  other. Merging them would need a rule for when two rows are the same
 *  product, which is the identity problem, and getting it wrong silently is
 *  how a quote ends up against a code the books cannot fulfil.
 *
 *  Nothing here is scored against the request. A `rel` is this organization's
 *  equivalence policy applied to a comparison (CLAUDE.md §1); a search
 *  compares nothing, so it claims nothing. Selecting a row is a substitution
 *  on this quote and never an asserted identity — the server refuses to record
 *  one without a proposal of its own behind it.
 */
export function ItemSearch({
  quoteId, token, readOnly = false, onSelect,
}: {
  quoteId: string;
  token: string;
  /** The reader may look the item up and may not put it on the line. */
  readOnly?: boolean;
  /** `manual` is always true from here: nothing in these lists was ranked. */
  onSelect: (code: string, manual: boolean) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState<Results | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_QUERY) {
      setResults(null);
      setError(null);
      setBusy(false);
      return;
    }
    // `cancelled` rather than an AbortController because the thing being
    // guarded is the *state write*, not the request: two searches in flight
    // can land out of order, and the slower one overwriting the newer answer
    // is the bug — a list that does not match the box it is under.
    let cancelled = false;
    setBusy(true);
    const timer = window.setTimeout(() => {
      api.searchItems(token, quoteId, q)
        .then((r) => { if (!cancelled) { setResults(r); setError(null); } })
        .catch((e: Error) => { if (!cancelled) { setError(e.message); setResults(null); } })
        .finally(() => { if (!cancelled) setBusy(false); });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, quoteId, token]);

  const typed = query.trim().length >= MIN_QUERY;
  const cat = results?.catalogue;
  const books = results?.books;
  const nothing = Boolean(
    results && cat?.available && cat.records.length === 0 && books?.records.length === 0);

  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
      <PanelMark>Choose an item by hand</PanelMark>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        Search this company&rsquo;s catalogue and its item master. Picking one
        sets the supply product for this line only.
      </Typography>
      <TextField
        fullWidth
        size="small"
        label="Item code or description"
        placeholder="CNMG120408, or 12mm carbide drill"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        slotProps={{
          htmlInput: { "aria-label": "Search for an item by code or description" },
          input: {
            endAdornment: busy ? (
              <InputAdornment position="end">
                <CircularProgress size={16} aria-label="Searching" />
              </InputAdornment>
            ) : undefined,
          },
        }}
      />

      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          The search did not run: {error}
        </Alert>
      )}

      {busy && !results && <Box sx={{ mt: 1.5 }}><LoadingState rows={2} /></Box>}

      {results && (
        <Stack spacing={1.5} sx={{ mt: 1.5 }}>
          {/* Both sides empty is one answer, said once. Headed sections each
              reporting their own nothing, over an empty state saying it a
              third time, is three sentences for a reader who needs one — and
              the one they need is what to try instead. */}
          {nothing ? (
            <EmptyState
              flat
              title="No item matches that"
              reason={
                "Nothing in this company's catalogue"
                + (cat && cat.searched > 0
                  ? ` (${cat.searched.toLocaleString()} records searched)` : "")
                + " or its item master reads like that. Try the bare part number"
                + " without its grade, or a word from the description."
              }
            />
          ) : (
            <>
              {/* The catalogue half. Its refusal is an Alert and not an empty
                  list, because "there was nothing to search" is a fact about
                  this deployment and an empty list would read as a fact about
                  the product — CLAUDE.md §1, absence of evidence is not a
                  pass. */}
              <Box>
                <PanelMark>In the catalogue</PanelMark>
                {!cat?.available ? (
                  <Alert severity="info" sx={{ mt: 0.5 }}>
                    {cat?.reason ?? "The catalogue could not be searched."}
                  </Alert>
                ) : cat.records.length === 0 ? (
                  <Meta>
                    Nothing in this company&rsquo;s catalogue reads like that
                    {cat.searched > 0 && ` (${cat.searched.toLocaleString()} records searched)`}.
                  </Meta>
                ) : (
                  cat.records.map((r) => (
                    <CatalogueRow key={r.code} record={r} readOnly={readOnly}
                                  onSelect={() => onSelect(r.code, true)} />
                  ))
                )}
              </Box>

              <Box>
                <PanelMark>In your item master</PanelMark>
                {books && books.records.length > 0 ? (
                  books.records.map((b) => (
                    <BookRow key={`${b.externalId}:${b.code}`} item={b} readOnly={readOnly}
                             onSelect={() => onSelect(b.code, true)} />
                  ))
                ) : (
                  <Meta>Nothing in this company&rsquo;s synced item master matches.</Meta>
                )}
              </Box>
            </>
          )}
        </Stack>
      )}

      {!typed && !busy && (
        <Meta sx={{ mt: 1 }}>
          Type at least {MIN_QUERY} characters.
        </Meta>
      )}
    </Paper>
  );
}

function CatalogueRow({
  record, readOnly, onSelect,
}: { record: CatalogueRecord; readOnly: boolean; onSelect: () => void }) {
  return (
    <div className="cand">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="code">{record.code}</span>
        {record.catalogue && <StatusChip label={record.catalogue} tone="neutral" dense />}
        {record.grade && (
          <StatusChip
            label={record.grade}
            tone="info"
            dense
            tip="The carbide grade as the maker's catalogue words it."
          />
        )}
        {/* How alike the *descriptions* read — never a fit. `null` is the
            identifier pass, where the record was found because it is the code
            and nothing was measured, so nothing is shown. */}
        {record.similarity !== null && (
          <span className="text-muted" style={{ fontSize: 11 }}>
            reads {(record.similarity * 100).toFixed(0)}% alike
          </span>
        )}
        <span style={{ flex: 1 }} />
        {!readOnly && (
          <Button variant="contained" size="small" onClick={onSelect}
                  title={`Set ${record.code} as the supply product`}>
            Select
          </Button>
        )}
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>{record.desc}</div>
    </div>
  );
}

function BookRow({
  item, readOnly, onSelect,
}: { item: BookItem; readOnly: boolean; onSelect: () => void }) {
  return (
    <div className="cand">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="code">{item.code}</span>
        <StatusChip
          label="in our book"
          tone="info"
          dense
          tip="From this company's own item master rather than the manufacturer catalogue — something the business already sells."
        />
        {/* An inactive item exists in the ledger and cannot go on a document,
            so it is shown and marked rather than hidden: a person searching
            for it needs to know it is there and why it will not work. */}
        {!item.active && (
          <StatusChip
            label="inactive"
            tone="warn"
            dense
            tip="Marked inactive in the item master, so it cannot be put on a document until somebody reactivates it."
          />
        )}
        {item.manufacturer && <Meta inline>{item.manufacturer}</Meta>}
        <span style={{ flex: 1 }} />
        {!readOnly && (
          <Button variant="contained" size="small" onClick={onSelect}
                  title={`Set ${item.code} as the supply product`}>
            Select
          </Button>
        )}
      </div>
      {item.name !== item.code && (
        <div className="text-muted" style={{ fontSize: 12 }}>{item.name}</div>
      )}
    </div>
  );
}
