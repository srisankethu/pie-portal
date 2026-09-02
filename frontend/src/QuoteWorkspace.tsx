/** The quote workspace — every draft the desk is working on, in one list.
 *
 * There was no such place. The Quote Builder kept exactly one draft, in the
 * browser that made it, under one `localStorage` key: a second quote replaced
 * the first, a colleague could see neither, and a server restart forgot the
 * lines behind both. Drafts are rows on the server now (`quote_drafts`), and
 * this screen is the list of them: who each is for, whose it is, how far
 * along it is, and — for the ones the approval policy has cleared — the
 * button that sends it into the books.
 *
 * **Shared across the organization by design.** A quote started on one desk
 * is on every desk's list. The row says who started it and who touched it
 * last, which is the whole of the coordination a small sales team needs;
 * locking a draft to its author is how quotes wait on somebody's day off.
 *
 * **The status is the send gate's own answer.** `readiness` is computed on
 * the server by the same functions the send runs — blockers, unpriced lines,
 * the customer, the approval gate — so a row reading "Ready to send" is one
 * the send would accept, and one reading "Needs approval" is one it would
 * refuse. The client only maps the words. Nothing on this screen is cost:
 * the total is the quote's own selling figure.
 */
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { CompanyRequired, api, forgetLegacyDraft } from "./api";
import type { QuoteCompany } from "./api";
import { CompanyPicker } from "./components/CompanyPicker";
import { money } from "./money";
import { DataGrid, numeric, text } from "./platform/DataGrid";
import type { ColDef } from "./platform/DataGrid";
import {
  CurrencyValue, EmptyState, ErrorState, FilterChip, FilterPanel, LoadingState,
  SectionHeader, StatusChip, TOUCH, type Tone,
} from "./platform/kit";
import { pathFor } from "./platform/route";
import type { PlatformSession } from "./platform/types";
import { since } from "./when";
import type { QuoteDraftSummary, QuoteReadiness } from "./types";

const SUB =
  "Every quote the desk is working on, shared across the organization. Open a "
  + "draft to price it; once the approval policy has cleared it, send it into the "
  + "customer's books from here or from the builder.";

/** The words for each readiness the server reports, and their tone.
 *
 *  `tip` carries the meaning, because a chip reading "Needs approval" that
 *  cannot say *what* needs approving is decoration. */
const READINESS: Record<QuoteReadiness, { label: string; tone: Tone; tip: string }> = {
  EMPTY: {
    label: "Empty", tone: "neutral",
    tip: "No lines yet. Open it and paste the RFQ.",
  },
  NEEDS_ATTENTION: {
    label: "Needs attention", tone: "warn",
    tip: "A line is unresolved, ambiguous, unconfirmed or unpriced. The send "
      + "refuses until every line is settled.",
  },
  NO_CUSTOMER: {
    label: "Needs a customer", tone: "warn",
    tip: "The lines are ready but nobody has said whose quote this is. Choose "
      + "the customer in the builder.",
  },
  NEEDS_APPROVAL: {
    label: "Needs approval", tone: "bad",
    tip: "A line is priced outside policy and no approval has been requested. "
      + "Open the line and request one.",
  },
  AWAITING_APPROVAL: {
    label: "Awaiting approval", tone: "info",
    tip: "An approval request is with a manager. The send is enabled the "
      + "moment it is granted.",
  },
  READY: {
    label: "Ready to send", tone: "good",
    tip: "Every line is settled and the approval policy is satisfied. Sending "
      + "creates the document in the customer's books.",
  },
  SENT: {
    label: "Sent", tone: "good",
    tip: "A document exists in the books for exactly what is on this quote. "
      + "Changing a line reopens it.",
  },
};

/** The filter row. Coarser than the seven readiness values on purpose — a
 *  desk asks "what is waiting on me", "what is waiting on a manager" and
 *  "what can go out", not for seven piles. */
const FILTERS: [string, string, readonly QuoteReadiness[]][] = [
  ["ALL", "All", ["EMPTY", "NEEDS_ATTENTION", "NO_CUSTOMER", "NEEDS_APPROVAL",
                  "AWAITING_APPROVAL", "READY", "SENT"]],
  ["WORK", "Needs work", ["EMPTY", "NEEDS_ATTENTION", "NO_CUSTOMER", "NEEDS_APPROVAL"]],
  ["WAIT", "Awaiting approval", ["AWAITING_APPROVAL"]],
  ["READY", "Ready to send", ["READY"]],
  ["SENT", "Sent", ["SENT"]],
];

/** What the customer cell prints for a draft nobody has assigned yet. Words
 *  rather than a blank, because a blank in a column of names reads as a
 *  loading failure. Exported for the test. */
export function customerLabel(q: Pick<QuoteDraftSummary, "customer">): string {
  return q.customer.trim() || "No customer yet";
}

