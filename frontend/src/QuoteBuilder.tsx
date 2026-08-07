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
import Avatar from "@mui/material/Avatar";
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
import type { Line, Quote } from "./types";
import { IntakeModal } from "./components/IntakeModal";
import { SupplyDrawer } from "./components/SupplyDrawer";
import { LineGrid } from "./components/LineGrid";
import { SummaryBar } from "./components/SummaryBar";
import { EmptyState, LoadingState, SectionHeader } from "./platform/kit";
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

/** The account a fresh quote opens against until somebody says otherwise. */
const DEFAULT_CUSTOMER = "Pitti Engineering Ltd";

/** What this screen answers — the sentence the Quotes door used to carry on a
 *  page of its own, in front of the thing it was describing. */
const SUB =
  "Paste an RFQ and the engine resolves each line into a quote-ready product. "
  + "Quote context shows this customer's own price history and — for managers — the "
  + "cost and margin, then leaves the price in your hands. It never pre-fills the field.";

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
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [focusId, setFocusId] = useState<string | null>(null);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [drawerLineId, setDrawerLineId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<string | null>(null);

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
    let live = true;
    api
      .createQuote(t, DEFAULT_CUSTOMER)
      .then((q) => live && setQuote(q))
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [quote, t, flash]);

  useEffect(() => {
    if (!quote) return;
    saveDraftQuote(quote);
    setDraftStatus(
      `Saved ${new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`,
    );
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
    setSelected({});
    setFocusId(null);
    setFilter("ALL");
    setSearch("");
    setDraftStatus(null);
    setQuote(null);   // the effect above opens the next one
    flash("Started a new quote");
  };

  const clearSelection = useCallback(() => {
    setSelected({});
    flash("Selection cleared");
  }, [flash]);

  const selectVisible = () => {
    if (!quote) return;
    const next = visible.reduce<Record<string, boolean>>((acc, line) => {
      acc[line.id] = true;
      return acc;
    }, {});
    setSelected((prev) => ({ ...prev, ...next }));
    flash(`${visible.length} visible line(s) selected`);
  };

  const selectAllVisible = useCallback(() => {
    const allVisibleSelected = visible.length > 0 && visible.every((line) => selected[line.id]);
    const next = visible.reduce<Record<string, boolean>>((acc, line) => {
      acc[line.id] = !allVisibleSelected;
      return acc;
    }, {});
    setSelected((prev) => ({ ...prev, ...next }));
    flash(allVisibleSelected ? "Selection cleared" : `${visible.length} visible line(s) selected`);
  }, [visible, selected, flash]);

  // Keyboard navigation (design: ↑↓ navigate, Enter open, Space select, / search).
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
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "a") {
        e.preventDefault();
        selectAllVisible();
        return;
      }
      if (mod && e.key.toLowerCase() === "d") {
        e.preventDefault();
        clearSelection();
        return;
      }
      if (typing || intakeOpen || drawerLineId) return;
      const ids = visible.map((l) => l.id);
      if (!ids.length) return;
      let idx = ids.indexOf(focusId ?? "");
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        idx = Math.min(ids.length - 1, idx + 1);
        setFocusId(ids[idx < 0 ? 0 : idx]);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        idx = idx <= 0 ? 0 : idx - 1;
        setFocusId(ids[idx]);
      } else if (e.key === "Enter" && idx >= 0) {
        e.preventDefault();
        setDrawerLineId(ids[idx]);
      } else if (e.key === " " && idx >= 0) {
        e.preventDefault();
        setSelected((s) => ({ ...s, [ids[idx]]: !s[ids[idx]] }));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, focusId, intakeOpen, drawerLineId, selectAllVisible, clearSelection]);

  if (error) {
    return (
      <Box>
        <SectionHeader title="Quote Builder" sub={SUB} />
        <Alert severity="error">
          <AlertTitle>The quote could not be started</AlertTitle>
          {error}
        </Alert>
      </Box>
    );
  }

  if (!quote) {
    return (
      <Box>
        <SectionHeader title="Quote Builder" sub={SUB} />
        <LoadingState rows={3} label="Starting a new quote…" />
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
      flash(`${q.summary.total} line(s) in quote`);
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
      setQuote(await api.createItem(t, quote!.id, id));
      flash("Item created in Zoho Books");
    });

  const doDiscount = (pct: number) =>
    guard(async () => {
      const ids = Object.keys(selected).filter((k) => selected[k]);
      const q = await api.discount(t, quote!.id, ids, pct);
      setQuote(q);
      flash(`${q.applied} line(s) discounted ${pct}%`);
    });

  const doEstimate = () =>
    guard(async () => {
      // The server re-checks the approval gate; this only avoids a round trip
      // that is certain to be refused, and says why in the same words.
      if (ci.gate && !ci.gate.can_submit) {
        flash(ci.gate.blocked_reason ?? "This quote needs approval before it can be sent.");
        setFilter("EXC");
        return;
      }
      const r = await api.createEstimate(t, quote!.id);
      flash(r.message);
      if (!r.ok && r.blockers.length) {
        setFilter("NEEDS");
      }
    });

  const selectedCount = Object.values(selected).filter(Boolean).length;
  const hasLines = quote.lines.length > 0;

  return (
    <Box>
      <SectionHeader
        title="Quote Builder"
        sub={SUB}
        actions={
          <>
            <Button variant="outlined" size="small" onClick={startNewQuote}>
              New quote
            </Button>
            <Button variant="contained" size="small" onClick={() => setIntakeOpen(true)}>
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
          <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
            {quote.customer}
          </Typography>
        </Box>
        <Box sx={{ flex: 1 }} />
        {draftStatus && (
          <Chip size="small" variant="outlined" label={draftStatus} />
        )}
        <Button variant="text" size="small" onClick={saveDraft} disabled={!hasLines}>
          Save draft
        </Button>
      </Paper>

      {/* Chips, matching the decision queue's filter row. These select what the
          grid shows; they are not actions, and rendering them as buttons said
          otherwise on both screens. */}
      <Paper
        variant="outlined"
        sx={{
          p: 1.5, mb: 2,
          display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1, rowGap: 1,
        }}
      >
        {FILTERS.map(([key, label]) => {
          const count = quote.filterCounts[key] ?? 0;
          // Unresolved lines and lines needing a decision are the two states
          // that stop a quote being sent, so their count is coloured even when
          // the chip is not the active one.
          const alert = (key === "NEEDS" || key === "UNRES") && count > 0;
          return (
            <Chip
              key={key}
              label={label}
              avatar={
                <Avatar
                  sx={{
                    bgcolor: "transparent",
                    fontSize: 11,
                    fontWeight: 700,
                    color: alert && filter !== key ? "var(--danger-fg)" : undefined,
                  }}
                >
                  {count}
                </Avatar>
              }
              color={filter === key ? "primary" : "default"}
              variant={filter === key ? "filled" : "outlined"}
              onClick={() => setFilter(key)}
            />
          );
        })}
        {mgmt && (quote.filterCounts.MFLOOR ?? 0) > 0 && (
          <Chip
            label="Below margin floor"
            avatar={
              <Avatar sx={{ bgcolor: "transparent", fontSize: 11, fontWeight: 700 }}>
                {quote.filterCounts.MFLOOR}
              </Avatar>
            }
            color={filter === "MFLOOR" ? "error" : "default"}
            variant={filter === "MFLOOR" ? "filled" : "outlined"}
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          />
        )}
        <Box sx={{ flex: 1 }} />
        <TextField
          id="qb-search"
          size="small"
          placeholder="Search  ( / )"
          aria-label="Search quote lines"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ maxWidth: 220 }}
        />
        <Button variant="outlined" size="small" onClick={selectVisible} disabled={!visible.length}>
          Select visible
        </Button>
        <Button variant="outlined" size="small" onClick={clearSelection} disabled={!selectedCount}>
          Clear
        </Button>
      </Paper>

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

      {selectedCount > 0 && (
        <Stack
          direction="row"
          spacing={1}
          useFlexGap
          sx={{ mb: 2, flexWrap: "wrap", alignItems: "center" }}
        >
          <Typography variant="body2" color="text.secondary">
            {selectedCount} selected
          </Typography>
          <Button variant="outlined" size="small" onClick={() => doDiscount(10)}>
            Apply 10% discount
          </Button>
          <Button variant="outlined" size="small" onClick={selectAllVisible}>
            Select all visible
          </Button>
          <Button variant="outlined" size="small" onClick={clearSelection}>
            Clear selection
          </Button>
        </Stack>
      )}

      {!hasLines ? (
        <EmptyState
          title="Paste an RFQ to start building the quote"
          reason={
            "Each line becomes a reviewed item with supplier options, availability and the right "
            + "next action. Nothing is priced for you — the engine resolves the product, you set "
            + "the number."
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
          <Box sx={{ overflowX: "auto" }}>
            <LineGrid
              lines={visible}
              mgmt={mgmt}
              intel={ci.byLineId}
              selected={selected}
              focusId={focusId}
              onToggle={(id) => setSelected((s) => ({ ...s, [id]: !s[id] }))}
              onOpen={(id) => {
                setFocusId(id);
                setDrawerLineId(id);
              }}
              onSetPrice={doSetPrice}
              onDeleteLine={doDeleteLine}
              onCreateItem={doCreateItem}
            />
          </Box>
          <div className="kbd-hints" style={{ marginTop: "var(--space-4)" }}>
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
              <span className="kbd">/</span> search
            </span>
            <span>
              <span className="kbd">Esc</span> close
            </span>
          </div>
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
    </Box>
  );
}
