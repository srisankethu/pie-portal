/** The Quote Builder — a screen of the platform, not a second application.
 *
 * It used to be the other half of a two-app shell: a `mode` flag swapped the
 * whole interface for one with its own brand bar, its own sign-in form and its
 * own session in `localStorage`. Opening it from Quotes therefore asked you to
 * sign in again and then displayed somebody else's name and role, because the
 * account you signed into there was one of two fixed demo accounts that
 * accepted any password. The URL did not change either, so Back went to
 * whatever preceded the platform rather than out of the builder.
 *
 * Now it is a route inside the shell, on the platform's own session: same nav,
 * same user, same sign-out, and `/quotes` is a link somebody can send.
 *
 * The draft still survives navigation — it is written to `localStorage` on
 * every change and read back on mount — which is what made a mode flag look
 * necessary in the first place.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, clearDraftQuote, loadDraftQuote, saveDraftQuote } from "./api";
import { CustomerPicker } from "./components/CustomerPicker";
import type { Line, Quote } from "./types";
import { IntakeModal } from "./components/IntakeModal";
import { SupplyDrawer } from "./components/SupplyDrawer";
import { LineGrid } from "./components/LineGrid";
import { NARROW_BREAKPOINT } from "./platform/DataGrid";
import { QuoteOutcomeBar } from "./components/QuoteOutcomeBar";
import { SummaryBar } from "./components/SummaryBar";
import { EmptyState, FilterChip, FilterPanel, LoadingState, SectionHeader, TOUCH }
  from "./platform/kit";
import { abilityFor } from "./platform/ability";
import type { PlatformSession } from "./platform/types";
import { useQuoteIntelligence } from "./useQuoteIntelligence";

const FILTERS: [string, string][] = [
  ["ALL", "All"],
  ["NEEDS", "Needs attention"],
  ["PROC", "Potential procurement"],
  ["BOOKS", "Missing Zoho item"],
  ["MANUAL", "Manual review"],
  ["UNRES", "Unresolved"],
  ["SUBST", "Substituted"],
  ["EXC", "Commercial exceptions"],
];


/** What this screen answers — the sentence the Quotes door used to carry on a
 *  page of its own, in front of the thing it was describing.
 *
 *  It used to end "It never pre-fills the field", and the field was pre-filled:
 *  a resolved line opens at the catalogue rate, so a four-line RFQ arrived
 *  priced with a Quotation total ready to send. The claim appeared three times —
 *  here, in the empty state, and in the rate column's own tooltip — while the
 *  behaviour was the opposite in all three. The default is worth keeping; a
 *  forty-line tender is not forty numbers to type. Saying so is the fix, and the
 *  rate cell now marks which numbers are still the catalogue's. */
const SUB =
  "Paste an RFQ and the engine resolves each line into a quote-ready product. "
  + "Quote context shows this customer's own price history and — for managers — the "
  + "cost and margin. A resolved line opens at the catalogue rate, marked “list” "
  + "until you price it; the number that goes out is yours.";

/** Said once per page load, not once per visit to this screen.
 *
 * Resuming is a fact worth announcing when the browser was closed and reopened.
 * Announcing it again every time somebody comes back from Approvals — which is
 * a mount, now that this is a route — is noise, and the status chip in the
 * header says it anyway. */
let resumeAnnounced = false;

function passesFilter(l: Line, f: string, flagged: Set<string>): boolean {
  switch (f) {
    case "EXC":
      return flagged.has(l.id);
    case "NEEDS":
      return l.flags.attention;
    case "PROC":
      return l.flags.procurement;
    case "BOOKS":
      return l.flags.missingBooks;
    case "MANUAL":
      return l.flags.manualReview;
    case "UNRES":
      return l.flags.unresolved;
    case "SUBST":
      return l.flags.substituted;
    case "MFLOOR":
      return !!l.economics?.below_floor;
    default:
      return true;
  }
}

