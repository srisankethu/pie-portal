import Button from "@mui/material/Button";
import Avatar from "@mui/material/Avatar";
import Chip from "@mui/material/Chip";
import { useSnackbar } from "notistack";
import { useEffect, useMemo, useState } from "react";
import { api, clearDraftQuote, clearSession, loadDraftQuote, loadSession, saveDraftQuote, saveSession } from "./api";
import type { Line, Quote, Session } from "./types";
import { SignIn } from "./components/SignIn";
import { IntakeModal } from "./components/IntakeModal";
import { SupplyDrawer } from "./components/SupplyDrawer";
import { LineGrid } from "./components/LineGrid";
import { SummaryBar } from "./components/SummaryBar";
import { platformToken } from "./intelligence";
import { CustomerPicker } from "./components/CustomerPicker";
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

export default function App({ onOpenPlatform }: { onOpenPlatform?: (path: string) => void } = {}) {
  const [session, setSession] = useState<Session | null>(loadSession());
  const [quote, setQuote] = useState<Quote | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [focusId, setFocusId] = useState<string | null>(null);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [drawerLineId, setDrawerLineId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftStatus, setDraftStatus] = useState<string | null>(null);
  // Open when there is no quote to work on, and on demand from the header.
  const [pickerOpen, setPickerOpen] = useState(false);

  const mgmt = session?.role === "mgmt";
  // One assessment for the whole quote — see useQuoteIntelligence.
  const ci = useQuoteIntelligence(quote);

  // The same `SnackbarProvider` the platform side already uses, rather than
  // the fixed-position div and 2.4s timer this used to hand-roll: two of them
  // in quick succession replaced each other, and 2.4s is too short to read a
  // sentence. `flash` keeps its name so every call site is unchanged.
  const { enqueueSnackbar } = useSnackbar();
  const flash = (msg: string, variant: "default" | "success" = "default") => {
    enqueueSnackbar(msg, { variant, autoHideDuration: variant === "success" ? 8000 : 3000 });
  };

  // Resume a saved draft on sign-in, or ask who the quote is for.
  //
  // This used to create a quote against the literal string "Pitti Engineering
  // Ltd", which is why every quote in the product was for one customer and the
  // header's "Customer" was a label rather than a control. A quote cannot be
  // priced without knowing whose price history to read, so the picker is the
  // first thing rather than a setting to go and find.
  useEffect(() => {
    if (session && !quote) {
      const draft = loadDraftQuote();
      if (draft) {
        setQuote(draft);
        setDraftStatus("Resumed draft");
        flash("Resumed your last draft");
        return;
      }
      setPickerOpen(true);
    }
  }, [session, quote, flash]);

  /** Start a quote for a customer. Also how the header changes customer: the
   *  lines carry the scope they were resolved under, so re-pointing an existing
   *  quote would leave resolutions filed against the previous customer. A new
   *  quote is the honest answer, and the confirm says so. */
  const startQuote = (c: { id: string; name: string }) => {
    if (!session) return;
    // Closed when the quote lands, not when the button is pressed. `flash` is
    // rebuilt every render, so the effect above re-runs constantly — closing
    // the picker here left it re-opened by that effect while the create was
    // still in flight, and the reopened dialog then sat over the app swallowing
    // every click. Holding it open until there is a quote makes the rule simple
    // enough to not have a race: the picker is open exactly while this session
    // has no quote to work on.
    guard(async () => {
      const q = await api.createQuote(session.token, c.name, c.id);
      clearDraftQuote();
      setQuote(q);
      setSelected({});
      setDraftStatus(null);
      setPickerOpen(false);
      flash(`Quote ${q.number} for ${c.name}`, "success");
    });
  };

  useEffect(() => {
    if (session && quote) {
      saveDraftQuote(quote);
      setDraftStatus(`Saved ${new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`);
    }
  }, [session, quote]);

  const onSignedIn = (s: Session) => {
    saveSession(s);
    setSession(s);
  };
  const signOut = () => {
    clearSession();
    clearDraftQuote();
    setSession(null);
    setQuote(null);
    setSelected({});
    setDraftStatus(null);
  };

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

  const selectVisible = () => {
    if (!quote) return;
    const next = visible.reduce<Record<string, boolean>>((acc, line) => {
      acc[line.id] = true;
      return acc;
    }, {});
    setSelected((prev) => ({ ...prev, ...next }));
    flash(`${visible.length} visible line(s) selected`);
  };

  const clearSelection = () => {
    setSelected({});
    flash("Selection cleared");
  };

  const selectAllVisible = () => {
    if (!quote) return;
    const allVisibleSelected = visible.length > 0 && visible.every((line) => selected[line.id]);
    if (allVisibleSelected) {
      const next = visible.reduce<Record<string, boolean>>((acc, line) => {
        acc[line.id] = false;
        return acc;
      }, {});
      setSelected((prev) => ({ ...prev, ...next }));
      flash("Selection cleared");
      return;
    }
    const next = visible.reduce<Record<string, boolean>>((acc, line) => {
      acc[line.id] = true;
      return acc;
    }, {});
    setSelected((prev) => ({ ...prev, ...next }));
    flash(`${visible.length} visible line(s) selected`);
  };

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
  }, [visible, focusId, intakeOpen, drawerLineId]);

  if (!session) return <SignIn onSignedIn={onSignedIn} />;
  if (!quote)
    return (
      <>
        <div className="signin-wrap">
          <div className="text-muted">
            {busy ? "Starting the quote…" : "Choose who this quote is for."}
          </div>
        </div>
        {/* No cancel: there is nothing behind this to go back to, and a quote
            with no customer cannot be priced — there is no price history to
            read. */}
        <CustomerPicker
          open={pickerOpen}
          title="Who is this quote for?"
          busy={busy}
          note="Pricing reads this customer's own history, so the quote needs to
                know whose. Start typing a name."
          onPick={startQuote}
        />
      </>
    );

  const t = session.token;
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
      const r = await api.createEstimate(t, quote!.id, platformToken());
      flash(r.message);
      if (!r.ok && r.blockers.length) {
        setFilter("NEEDS");
      }
    });

  const selectedCount = Object.values(selected).filter(Boolean).length;
  const hasLines = quote.lines.length > 0;

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">SANKETH · QUOTE BUILDER</span>
        <div className="meta">
          <span>
            Quote <b>{quote.number}</b>
          </span>
          <span>
            Customer{" "}
            <Button
              type="button"
              variant="text"
              size="small"
              sx={{ p: 0, minWidth: 0, fontWeight: 700, textTransform: "none",
                    verticalAlign: "baseline" }}
              onClick={() => setPickerOpen(true)}
            >
              {quote.customer}
            </Button>
          </span>
        </div>
        <div className="spacer" />
        {draftStatus && <span className="status-pill">{draftStatus}</span>}
        <span className={"role-badge" + (mgmt ? " mgmt" : "")}>
          {mgmt ? "Management · full economics" : "Sales"} · {session.name}
        </span>
        <Button variant="outlined" size="small" onClick={signOut}>
          Sign out
        </Button>
      </div>

      <div className="toolbar">
        {/* Chips, matching the decision queue's filter row. These select what
            the grid shows; they are not actions, and rendering them as buttons
            said otherwise on both screens. */}
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
        <div className="spacer" style={{ flex: 1 }} />
        <input
          id="qb-search"
          className="input"
          style={{ maxWidth: 220, minHeight: 30 }}
          placeholder="Search  ( / )"
          value={search}
          aria-label="Search quote lines"
          onChange={(e) => setSearch(e.target.value)}
        />
        <Button variant="outlined" size="small" onClick={saveDraft}>
          Save draft
        </Button>
        <Button variant="outlined" size="small" onClick={selectVisible} disabled={!visible.length}>
          Select visible
        </Button>
        <Button variant="outlined" size="small" onClick={clearSelection} disabled={!selectedCount}>
          Clear
        </Button>
        <Button variant="contained" size="small" onClick={() => setIntakeOpen(true)}>
          Paste RFQ
        </Button>
      </div>

      {mgmt && quote.marginFloor && (
        <div className="floor-banner">
          <b>
            {quote.marginFloor.count} line(s) priced below the{" "}
            {Math.round(quote.marginFloor.floor * 100)}% margin floor
          </b>
          <span>
            · lowest margin {(quote.marginFloor.worst * 100).toFixed(1)}% — review before creating the
            estimate
          </span>
          <span style={{ flex: 1 }} />
          <Button
            variant="text" size="small"
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          >
            {filter === "MFLOOR" ? "Show all" : "Review these"}
          </Button>
        </div>
      )}

      {selectedCount > 0 && (
        <div className="bulk-actions">
          <span>{selectedCount} selected</span>
          <Button variant="outlined" size="small" onClick={() => doDiscount(10)}>
            Apply 10% discount
          </Button>
          <Button variant="outlined" size="small" onClick={selectAllVisible}>
            Select all visible
          </Button>
          <Button variant="outlined" size="small" onClick={clearSelection}>
            Clear selection
          </Button>
        </div>
      )}

      <div className="grid-wrap">
        {!hasLines ? (
          <div className="empty-state-card">
            <div className="empty-state-card__eyebrow">Start a quote</div>
            <h3>Paste an RFQ and let the engine resolve it into a quote-ready grid.</h3>
            <p>
              Each line becomes a reviewed item with supplier options, availability, and the right
              next action.
            </p>
            <div className="empty-state-actions">
              <Button variant="contained" onClick={() => setIntakeOpen(true)}>
                Paste RFQ
              </Button>
              <Button variant="outlined" onClick={() => setIntakeOpen(true)}>
                Load sample RFQ
              </Button>
            </div>
            <div className="inline-help">
              <span>
                Use <span className="kbd">/</span> to jump to search
              </span>
              <span>
                Use <span className="kbd">↑↓</span> and <span className="kbd">Enter</span> to review
                lines quickly
              </span>
            </div>
          </div>
        ) : visible.length === 0 ? (
          <div className="empty-state-card compact">
            <div className="empty-state-card__eyebrow">No matching lines</div>
            <h3>Nothing matches the current filter or search.</h3>
            <p>Try clearing the filter, changing the search term, or adding a fresh RFQ.</p>
          </div>
        ) : (
          <>
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
      </div>

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
          mgmt={mgmt}
          intel={ci.byLineId[drawerLine.id] ?? null}
          intelLoading={ci.loading}
          intelError={ci.error}
          intelConnected={ci.connected}
          onRecordOverride={ci.recordOverride}
          onRequestApproval={ci.requestApproval}
          approvalStatus={
            ci.gate?.requests.find((r) => r.subject_line_id === drawerLine.id) ?? null
          }
          onOpenPlatform={onOpenPlatform}
          onClose={() => setDrawerLineId(null)}
          onSelect={doSelect}
          onRevert={doRevert}
        />
      )}

      {/* Changing the customer starts a new quote rather than re-pointing this
          one. Each line remembers the identity scope it was resolved under, so
          re-pointing would leave those resolutions filed against the previous
          customer — and silently wrong is worse than plainly starting again. */}
      <CustomerPicker
        open={pickerOpen}
        title="Change customer"
        busy={busy}
        note={hasLines
          ? `This quote has ${quote.lines.length} line(s) resolved for `
            + `${quote.customer}. Choosing another customer starts a new quote; `
            + `the current one is not kept.`
          : "Pricing reads this customer's own history."}
        onPick={startQuote}
        onCancel={() => setPickerOpen(false)}
      />
    </div>
  );
}