export default function QuoteWorkspace({ session }: { session: PlatformSession }) {
  const t = session.token;
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();

  const [rows, setRows] = useState<QuoteDraftSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  /** The organization reads several companies' books and the server refused
   *  to choose one for the new draft. Asked, then retried with the answer. */
  const [companyChoice, setCompanyChoice] = useState<QuoteCompany[] | null>(null);
  const [toDelete, setToDelete] = useState<QuoteDraftSummary | null>(null);

  const load = useCallback(() => {
    setError(null);
    return api.listQuotes(t).then(setRows).catch((e) => setError((e as Error).message));
  }, [t]);

  useEffect(() => {
    // The one copy the old builder kept in this browser is stale by
    // definition now — the server holds every draft — so it goes.
    forgetLegacyDraft();
    void load();
  }, [load]);

  async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      enqueueSnackbar((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Start a draft with no customer and open it. The customer is chosen in
   *  the builder — after the RFQ is pasted, if that is the order it arrived
   *  in — rather than demanded here as the price of getting a number. */
  const startQuote = (connectionId?: string) =>
    guard(async () => {
      let q;
      try {
        q = await api.createQuote(t, "", undefined, connectionId);
      } catch (e) {
        if (e instanceof CompanyRequired) {
          setCompanyChoice(e.companies);
          return;
        }
        throw e;
      }
      setCompanyChoice(null);
      navigate(pathFor("quotes", q.id));
    });

  /** Send a draft the policy has cleared. The same endpoint the builder's
   *  button calls, so the refusal — if the gate has moved since the list was
   *  read — arrives in the same words. */
  const send = (q: QuoteDraftSummary) =>
    guard(async () => {
      const r = await api.createEstimate(t, q.id);
      enqueueSnackbar(r.message, {
        variant: r.ok ? "success" : "default",
        autoHideDuration: r.ok ? 8000 : 5000,
      });
      await load();
    });

  const remove = (q: QuoteDraftSummary) =>
    guard(async () => {
      await api.deleteQuote(t, q.id);
      setToDelete(null);
      enqueueSnackbar(`${q.number} removed`);
      await load();
    });

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const [key, , kinds] of FILTERS) {
      c[key] = (rows ?? []).filter((r) => kinds.includes(r.readiness)).length;
    }
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    if (!rows) return [];
    const kinds = FILTERS.find(([k]) => k === filter)?.[2] ?? FILTERS[0][2];
    const q = search.trim().toLowerCase();
    return rows.filter((r) =>
      kinds.includes(r.readiness)
      && (!q || [r.number, r.customer, r.createdBy, r.updatedBy, r.sent?.number]
        .filter(Boolean).join(" ").toLowerCase().includes(q)));
  }, [rows, filter, search]);

  const open = useCallback(
    (q: QuoteDraftSummary) => navigate(pathFor("quotes", q.id)), [navigate]);

  const columns = useMemo<ColDef<QuoteDraftSummary>[]>(() => [
    text("number", "Quote", { flex: 0, minWidth: 110, width: 110 }),
    text("customer", "Customer", {
      minWidth: 200,
      valueGetter: (p) => (p.data ? customerLabel(p.data) : ""),
      cellRenderer: (p: { data?: QuoteDraftSummary }) =>
        p.data && !p.data.customer.trim()
          ? <Box component="span" sx={{ color: "text.secondary", fontStyle: "italic" }}>
              {customerLabel(p.data)}
            </Box>
          : customerLabel(p.data ?? { customer: "" }),
    }),
    numeric("lineCount", "Lines", (v) => String(v), { width: 90, flex: 0 }),
    numeric("total", "Total", (v) => money(v), { width: 140, flex: 0 }),
    {
      field: "readiness", headerName: "Status", width: 170, flex: 0,
      cellRenderer: (p: { data?: QuoteDraftSummary }) => {
        if (!p.data) return null;
        const r = READINESS[p.data.readiness];
        return <StatusChip label={r.label} tone={r.tone} tip={r.tip} />;
      },
    },
    text("updatedAt", "Last change", {
      minWidth: 170,
      valueGetter: (p) => p.data?.updatedAt ?? "",
      cellRenderer: (p: { data?: QuoteDraftSummary }) =>
        p.data ? changeLabel(p.data) : "",
    }),
    {
      headerName: "", width: 250, flex: 0, sortable: false, filter: false,
      cellClass: "ag-actions",
      cellRenderer: (p: { data?: QuoteDraftSummary }) =>
        p.data ? (
          <Actions q={p.data} busy={busy} onOpen={open} onSend={send}
                   onDelete={setToDelete} />
        ) : null,
    },
  ], [busy, open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box>
      <SectionHeader
        title="Quotes"
        sub={SUB}
        actions={
          <Button variant="contained" size="small" sx={TOUCH}
                  onClick={() => startQuote()} disabled={busy}>
            New quote
          </Button>
        }
      />

      {error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingState rows={4} label="Reading the workspace…" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No quotes yet"
          reason="A quote starts empty — paste the RFQ, choose the customer, price
                  each line — and stays here, for everyone on the desk, until it
                  is sent."
          action={
            <Button variant="contained" onClick={() => startQuote()} disabled={busy}>
              Start the first quote
            </Button>
          }
        />
      ) : (
        <>
          <FilterPanel>
            {FILTERS.map(([key, label]) => (
              <FilterChip
                key={key}
                label={label}
                count={counts[key] ?? 0}
                selected={filter === key}
                alert={key === "WORK" && (counts[key] ?? 0) > 0}
                onClick={() => setFilter(key)}
              />
            ))}
            <Box sx={{ flex: 1 }} />
            <TextField
              size="small"
              placeholder="Search number, customer, who"
              aria-label="Search quotes"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{ width: { xs: "100%", sm: 260 }, "& .MuiInputBase-root": TOUCH }}
            />
          </FilterPanel>

          <DataGrid<QuoteDraftSummary>
            rows={visible}
            columns={columns}
            getRowId={(r) => r.id}
            onRowClick={open}
            onRowActivate={open}
            ariaLabel="Quotes in the workspace"
            empty={
              <EmptyState
                title="Nothing matches"
                reason="Clear the filter or the search term."
                action={
                  <Button variant="outlined"
                          onClick={() => { setFilter("ALL"); setSearch(""); }}>
                    Show all {rows.length} quotes
                  </Button>
                }
              />
            }
            renderNarrow={(q) => (
              <DraftCard key={q.id} q={q} busy={busy} onOpen={open} onSend={send}
                         onDelete={setToDelete} />
            )}
          />
        </>
      )}

      {companyChoice && (
        <CompanyPicker
          open
          busy={busy}
          companies={companyChoice}
          customer=""
          onPick={(id) => startQuote(id)}
          onCancel={() => setCompanyChoice(null)}
        />
      )}

      <Dialog open={toDelete !== null} onClose={() => setToDelete(null)}>
        <DialogTitle>Remove {toDelete?.number}?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {toDelete && toDelete.lineCount > 0
              ? `This draft has ${toDelete.lineCount} line(s)${toDelete.customer
                  ? ` for ${toDelete.customer}` : ""}. Removing it cannot be undone.`
              : "This draft is empty. Removing it cannot be undone."}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setToDelete(null)}>Keep it</Button>
          <Button color="error" variant="contained" disabled={busy}
                  onClick={() => toDelete && remove(toDelete)}>
            Remove
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

