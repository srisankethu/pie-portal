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
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { CompanyRequired, api, forgetLegacyDraft } from "./api";
import type { QuoteCompany } from "./api";
import { CompanyPicker } from "./components/CompanyPicker";
import { ErpQuoteList } from "./components/ErpQuoteList";
import { CompanyFilter, useCompanyFilter } from "./platform/CompanyFilter";
import { money } from "./money";
import { DataGrid, numeric, text } from "./platform/DataGrid";
import type { ColDef } from "./platform/DataGrid";
import {
  CurrencyValue, EmptyState, ErrorState, FilterChip, FilterPanel, LoadingState,
  Meta, SectionHeader, StatusChip, TOUCH, type Tone,
} from "./platform/kit";
import { pathFor } from "./platform/route";
import type { PlatformSession } from "./platform/types";
import { since } from "./when";
import type { ErpQuoteBook, QuoteDraftSummary, QuoteReadiness } from "./types";

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
  MISSING_DETAILS: {
    label: "Details missing", tone: "warn",
    tip: "A detail this organization requires on every quote is empty. Open "
      + "the quote and fill it in under Quote details.",
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
  ["ALL", "All", ["EMPTY", "NEEDS_ATTENTION", "MISSING_DETAILS", "NO_CUSTOMER",
                  "NEEDS_APPROVAL", "AWAITING_APPROVAL", "READY", "SENT"]],
  ["MINE", "Mine", ["EMPTY", "NEEDS_ATTENTION", "MISSING_DETAILS", "NO_CUSTOMER",
                    "NEEDS_APPROVAL", "AWAITING_APPROVAL", "READY", "SENT"]],
  ["WORK", "Needs work", ["EMPTY", "NEEDS_ATTENTION", "MISSING_DETAILS", "NO_CUSTOMER",
                          "NEEDS_APPROVAL"]],
  ["WAIT", "Awaiting approval", ["AWAITING_APPROVAL"]],
  ["READY", "Ready to send", ["READY"]],
  ["SENT", "Sent", ["SENT"]],
];

/** The ERP tab's piles: the sync's own classification of the ERP's word, and
 *  nothing this screen decided. "No outcome" is the common case and not a
 *  fault — silence is never read as a loss. */
const ERP_FILTERS: [string, string][] = [
  ["ALL", "All"], ["UNRECORDED", "No outcome"], ["WON", "Won"], ["LOST", "Lost"],
];

/** What the customer cell prints for a draft nobody has assigned yet. Words
 *  rather than a blank, because a blank in a column of names reads as a
 *  loading failure. Exported for the test. */
export function customerLabel(q: Pick<QuoteDraftSummary, "customer">): string {
  return q.customer.trim() || "No customer yet";
}

/** The ERP's word for a document this platform wrote, as a chip: the tone is
 *  the sync's classification (won / lost / neither), the label is the ERP's
 *  own status verbatim. Exported for the test. */
export function erpWord(erp: NonNullable<QuoteDraftSummary["sent"]>["erp"]):
    { label: string; tone: Tone } | null {
  if (!erp) return null;
  const tone: Tone = erp.outcome === "WON" ? "good" : erp.outcome === "LOST" ? "bad" : "neutral";
  return { label: erp.sourceStatus.replace(/_/g, " ") || "—", tone };
}

