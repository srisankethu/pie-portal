import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { DataGrid, numeric } from "./DataGrid";
import { EntityName, EntitySource } from "./EntityName";
import { CompanyFilter, useCompanyFilter } from "./CompanyFilter";
import { EmptyState, ErrorState, FilterChip, HumanLog, LoadingState, SectionHeader, StatusChip } from "./kit";
import { formatDate } from "../when";
import {
  clearPlatformSession,
  isAuthError,
  loadPlatformSession,
  papi,
  savePlatformSession,
} from "./api";
import type { Account, DecisionDetail, DecisionSummary, DecisionTrace, PlatformSession, Role, StatusFilter } from "./types";
import { aiState, factLabel, factValue, isPrimaryFact, stateFieldLabel, ROLE_LABEL } from "./format";
import { ActionsPanel, Bp, Conf, DecisionCard, ImpactPanel, Interpretation, Labelled,
         Pri, RankingPanel, Tip, WhyPanel, typeLabel } from "./ui";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Skeleton from "@mui/material/Skeleton";
import { useSnackbar } from "notistack";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  LEGACY_ACCOUNTS, PATH, PATTERN, pathFor, screenAt, vizPath, type Screen,
} from "./route";
import AppShell, { type NavItem } from "./AppShell";
import { SignInCard } from "../SignInCard";
import { abilityFor } from "./ability";
import { Seg } from "./viz/Seg";
import { money } from "../money";
import "./viz/viz.css";

/* ── screens, loaded when they are opened ───────────────────────────────────
 *
 * Everything above this line is the shell: the nav, the sign-in card, the
 * shared vocabulary, the router. Everything below is a screen, and a screen is
 * only worth downloading when somebody goes to it.
 *
 * The measurement that put them here: the built bundle was a 1.07 MB main chunk
 * plus the 1.16 MB ag-grid chunk that `DataGrid` already splits off, and the
 * API responses behind all of it are 2–31 kB. A salesperson in a machine shop
 * on a phone was paying for the twenty analysis screens — the target wall, the
 * bond swarm, the migration matrix — to open a quote.
 *
 * Written out one import at a time rather than through a `lazyScreen(module,
 * "Name")` helper. The helper is four lines shorter and has to cast, which
 * throws away the check that the props at the call site match the component: a
 * renamed prop would compile and fail in the browser, on the screen nobody
 * opens in development. `.then(m => ({ default: m.X }))` keeps the inference.
 *
 * Screens that share a module share a chunk, which is deliberate — `Screens`
 * and `Patterns` are each a set of views somebody moves between.
 */
const QuoteBuilder = lazy(() => import("../QuoteBuilder"));
const ApprovalsScreen = lazy(() =>
  import("./AdminScreens").then((m) => ({ default: m.ApprovalsScreen })));
const SettingsScreen = lazy(() =>
  import("./AdminScreens").then((m) => ({ default: m.SettingsScreen })));
const IdentityScreen = lazy(() =>
  import("./IdentityScreen").then((m) => ({ default: m.IdentityScreen })));
const TrustScreen = lazy(() =>
  import("./TrustScreen").then((m) => ({ default: m.TrustScreen })));
const DataScreen = lazy(() =>
  import("./DataScreen").then((m) => ({ default: m.DataScreen })));
const CustomerCommercial = lazy(() =>
  import("./CommercialScreens").then((m) => ({ default: m.CustomerCommercial })));
const CustomerItemScreen = lazy(() =>
  import("./CommercialScreens").then((m) => ({ default: m.CustomerItemScreen })));
const Storyboard = lazy(() =>
  import("./viz/Storyboard").then((m) => ({ default: m.Storyboard })));
const JourneyScreen = lazy(() =>
  import("./viz/Screens").then((m) => ({ default: m.JourneyScreen })));
const LostRevenueScreen = lazy(() =>
  import("./viz/Screens").then((m) => ({ default: m.LostRevenueScreen })));
const QuoteOutcomesScreen = lazy(() =>
  import("./viz/QuoteOutcomes").then((m) => ({ default: m.QuoteOutcomesScreen })));
const OpportunityScreen = lazy(() =>
  import("./viz/Screens").then((m) => ({ default: m.OpportunityScreen })));
const SimulatorScreen = lazy(() =>
  import("./viz/Screens").then((m) => ({ default: m.SimulatorScreen })));
const WeatherScreen = lazy(() =>
  import("./viz/Screens").then((m) => ({ default: m.WeatherScreen })));
const CadenceScreen = lazy(() =>
  import("./viz/Patterns").then((m) => ({ default: m.CadenceScreen })));
const CompositionScreen = lazy(() =>
  import("./viz/Patterns").then((m) => ({ default: m.CompositionScreen })));
const LandscapeScreen = lazy(() =>
  import("./viz/Patterns").then((m) => ({ default: m.LandscapeScreen })));
const DailyScreen = lazy(() =>
  import("./viz/Daily").then((m) => ({ default: m.DailyScreen })));
const BondsScreen = lazy(() =>
  import("./viz/Bonds").then((m) => ({ default: m.BondsScreen })));
const MixScreen = lazy(() =>
  import("./viz/Mix").then((m) => ({ default: m.MixScreen })));
const DependencyScreen = lazy(() =>
  import("./viz/Dependency").then((m) => ({ default: m.DependencyScreen })));
const TargetWallScreen = lazy(() =>
  import("./viz/TargetWall").then((m) => ({ default: m.TargetWallScreen })));
const CatalogueScreen = lazy(() =>
  import("./viz/Catalogue").then((m) => ({ default: m.CatalogueScreen })));
const CustomerHealthTimeline = lazy(() =>
  import("./viz/History").then((m) => ({ default: m.CustomerHealthTimeline })));
const MigrationMatrix = lazy(() =>
  import("./viz/History").then((m) => ({ default: m.MigrationMatrix })));
const PayablesScreen = lazy(() =>
  import("./viz/TheBook").then((m) => ({ default: m.PayablesScreen })));
const PaymentsScreen = lazy(() =>
  import("./viz/TheBook").then((m) => ({ default: m.PaymentsScreen })));
const OrderToCashScreen = lazy(() =>
  import("./viz/OrderToCash").then((m) => ({ default: m.OrderToCashScreen })));
const StatutoryScreen = lazy(() =>
  import("./viz/Statutory").then((m) => ({ default: m.StatutoryScreen })));
const StockScreen = lazy(() =>
  import("./viz/TheBook").then((m) => ({ default: m.StockScreen })));
const GmroiScreen = lazy(() =>
  import("./viz/Gmroi").then((m) => ({ default: m.GmroiScreen })));
const SupplyScreen = lazy(() =>
  import("./viz/TheBook").then((m) => ({ default: m.SupplyScreen })));
const NegotiateScreen = lazy(() =>
  import("./viz/Negotiate").then((m) => ({ default: m.NegotiateScreen })));

const ROLE_HOME: Record<Role, { title: string; sub: string; nav: string }> = {
  SALESPERSON: { title: "Today", sub: "Decisions that need you, most urgent first", nav: "Today" },
  SALES_MANAGER: {
    // Not "Team focus". This page shows the organization's decisions and the
    // approvals waiting on you; it has never shown a view of the team, and a
    // title promising one sends a manager looking for a screen that does not
    // exist. The per-person roll-up is a deliberate omission while there is one
    // salesperson to roll up — but the title should describe the page as it is.
    title: "Where to act",
    sub: "The decisions worth your attention, and what is waiting on your sign-off",
    nav: "Where to act",
  },
  OWNER: { title: "Where to intervene", sub: "The commercial situations that deserve a decision", nav: "Where to intervene" },
};

// ── sign in ──────────────────────────────────────────────────────────────────
/** The platform's door. The card itself is `src/SignInCard.tsx`, shared with
 *  the Quote Builder — the two forms had drifted, and the copy that drifted was
 *  the one still telling people any password worked. */
function SignIn({ onIn, notice }: { onIn: (s: PlatformSession) => void; notice?: string | null }) {
  return (
    <SignInCard
      title="Commercial Decisions"
      blurb="One product, three doors. Your account decides what you see first and what you may act on."
      submitLabel="Sign in"
      notice={notice}
      onSubmit={async (email, password) => {
        const r = await papi.login(email, password);
        // Field by field rather than spreading the response, so a field the
        // server adds cannot arrive in the stored session unexamined. The cost
        // is that a new one has to be added here too — `email` was sent, typed
        // and schema-validated and still never reached the session, because
        // this line did not mention it.
        onIn({ token: r.token, role: r.role, name: r.name, email: r.email,
               user_id: r.user_id,
               organization_id: r.organization_id, currency: r.currency,
               timezone: r.timezone,
               must_change_password: r.must_change_password });
      }}
      footer={
        "Your account decides your role. An owner creates accounts and sets roles " +
        "from Settings; if you have not been given one, ask them."
      }
    />
  );
}

