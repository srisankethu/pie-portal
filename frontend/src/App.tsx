import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, clearSession, loadSession, saveSession } from "./api";
import type { Line, Quote, Session } from "./types";
import { SignIn } from "./components/SignIn";
import { IntakeModal } from "./components/IntakeModal";
import { SupplyDrawer } from "./components/SupplyDrawer";
import { LineGrid } from "./components/LineGrid";
import { SummaryBar } from "./components/SummaryBar";

const FILTERS: [string, string][] = [
  ["ALL", "All"],
  ["NEEDS", "Needs attention"],
  ["PROC", "Potential procurement"],
  ["BOOKS", "Missing Zoho item"],
  ["MANUAL", "Manual review"],
  ["UNRES", "Unresolved"],
  ["SUBST", "Substituted"],
];

function passesFilter(l: Line, f: string): boolean {
  switch (f) {
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

export default function App() {
  const [session, setSession] = useState<Session | null>(loadSession());
  const [quote, setQuote] = useState<Quote | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [focusId, setFocusId] = useState<string | null>(null);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [drawerLineId, setDrawerLineId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toastTimer = useRef<number | undefined>(undefined);

  const mgmt = session?.role === "mgmt";

  const flash = useCallback((msg: string) => {
    setToast(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2400);
  }, []);

  // Create a fresh quote on sign-in.
  useEffect(() => {
    if (session && !quote) {
      api
        .createQuote(session.token, "Pitti Engineering Ltd")
        .then(setQuote)
        .catch((e) => flash((e as Error).message));
    }
  }, [session, quote, flash]);

  const onSignedIn = (s: Session) => {
    saveSession(s);
    setSession(s);
  };
  const signOut = () => {
    clearSession();
    setSession(null);
    setQuote(null);
    setSelected({});
  };

  const visible = useMemo(() => {
    if (!quote) return [];
    const q = search.trim().toLowerCase();
    return quote.lines.filter(
      (l) =>
        passesFilter(l, filter) &&
        (!q ||
          [l.reqCode, l.reqDesc, l.supplyCode, l.raw].filter(Boolean).join(" ").toLowerCase().includes(q)),
    );
  }, [quote, filter, search]);

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
      <div className="signin-wrap">
        <div className="text-muted">Starting a new quote…</div>
      </div>
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
      flash(code === drawerLine?.reqCode ? "Reverted to requested product" : `Supply set to ${code}`);
    });

  const doRevert = () =>
    guard(async () => {
      const q = await api.selectSupply(t, quote!.id, drawerLineId!, drawerLine!.reqCode, false);
      setQuote(q);
    });

  const doSetPrice = (id: string, price: number | null) =>
    guard(async () => setQuote(await api.setPrice(t, quote!.id, id, price)));

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
      const r = await api.createEstimate(t, quote!.id);
      flash(r.message);
      if (!r.ok && r.blockers.length) {
        setFilter("NEEDS");
      }
    });

  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">SANKETH · QUOTE BUILDER</span>
        <div className="meta">
          <span>
            Quote <b>{quote.number}</b>
          </span>
          <span>
            Customer <b>{quote.customer}</b>
          </span>
        </div>
        <div className="spacer" />
        <span className={"role-badge" + (mgmt ? " mgmt" : "")}>
          {mgmt ? "Management · full economics" : "Sales"} · {session.name}
        </span>
        <button className="btn btn-secondary btn-sm" onClick={signOut}>
          Sign out
        </button>
      </div>

      <div className="toolbar">
        {FILTERS.map(([key, label]) => {
          const count = quote.filterCounts[key] ?? 0;
          const alert = (key === "NEEDS" || key === "UNRES") && count > 0;
          return (
            <button
              key={key}
              className={"filter" + (filter === key ? " active" : "")}
              onClick={() => setFilter(key)}
            >
              {label}
              <span className={"count" + (alert ? " alert" : "")}>{count}</span>
            </button>
          );
        })}
        {mgmt && (quote.filterCounts.MFLOOR ?? 0) > 0 && (
          <button
            className={"filter" + (filter === "MFLOOR" ? " active" : "")}
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          >
            Below margin floor
            <span className="count alert">{quote.filterCounts.MFLOOR}</span>
          </button>
        )}
        <div className="spacer" style={{ flex: 1 }} />
        <input
          id="qb-search"
          className="input"
          style={{ maxWidth: 220, minHeight: 30 }}
          placeholder="Search  ( / )"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button className="btn btn-primary btn-sm" onClick={() => setIntakeOpen(true)}>
          Paste RFQ
        </button>
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
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          >
            {filter === "MFLOOR" ? "Show all" : "Review these"}
          </button>
        </div>
      )}

      <div className="grid-wrap">
        <LineGrid
          lines={visible}
          mgmt={mgmt}
          selected={selected}
          focusId={focusId}
          onToggle={(id) => setSelected((s) => ({ ...s, [id]: !s[id] }))}
          onOpen={(id) => {
            setFocusId(id);
            setDrawerLineId(id);
          }}
          onSetPrice={doSetPrice}
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
      </div>

      <SummaryBar
        quote={quote}
        selectedCount={selectedCount}
        onDiscount={doDiscount}
        onCreateEstimate={doEstimate}
        busy={busy}
      />

      {intakeOpen && <IntakeModal onClose={() => setIntakeOpen(false)} onSubmit={doIntake} />}
      {drawerLine && (
        <SupplyDrawer
          line={drawerLine}
          mgmt={mgmt}
          onClose={() => setDrawerLineId(null)}
          onSelect={doSelect}
          onRevert={doRevert}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