export default function QuoteBuilder({ session }: { session: PlatformSession }) {
  const t = session.token;
  // Mirrors the server, which omits cost and margin for a salesperson rather
  // than sending them for the browser to hide. `ability.ts` is the one table
  // that answers this, so the answer here cannot drift from the nav's.
  const mgmt = abilityFor(session).can("read", "economics");
  const navigate = useNavigate();

  const [quote, setQuote] = useState<Quote | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  // Ids, not a `Record<id, boolean>`: the grid speaks ids, the discount call
  // takes ids, and a map that kept `false` entries made "how many are selected"
  // a filter over the keys rather than a length.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [drawerLineId, setDrawerLineId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Open when there is no quote to work on, and on demand from the header.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [draftStatus, setDraftStatus] = useState<string | null>(null);
  // Why the last attempt to send was refused. Held on the screen rather than
  // flashed, and cleared by the next change to the quote — which is exactly
  // when the sentence might stop being true.
  const [sendBlock, setSendBlock] = useState<string | null>(null);

  // One assessment for the whole quote — see useQuoteIntelligence.
  const ci = useQuoteIntelligence(quote, t);

  // The same `SnackbarProvider` the rest of the platform uses, rather than the
  // fixed-position div and 2.4s timer this used to hand-roll: two of them in
  // quick succession replaced each other, and 2.4s is too short to read a
  // sentence. `flash` keeps its name so every call site is unchanged.
  const { enqueueSnackbar } = useSnackbar();
  const flash = useCallback(
    (msg: string, variant: "default" | "success" = "default") => {
      enqueueSnackbar(msg, { variant, autoHideDuration: variant === "success" ? 8000 : 3000 });
    },
    [enqueueSnackbar],
  );

  // Resume the saved draft, or start a quote. Runs on mount rather than on
  // sign-in: the session is already established by the time this screen exists.
  useEffect(() => {
    if (quote) return;
    const draft = loadDraftQuote();
    if (draft) {
      setQuote(draft);
      setDraftStatus("Resumed draft");
      if (!resumeAnnounced) {
        resumeAnnounced = true;
        flash("Resumed your last draft");
      }
      return;
    }
    // Ask who the quote is for rather than opening one against a literal.
    // This used to be `createQuote(t, "Pitti Engineering Ltd")`, so every quote
    // in the product was for one customer and the header's "Customer" was a
    // label with nothing behind it. A quote cannot be priced without knowing
    // whose price history to read, so it is the first question, not a setting.
    setPickerOpen(true);
  }, [quote, t, flash]);

  useEffect(() => {
    if (!quote) return;
    saveDraftQuote(quote);
    setDraftStatus(
      `Saved ${new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`,
    );
    // A refusal describes the quote as it was. Any change to the quote may have
    // answered it, and a stale blocker is worse than none.
    setSendBlock(null);
  }, [quote]);

  const flaggedLines = useMemo(
    () =>
      new Set(
        Object.values(ci.byLineId)
          .filter((i) => i.exceptions.some((e) => e.severity !== "INFO"))
          .map((i) => i.line_id),
      ),
    [ci.byLineId],
  );

  const visible = useMemo(() => {
    if (!quote) return [];
    const q = search.trim().toLowerCase();
    return quote.lines.filter(
      (l) =>
        passesFilter(l, filter, flaggedLines) &&
        (!q ||
          [l.reqCode, l.reqDesc, l.supplyCode, l.raw].filter(Boolean).join(" ").toLowerCase().includes(q)),
    );
  }, [quote, filter, search, flaggedLines]);

  const saveDraft = () => {
    if (!quote) return;
    saveDraftQuote(quote);
    setDraftStatus(`Saved ${new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`);
    flash("Draft saved locally");
  };

  /** Abandon the draft and open a fresh quote.
   *
   *  The way out of a finished quote, which used to be "sign out" — the only
   *  control that cleared the draft, and it also ended the session. */
  const startNewQuote = () => {
    clearDraftQuote();
    setSelectedIds([]);
    setFilter("ALL");
    setSearch("");
    setDraftStatus(null);
    setQuote(null);   // the effect above opens the next one
    flash("Started a new quote");
  };

  const clearSelection = () => {
    setSelectedIds([]);
    flash("Selection cleared");
  };

  const selectVisible = () => {
    const ids = visible.map((l) => l.id);
    setSelectedIds((prev) => [...new Set([...prev, ...ids])]);
    flash(`${ids.length} visible line(s) selected`);
  };

  /** A line deleted, or filtered out of the grid, must not keep counting
   *  towards "3 selected" — or towards a discount applied to it. */
  const visibleIds = useMemo(() => new Set(visible.map((l) => l.id)), [visible]);
  const selection = useMemo(
    () => selectedIds.filter((id) => visibleIds.has(id)), [selectedIds, visibleIds]);

  // Keyboard: `/` to search and Escape to close, both of which belong to the
  // page. Everything *inside* the grid — ↑↓, Enter to open a line, Space to
  // select — is ag-grid's now. It used to be re-implemented here over a
  // `focusId` of our own, which meant two listeners for one keystroke and a
  // focus ring that could disagree with the row the grid thought was current.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (document.activeElement?.tagName || "").toUpperCase();
      const typing = tag === "INPUT" || tag === "TEXTAREA";
      if (e.key === "Escape") {
        if (intakeOpen) setIntakeOpen(false);
        else if (drawerLineId) setDrawerLineId(null);
        else if (typing) (document.activeElement as HTMLElement).blur();
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        document.getElementById("qb-search")?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [intakeOpen, drawerLineId]);

  // No "could not be started" screen any more. It existed for the auto-create
  // that opened a quote against a literal customer on mount; starting a quote
  // is now something a person does, and a failure to do it belongs next to the
  // control they pressed. `guard` reports it through `flash`, like every other
  // action on this screen.

  /** Start a quote for a customer. Also how the header changes customer: each
   *  line remembers the identity scope it was resolved under, so re-pointing an
   *  existing quote would leave those resolutions filed against the previous
   *  customer. A new quote is the honest answer, and the confirm says so.
   *
   *  The dialog closes when the quote *lands*, not on the press, so the rule
   *  has no race in it: open exactly while there is no quote to work on. */
  const startQuote = (c: { id: string; name: string }) =>
    guard(async () => {
      const q = await api.createQuote(t, c.name, c.id);
      clearDraftQuote();
      setQuote(q);
      setSelectedIds([]);
      setDraftStatus(null);
      setPickerOpen(false);
      flash(`Quote ${q.number} for ${c.name}`, "success");
    });

  if (!quote) {
    return (
      <Box>
        <SectionHeader title="Quote Builder" sub={SUB} />
        <LoadingState rows={3} label="Choose who this quote is for…" />
        {/* No cancel: there is nothing behind this to return to, and a quote
            with no customer cannot be priced — there is no price history to
            read. */}
        <CustomerPicker
          open={pickerOpen}
          token={t}
          busy={busy}
          title="Who is this quote for?"
          note="Pricing reads this customer's own history, so the quote needs to
                know whose. Start typing a name."
          onPick={startQuote}
        />
      </Box>
    );
  }

  const drawerLine = quote.lines.find((l) => l.id === drawerLineId) || null;

  async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const doIntake = (text: string) =>
    guard(async () => {
      const q = await api.intake(t, quote!.id, text);
      setQuote(q);
      setIntakeOpen(false);
      const read = q.lines.filter((l) => l.proposed).length;
      // Says which produced the lines. A reading presented as though somebody
      // had typed it is the one outcome worth avoiding here.
      flash(read
        ? `${q.summary.total} line(s) read from your message — check each one`
        : `${q.summary.total} line(s) in quote`);
    });

  const doConfirmReading = (id: string) =>
    guard(async () => {
      const q = await api.confirmReading(t, quote!.id, id);
      setQuote(q);
    });

  const doSelect = (code: string, manual: boolean) =>
    guard(async () => {
      const q = await api.selectSupply(t, quote!.id, drawerLineId!, code, manual);
      setQuote(q);
      // A confirmed mapping is a durable fact the person just taught the
      // system — it outranks the routine "supply set" acknowledgement, and is
      // held longer because it is a sentence rather than a status.
      if (q.note) flash(q.note, "success");
      else flash(code === drawerLine?.reqCode
        ? "Reverted to requested product" : `Supply set to ${code}`);
    });

  const doRevert = () =>
    guard(async () => {
      const q = await api.selectSupply(t, quote!.id, drawerLineId!, drawerLine!.reqCode, false);
      setQuote(q);
    });

  const doSetPrice = (id: string, price: number | null) =>
    guard(async () => setQuote(await api.setPrice(t, quote!.id, id, price)));

  const doDeleteLine = (id: string) =>
    guard(async () => {
      const q = await api.deleteLine(t, quote!.id, id);
      setQuote(q);
      flash("Line removed from quote");
    });

  const doCreateItem = (id: string) =>
    guard(async () => {
      const q = await api.createItem(t, quote!.id, id);
      setQuote(q);
      // Against the mock this write could not fail, so the message was
      // unconditional. Against a real ledger it can, and announcing a creation
      // that did not happen is the one thing this screen must not do — the line
      // is left reading CREATE FAILED, and the reason is said out loud.
      flash(q.createItemError || "Item created in Zoho Books");
    });

  const doDiscount = (pct: number) =>
    guard(async () => {
      const q = await api.discount(t, quote!.id, selection, pct);
      setQuote(q);
      flash(`${q.applied} line(s) discounted ${pct}%`);
    });

  /** Create the Zoho estimate, or say — durably — why not.
   *
   *  The refusal used to be a three-second grey snackbar and a filter change.
   *  Press the button, watch the grid re-filter, and by the time you have
   *  looked down at it the only statement of *what was wrong* has gone; if you
   *  were already on that filter, nothing on screen changed at all. The reason
   *  now stays until the quote changes, which is also when it stops being true.
   *  Success is held the same way — see the sent chip in the summary bar. */
  const doEstimate = () =>
    guard(async () => {
      setSendBlock(null);
      // The server re-checks the approval gate; this only avoids a round trip
      // that is certain to be refused, and says why in the same words.
      if (ci.gate && !ci.gate.can_submit) {
        setSendBlock(ci.gate.blocked_reason
          ?? "This quote needs approval before it can be sent.");
        setFilter("EXC");
        return;
      }
      const r = await api.createEstimate(t, quote!.id);
      if (!r.ok) {
        setSendBlock(r.message);
        if (r.blockers.length) setFilter("NEEDS");
        return;
      }
      // Re-read the quote so the summary bar learns the estimate number it now
      // carries; the send endpoint answers with the estimate, not the quote.
      setQuote(await api.getQuote(t, quote!.id));
      flash(r.message, "success");
    });

  const selectedCount = selection.length;
  const hasLines = quote.lines.length > 0;

  return (
    <Box>
      <SectionHeader
        title="Quote Builder"
        sub={SUB}
        actions={
          <>
            {/* The two things this screen is opened to do, so they carry a
                full tap target like the controls below them. */}
            <Button variant="outlined" size="small" sx={TOUCH} onClick={startNewQuote}>
              New quote
            </Button>
            <Button variant="contained" size="small" sx={TOUCH}
                    onClick={() => setIntakeOpen(true)}>
              Paste RFQ
            </Button>
          </>
        }
      />

      {/* Which quote this is, and whether the draft is safe. A `Paper` strip
          rather than the brand bar this used to occupy: the shell above already
          says who is signed in and what the product is called, and repeating it
          here was half of why the screen felt like a different application. */}
      <Paper
        variant="outlined"
        sx={{
          p: 1.5, mb: 2,
          display: "flex", flexWrap: "wrap", alignItems: "center", gap: 2, rowGap: 1,
        }}
      >
        <Box>
          <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
            Quote
          </Typography>
          <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
            {quote.number}
          </Typography>
        </Box>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
            Customer
          </Typography>
          {/* A control, not a caption. There was no way to change the
              customer at all before this. */}
          <Button
            type="button"
            variant="text"
            size="small"
            onClick={() => setPickerOpen(true)}
            sx={{ ...TOUCH, p: 0, justifyContent: "flex-start",
                  textTransform: "none", lineHeight: 1.4,
                  fontFamily: "var(--font-heading)", fontWeight: 600 }}
          >
            {quote.customer}
          </Button>
        </Box>
        <Box sx={{ flex: 1 }} />
        {draftStatus && (
          <Chip size="small" variant="outlined" label={draftStatus} />
        )}
        <Button variant="text" size="small" sx={TOUCH} onClick={saveDraft}
                disabled={!hasLines}>
          Save draft
        </Button>
      </Paper>

      {/* Chips, matching the decision queue's filter row. These select what the
          grid shows; they are not actions, and rendering them as buttons said
          otherwise on both screens.

          `FilterPanel` rather than a `Paper` spelling out the same six sx
          properties: kit.tsx §10 already owns "the controls above a list", and
          a duplicate scan named this strip and the header strip above it as one
          clone. The header strip is not this — it identifies the quote — so
          only this one moves. */}
      <FilterPanel>
        {FILTERS.map(([key, label]) => (
          <FilterChip
            key={key}
            label={label}
            count={quote.filterCounts[key] ?? 0}
            selected={filter === key}
            // Unresolved lines and lines needing a decision are the two states
            // that stop a quote being sent, so their count is coloured even when
            // the chip is not the active one.
            alert={(key === "NEEDS" || key === "UNRES")
                   && (quote.filterCounts[key] ?? 0) > 0}
            onClick={() => setFilter(key)}
          />
        ))}
        {mgmt && (quote.filterCounts.MFLOOR ?? 0) > 0 && (
          <FilterChip
            label="Below margin floor"
            count={quote.filterCounts.MFLOOR}
            selected={filter === "MFLOOR"}
            tone="error"
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          />
        )}
        <Box sx={{ flex: 1 }} />
        {/* Full width on a phone: a 220px search box beside a wrapped row of
            chips left ~90px of usable field, which is not a search box. */}
        <TextField
          id="qb-search"
          size="small"
          placeholder="Search  ( / )"
          aria-label="Search quote lines"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ width: { xs: "100%", sm: 220 }, "& .MuiInputBase-root": TOUCH }}
        />
        <Button variant="outlined" size="small" sx={TOUCH}
                onClick={selectVisible} disabled={!visible.length}>
          Select visible
        </Button>
        <Button variant="outlined" size="small" sx={TOUCH}
                onClick={clearSelection} disabled={!selectedCount}>
          Clear
        </Button>
      </FilterPanel>

      {/* An `Alert`, not a hand-coloured banner: the severity carries an icon
          and a role as well as a hue, which is the standard everywhere else in
          this product. */}
      {mgmt && quote.marginFloor && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
            >
              {filter === "MFLOOR" ? "Show all" : "Review these"}
            </Button>
          }
        >
          <AlertTitle>
            {quote.marginFloor.count} line(s) priced below the{" "}
            {Math.round(quote.marginFloor.floor * 100)}% margin floor
          </AlertTitle>
          Lowest margin {(quote.marginFloor.worst * 100).toFixed(1)}% — review before creating the
          estimate.
        </Alert>
      )}

      {/* Why the last send was refused, in the words the server used. An
          `Alert` for the same reason the one above is: the severity carries an
          icon and a role as well as a hue. It names the lines, so "3 line(s)
          must be resolved" is followed by which three. */}
      {sendBlock && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          onClose={() => setSendBlock(null)}
        >
          <AlertTitle>This quote was not sent</AlertTitle>
          {sendBlock}
        </Alert>
      )}

      {/* No selection strip here. "3 selected · Apply 10% discount" was on this
          screen twice — once above the grid and once in the summary bar, which
          is sticky and therefore always on screen anyway — and "Clear selection"
          was a third spelling of the toolbar's own Clear. A jscpd pass named it;
          it is the kind of duplicate that reads as thoroughness until somebody
          asks which of the two buttons is the real one. */}

      {!hasLines ? (
        <EmptyState
          title="Paste an RFQ to start building the quote"
          reason={
            "Each line becomes a reviewed item with supplier options, availability and the right "
            + "next action. Resolved lines open at the catalogue rate as a starting point — the "
            + "price that goes out is the one you set."
          }
          action={
            <Stack direction="row" spacing={1} useFlexGap sx={{ justifyContent: "center", flexWrap: "wrap" }}>
              <Button variant="contained" onClick={() => setIntakeOpen(true)}>
                Paste RFQ
              </Button>
            </Stack>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          title="Nothing matches the current filter or search"
          reason="Clear the filter, change the search term, or add a fresh RFQ."
          action={
            <Button variant="outlined" onClick={() => { setFilter("ALL"); setSearch(""); }}>
              Show all {quote.lines.length} lines
            </Button>
          }
        />
      ) : (
        <>
          {/* The grid scrolls inside its own box; the page never scrolls
              sideways (ui-standards §3). */}
          <LineGrid
            lines={visible}
            mgmt={mgmt}
            intel={ci.byLineId}
            selectedIds={selection}
            onSelectionChange={setSelectedIds}
            onOpen={setDrawerLineId}
            onSetPrice={doSetPrice}
            onDeleteLine={doDeleteLine}
            onCreateItem={doCreateItem}
            onConfirmReading={doConfirmReading}
          />
          {/* Hidden on the phone rendering. Every hint here is about the grid
              — ↑↓ moves the focused row, F2 opens the rate editor, Enter opens
              the supply drawer — and none of it is true of the cards, which
              have no keyboard and no editor to open. A legend for controls that
              are not there is worse than no legend. */}
          <Box
            className="kbd-hints"
            sx={{
              mt: "var(--space-4)",
              display: "none",
              // The wrapper's own breakpoint, read rather than restated: the
              // legend must appear exactly when the grid it describes does.
              [`@media (min-width:${NARROW_BREAKPOINT}px)`]: { display: "flex" },
            }}
          >
            <span>
              <span className="kbd">↑↓</span> navigate
            </span>
            <span>
              <span className="kbd">Enter</span> supply options
            </span>
            <span>
              <span className="kbd">Space</span> select
            </span>
            <span>
              <span className="kbd">F2</span> edit the rate
            </span>
            <span>
              <span className="kbd">/</span> search
            </span>
            <span>
              <span className="kbd">Esc</span> close
            </span>
          </Box>
        </>
      )}

      <SummaryBar
        quote={quote}
        selectedCount={selectedCount}
        onDiscount={doDiscount}
        onCreateEstimate={doEstimate}
        gateBlockedReason={ci.gate && !ci.gate.can_submit ? ci.gate.blocked_reason : null}
        busy={busy}
      />

      {/* Below the total, not above it: the question "did this win?" only
          arises once the quote has gone out, and the estimate button is what
          sends it. Nothing rendered until the quote has been recorded at all —
          QuoteOutcomeBar returns null without an outcome. */}
      <QuoteOutcomeBar
        outcome={ci.data?.outcome ?? null}
        onRecord={ci.recordOutcome}
        busy={busy || ci.loading}
      />

      {intakeOpen && <IntakeModal onClose={() => setIntakeOpen(false)} onSubmit={doIntake} />}
      {drawerLine && (
        <SupplyDrawer
          line={drawerLine}
          customer={quote.customer}
          token={t}
          mgmt={mgmt}
          intel={ci.byLineId[drawerLine.id] ?? null}
          intelLoading={ci.loading}
          intelError={ci.error}
          onRecordOverride={ci.recordOverride}
          onRequestApproval={ci.requestApproval}
          approvalStatus={
            ci.gate?.requests.find((r) => r.subject_line_id === drawerLine.id) ?? null
          }
          // A real navigation now: the drawer's "open the analysis" link lands
          // on a platform URL in the same shell, rather than swapping the app.
          onOpenPlatform={(path) => navigate(path)}
          onClose={() => setDrawerLineId(null)}
          onSelect={doSelect}
          onRevert={doRevert}
        />
      )}

      {/* Changing the customer starts a new quote rather than re-pointing this
          one — each line remembers the identity scope it was resolved under,
          and silently wrong is worse than plainly starting again. */}
      <CustomerPicker
        open={pickerOpen}
        token={t}
        busy={busy}
        title="Change customer"
        note={quote.lines.length
          ? `This quote has ${quote.lines.length} line(s) resolved for `
            + `${quote.customer}. Choosing another customer starts a new quote; `
            + `the current one is not kept.`
          : "Pricing reads this customer's own history."}
        onPick={startQuote}
        onCancel={() => setPickerOpen(false)}
      />
    </Box>
  );
}