/** The document a sent draft became, and what the ERP says about it. */
function SentCell({ sent }: { sent: NonNullable<QuoteDraftSummary["sent"]> }) {
  const word = erpWord(sent.erp);
  return (
    <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
      <Typography variant="body2" sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
        {sent.number}
      </Typography>
      {word && (
        <StatusChip
          label={`${sent.systemLabel}: ${word.label}`} tone={word.tone}
          tip={`What ${sent.systemLabel} itself says about this document, as of the last sync.`}
        />
      )}
    </Stack>
  );
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
  /** Held across the company question, so an answer to "which book?" does not
   *  lose the customer the caller arrived with. */
  const [pendingCustomer, setPendingCustomer] =
    useState<{ id: string; name: string } | null>(null);
  const [toDelete, setToDelete] = useState<QuoteDraftSummary | null>(null);

  /** The two lists this screen shows, and they are fetched independently.
   *
   *  A failure reading the ERP book must not blank the drafts: the drafts are
   *  this desk's own work in progress and the book is a read of somebody else's
   *  system, so one being unavailable is not a reason to hide the other. The
   *  tab carries its own error for the same reason. */
  const [tab, setTab] = useState<"drafts" | "erp">("drafts");
  const [book, setBook] = useState<ErpQuoteBook | null>(null);
  const [bookError, setBookError] = useState<string | null>(null);
  /** Which outcome the ERP tab shows. The ERP's own classification — silence
   *  is "No outcome", never a loss — so the piles are the sync's, not this
   *  screen's. */
  const [erpFilter, setErpFilter] = useState("ALL");
  /* One company or all, on each tab — the directory's own control, over the
     rows already loaded, and rendering nothing below two companies. The
     drafts carry the company their lines were priced from; the ERP rows carry
     the book that raised them. Two instances because the two lists are
     fetched and narrowed independently. */
  const draftCompany = useCompanyFilter(rows ?? []);
  const erpCompany = useCompanyFilter(book?.quotes_listed ?? []);

  const load = useCallback(() => {
    setError(null);
    return api.listQuotes(t).then(setRows).catch((e) => setError((e as Error).message));
  }, [t]);

  const loadBook = useCallback(() => {
    setBookError(null);
    return api.listErpQuotes(t).then(setBook)
      .catch((e) => setBookError((e as Error).message));
  }, [t]);

  useEffect(() => {
    // The one copy the old builder kept in this browser is stale by
    // definition now — the server holds every draft — so it goes.
    forgetLegacyDraft();
    void load();
    // Fetched on arrival rather than on first tab click, so the count on the
    // tab is true before somebody presses it. A tab labelled with a count it
    // only learns after being opened is a tab nobody opens.
    void loadBook();
  }, [load, loadBook]);

  /* Arriving from an account page: `?customer=<id>&name=<label>` means "start
   * one for them". The parameters are cleared before the draft is asked for,
   * so a refresh of the resulting URL — or a Back into this screen — does not
   * open a second empty quote against the same customer. */
  const [params, setParams] = useSearchParams();
  const askedFor = params.get("customer");
  const askedName = params.get("name");
  useEffect(() => {
    if (!askedFor) return;
    setParams({}, { replace: true });
    void startQuote(undefined, { id: askedFor, name: askedName ?? "" });
    // Once, on arrival. `startQuote` is rebuilt every render and would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askedFor, askedName]);

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

  /** Open a blank quote form and go to it. **Nothing is created.**
   *
   *  This used to write the row and mint the number on the press, so opening
   *  the builder and changing your mind left an empty QB-0042 on this list —
   *  everybody's list — for good, and the number was spent. The form is
   *  unsaved until somebody presses Save quote in the builder, which is the
   *  only place a quote is now made.
   *
   *  Normally with no customer: it is chosen in the builder — after the RFQ is
   *  pasted, if that is the order it arrived in — rather than demanded here as
   *  the price of getting a number.
   *
   *  With one when the caller named it. An account page links here carrying the
   *  customer it was showing, because "quote this account" is the action that
   *  page prepares somebody for and it used to mean finding the same name again
   *  in a dialog. The creating stays here rather than there: this screen already
   *  owns the company chooser for an organization reading more than one set of
   *  books, and that answer has to survive being asked. */
  const startQuote = (connectionId?: string, forCustomer?: { id: string; name: string }) =>
    guard(async () => {
      let q;
      try {
        q = await api.createQuoteForm(
          t, forCustomer?.name ?? "", forCustomer?.id, connectionId,
        );
      } catch (e) {
        if (e instanceof CompanyRequired) {
          setCompanyChoice(e.companies);
          setPendingCustomer(forCustomer ?? null);
          return;
        }
        throw e;
      }
      setCompanyChoice(null);
      setPendingCustomer(null);
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
      // The document was created and the outcome could not follow it: a second
      // sentence beside the success, in the same words the builder shows.
      if (r.warning) enqueueSnackbar(r.warning, { autoHideDuration: 8000 });
      await load();
    });

  const remove = (q: QuoteDraftSummary) =>
    guard(async () => {
      await api.deleteQuote(t, q.id);
      setToDelete(null);
      enqueueSnackbar(`${q.number} removed`);
      await load();
    });

  // "Mine" is the one filter that is not a readiness: the quotes this
  // person owns, whatever state they are in. Every quote has an owner now,
  // and the owner is who the desk asks "where is that quote" of.
  const inFilter = useCallback((r: QuoteDraftSummary, key: string) => {
    const kinds = FILTERS.find(([k]) => k === key)?.[2] ?? FILTERS[0][2];
    return kinds.includes(r.readiness) && (key !== "MINE" || r.ownerId === session.user_id);
  }, [session.user_id]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const [key] of FILTERS) {
      c[key] = (rows ?? []).filter((r) => inFilter(r, key)).length;
    }
    return c;
  }, [rows, inFilter]);

  const visible = useMemo(() => {
    if (!rows) return [];
    const q = search.trim().toLowerCase();
    const company = draftCompany.company;
    return rows.filter((r) =>
      inFilter(r, filter)
      && (!company || r.origin?.connection_id === company)
      && (!q || [r.number, r.customer, r.company, r.owner, r.updatedBy, r.sent?.number]
        .filter(Boolean).join(" ").toLowerCase().includes(q)));
  }, [rows, filter, search, inFilter, draftCompany.company]);

  /* The ERP tab's piles and its narrowed list. Counted over the whole page the
     server sent, so a chip says how big each pile is before it is pressed. */
  const erpCounts = useMemo(() => {
    const c: Record<string, number> = { ALL: 0, UNRECORDED: 0, WON: 0, LOST: 0 };
    for (const q of book?.quotes_listed ?? []) {
      c.ALL += 1;
      c[q.outcome] = (c[q.outcome] ?? 0) + 1;
    }
    return c;
  }, [book]);
  const erpVisible = useMemo(() => {
    const company = erpCompany.company;
    return (book?.quotes_listed ?? []).filter((q) =>
      (erpFilter === "ALL" || q.outcome === erpFilter)
      && (!company || q.origin?.connection_id === company));
  }, [book, erpFilter, erpCompany.company]);

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
    // Only where the drafts come from more than one company — the rule the
    // source badges follow: one company is one word repeated down a column.
    ...(draftCompany.show
      ? [text<QuoteDraftSummary>("company", "Book", { minWidth: 150, flex: 0, width: 170 })]
      : []),
    text("owner", "Owner", { minWidth: 140, flex: 0, width: 160 }),
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
    {
      // The document a sent quote became, and — once a sync has read it back —
      // the ERP's own word for it. "Sent" in the status column is this
      // platform's claim that a document was written; the chip here is what
      // the ERP says about that document, verbatim, and absent until synced.
      field: "sent", headerName: "Document", width: 220, flex: 0,
      valueGetter: (p) => p.data?.sent?.number ?? "",
      cellRenderer: (p: { data?: QuoteDraftSummary }) =>
        p.data?.sent ? <SentCell sent={p.data.sent} /> : null,
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
      // The column carries its own controls, so a click in it must not also
      // open the row. `stopPropagation` on the React event is not enough —
      // ag-grid's row click is its own listener — which is why "Remove"
      // opened the quote: the grid's rule is this flag, and it was missing.
      context: { noRowClick: true },
      cellRenderer: (p: { data?: QuoteDraftSummary }) =>
        p.data ? (
          <Actions q={p.data} busy={busy} onOpen={open} onSend={send}
                   onDelete={setToDelete} />
        ) : null,
    },
  ], [busy, open, draftCompany.show]); // eslint-disable-line react-hooks/exhaustive-deps

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

      {/* Two panels of one screen, so a tab list rather than a `FilterChip` row
          — `ui-standards` §9, and the same idiom `IdentityScreen` uses. The
          chips below narrow *within* the drafts; this switches between the
          desk's own work and what the connected books hold, which are two
          different things and were never both visible here before. */}
      <Tabs
        value={tab}
        onChange={(_e, v) => setTab(v as "drafts" | "erp")}
        aria-label="Which quotes"
        sx={{ mb: 2 }}
      >
        <Tab
          value="drafts" id="q-tab-drafts" aria-controls="q-panel-drafts"
          label={
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <span>Drafts</span>
              {rows !== null && <Chip size="small" label={rows.length} />}
            </Stack>
          }
        />
        <Tab
          value="erp" id="q-tab-erp" aria-controls="q-panel-erp"
          label={
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <span>From your ERP</span>
              {book !== null && <Chip size="small" label={book.count} />}
            </Stack>
          }
        />
      </Tabs>

      {tab === "erp" ? (
        <Box role="tabpanel" id="q-panel-erp" aria-labelledby="q-tab-erp">
          {bookError ? (
            <ErrorState error={bookError} onRetry={() => void loadBook()} />
          ) : book === null ? (
            <LoadingState rows={4} label="Reading the connected books…" />
          ) : (
            <>
              {/* Said in words because the grid cannot: the page is capped, and
                  a reader who cannot tell a full book from a first page cannot
                  tell this screen from the bug it was built to fix. */}
              {book.listed < book.count && (
                <Meta>
                  Showing the {book.listed} most recent of {book.count}.
                </Meta>
              )}
              {book.quotes_listed.length > 0 && (
                <FilterPanel>
                  {ERP_FILTERS.map(([key, label]) => (
                    <FilterChip
                      key={key}
                      label={label}
                      count={erpCounts[key] ?? 0}
                      selected={erpFilter === key}
                      onClick={() => setErpFilter(key)}
                    />
                  ))}
                  <Box sx={{ flex: 1 }} />
                  <CompanyFilter options={erpCompany.options} value={erpCompany.company}
                                 onChange={erpCompany.setCompany} show={erpCompany.show} />
                </FilterPanel>
              )}
              <ErpQuoteList quotes={erpVisible}
                            showCompany={erpCompany.show}
                            emptyReason={book.quotes_listed.length
                              ? "Nothing matches this filter."
                              : book.empty_reason} />
            </>
          )}
        </Box>
      ) : (
      <Box role="tabpanel" id="q-panel-drafts" aria-labelledby="q-tab-drafts">
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
            <CompanyFilter options={draftCompany.options} value={draftCompany.company}
                           onChange={draftCompany.setCompany} show={draftCompany.show} />
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
                         onDelete={setToDelete} showCompany={draftCompany.show} />
            )}
          />
        </>
      )}
      </Box>
      )}

      {companyChoice && (
        <CompanyPicker
          open
          busy={busy}
          companies={companyChoice}
          customer=""
          onPick={(id) => startQuote(id, pendingCustomer ?? undefined)}
          onCancel={() => { setCompanyChoice(null); setPendingCustomer(null); }}
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
 *  draft is unsent, because a sent quote is a record. Neither for a quote
 *  this reader may not change (`canEdit`): every quote has an owner, and the
 *  server would answer 403 in the owner's name. */
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
      {q.readiness === "READY" && q.canEdit && (
        <Button size="small" variant="contained" disabled={busy}
                onClick={() => onSend(q)}
                title="Create the document in the customer's books">
          Send
        </Button>
      )}
      {!q.sent && q.canEdit && (
        <Button size="small" color="error" variant="text" disabled={busy}
                onClick={() => onDelete(q)}>
          Remove
        </Button>
      )}
    </Stack>
  );
}

/** One draft as a card, for the phone rendering the grid hands off to. */
function DraftCard({ q, busy, onOpen, onSend, onDelete, showCompany = false }: {
  q: QuoteDraftSummary;
  busy: boolean;
  onOpen: (q: QuoteDraftSummary) => void;
  onSend: (q: QuoteDraftSummary) => void;
  onDelete: (q: QuoteDraftSummary) => void;
  /** Name the book on the card — only where the list spans more than one. */
  showCompany?: boolean;
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
        {q.lineCount} line(s) · <CurrencyValue value={q.total} />
        {q.owner ? ` · ${q.owner}'s` : ""} · {changeLabel(q)}
        {showCompany && q.company ? ` · ${q.company}` : ""}
      </Typography>
      {q.sent && (
        <Box sx={{ mt: 0.5 }}>
          <SentCell sent={q.sent} />
        </Box>
      )}
      <Box sx={{ mt: 1 }}>
        <Actions q={q} busy={busy} onOpen={onOpen} onSend={onSend} onDelete={onDelete} />
      </Box>
    </Card>
  );
}