// ── action modal ─────────────────────────────────────────────────────────────
//
// Four intents, four distinct server actions. They used to be three, and both
// collapses cost something.
//
// `modify` posted `ACT`, exactly as `accept` did. Afterwards the two were
// indistinguishable, so the queue could report how often it was engaged with
// and never how often it was *agreed* with — while this very dialog told the
// person that "the difference from the recommendation is the most useful
// feedback the system gets", and then discarded it.
//
// `escalate` posted `OVERRIDE`, which is a closing status. That is the bug
// `HumanAction.ESCALATE` was added to fix: the server grew the whole
// escalation path — raise the approval request, park the decision in
// ESCALATED, settle it from the approvals queue — and this map was never
// pointed at it, so "Send to management" closed the decision and told
// management nothing. `AdminScreens` has described that as fixed since the
// server side landed.
//
// Rows written before this carry the old meanings: `ACTIONED` is accept-or-
// modify, `OVERRIDDEN` is escalate. `docs/architecture.md` records the
// cut-over — any adoption figure spanning it compares two definitions.
const ACTION_META: Record<string, { title: string; body: string; api: string; needsNote?: boolean }> = {
  accept: { title: "Accept and act", body: "The recommendation is logged against this decision. Nothing is sent to the customer.", api: "ACT" },
  modify: { title: "Do something different", body: "Record what you will actually do — the difference from the recommendation is the most useful feedback the system gets.", api: "OVERRIDE", needsNote: true },
  dismiss: { title: "Dismiss this decision", body: "Tell us why, so the same thing is not raised again next week.", api: "DISMISS", needsNote: true },
  escalate: { title: "Send to management", body: "Routed to someone who can see the full economics and approve a price. They receive the facts, the interpretation and your note.", api: "ESCALATE" },
};

/** Record what was decided.
 *
 * A `Dialog` rather than the hand-rolled overlay this replaces, for three
 * things that were missing and are not worth reimplementing: focus is trapped
 * inside while it is open, the page behind is inert to a screen reader, and
 * focus returns to whatever opened it on close. The old version also closed on
 * a click anywhere in the backdrop — including the blueprint corner marks,
 * which sit outside the element that stopped propagation, so clipping a corner
 * discarded a half-typed note.
 */