/** "12 min ago · R. Nair". The person is the part a shared list needs. */
function changeLabel(q: QuoteDraftSummary): string {
  const who = q.updatedBy || q.createdBy;
  return who ? `${since(q.updatedAt)} · ${who}` : since(q.updatedAt);
}

/** The controls a row carries. `Send` only where the server said READY —
 *  the same rule the builder's button follows — and `Remove` only while the
 *  draft is unsent, because a sent quote is a record. */
function Actions({ q, busy, onOpen, onSend, onDelete }: {
  q: QuoteDraftSummary;
  busy: boolean;
  onOpen: (q: QuoteDraftSummary) => void;
  onSend: (q: QuoteDraftSummary) => void;
  onDelete: (q: QuoteDraftSummary) => void;
}) {
  return (
    <Stack direction="row" spacing={0.5} onClick={(e) => e.stopPropagation()}>
      <Button size="small" variant="outlined" onClick={() => onOpen(q)}>
        Open
      </Button>
      {q.readiness === "READY" && (
        <Button size="small" variant="contained" disabled={busy}
                onClick={() => onSend(q)}
                title="Create the document in the customer's books">
          Send
        </Button>
      )}
      {!q.sent && (
        <Button size="small" color="error" variant="text" disabled={busy}
                onClick={() => onDelete(q)}>
          Remove
        </Button>
      )}
    </Stack>
  );
}

/** One draft as a card, for the phone rendering the grid hands off to. */
function DraftCard({ q, busy, onOpen, onSend, onDelete }: {
  q: QuoteDraftSummary;
  busy: boolean;
  onOpen: (q: QuoteDraftSummary) => void;
  onSend: (q: QuoteDraftSummary) => void;
  onDelete: (q: QuoteDraftSummary) => void;
}) {
  const r = READINESS[q.readiness];
  return (
    <Card variant="outlined" sx={{ p: 1.5, mb: 1 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.5 }}>
        <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
          {q.number}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <StatusChip label={r.label} tone={r.tone} tip={r.tip} />
      </Stack>
      <Typography variant="body2"
                  sx={q.customer.trim() ? undefined
                    : { color: "text.secondary", fontStyle: "italic" }}>
        {customerLabel(q)}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {q.lineCount} line(s) · <CurrencyValue value={q.total} /> · {changeLabel(q)}
      </Typography>
      <Box sx={{ mt: 1 }}>
        <Actions q={q} busy={busy} onOpen={onOpen} onSend={onSend} onDelete={onDelete} />
      </Box>
    </Card>
  );
}