function ActionModal({
  kind,
  onClose,
  onConfirm,
  busy,
}: {
  kind: string;
  onClose: () => void;
  onConfirm: (note: string) => void;
  busy: boolean;
}) {
  const meta = ACTION_META[kind];
  const [note, setNote] = useState("");
  const missingNote = !!meta.needsNote && !note.trim();

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography
          component="div"
          variant="overline"
          sx={{ color: "text.secondary", display: "block", lineHeight: 1.4 }}
        >
          {kind.toUpperCase()}
        </Typography>
        {meta.title}
      </DialogTitle>
      <DialogContent sx={{ pt: 2 }}>
        <DialogContentText sx={{ fontSize: 13.5, mb: 2 }}>{meta.body}</DialogContentText>
        <TextField
          label={meta.needsNote ? "What you will do / why" : "Anything to add (optional)"}
          placeholder="Kept on the decision. Not sent to the customer."
          multiline
          minRows={3}
          fullWidth
          autoFocus
          required={meta.needsNote}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          // The confirm button below is disabled until this is filled in. Saying
          // so here is the difference between a form that is waiting and one
          // that looks broken.
          helperText={
            missingNote
              ? "Required — this is the note the next person reads."
              : " "
          }
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={busy || missingNote}
          onClick={() => onConfirm(note)}
        >
          {busy ? "Logging…" : "Log decision"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── main ─────────────────────────────────────────────────────────────────────
export default function PlatformApp() {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const [session, setSession] = useState<PlatformSession | null>(loadPlatformSession());
  // The URL is the screen, so Back, reload and shareable links all work. React
  // Router owns the matching; `screen` is only what the nav highlights, which is
  // a different question — a decision detail has no nav item of its own.
  const location = useLocation();
  const navigate = useNavigate();
  const screen: Screen = screenAt(location.pathname);
  const [summaries, setSummaries] = useState<DecisionSummary[] | null>(null);
  const [details, setDetails] = useState<Record<string, DecisionDetail>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listType, setListType] = useState("");
  const [modal, setModal] = useState<{ id: string; kind: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // A badge on the nav, because an approval queue nobody notices is the same as
  // no approval queue.
  const [pendingApprovals, setPendingApprovals] = useState(0);

  /** A destination named by the insight layer, followed. The naming table is
   *  in `route.ts` next to the paths it produces. */
  const goViz = useCallback((route: string) => {
    navigate(vizPath(route));
  }, [navigate]);

  /** Say something, and offer the way back if there is one.
   *
   * notistack rather than one piece of state holding one message. The old
   * version could only ever show the most recent: acting on two decisions in
   * quick succession replaced the first toast — and with it the only offer to
   * undo that action — before anybody could read it. The undo window is longer
   * than a plain acknowledgement for the same reason it always was.
   */
  const flash = useCallback((msg: string, undo?: () => void) => {
    enqueueSnackbar(msg, {
      autoHideDuration: undo ? 9000 : 3500,
      action: undo
        ? (key) => (
            <Button
              size="small"
              sx={{ color: "var(--color-accent-300)" }}
              onClick={() => { closeSnackbar(key); undo(); }}
            >
              Undo
            </Button>
          )
        : undefined,
    });
  }, [enqueueSnackbar, closeSnackbar]);

  const signOut = useCallback(() => {
    clearPlatformSession();
    setSession(null);
    setSummaries(null);
    setDetails({});
    setError(null);
  }, []);

  /** A dead session must return the user to sign-in, not strand them inside
   *  application chrome that looks live but can load nothing. */
  const handleAuthLoss = useCallback(() => {
    signOut();
    // The toast lives inside the signed-in shell, which is about to unmount —
    // the message has to survive onto the sign-in screen to be seen at all.
    setNotice("Your session expired. Please sign in again.");
  }, [signOut]);

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const list = await papi.listDecisions(session.token);
      setSummaries(list);
      const entries = await Promise.all(
        list.map(async (s) => [s.decision_id, await papi.getDetail(session.token, s.decision_id)] as const),
      );
      setDetails(Object.fromEntries(entries));
    } catch (e) {
      if (isAuthError(e)) return handleAuthLoss();
      // Keep whatever we last knew, but never let a stale/empty list be
      // presented as "nothing to do" — see the error branch in the render.
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session, handleAuthLoss]);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    papi
      .listApprovals(session.token, "PENDING")
      .then((r) => !cancelled && setPendingApprovals(r.pending_for_me))
      .catch(() => undefined);   // a badge must never break the shell
    return () => {
      cancelled = true;
    };
  }, [session, screen]);

  const signIn = (s: PlatformSession) => {
    setNotice(null);
    savePlatformSession(s);
    setSession(s);
    navigate(PATH.home);
  };
  const refresh = useCallback(async (id?: string) => {
    if (!session) return;
    if (id) {
      const d = await papi.getDetail(session.token, id);
      setDetails((m) => ({ ...m, [id]: d }));
    }
    setSummaries(await papi.listDecisions(session.token));
  }, [session]);

  // Opening a card is the one lifecycle event nothing recorded, and it is the
  // denominator for all the others: without it "nobody looked at this" and
  // "somebody read it and moved on" are the same row — which is the difference
  // between a detector that is wrong and a queue that is not being worked.
  //
  // Recorded here rather than in `getDetail`, because `load` prefetches the
  // detail of every decision in the list: hanging it off the fetch would mark
  // the whole queue VIEWED the moment the queue rendered. Navigation is the
  // only place that means a person opened this one.
  //
  // Sent only while the decision is still OPEN. That is exactly when it carries
  // information — the server's VIEW handler moves OPEN → VIEWED and leaves every
  // other status alone — so it fires once per open period, and again after a
  // REOPEN, instead of appending a trail entry on every visit. Fire-and-forget:
  // reading a card must never fail because recording the read did.
  const openDetail = useCallback(
    (id: string) => {
      navigate(pathFor("detail", id));
      if (!session) return;
      // Unknown status — the list has not loaded — is not OPEN for this
      // purpose. Recording a view we cannot place in the lifecycle would be a
      // guess, and the whole value of this event is that it is not one.
      if (summaries?.find((s) => s.decision_id === id)?.status !== "OPEN") return;
      papi.act(session.token, id, { action: "VIEW" })
        .then(() => refresh(id))
        .catch(() => undefined);
    },
    [navigate, session, summaries, refresh]);

  const undoAction = async (id: string) => {
    if (!session) return;
    try {
      await papi.reopen(session.token, id);
      await refresh(id);
      flash("Restored to your queue.");
    } catch (e) {
      if (isAuthError(e)) return handleAuthLoss();
      flash((e as Error).message);
    }
  };

  const doAction = async (id: string, kind: string, note: string) => {
    if (!session) return;
    setBusy(true);
    try {
      const meta = ACTION_META[kind];
      // `reason` is what the server persists to `override_reason`, and it does
      // that for OVERRIDE exactly as it does for DISMISS. Sending it only for
      // `dismiss` left the column null on the modify rows — the ones where the
      // reason is the entire point, because a modify is a disagreement with the
      // recommendation and the note is what says why. Keyed on `needsNote`
      // rather than on the kind, so an action that asks for a note is always
      // the same action that stores one.
      await papi.act(session.token, id,
        { action: meta.api, note, reason: meta.needsNote ? note : undefined });
      setModal(null);
      await refresh(id);
      // Every action here closes or changes a decision. Offer the way back:
      // one misclick should never be unrecoverable.
      flash(`${meta.title} · logged`, () => undoAction(id));
    } catch (e) {
      if (isAuthError(e)) return handleAuthLoss();
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openDecisions = useMemo(
    () => (summaries || []).filter((s) => s.status === "OPEN" || s.status === "VIEWED"),
    [summaries],
  );

  if (!session) return <SignIn onIn={signIn} notice={notice} />;

  // An account still holding the password it was issued reaches nothing else —
  // the server refuses every request but the change, so showing the shell would
  // be showing a screen where everything 403s. This is the way out, not a nag.
  if (session.must_change_password) {
    return (
      <ForcedPasswordChange
        session={session}
        onChanged={(token) => {
          const next = { ...session, token, must_change_password: false };
          savePlatformSession(next);
          setSession(next);
          navigate(PATH.home);
        }}
        onSignOut={signOut}
      />
    );
  }

  const rh = ROLE_HOME[session.role];
  const roleShort = session.role === "SALESPERSON" ? "Salesperson" : session.role === "SALES_MANAGER" ? "Manager" : "Owner";
  // What this role is offered, from one table rather than a ternary per item.
  // `ability.ts` says plainly what this is and is not: the server decides what
  // a role may *read*; this decides what the interface bothers to show, so a
  // nav item that would always 403 is simply absent.
  const ability = abilityFor(session);
  // The insight screens sit next to the briefing they are reached from. The two
  // that are entirely margin are omitted for a salesperson rather than shown and
  // then refused — a nav item that always 403s is a nav item that teaches people
  // the product is broken.
  const navItems: NavItem[] = [
    // ── Decide ──
    // The landing screen leads with what needs deciding, so it takes the role's
    // own name for that — "Today", "Team focus", "Where to intervene". It used
    // to be called "Storyboard" and the queue below it carried the role name,
    // which left two nav items claiming to be the place decisions live.
    { key: "home", label: rh.nav, group: "decide",
      // Open, not total: a badge counting closed decisions is a badge that
      // never goes down, and one that never goes down stops being read.
      count: openDecisions.length },
    // The full table, filterable and sortable, as opposed to the landing
    // screen's prioritised head of the same list.
    { key: "list", label: "All decisions", group: "decide",
      // A decision detail page has no nav entry of its own; it belongs to the
      // queue it was opened from, and the sidebar should say so.
      alsoCurrentFor: ["detail"] },
    // Every role: it is the salesperson's own screen, and a manager needs to
    // see what their team is proposing.
    { key: "negotiate", label: "Negotiate", group: "decide" },
    ...(ability.can("read", "simulation")
      ? ([{ key: "simulate", label: "Simulator", group: "decide" }] as NavItem[])
      : []),
    { key: "quotes", label: "Quotes", group: "decide" },
    // Every role: a win rate carries no cost, and a salesperson sees their
    // own accounts. The margin behind the losses is a second request the
    // server only answers for a manager, so the nav item is not scoped.
    { key: "quoteOutcomes", label: "Won & lost", group: "decide" },
    { key: "approvals", label: "Approvals", group: "decide", count: pendingApprovals },

    // ── Understand ──
    // The insight screens sit next to the briefing they are reached from. The
    // two that are entirely margin are omitted for a salesperson rather than
    // shown and then refused — a nav item that always 403s is a nav item that
    // teaches people the product is broken.
    ...(ability.can("read", "economics")
      ? ([{ key: "weather", label: "Weather", group: "understand" },
          { key: "opportunities", label: "Opportunities", group: "understand" },
          { key: "lostRevenue", label: "Lost revenue", group: "understand" },
          // Margin is on the vertical axis, and the endpoint is manager-scoped
          // whichever measure is asked for.
          { key: "landscape", label: "Landscape", group: "understand" }] as NavItem[])
      : []),
    // Mix and rhythm are revenue and dates — no cost anywhere in either — so
    // both are visible to a salesperson.
    { key: "composition", label: "Mix", group: "understand" },
    { key: "cadence", label: "Rhythm", group: "understand" },
    // Visible to everybody, unlike Suppliers: the customer half carries no cost
    // and no margin, and the server omits the supplier half from a
    // salesperson's response rather than the nav hiding the whole screen. A
    // salesperson has a real question here — which of my accounts is drifting —
    // and 403-ing them out of it to protect the other half would answer it by
    // removing it.
    { key: "bonds", label: "Bonds", group: "understand" },
    // Revenue and dates, no cost — and the conversation it exists for is a
    // salesperson's, so hiding it from them would be removing the feature to
    // protect a field it does not contain.
    { key: "mix", label: "Product mix", group: "understand" },
    // Both halves on one screen. The customer half is revenue and counts, so a
    // salesperson sees it; the server omits the supplier half from their
    // response rather than the nav hiding the whole screen.
    { key: "dependency", label: "Dependency", group: "understand" },
    // Manager and above: a principal's target is measured against purchase
    // spend, which is cost by another name.
    ...(ability.can("read", "supply")
      ? ([{ key: "targets", label: "Supplier targets", group: "understand" }] as NavItem[])
      : []),

    // ── The book ──
    // One "Customers" door, not two. The account picker, the month-by-month
    // journey and the period-against-period migration answer the same question
    // at three zoom levels; splitting them across "Customers" and "Accounts"
    // meant nobody found the second one, and the two names did not say which
    // held what.
    { key: "customer", label: "Customers", group: "book",
      alsoCurrentFor: ["customerItem", "journey"] },
    // Neither carries cost: receivables are money in, and stock structure is
    // counts. The purchase rate is dropped from a salesperson's stock copy.
    { key: "stock", label: "Stock", group: "book" },
    ...(ability.can("read", "economics")
      // Gross profit ÷ what the stock cost, end to end — there is no version of
      // this screen with the economics taken out, so `require_manager_or_owner`
      // guards the endpoint and the nav item follows it rather than offering a
      // door that always 403s. A salesperson is not left wondering: `/stock`
      // carries the withholding notice in its own `unavailable` list.
      ? ([{ key: "gmroi", label: "Return on stock", group: "book" }] as NavItem[])
      : []),
    ...(ability.can("read", "supply")
      // Supplier spend is purchase cost by another name, so the endpoint is
      // manager-scoped and the nav item follows it rather than 403-ing.
      ? ([{ key: "supply", label: "Suppliers", group: "book" }] as NavItem[])
      : []),
    { key: "payments", label: "Cash", group: "book" },
    // Beside Cash and scoped with it: dates and day counts carry no commercial
    // position, and the half of the cycle this screen exists to surface is the
    // half a salesperson can chase.
    { key: "orderToCash", label: "Order to cash", group: "book" },
    ...(ability.can("read", "supply")
      // How long we string a supplier along is a commercial position, not a
      // call list, so it is scoped like Suppliers rather than like Cash.
      ? ([{ key: "payables", label: "How we pay", group: "book" }] as NavItem[])
      : []),
    ...(ability.can("read", "supply")
      // Every row is a supplier balance against a date, so it is scoped with
      // the rest of the payable side rather than shown and then refused.
      ? ([{ key: "statutory", label: "Statutory deadlines",
            group: "book" }] as NavItem[])
      : []),

    // ── Setup ──
    // Setup, not Understand: placing an item is catalogue maintenance, and it
    // is where somebody goes when a mix screen says its coverage is thin.
    // Manager and above, like the policy it is.
    ...(ability.can("read", "supply")
      ? ([{ key: "catalogue", label: "Item lines", group: "setup" }] as NavItem[])
      : []),
    { key: "data", label: "Data & connection", group: "setup" },
    // Every call this screen makes is `require_manager_or_owner` — the list, the
    // pending suggestions, the settings policy — so for a salesperson it was a
    // nav item where nothing on the page worked. Unconditional here, three lines
    // below the comment forbidding exactly that. `read policy` is the same
    // manager-or-owner pair the identity reads carry; reusing it keeps the
    // vocabulary in `ability.ts` from growing a noun per screen.
    ...(ability.can("read", "policy")
      ? ([{ key: "identity", label: "Identities", group: "setup" }] as NavItem[])
      : []),
    { key: "states", label: "AI states", group: "setup" },
    // Owner only, mirroring `require_owner` on every `/trust/*` route. Named for
    // the question rather than for the mechanism: an owner looks for "my data",
    // not for "disclosure and break-glass".
    ...(ability.can("read", "trust")
      ? ([{ key: "trust", label: "Your data", group: "setup" }] as NavItem[])
      : []),
    { key: "settings", label: "Settings", group: "setup" },
  ];

  return (
    <AppShell
      items={navItems}
      current={screen}
      userName={session.name}
      roleLabel={roleShort}
      onSignOut={signOut}
    >
      <div>
        {/* An unreachable API must never be dressed as "nothing to do". The
            error REPLACES the queue rather than sitting above a reassuring
            empty state — the previous behaviour told a salesperson everything
            was fine at exactly the moment the system knew nothing. */}
        {error ? (
          <LoadFailed error={error} onRetry={load} busy={loading} />
        ) : (
          /* One boundary for every route, rather than one per screen: the
             fallback is only ever on screen for the moment a chunk is in
             flight, and thirty boundaries would be thirty places to get the
             shape of that moment wrong. `LoadingState` reserves height, so the
             page does not jump when the chunk lands — the same reason
             `DataGrid` sizes its own placeholder. */
          <Suspense fallback={<LoadingState rows={3} label="Opening…" />}>
          <Routes>
            {/* ── HOME: the Commercial Storyboard ──
                A briefing, not a queue. The decision list it used to show is
                still one click away at /decisions; what belongs on the first
                screen is what changed and what to do about it, which the queue
                alone cannot say — a list of open items answers "what is
                outstanding", never "what happened". */}
            <Route
              path={PATH.home}
              element={
                <HomeScreen
                  session={session}
                  title={rh.title}
                  sub={rh.sub}
                  open={openDecisions}
                  details={details}
                  loading={loading}
                  onOpen={openDetail}
                  onSeeAll={() => navigate(PATH.list)}
                  onNavigate={goViz}
                />
              }
            />

            {/* ── DECISION LIST ── */}
            <Route
              path={PATH.list}
              element={
                <ListScreen
                  summaries={summaries}
                  details={details}
                  loading={loading}
                  listType={listType}
                  setListType={setListType}
                  onOpen={openDetail}
                />
              }
            />

            {/* ── DETAIL ── */}
            <Route
              path={PATTERN.detail}
              element={
                <DetailRoute
                  details={details}
                  token={session.token}
                  loading={loading}
                  onAct={(id, kind) => setModal({ id, kind })}
                />
              }
            />

            {/* ── CUSTOMERS ──
                "Customers" and "Accounts" were two nav items for one thing, and
                the names did not say which held what. One screen now, and the
                merge is inside the screen rather than a stack of the two old
                ones: the directory carries what each account has actually been
                doing so it can be *chosen from* rather than only searched, and
                the two whole-book views sit under a heading that says they are
                the whole book. Picking an account replaces the lot with that
                account — which is a navigation, so it lands in the URL and Back
                returns to the directory.

                Two paths, one screen: the picker, and one account. */}
            {[PATH.customer, PATTERN.account].map((path) => (
              <Route
                key={path}
                path={path}
                element={
                  <CustomerRoute
                    session={session}
                    details={details}
                    onOpen={openDetail}
                    onNavigate={goViz}
                  />
                }
              />
            ))}

            {/* ── CUSTOMER x ITEM (the grain that names what is eroding) ── */}
            <Route path={PATTERN.customerItem} element={<CustomerItemRoute session={session} />} />

            {/* The path the account picker used to live at. Redirected rather
                than served, so a saved link ends up on the current URL. */}
            <Route path={LEGACY_ACCOUNTS} element={<Navigate to={PATH.customer} replace />} />

            {/* ── the visualization layer ── */}
            <Route path={PATH.weather} element={<WeatherScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.opportunities} element={<OpportunityScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.lostRevenue} element={<LostRevenueScreen session={session} onNavigate={goViz} />} />
            {/* Two answers to one question, stacked rather than split across two
                nav items: the journey chart is month by month, the migration
                matrix is period against period and names who moved. `journey`
                is kept as a route so existing links and the storyboard's own
                navigation still resolve. */}
            <Route
              path={PATH.journey}
              element={
                <div className="screen-stack">
                  <JourneyScreen session={session} onNavigate={goViz} />
                  <MigrationMatrix session={session} months={3} onNavigate={goViz} />
                </div>
              }
            />
            <Route path={PATH.simulate} element={<SimulatorScreen session={session} />} />
            <Route path={PATH.landscape} element={<LandscapeScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.composition} element={<CompositionScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.cadence} element={<CadenceScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.payments} element={<PaymentsScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.payables} element={<PayablesScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.orderToCash} element={<OrderToCashScreen session={session} />} />
            <Route path={PATH.statutory} element={<StatutoryScreen session={session} />} />
            <Route path={PATH.stock} element={<StockScreen session={session} />} />
            <Route path={PATH.gmroi} element={<GmroiScreen session={session} />} />
            <Route path={PATH.supply} element={<SupplyScreen session={session} />} />
            <Route path={PATH.bonds} element={<BondsScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.mix} element={<MixScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.dependency} element={<DependencyScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.targets} element={<TargetWallScreen session={session} />} />
            <Route path={PATH.catalogue} element={<CatalogueScreen session={session} />} />
            <Route path={PATH.negotiate} element={<NegotiateScreen session={session} />} />
            <Route path={PATH.quoteOutcomes} element={<QuoteOutcomesScreen session={session} />} />

            {/* ── QUOTES ──
                The Quote Builder itself, not a door in front of it. It used to
                be a second application behind a button here: clicking through
                replaced the whole shell, asked for a second sign-in, and then
                showed a different name in a different brand bar. It is a screen
                like any other now, on this session. */}
            <Route path={PATH.quotes} element={<QuoteBuilder session={session} />} />

            {/* ── DATA & CONNECTION ── */}
            <Route path={PATH.data} element={<DataScreen session={session} onSynced={load} />} />
            <Route path={PATH.approvals} element={<ApprovalsScreen session={session} />} />
            <Route path={PATH.identity} element={<IdentityScreen token={session.token} />} />
            <Route path={PATH.trust} element={<TrustScreen session={session} />} />
            <Route path={PATH.settings} element={<SettingsScreen session={session} />} />

            {/* ── AI STATES (reference) ── */}
            <Route path={PATH.states} element={<StatesScreen />} />

            {/* A path nobody recognises. Redirected rather than rendered as
                home, so what the address bar says and what the screen shows do
                not disagree. */}
            <Route path="*" element={<Navigate to={PATH.home} replace />} />
          </Routes>
          </Suspense>
        )}
      </div>

      {modal && (
        <ActionModal
          kind={modal.kind}
          busy={busy}
          onClose={() => setModal(null)}
          onConfirm={(note) => doAction(modal.id, modal.kind, note)}
        />
      )}
    </AppShell>
  );
}

// ── the routes that carry an id ──────────────────────────────────────────────
/* Three screens are *about* something named in the URL. Each gets a thin
 * component so `useParams` reads the id where React Router matched it, instead
 * of the shell re-parsing `location.pathname` to work out what it is showing.
 * Module level rather than nested inside `PlatformApp`: a component redefined
 * on every render is a component React remounts on every render, and these hold
 * their screens' state. */

function DetailRoute({
  details, token, loading, onAct,
}: {
  details: Record<string, DecisionDetail>;
  token: string;
  loading: boolean;
  onAct: (id: string, kind: string) => void;
}) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  return (
    <DetailScreen
      d={details[id]}
      token={token}
      loading={loading}
      onBack={() => navigate(PATH.list)}
      onAct={(kind) => onAct(id, kind)}
      onOpenAccount={(cid) => navigate(pathFor("customer", cid))}
    />
  );
}

function CustomerRoute({
  session, details, onOpen, onNavigate,
}: {
  session: PlatformSession;
  details: Record<string, DecisionDetail>;
  onOpen: (id: string) => void;
  onNavigate: (route: string) => void;
}) {
  // Absent on the picker path, present on `/account/<id>` — the one piece of
  // state this screen used to hold and now reads from where it belongs.
  const { id } = useParams();
  const navigate = useNavigate();
  return (
    <CustomerScreen
      session={session}
      details={details}
      customerId={id ?? null}
      setCustomerId={(cid) => navigate(cid ? pathFor("customer", cid) : PATH.customer)}
      onOpen={onOpen}
      onOpenItem={(pid) => id && navigate(pathFor("customerItem", id, pid))}
      onNavigate={onNavigate}
    />
  );
}

function CustomerItemRoute({ session }: { session: PlatformSession }) {
  const { id = "", itemId = "" } = useParams();
  const navigate = useNavigate();
  return (
    <CustomerItemScreen
      session={session}
      customerId={id}
      productId={itemId}
      onBack={() => navigate(pathFor("customer", id))}
      // A row in the peer comparison is another account buying the same item —
      // clicking it opens that relationship, which is the next question anybody
      // asks of that table.
      onOpenCustomer={(cid) => navigate(pathFor("customerItem", cid, itemId))}
    />
  );
}

// ── load failure ─────────────────────────────────────────────────────────────
/** Shown INSTEAD OF the queue when decisions could not be loaded.
 *
 * The distinction that matters: "we could not look" is not "there is nothing
 * to see". This screen never implies the latter, and it always offers the way
 * back rather than requiring a manual page reload. */
function LoadFailed({ error, onRetry, busy }: { error: string; onRetry: () => void; busy: boolean }) {
  return (
    <Box sx={{ maxWidth: 620 }}>
      <ErrorState
        title="Decisions could not be loaded"
        error={
          <>
            This is a loading failure, not an empty queue — there may well be
            decisions waiting. Nothing has been lost; your data is untouched.
            <Box sx={{ mt: 1, fontWeight: 600 }}>{error}</Box>
          </>
        }
        onRetry={onRetry}
        busy={busy}
      />
    </Box>
  );
}

// ── the landing screen ───────────────────────────────────────────────────────
/** What needs a decision, and then what changed.
 *
 * This screen used to be the storyboard alone. The storyboard is a good
 * briefing — it is an ordered list of beats, each carrying what changed, why,
 * and where to go — but it answers "what happened to the business" and the
 * product's actual output is "what should someone do today". That output lived
 * one click away, behind a nav item, which made the queue something you had to
 * know to look for.
 *
 * So the order is inverted rather than the storyboard replaced: the decisions
 * first, the movement that produced them underneath as supporting context. The
 * storyboard is unchanged and still reachable on its own terms.
 *
 * **Why there is no total.** The obvious header is "N decisions worth ₹X", and
 * it would be wrong. Dead-stock capital and revenue at risk are different
 * claims, so summing `impact.financial` across types produces a figure that
 * means nothing and invites a decision against it. The count is broken down by
 * priority band instead — which is not a money claim — and each card carries
 * its own figure with the sentence that says what it is.
 */
const BAND_ORDER = ["HIGH", "MEDIUM", "LOW"] as const;

/** How many cards before the screen stops being a summary. The rest are one
 *  click away, and the link says how many. */
const HOME_CARDS = 5;

function HomeScreen({
  session, title, sub, open, details, loading, onOpen, onSeeAll, onNavigate,
}: {
  session: PlatformSession;
  title: string;
  sub: string;
  open: DecisionSummary[];
  details: Record<string, DecisionDetail>;
  loading: boolean;
  onOpen: (id: string) => void;
  onSeeAll: () => void;
  onNavigate: (route: string) => void;
}) {
  // Same subject as `CashProjection`'s gate, and for the same reason: the
  // morning read carries what we owe suppliers alongside what is owed to us.
  // `supply` mirrors `require_manager_or_owner`, which is the dependency on the
  // endpoint — see ability.ts.
  const mayReadDaily = abilityFor(session).can("read", "supply");

  // Already sorted by the server on priority then recency; take the head.
  const top = open.slice(0, HOME_CARDS);
  // The morning read sits above the queue rather than replacing it. The queue
  // answers "what needs deciding"; the bands answer "what is going on" — and
  // the queue is one tile inside them, so the tile links down to the list
  // rather than the two competing for the same space.
  const bands = BAND_ORDER
    .map((b) => [b, open.filter((s) => s.priority_band === b).length] as const)
    .filter(([, n]) => n > 0);

  return (
    <div>
      <div className="dp-head">
        <h1>{title}</h1>
        <p>{sub}</p>
      </div>

      {/* The morning read. Above the queue because the first question is "can I
          trust this and what is going on", and the queue is one tile inside the
          answer. It loads independently and degrades in place — a landing page
          that blanks because one endpoint failed is worse than one that says
          which part is missing.

          Offered only to the roles that can load it. `GET /insight/daily` is
          `require_manager_or_owner`, half of it being what we owe suppliers, so
          rendering it for a salesperson put an amber "The morning read did not
          load — Manager or owner role required" at the top of the first screen
          they see every day. Omitted rather than rendered and then 403'd, for
          the reason `PaymentsScreen` gives about `CashProjection`: a panel that
          always fails teaches people the product is broken. */}
      {/* Its own boundary, not the route's: the queue below is the reason
          somebody opened this page, and holding it behind a chunk that belongs
          to the panel above it would trade one blank screen for another. */}
      {mayReadDaily && (
        <Suspense fallback={<LoadingState rows={2} label="Reading this morning…" />}>
          <DailyScreen session={session} onNavigate={onNavigate} />
        </Suspense>
      )}

      {loading && open.length === 0 ? (
        <Stack spacing={1.5} sx={{ mb: 4 }}>
          <Skeleton variant="rounded" height={104} />
          <Skeleton variant="rounded" height={104} />
        </Stack>
      ) : open.length === 0 ? (
        /* Not "all clear". Nothing is flagged, which is a fact about the
           evidence and not a verdict on the business. */
        <div className="empty-state-card compact" style={{ marginBottom: 28 }}>
          <div className="empty-state-card__eyebrow">Nothing waiting</div>
          <h3>No decision needs you right now</h3>
          <p>
            Nothing in the book currently clears the thresholds that raise a decision. That is a
            statement about the evidence, not a judgement that everything is well — what changed
            over the period is below.
          </p>
        </div>
      ) : (
        <>
          <div className="dp-count">
            {open.length} open {open.length === 1 ? "decision" : "decisions"}
            {bands.length > 0 && (
              <>
                {" · "}
                {bands.map(([b, n], i) => (
                  <span key={b}>
                    {i > 0 && " · "}
                    {n} {b.toLowerCase()}
                  </span>
                ))}
              </>
            )}
          </div>

          <div className="dp-cards tight">
            {top.map((s) => {
              const d = details[s.decision_id];
              return d
                ? <DecisionCard key={s.decision_id} d={d} onOpen={onOpen} compact />
                : <Skeleton key={s.decision_id} variant="rounded" height={104} />;
            })}
          </div>

          {open.length > top.length && (
            <Button onClick={onSeeAll} sx={{ mt: 1.5 }}>
              See all {open.length} decisions →
            </Button>
          )}
        </>
      )}

      {/* The briefing, demoted to what it is: the movement these decisions came
          out of, for whoever wants to check the arithmetic behind them. */}
      <div className="home-context">
        <div className="home-context-mark">
          <Labelled tip="The period movement the decisions above were detected against. Kept on this screen rather than behind a nav item because 'why is this being raised now' is the first question anyone asks of a queue.">
            The evidence behind them
          </Labelled>
        </div>
        {/* Below the fold and below the queue, so its chunk arrives while
            somebody is already reading — and never at all for somebody who
            only came to work the queue. */}
        <Suspense fallback={<LoadingState rows={3} />}>
          <Storyboard session={session} onNavigate={onNavigate} />
        </Suspense>
      </div>
    </div>
  );
}

// ── list screen ──────────────────────────────────────────────────────────────
/** A summary joined to its detail, which arrives a moment later. Joined here
 *  rather than looked up inside a cell so the account and the reason are
 *  *sortable and filterable* — a column resolved in a renderer displays fine
 *  and sorts on nothing. */
type DecisionRow = DecisionSummary & { detail?: DecisionDetail };

function ListScreen({
  summaries,
  details,
  loading,
  listType,
  setListType,
  onOpen,
}: {
  summaries: DecisionSummary[] | null;
  details: Record<string, DecisionDetail>;
  loading: boolean;
  listType: string;
  setListType: (t: string) => void;
  onOpen: (id: string) => void;
}) {
  // Derived from what is actually in the queue rather than hardcoded. There
  // are twelve decision types now and a fixed list of five silently hid seven
  // of them — while a fixed list of twelve would put chips on screen for
  // categories this book has never raised. Ordered by how many rows each has,
  // so the busiest filter is the easiest to reach.
  const counts = new Map<string, number>();
  for (const s of summaries || []) {
    counts.set(s.decision_type, (counts.get(s.decision_type) || 0) + 1);
  }
  const types = ["", ...[...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([t]) => t)];
  const byType = (summaries || []).filter((s) => !listType || s.decision_type === listType);
  // Company narrows what the type chips already narrowed. Applied last so the
  // chip counts stay counts of the whole queue — a chip that changed its own
  // number when a company was chosen would be reporting on itself.
  const company = useCompanyFilter(byType);
  const rows = company.filtered;
  return (
    <div>
      <div className="dp-head">
        <h1>Decisions</h1>
        <p>Every decision raised, open and closed — sorted by priority then recency.</p>
      </div>
      {/* Chips rather than buttons: these select what is shown, they do not
          perform an action, and a row of things that look like buttons reads as
          a row of things that will do something. */}
      <Stack direction="row" spacing={1} useFlexGap sx={{ mb: 2, flexWrap: "wrap" }}>
        {types.map((t) => (
          <FilterChip
            key={t || "all"}
            label={t ? typeLabel(t) : "All"}
            count={t ? counts.get(t) : (summaries || []).length}
            selected={listType === t}
            onClick={() => setListType(t)}
          />
        ))}
        <CompanyFilter options={company.options} value={company.company}
                       onChange={company.setCompany} show={company.show} />
      </Stack>
      {loading && !summaries ? (
        <Stack spacing={1}>
          <Skeleton variant="rounded" height={44} />
          <Skeleton variant="rounded" height={44} />
          <Skeleton variant="rounded" height={44} />
        </Stack>
      ) : rows.length === 0 ? (
        <EmptyState title="No decisions match this filter"
                    reason="Clear a chip or a company above to widen it." />
      ) : (
        <DataGrid<DecisionRow>
          ariaLabel="Decisions"
          twoLineRows
          rows={rows.map((r) => ({ ...r, detail: details[r.decision_id] }))}
          onRowClick={(r) => onOpen(r.decision_id)}
          columns={[
            {
              // Sorted on the score, not the band: three HIGH rows in an
              // arbitrary order is a list that does not answer "what first?".
              field: "priority_score", headerName: "Priority", width: 130, flex: 0,
              filter: "agNumberColumnFilter", sort: "desc",
              headerTooltip: "Computed from what the movement is worth and how "
                + "certain it is — deterministic first, with any AI adjustment "
                + "recorded separately and bounded.",
              cellRenderer: (p: { data?: DecisionRow }) =>
                p.data ? <Pri band={p.data.priority_band} /> : null,
            },
            {
              field: "decision_type", headerName: "Type", width: 190, flex: 0,
              filter: "agTextColumnFilter",
              cellStyle: { fontFamily: "var(--font-heading)" },
              valueFormatter: (p) => typeLabel(String(p.value)),
            },
            {
              // The label arrives with the detail, a moment after the summary.
              // Until then this said the raw entity id — a UUID nobody
              // recognises, in the column people scan to find their account.
              // The name, and which connected company it belongs to. Pooled
              // across three books, "ABC Industries" appears three times and
              // they are three different customers with three different
              // problems — a queue that cannot tell them apart cannot be
              // worked from. Filtering and sorting still run on the name, so
              // the source line is information rather than a sort key.
              headerName: "Account / subject", flex: 1, minWidth: 240,
              filter: "agTextColumnFilter",
              valueGetter: (p) => p.data?.detail?.subject_label ?? "",
              cellRenderer: (p: { data?: DecisionRow; value?: string }) =>
                p.value ? (
                  <EntityName
                    name={p.value}
                    origin={p.data?.subject_origin}
                    show={Boolean(p.data?.sources_differ)}
                  />
                ) : (
                  <span className="viz-muted">…</span>
                ),
            },
            {
              // Two producers, two answers to "why". A signal decision has a
              // reading of the evidence; a state one has the arithmetic that
              // produced it. Showing the AI field for both left every state
              // row with an em dash in the column people scan to decide what
              // to open.
              headerName: "Why", flex: 1.6, minWidth: 260, filter: "agTextColumnFilter",
              valueGetter: (p) =>
                (p.data?.detail?.origin === "STATE"
                  ? p.data?.detail?.rationale
                  : p.data?.detail?.interpretation.explanation) ?? "",
              valueFormatter: (p) => p.value || "—",
              tooltipValueGetter: (p) => String(p.value || ""),
            },
            {
              // Likewise: a state decision is worth a number of rupees, and a
              // signal decision is backed by an amount of evidence. Neither
              // column can carry both, so the cell renders whichever the row
              // actually has.
              headerName: "Worth / confidence", width: 165, flex: 0, sortable: false,
              filter: false,
              headerTooltip: "For a decision folded from business state, what it "
                + "is worth. For one raised from a signal, how much evidence "
                + "stands behind the reading — not how sure a model is.",
              cellRenderer: (p: { data?: DecisionRow }) => {
                const d = p.data?.detail;
                if (!d) return <span className="viz-muted">—</span>;
                if (d.origin === "STATE") {
                  return d.impact?.financial
                    ? <b style={{ fontVariantNumeric: "tabular-nums" }}>
                        {money(d.impact.financial)}
                      </b>
                    : <span className="viz-muted">not sizeable</span>;
                }
                return <Conf level={d.confidence?.evidence_sufficiency} />;
              },
            },
            { field: "status", headerName: "Status", width: 120, flex: 0,
              filter: "agTextColumnFilter" },
          ]}
        />
      )}
    </div>
  );
}

// ── detail screen ────────────────────────────────────────────────────────────
/** The drill-down: decision → state → transition → event → ERP record.
 *
 *  Collapsed by default and fetched only when opened. Almost nobody opens it —
 *  but the people who do are the ones asking "where did this number come
 *  from", and until now the honest answer was a shrug. Paying for the chain on
 *  every card view would be paying for the exception.
 */
function TracePanel({ decisionId, token }: { decisionId: string; token: string }) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<DecisionTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    // Reset when the card changes, or the panel would show the last
    // decision's chain under this one's heading.
    setOpen(false);
    setTrace(null);
    setError(null);
  }, [decisionId]);

  useEffect(() => {
    if (!open || trace) return;
    let live = true;
    papi.getTrace(token, decisionId)
      .then((t) => { if (live) setTrace(t); })
      .catch((e) => { if (live) setError(String(e?.message || e)); });
    return () => { live = false; };
  }, [open, trace, token, decisionId]);

  /** Append the next page rather than replacing it: somebody following a
   *  figure back through a year is building a picture, and losing the rows
   *  they already read would make them start again. */
  const more = useCallback(async () => {
    if (!trace) return;
    const shown = trace.states.reduce((n, l) => n + l.transitions.length, 0);
    setLoadingMore(true);
    try {
      const next = await papi.getTrace(token, decisionId, shown);
      setTrace({
        ...next,
        states: next.states.map((level, i) => ({
          ...level,
          transitions: [...(trace.states[i]?.transitions ?? []), ...level.transitions],
        })),
      });
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setLoadingMore(false);
    }
  }, [trace, token, decisionId]);

  return (
    <>
      <div className="section-h">
        <Labelled tip="Every hop from this decision down to the document in Zoho it was ultimately read from. Nothing in the chain is stored twice — each step is a lookup along a key that already exists, so it cannot disagree with the card above it.">
          Where this came from
        </Labelled>
      </div>
      <Button variant="text" size="small" onClick={() => setOpen(!open)}>
        {open ? "Hide the chain" : "Trace it to the source →"}
      </Button>

      {open && error && (
        <div className="dp-empty" style={{ padding: 12, textAlign: "left" }}>
          The chain could not be loaded: {error}
        </div>
      )}
      {open && !trace && !error && <div className="dp-loading">Following the chain…</div>}
      {open && trace?.unavailable && (
        <div className="dp-empty" style={{ padding: 12, textAlign: "left" }}>
          {trace.unavailable}
        </div>
      )}

      {open && trace?.states.map((level) => (
        <div className="trace" key={`${level.state}:${level.key}`}>
          <div className="trace-level">
            <span className="trace-mark">Business state</span>
            <b>{level.state}</b> · {level.label} · as of {formatDate(level.as_of)}
            <div className="text-muted" style={{ fontSize: 11 }}>
              folded from {level.event_count} event{level.event_count === 1 ? "" : "s"}
              {level.thresholds_version ? ` · policy ${level.thresholds_version}` : ""}
            </div>
          </div>

          <div className="trace-level">
            <span className="trace-mark">
              State transitions · what moved it
              {level.transitions_total > level.transitions.length &&
                ` (newest ${level.transitions.length} of ${level.transitions_total})`}
            </span>
            <table className="facttable trace-table">
              <tbody>
                {level.transitions.map((t) => (
                  <tr key={t.event_seq}>
                    <td>
                      {formatDate(t.occurred_on)}
                      <div className="fsrc">{t.event_type.toLowerCase().replace(/_/g, " ")}</div>
                    </td>
                    <td>
                      {t.changes.map(([op, field, value], i) => (
                        <div key={i} className="trace-change">
                          <span className="op">{op}</span> {stateFieldLabel(field)}
                          {" → "}<b>{String(value)}</b>
                        </div>
                      ))}
                    </td>
                    {/* The bottom of the chain: a document somebody can open.
                        A stock reading is the exception — it rides on the item
                        list rather than being a document of its own, and
                        printing a synthetic id would send somebody looking in
                        Zoho for something that is not there. */}
                    <td className="fv">
                      {!t.erp ? (
                        <span className="text-muted">event no longer held</span>
                      ) : t.erp.record_type === "stock" ? (
                        <span className="text-muted">counted from the item list</span>
                      ) : (
                        <span title={t.erp.system}>
                          {t.erp.record_type} {t.erp.record_id}
                          {t.erp.line_id ? ` · line ${t.erp.line_id}` : ""}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {level.has_more && (
              <Button variant="text" size="small" onClick={more}
                      disabled={loadingMore}>
                {loadingMore
                  ? "Following further back…"
                  : `Show ${Math.min(40, level.transitions_total - level.transitions.length)} more of ${level.transitions_total}`}
              </Button>
            )}
          </div>
        </div>
      ))}
    </>
  );
}

function DetailScreen({
  d,
  token,
  loading,
  onBack,
  onAct,
  onOpenAccount,
}: {
  d: DecisionDetail | undefined;
  token: string;
  loading: boolean;
  onBack: () => void;
  onAct: (kind: string) => void;
  onOpenAccount: (cid: string) => void;
}) {
  if (loading && !d) return <div className="dp-loading">Loading decision…</div>;
  if (!d) {
    return (
      <EmptyState
        title="Decision not found in this view"
        reason="It may have been closed, or it may belong to somebody else's queue." />
    );
  }
  const state = aiState(d.interpretation.status);
  const closed = d.status !== "OPEN" && d.status !== "VIEWED";
  // Two producers, two kinds of claim, two cards. Read from the row rather
  // than sniffed from which fields are populated — a signal decision with an
  // empty impact must not render as a state one worth nothing.
  const fromState = d.origin === "STATE";
  return (
    <div>
      <Button variant="text" size="small" onClick={onBack} style={{ marginBottom: 10 }}>
        ← All decisions
      </Button>
      <div className="dcard-top" style={{ marginBottom: 4 }}>
        <span className="dcard-type">{typeLabel(d.decision_type)}</span>
        <Pri band={d.priority.band} />
        {closed && <StatusChip label={d.status} tone="neutral" />}
      </div>
      {/* The card is where somebody decides, so it has to say which book it is
          about. Two accounts called "Pitti Engineering" raise two cards, and
          opening one without knowing which is opening the wrong one half the
          time. The queue carried this before the card did, which was backwards:
          the queue is scanned, the card is acted on. */}
      <h1 style={{ margin: "2px 0 4px" }}>{d.subject_label}</h1>
      <div style={{ margin: "0 0 18px" }}>
        <EntitySource origin={d.subject_origin} show={Boolean(d.sources_differ)} />
        {/* Whose this is. `assigned_user_id` and `assigned_role` were both on the
            wire and neither reached a screen, so a card could not answer the
            first question anybody asks about a decision. A null assignee with a
            role set is not missing data — the decision belongs to the role — and
            saying that beats rendering a blank. */}
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
          {d.assigned_to
            ? `Covered by ${d.assigned_to}`
            : `Routed to ${ROLE_LABEL[d.assigned_role] ?? d.assigned_role} — no individual owner`}
        </Typography>
      </div>

      <div className="dp-split">
        {/* LEFT — what the data shows. For a state decision that is the
            impact, the reason and the evidence it was computed from; for a
            signal decision it is the facts the detector measured. */}
        <div>
          {fromState ? (
            <>
              <ImpactPanel impact={d.impact} />
              <WhyPanel rationale={d.rationale} evidence={d.state_evidence} />
              <TracePanel decisionId={d.decision_id} token={token} />
              {d.human_action && (
                <HumanLog action={d.human_action} />
              )}
            </>
          ) : (
          <>
          <div className="facts-mark">Facts · what the data shows</div>
          {d.facts.length === 0 ? (
            <div className="dp-empty" style={{ padding: 16, textAlign: "left" }}>
              No numeric facts are exposed at your permission level for this decision.
            </div>
          ) : (
            <Bp style={{ padding: "8px 14px" }}>
              <table className="facttable">
                <tbody>
                  {d.facts.filter((f) => isPrimaryFact(f.label)).map((f) => (
                    <tr key={f.label}>
                      <td>
                        {factLabel(f.label)}
                        <div className="fsrc">{f.source}</div>
                      </td>
                      <td className="fv">{factValue(f.label, f.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Bp>
          )}

          <div className="section-h">
            <Labelled tip="The source records this decision was built from, by system. Every fact above traces back to one of them — nothing here is inferred.">
              Evidence used
            </Labelled>
          </div>
          {d.evidence.length === 0 ? (
            <div className="text-muted" style={{ fontSize: 13 }}>
              Source records are recorded with the signal.
            </div>
          ) : (
            groupEvidence(d.evidence).map((e, i) => (
              <div className="evi" key={i}>
                <span>{e.system}</span>
                <span className="text-muted">{e.count}</span>
              </div>
            ))
          )}

          {d.human_action && (
            <HumanLog action={d.human_action} />
          )}
          </>
          )}
        </div>

        {/* RIGHT — what to do about it */}
        <div>
          {fromState ? (
            <>
              <ActionsPanel actions={d.actions} />
              <RankingPanel ranking={d.ranking} />
              <div className="text-muted" style={{ fontSize: 11, marginTop: 8 }}>
                Computed from business state as of {d.state.as_of || "—"} ·
                no model was involved
                <Tip text="This decision is arithmetic over folded business state. Nothing about it was written or scored by a model, which is why it carries no confidence percentage — it carries its working instead." />
              </div>
            </>
          ) : (
          <>
          <Interpretation d={d} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "10px 0 4px" }}>
            <Conf level={d.confidence?.evidence_sufficiency} aiStatus={d.interpretation.status} />
            <span className="text-muted" style={{ fontSize: 11 }}>
              priority {d.priority.score}/100 · base {d.priority.deterministic_base}
              {d.priority.ai_adjustment ? ` · ai ${d.priority.ai_adjustment > 0 ? "+" : ""}${d.priority.ai_adjustment}` : ""}
              <Tip text="The base is computed from the signal's own figures. Any AI adjustment is shown separately and cannot move the score far — and the queue orders on the base, so a model can colour a card but never move it up the list." />
            </span>
          </div>
          </>
          )}

          {!closed && (
            <div className="action-panel">
              {state === "ok" && (
                <Button variant="contained" onClick={() => onAct("accept")}>
                  Accept the recommendation
                </Button>
              )}
              <Button variant="outlined" onClick={() => onAct("modify")}>
                {/* A state decision offers options and recommends none, so
                    there is nothing to "do differently" from. */}
                {fromState ? "Record what you did" : "Do something different"}
              </Button>
              <div style={{ display: "flex", gap: 8 }}>
                <Button variant="text" size="small" onClick={() => onAct("dismiss")}>
                  Dismiss with reason
                </Button>
                <Button variant="text" size="small" onClick={() => onAct("escalate")}>
                  Escalate to management
                </Button>
              </div>
              <Button
                variant="text" size="small"
                style={{ alignSelf: "flex-start" }}
                onClick={() => onOpenAccount(d.subject_entity_id)}
              >
                Open the account →
              </Button>
            </div>
          )}
          {closed && (
            <div className="outcome">
              <div className="outcome-mark">Closed</div>
              <p style={{ margin: 0, fontSize: 13.5 }}>
                This decision was {d.status.toLowerCase()}. Outcome measurement runs on later Zoho data and will
                appear here when available.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── customer intelligence (light) ────────────────────────────────────────────
function CustomerScreen({
  session,
  details,
  customerId,
  setCustomerId,
  onOpen,
  onOpenItem,
  onNavigate,
}: {
  session: PlatformSession;
  details: Record<string, DecisionDetail>;
  customerId: string | null;
  setCustomerId: (id: string | null) => void;
  onOpen: (id: string) => void;
  onOpenItem: (productId: string) => void;
  /** Where the whole-book views send a click. Same signature every viz screen
   *  takes, so this screen does not become a second routing table. */
  onNavigate: (route: string) => void;
}) {
  const all = Object.values(details);
  // The directory is every account in scope — not only those that happen to
  // have an open decision. "What does this account look like before I call
  // them?" is the question this screen exists to answer, and it cannot be
  // answered for a quiet customer if quiet customers are invisible.
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [sort, setSort] = useState<"name" | "recent" | "value">("name");
  const [accErr, setAccErr] = useState<string | null>(null);
  // Options from the whole directory, not from whatever the search left — a
  // company that vanishes from the dropdown when you type is a company you
  // cannot get back to without clearing the box first.
  const accountCompany = useCompanyFilter(accounts ?? []);

  useEffect(() => {
    let cancelled = false;
    setAccounts(null);
    papi
      .listAccounts(session.token, "", status)
      .then((a) => !cancelled && setAccounts(a))
      .catch((e) => !cancelled && setAccErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [session.token, status]);

  const openCountFor = (id: string) =>
    all.filter((d) => d.subject_entity_id === id && (d.status === "OPEN" || d.status === "VIEWED")).length;

  /** One account as the grid sees it: the directory row plus the open-decision
   *  count, folded in here so the column can be *sorted* on it. A count
   *  computed inside a cell renderer is a column that renders correctly and
   *  sorts on nothing. */
  type AccountRow = Account & { open_decisions: number };

  if (!customerId) {
    const needle = q.trim().toLowerCase();
    // Sorted client-side: the list is one request and a few hundred rows, and
    // a round trip to re-order something already in hand is a round trip the
    // person waits for.
    const shown: AccountRow[] = (accounts || [])
      .filter((a) => !needle || a.name.toLowerCase().includes(needle))
      .map((a) => ({ ...a, open_decisions: openCountFor(a.customer_id) }))
      .sort((x, y) => {
        if (sort === "value") return y.revenue_12m - x.revenue_12m;
        if (sort === "recent") {
          // Never-ordered sorts last rather than first: an empty string would
          // put every contact-only account above the accounts actually trading.
          return (y.last_order || "").localeCompare(x.last_order || "");
        }
        return x.name.localeCompare(y.name);
      });
    // Applied after search and sort, so the count below reports what is on
    // screen. Hooks are called unconditionally: this branch is inside the
    // component, and a filter behind an `if` is a filter React forbids.
    const company = accountCompany;
    const rows = company.apply(shown);
    const quiet = rows.filter((a) => !a.last_order).length;

    return (
      <div>
        <div className="dp-head">
          <h1>Customers</h1>
          <p>
            Every account you cover, with what they have actually been doing —
            then the whole book's movement underneath.
          </p>
        </div>

        <div className="acct-controls">
          <input
            className="input acct-search"
            placeholder="Search customers…"
            aria-label="Search customers"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {/* Active by default. The pull reads inactive contacts because their
              history has to resolve, which is not a reason to put a dormant
              2019 account in the list somebody scans before a call. */}
          <Seg label="Show" value={status}
               onChange={(v) => setStatus(v as StatusFilter)}
               options={[["active", "Active"], ["inactive", "Inactive"], ["all", "All"]]} />
          <Seg label="Sort by" value={sort}
               onChange={(v) => setSort(v as "name" | "recent" | "value")}
               options={[["name", "Name"], ["recent", "Last order"], ["value", "12-month value"]]} />
          <CompanyFilter options={company.options} value={company.company}
                         onChange={company.setCompany} show={company.show} />
        </div>

        {accErr ? (
          <ErrorState title="Customers could not be loaded" error={accErr} />
        ) : accounts === null ? (
          <>
            <LoadingState rows={2} />
          </>
        ) : rows.length === 0 ? (
          <div className="dp-empty">
            {needle
              ? `No ${status === "all" ? "" : status + " "}customer matches “${q}”.`
              : status === "inactive"
                ? "No customer is marked inactive."
                : "No customers are assigned to you yet."}
          </div>
        ) : (
          <>
            <div className="dp-count">
              {rows.length} {rows.length === 1 ? "customer" : "customers"}
              {status !== "all" && ` marked ${status}`}
              {quiet > 0 && ` · ${quiet} have never ordered`}
            </div>
            <DataGrid<AccountRow>
              ariaLabel="Customers"
              twoLineRows
              pageSize={25}
              rows={rows}
              onRowClick={(a) => setCustomerId(a.customer_id)}
              columns={[
                {
                  field: "name", headerName: "Customer", flex: 1, minWidth: 240,
                  filter: "agTextColumnFilter",
                  // Marked on the row rather than in a column of its own: it
                  // only matters when it is true, and only when the filter is
                  // showing them.
                  cellRenderer: (p: { data?: AccountRow }) => (
                    <EntityName
                      name={p.data?.name ?? ""}
                      origin={p.data?.origin}
                      show={Boolean(p.data?.sources_differ)}
                      sub={(p.data?.status || "").toUpperCase() !== "ACTIVE"
                        ? <span className="acct-flag">inactive</span>
                        : undefined}
                    />
                  ),
                },
                {
                  field: "last_order", headerName: "Last order", width: 150, flex: 0,
                  context: { minGridWidth: 460 },
                  // A text filter, not agDateColumnFilter: the value is an ISO
                  // string, so it already sorts chronologically, and the date
                  // filter would render a US mm/dd/yy picker on an Indian
                  // screen and compare it against text.
                  filter: "agTextColumnFilter",
                  valueFormatter: (p) =>
                    p.value ? formatDate(String(p.value)) : "never ordered",
                },
                numeric<AccountRow>("orders_12m", "Orders (12m)", (n) => String(n),
                                    { width: 145, flex: 0,
                                      context: { minGridWidth: 760 } }),
                numeric<AccountRow>("revenue_12m", "Value (12m)", money,
                                    { width: 165, flex: 0,
                                      context: { minGridWidth: 560 } }),
                numeric<AccountRow>("open_decisions", "Needs you", (n) => String(n), {
                  width: 135, flex: 0, context: { minGridWidth: 900 },
                  valueFormatter: (p) =>
                    Number(p.value) > 0 ? `${p.value} open` : "nothing",
                }),
                {
                  // Whose account this is. The server has always sent
                  // `assigned_user_id` and nothing rendered it, so no screen
                  // could answer the first question a manager asks — on a
                  // landing page called "Team focus". Last column and the first
                  // to drop on a narrow screen: it is context, not the number
                  // somebody came for.
                  field: "assigned_to", headerName: "Covered by", width: 150, flex: 0,
                  context: { minGridWidth: 1040 },
                  filter: "agTextColumnFilter",
                  valueFormatter: (p) => (p.value ? String(p.value) : "unassigned"),
                },
              ]}
            />
          </>
        )}

        {/* The whole book, below the list and named as such. These were a
            second nav item; the split was arbitrary — both are the customer
            view — but stacking them unlabelled just moved the confusion. */}
        <div className="section-h" style={{ marginTop: 26 }}>
          <Labelled tip="The two whole-book views. The list above is who; these are how the base as a whole is moving.">
            The book as a whole
          </Labelled>
        </div>
        <div className="screen-stack">
          <JourneyScreen session={session} onNavigate={onNavigate} />
          <MigrationMatrix session={session} months={3} onNavigate={onNavigate} />
        </div>
      </div>
    );
  }
  const decs = all.filter((d) => d.subject_entity_id === customerId);
  const account = accounts?.find((a) => a.customer_id === customerId);
  const name = decs[0]?.subject_label || account?.name || customerId;
  return (
    <div>
      <Button variant="text" size="small" onClick={() => setCustomerId(null)} style={{ marginBottom: 10 }}>
        ← All accounts
      </Button>
      <div className="dp-head">
        <h1 style={{ marginBottom: 2 }}>{name}</h1>
        <EntitySource origin={account?.origin} show={Boolean(account?.sources_differ)} />
        <p>Trading facts and what we read from them.</p>
      </div>
      {/* How this account has behaved over time. Every role gets this: the
          server omits the margin field for a salesperson rather than blanking
          it, so revenue and order cadence still land. It leads because
          "what has this account been doing" is the question somebody opening
          an account page has, before "which items are eroding". */}
      <CustomerHealthTimeline session={session} customerId={customerId} />

      {/* Which items are driving this account's margin. Cost/margin throughout,
          so it is shown only to the roles allowed to see economics — a
          salesperson gets the decisions below and nothing from this surface. */}
      {abilityFor(session).can("read", "economics") && (
        <div style={{ marginTop: 18 }}>
          <CustomerCommercial
            session={session}
            customerId={customerId}
            onOpenItem={onOpenItem}
          />
        </div>
      )}

      <div className="section-h" style={{ marginTop: 20 }}>Open decisions</div>
      {decs.length === 0 ? (
        <div className="dp-empty">
          Nothing is flagged on this account right now. That is a fact about the data, not a
          judgement about the relationship.
        </div>
      ) : (
      <>
      <div className="dp-count">
        {decs.length} open {decs.length === 1 ? "decision" : "decisions"} on this account
        {decs.length > 1 && " — the same account is flagged for more than one reason"}
      </div>
      <div className="dp-cards">
        {/* The card this was extracted from. It only ever rendered the
            interpretation, so a state-derived decision on this account showed a
            blank body — the impact and the rationale it does carry were not in
            the branch. Sharing the card fixed that here as a side effect. */}
        {decs.map((d) => (
          <DecisionCard key={d.decision_id} d={d} onOpen={onOpen} />
        ))}
      </div>
      </>
      )}
    </div>
  );
}

// ── data & AI states reference ───────────────────────────────────────────────
function StatesScreen() {
  const items = [
    ["AI unavailable", "The reasoning service is down. Facts, history and evidence are read straight from your systems and shown; only the reading is missing. You can still act."],
    ["Recommendation withheld", "Evidence is insufficient or the situation should not be acted on blindly. The movement is shown; a judgement is withheld rather than manufactured."],
    ["Interpretation degraded", "The model responded but failed validation (a number it could not support). The deterministic signal is shown instead — never a hedged guess."],
    ["Restricted data", "Cost and margin are absent for a salesperson — removed before they reach the interpretation, the API and this screen."],
  ];
  return (
    <div>
      <div className="dp-head">
        <h1>Data &amp; AI states</h1>
        <p>How the product behaves when it does not know — a designed state, never a spinner in place of an answer.</p>
      </div>
      <Stack spacing={2}>
        {items.map(([h, b]) => (
          <Paper variant="outlined" sx={{ p: 2 }} key={h}>
            <Typography variant="overline" color="text.secondary" sx={{ display: "block" }}>
              {h}
            </Typography>
            <Typography variant="body2">{b}</Typography>
          </Paper>
        ))}
      </Stack>
    </div>
  );
}

function groupEvidence(evi: { source_system?: string; record_type?: string }[]) {
  const m = new Map<string, number>();
  evi.forEach((e) => {
    const k = `${e.source_system || "zoho"} · ${e.record_type || "record"}`;
    m.set(k, (m.get(k) || 0) + 1);
  });
  return [...m.entries()].map(([system, n]) => ({ system, count: `${n} record${n === 1 ? "" : "s"}` }));
}

/** The way out for an account holding a password somebody else issued.
 *
 * Not a nag screen. `authz.current_principal` refuses a flagged account every
 * path but the change itself, so the shell behind this would be a screen where
 * every panel 403s. The seed sets the flag and the README publishes the password
 * it sets, which is why this exists at all: for a while the flag was read only by
 * the login response and a label on the admin grid, and `change-me-now` stayed
 * live on every seeded account indefinitely.
 *
 * Sign out is offered because the alternative — a screen with one action and no
 * exit — traps somebody who signed in as the wrong account.
 */
function ForcedPasswordChange({
  session, onChanged, onSignOut,
}: {
  session: PlatformSession;
  onChanged: (token: string) => void;
  onSignOut: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await papi.changeOwnPassword(session.token, current, next);
      onChanged(r.token);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box sx={{ maxWidth: 460, mx: "auto", mt: 8, px: 2 }}>
      <Paper sx={{ p: 3 }}>
        <SectionHeader
          title="Choose a password"
          sub={`This account still uses the password it was issued, ${session.name}. Set your own before going on.`}
          level="section"
        />
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <form onSubmit={submit}>
          <Stack spacing={2}>
            <TextField
              label="Current password" type="password" autoComplete="current-password"
              value={current} onChange={(e) => setCurrent(e.target.value)} required fullWidth
            />
            <TextField
              label="New password" type="password" autoComplete="new-password"
              value={next} onChange={(e) => setNext(e.target.value)} required fullWidth
            />
            <Stack direction="row" spacing={1} sx={{ justifyContent: "space-between" }}>
              <Button type="button" variant="text" onClick={onSignOut} disabled={busy}>
                Sign out
              </Button>
              <Button type="submit" variant="contained" disabled={busy || !current || !next}>
                {busy ? "Saving…" : "Set password"}
              </Button>
            </Stack>
          </Stack>
        </form>
      </Paper>
    </Box>
  );
}
