import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DataGrid, numeric } from "./DataGrid";
import { EntityName, EntitySource } from "./EntityName";
import { CompanyFilter, useCompanyFilter } from "./CompanyFilter";
import { EmptyState, ErrorState, FilterChip, HumanLog, InlineLink, LoadingState, PanelMark, SectionHeader, StatusChip, TOUCH } from "./kit";
import { formatDate } from "../when";
import {
  clearPlatformSession,
  isAuthError,
  loadPlatformSession,
  onSessionChangedElsewhere,
  papi,
  savePlatformSession,
  setAuthLossHandler,
} from "./api";
import type { Account, DecisionDetail, DecisionSummary, DecisionTrace, PlatformSession, SignupOffer, StatusFilter } from "./types";
import { aiState, factLabel, factValue, isPrimaryFact, stateFieldLabel, ROLE_LABEL } from "./format";
import { ActionsPanel, Bp, Conf, DecisionCard, ImpactPanel, Interpretation, Labelled,
         Pri, RankingPanel, Tip, WhyPanel, typeLabel } from "./ui";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
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
import Card from "@mui/material/Card";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  LEGACY_ACCOUNTS, PATH, PATTERN, pathFor, screenAt, vizPath, type Screen,
} from "./route";
import AppShell from "./AppShell";
import CommandPalette from "./CommandPalette";
import TodayScreen from "./today/TodayScreen";
import DestinationLayout from "./DestinationLayout";
import EvidenceLibrary from "./EvidenceLibrary";
import { MONEY_TABS, SETUP_TABS } from "./destinations";
import { SetupChecklist } from "./SetupChecklist";
import { TrialNotice } from "./TrialNotice";
import { SignInCard } from "../SignInCard";
import { SignUpCard } from "../SignUpCard";
import { Landing } from "../landing/Landing";
import { abilityFor } from "./ability";
import { Seg } from "./viz/Seg";
import { money } from "../money";
// `tokens.mark` — the square on a panel mark, which has to stay the size of
// the one the interpretation panel draws in ui.tsx.
import { tokens } from "../theme";
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
const QuoteWorkspace = lazy(() => import("../QuoteWorkspace"));
const ApprovalsScreen = lazy(() =>
  import("./AdminScreens").then((m) => ({ default: m.ApprovalsScreen })));
const SettingsScreen = lazy(() =>
  import("./AdminScreens").then((m) => ({ default: m.SettingsScreen })));
const IdentityScreen = lazy(() =>
  import("./IdentityScreen").then((m) => ({ default: m.IdentityScreen })));
const ObservabilityDashboard = lazy(() =>
  import("./ObservabilityDashboard").then((m) => ({ default: m.ObservabilityDashboard })));
const TrustScreen = lazy(() =>
  import("./TrustScreen").then((m) => ({ default: m.TrustScreen })));
const AttributionScreen = lazy(() =>
  import("./AttributionScreen").then((m) => ({ default: m.AttributionScreen })));
const RetrospectiveScreen = lazy(() =>
  import("./RetrospectiveScreen").then((m) => ({ default: m.RetrospectiveScreen })));
const MonetizationScreen = lazy(() =>
  import("./MonetizationScreen").then((m) => ({ default: m.MonetizationScreen })));
const DataScreen = lazy(() =>
  import("./DataScreen").then((m) => ({ default: m.DataScreen })));
const CatalogScreen = lazy(() =>
  import("./CatalogScreen").then((m) => ({ default: m.CatalogScreen })));
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
const UnrecordedQuotesScreen = lazy(() =>
  import("./UnrecordedQuotes").then((m) => ({ default: m.UnrecordedQuotesScreen })));
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
const CashCycleScreen = lazy(() =>
  import("./viz/CashCycle").then((m) => ({ default: m.CashCycleScreen })));
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

// ── sign in ──────────────────────────────────────────────────────────────────
/** Which door a visitor arriving from outside the application asked for.
 *
 * The marketing pages at `/erp/{system}` are static documents that ship no
 * bundle, so their only way to ask for the sign-in card is the address they
 * link to — `/#signin`. Nothing read it: the door was always "landing", so a
 * buyer who pressed "Sign in" on an ERP page was shown the marketing page and
 * had to press the same button a second time.
 *
 * Read once, at mount, and only consulted while signed out. A signed-in
 * visitor never reaches the door at all, and `#signin` is not a route
 * (`route.screenAt` answers "home" for it), so nothing else competes for the
 * fragment. "signup" falls through to the sign-in card where a deployment does
 * not offer sign-ups, which is the same behaviour the buttons already have.
 */
function doorFromHash(): "landing" | "signin" | "signup" {
  if (typeof window === "undefined") return "landing";
  const asked = window.location.hash.replace(/^#\/?/, "");
  return asked === "signin" || asked === "signup" ? asked : "landing";
}

/** The platform's door. The card itself is `src/SignInCard.tsx`, shared with
 *  the Quote Builder — the two forms had drifted, and the copy that drifted was
 *  the one still telling people any password worked. */
function SignIn({ onIn, notice, onSignUp }: {
  onIn: (s: PlatformSession) => void;
  notice?: string | null;
  /** Passed straight through; the sentence is the card's, for the reason its
   *  own prop docstring gives. Absent where sign-up is off. */
  onSignUp?: () => void;
}) {
  return (
    <SignInCard
      onSignUp={onSignUp}
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

/** The other door: sign up for an organization that does not exist yet.
 *
 *  Reached only where the deployment offers it — see `useSignupOffer`. The
 *  response is the login response, so this hands the session on through exactly
 *  the same `onIn` the sign-in card uses; a second way to become signed in is a
 *  second place to forget the currency. */
function SignUp({ onIn, onSignIn, offer, defaultPlan }: {
  onIn: (s: PlatformSession) => void;
  onSignIn: () => void;
  offer: SignupOffer | null;
  defaultPlan?: string;
}) {
  return (
    <SignUpCard
      onSignIn={onSignIn}
      offer={offer}
      defaultPlan={defaultPlan}
      onSubmit={async (d) => {
        const r = await papi.signUp(d);
        // Same field-by-field copy as sign-in, for the reason given there.
        onIn({ token: r.token, role: r.role, name: r.name, email: r.email,
               user_id: r.user_id,
               organization_id: r.organization_id, currency: r.currency,
               timezone: r.timezone,
               must_change_password: r.must_change_password });
      }}
    />
  );
}

/** What this deployment offers a stranger: sign-up or not, and on what terms.
 *
 *  Asked once, before the door is drawn, and answered `null` on any failure —
 *  an older backend has no such endpoint and 404s, and the right reading of
 *  "this deployment did not answer" is that it does not offer sign-up, not that
 *  it does. Never fetched while signed in: the answer changes nothing then.
 *
 *  The whole object rather than a boolean, because the sign-up card needs the
 *  plan ladder and the trial length and neither should be written a second time
 *  in the browser. `signupOffered` below is still the boolean the doors key off. */
function useSignupOffer(signedOut: boolean): SignupOffer | null {
  const [offer, setOffer] = useState<SignupOffer | null>(null);
  useEffect(() => {
    if (!signedOut) return;
    let live = true;
    papi.signupOffer()
      .then((o) => { if (live) setOffer(o.enabled ? o : null); })
      .catch(() => { if (live) setOffer(null); });
    return () => { live = false; };
  }, [signedOut]);
  return offer;
}

/** Whether this deployment has a demonstration workspace, asked the same way
 *  and answered `false` the same way on any failure.
 *
 *  This said the two hooks were near-identical and that sharing them would be
 *  indirection over four lines of state. Half of that is now out of date:
 *  `useSignupOffer` returns the offer itself, because the sign-up form needs
 *  the plan list out of it, while this one is only ever a yes or no. They are
 *  two questions with two answer shapes and the earlier reasoning has stopped
 *  applying — noted rather than left, because a stale justification is worse
 *  than none. */
function useDemoOffer(signedOut: boolean): boolean {
  const [offered, setOffered] = useState(false);
  useEffect(() => {
    if (!signedOut) return;
    let live = true;
    papi.demoOffer()
      .then((o) => { if (live) setOffered(!!o.enabled); })
      .catch(() => { if (live) setOffered(false); });
    return () => { live = false; };
  }, [signedOut]);
  return offered;
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
  // Held so switching workspace can drop every cached row belonging to the one
  // being left. Query keys carry the organization id, so most would re-key on
  // their own; clearing is the version that does not depend on every future
  // key remembering to.
  const queryClient = useQueryClient();
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const [session, setSession] = useState<PlatformSession | null>(loadPlatformSession());
  //: The session as it is *now*, for the storage listener below. That listener
  //: is registered once and would otherwise compare against the session that
  //: existed when it was registered.
  const sessionRef = useRef(session);
  useEffect(() => { sessionRef.current = session; }, [session]);
  // Signed out, there are three doors: the public landing page (the default),
  // the sign-in card one click behind it, and — where the deployment offers it
  // — the sign-up card. State rather than a route on purpose: a person
  // deep-linked to any screen should land on the landing page, not on a bare
  // form, and the URL they wanted is preserved for after sign-in.
  const [door, setDoor] = useState<"landing" | "signin" | "signup">(doorFromHash);
  // There used to be a `wantedPlan` here — which pricing panel the visitor came
  // through, so the sign-up form could open on the plan they had been reading
  // about. The landing page names no plan any more (it asks for a demo and
  // nothing else), so nothing ever set it, and a preselection nobody selects is
  // a prop that only looks like a feature. `SignUpCard` takes `defaultPlan`
  // optionally and is unchanged; if a panel that knows the answer ever returns,
  // this is the wire it reconnects.
  // The URL is the screen, so Back, reload and shareable links all work. React
  // Router owns the matching; `screen` is only what the nav highlights, which is
  // a different question — a decision detail has no nav item of its own.
  const location = useLocation();
  const navigate = useNavigate();
  const screen: Screen = screenAt(location.pathname);
  // The destination a signed-out visitor actually asked for, held across the
  // sign-in detour. A shared link to an account, or the screen a user was on
  // when their token was retired, is otherwise lost: sign-in navigated to home
  // unconditionally, under a comment claiming the deep link was preserved.
  // Seeded from the first render because that is when the requested URL is
  // still on screen.
  const intended = useRef<string | null>(
    screenAt(location.pathname) === "home" ? null : location.pathname + location.search);
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

  //: Whether this identity is PIE staff, answered by the server rather than
  //: inferred from a role. There is no tenant role that could mean this — an
  //: owner who could grant themselves the pricing console would defeat it — so
  //: the shell asks `/monetization/access`, which always answers 200 and tells
  //: a tenant `false`. Defaults to false, so a failed probe hides the door
  //: rather than offering one that 403s.
  const [isOperator, setIsOperator] = useState(false);

  //: The intent search. Held by the shell rather than by a screen because ⌘K
  //: has to answer from wherever somebody is standing, which is the whole of
  //: what it is for.
  const [commandsOpen, setCommandsOpen] = useState(false);

  // ⌘K, and Ctrl-K where there is no ⌘. Bound on the window rather than on the
  // button, because the point of the shortcut is that it works while the reader
  // is looking at a screen and not at the bar above it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandsOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  /** Drop this browser's view of the session. Local only — see `signOut`. */
  const forgetSession = useCallback(() => {
    clearPlatformSession();
    setSession(null);
    setSummaries(null);
    setDetails({});
    setError(null);
    // Back to the public front, not to a bare form: signing out is leaving.
    setDoor("landing");
    // And leaving takes the address with it. The door changed here and the URL
    // did not, so signing out of `/#/account/CUST-123` drew the public landing
    // page under an address bar still naming that customer — a screen the
    // browser is no longer showing and whoever is holding it may no longer be
    // allowed to read. `replace`, so Back does not lead to the screen just
    // left.
    //
    // `intended` goes with it, because it is the same fact stored twice.
    // It is seeded from the arrival URL, and a session restored from storage
    // never reaches `signIn` to spend it — so it outlived the session that set
    // it, and the next person to sign in on this tab was sent to the previous
    // person's account screen. Clearing the address and leaving the ref would
    // have fixed the half that is visible and kept the half that acts.
    //
    // Anything that does want a destination carried across the sign-in detour
    // records it *after* this call. `handleAuthLoss` is the one that does, and
    // says so.
    intended.current = null;
    navigate(PATH.home, { replace: true });
  }, [navigate]);

  const signOut = useCallback(() => {
    // Tell the server first, and do not wait for it or let it fail the sign-out:
    // the local half must happen whether or not the network does, or a person on
    // a flaky connection is left signed in by an error they cannot act on. The
    // server call is what actually ends the session — clearing storage alone was
    // the old behaviour, and it ended nothing.
    void papi.logout().catch(() => { /* already invalid, or offline — leave anyway */ });
    forgetSession();
  }, [forgetSession]);

  /** A dead session must return the user to sign-in, not strand them inside
   *  application chrome that looks live but can load nothing. */
  const handleAuthLoss = useCallback(() => {
    // `forgetSession`, not `signOut`: the session is already gone, and posting
    // to /auth/logout with a dead credential answers 401, which is an auth loss,
    // which calls this again. Local cleanup only.
    forgetSession();
    // Where they were, so signing back in returns them there rather than to
    // home. Recorded *after* the line above, which clears this ref along with
    // the address bar — an expiry is not a departure, and this is the one
    // caller that has somewhere to come back to. `location` is still this
    // render's, so it reads the screen they were on rather than the home the
    // navigate is on its way to.
    intended.current = location.pathname + location.search;
    // The toast lives inside the signed-in shell, which is about to unmount —
    // the message has to survive onto the sign-in screen to be seen at all.
    setNotice("Your session expired. Please sign in again.");
  }, [forgetSession, location.pathname, location.search]);

  /** One session per browser, and now every tab agrees which one it is.
   *
   *  Without this the tabs drifted: a sign-out in one left the others drawing a
   *  live-looking shell over a dead session, and a sign-in as somebody else left
   *  the first tab showing the previous person's name and role until something
   *  happened to reload it. */
  useEffect(() => onSessionChangedElsewhere((next) => {
    if (!next) {
      forgetSession();
      // `setNotice` is right for this one alone: it ends with the shell
      // unmounting, and the sign-in card that replaces it is what renders it.
      setNotice("You signed out in another tab.");
      return;
    }
    // Read the current session through the ref rather than a `setSession`
    // updater's `prev`. The toast below is a side effect, and an updater is a
    // place React is entitled to run twice — it does so in development under
    // StrictMode, which `main.tsx` enables — while the effect itself
    // is registered once and closes over the session it saw then, which is why
    // the updater was reached for in the first place.
    const prev = sessionRef.current;
    if (prev && prev.user_id === next.user_id) return;   // same person; nothing to do
    // A different account signed in elsewhere. Adopt it and drop everything
    // loaded for the previous one rather than showing one person's data under
    // another's name.
    setSummaries(null);
    setDetails({});
    setError(null);
    setSession(next);
    // A toast, because this tab keeps its shell: `notice` has no renderer while
    // a session exists, so this sentence was being set and never seen — the tab
    // silently became somebody else.
    flash(`Signed in as ${next.name} in another tab.`);
  }), [forgetSession, flash]);

  // Registered once for the whole app. Before this, a 401 was recognised only
  // where a screen remembered to ask `isAuthError` — three call sites, all on
  // the decision queue — so every other screen showed "this did not load"
  // inside a shell that still looked signed in.
  useEffect(() => {
    setAuthLossHandler(handleAuthLoss);
    return () => setAuthLossHandler(null);
  }, [handleAuthLoss]);

  /** Confirm the stored profile against the cookie, once, on boot.
   *
   *  What is in `localStorage` is a cache so the shell can draw immediately; the
   *  cookie is the credential and the server is the authority on what it means.
   *  Asking closes the gap between them — a session revoked from another device,
   *  or a role changed by an owner, is reflected on load instead of at whichever
   *  request happens to fail first. A 401 here routes through the normal
   *  auth-loss path, so a stale profile cannot leave a signed-out person looking
   *  signed in. */
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current || !session) return;
    booted.current = true;
    void papi.me()
      .then((me) => {
        const next: PlatformSession = { ...session, ...me, token: "" };
        savePlatformSession(next);
        setSession(next);
      })
      // Swallowed on purpose: a 401 has already been turned into an auth loss by
      // the transport, and anything else (offline, a 502 from the proxy) is not
      // a reason to throw someone out of a shell that is otherwise working.
      .catch(() => { /* handled by the auth-loss path, or transient */ });
  }, [session]);

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const list = await papi.listDecisions(session.token, { include_detail: true });
      setSummaries(list);
      const details = list.reduce((acc, d) => {
        acc[d.decision_id] = d as unknown as DecisionDetail;
        return acc;
      }, {} as Record<string, DecisionDetail>);
      setDetails(details);
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

  // Asked once per session rather than per screen: whether somebody is PIE
  // staff cannot change while they are signed in, and re-asking on every
  // navigation would put a request on every click for an answer that never
  // moves. A failure leaves it false — the door stays hidden, which is the
  // safe direction for a surface that carries PIE's own cost and take rate.
  useEffect(() => {
    if (!session) { setIsOperator(false); return; }
    let cancelled = false;
    papi
      .monetizationAccess(session.token)
      .then((r) => !cancelled && setIsOperator(r.operator))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [session]);

  const signIn = (s: PlatformSession) => {
    setNotice(null);
    savePlatformSession(s);
    setSession(s);
    // Back to what they asked for, if they asked for anything. `intended` is
    // only ever set from a path `screenAt` recognises as a real screen, so an
    // unknown or landing-page path still goes home — which is where the
    // catch-all route would have put them anyway.
    //
    // `replace`, so Back does not return to the sign-in card they have just
    // left. Cleared afterwards: a second sign-in in the same tab, from home,
    // must not be sent to a destination the previous session wanted.
    const wanted = intended.current;
    intended.current = null;
    navigate(wanted ?? PATH.home, { replace: true });
  };
  /** Swap in a token the server has just issued, keeping the rest of the
   *  session. Changing a password retires every token minted before it, so a
   *  screen that performs one and keeps the old token has signed the user out
   *  without either of them knowing. */
  const adoptToken = useCallback((token: string) => {
    setSession((prev) => {
      if (!prev) return prev;
      const next = { ...prev, token };
      savePlatformSession(next);
      return next;
    });
  }, []);

  /** Act for another workspace, and rebuild the shell around it.
   *
   *  A full reload of the session rather than a patch of the organization id,
   *  because almost everything on screen is scoped to the workspace: the role
   *  can differ, the currency and the business timezone can differ, and every
   *  cached query holds another customer's rows. So the new session envelope
   *  replaces the old one and the caches are dropped — the same thing signing
   *  in as somebody else does, which is the closest analogy to what this is.
   *
   *  The server is the authority on whether the switch is allowed; a refusal
   *  leaves the current session exactly as it was.
   */
  const switchOrganization = useCallback(async (organizationId: string) => {
    if (!session || organizationId === session.organization_id) return;
    try {
      const r = await papi.switchOrganization(session.token, organizationId);
      const target = (session.organizations || []).find(
        (o) => o.organization_id === organizationId);
      const next: PlatformSession = {
        ...session,
        token: r.token,
        organization_id: r.organization_id,
        organization_name: r.name || target?.name || "",
        // From the server's answer, not from the menu row that was clicked: the
        // list in a stored session can be stale, and the role decides what the
        // nav offers.
        role: r.role as PlatformSession["role"],
      };
      savePlatformSession(next);
      setSession(next);
      // Everything cached belongs to the workspace that was just left.
      queryClient.clear();
      setSummaries(null);
      setDetails({});
      setError(null);
      navigate(PATH.home, { replace: true });
    } catch {
      // Deliberately quiet beyond the toast: a refused switch means the
      // membership is gone or was never there, and the honest response is to
      // stay exactly where the person already is.
      //
      // `flash`, not `setNotice`. The session survives a refused switch, so the
      // shell stays mounted and the sign-in card — the only thing that renders
      // `notice` — is never reached. The sentence was being set and never
      // shown: the switch failed in silence, and the stale notice could then
      // surface at the next sign-out, describing something that happened long
      // before.
      flash("That workspace could not be opened. It may no longer be yours.");
    }
  }, [session, navigate, queryClient, flash]);

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
  // the whole queue VIEWED the moment the queue rendered. Opening one is the
  // only thing that means a person read this one.
  //
  // Its own callback, separate from the navigation, because a decision card's
  // Open is an `<a href>` now (§9) and the browser does the moving. Handing the
  // card `openDetail` instead would `navigate()` on a ctrl-click too, taking
  // over the tab the reader asked to keep — the exact thing the anchor is for.
  //
  // Sent only while the decision is still OPEN. That is exactly when it carries
  // information — the server's VIEW handler moves OPEN → VIEWED and leaves every
  // other status alone — so it fires once per open period, and again after a
  // REOPEN, instead of appending a trail entry on every visit. Fire-and-forget:
  // reading a card must never fail because recording the read did.
  const recordView = useCallback(
    (id: string) => {
      if (!session) return;
      // Unknown status — the list has not loaded — is not OPEN for this
      // purpose. Recording a view we cannot place in the lifecycle would be a
      // guess, and the whole value of this event is that it is not one.
      if (summaries?.find((s) => s.decision_id === id)?.status !== "OPEN") return;
      papi.act(session.token, id, { action: "VIEW" })
        .then(() => refresh(id))
        .catch(() => undefined);
    },
    [session, summaries, refresh]);

  /** Where a decision lives. One arrow rather than an inline one per screen, so
   *  the destination is written once and the cards are links to it. */
  const detailPath = useCallback((id: string) => pathFor("detail", id), []);

  /** Opening a decision from something that is *not* a link: the list's grid
   *  rows, which AG Grid draws itself — §9's one exception, and the reason this
   *  still exists beside `detailPath`. */
  const openDetail = useCallback(
    (id: string) => {
      navigate(detailPath(id));
      recordView(id);
    },
    [navigate, detailPath, recordView]);

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

  // Before the early returns below: hooks run in the same order every render.
  const signupOffer = useSignupOffer(!session);
  const signupOffered = signupOffer !== null;
  const demoOffered = useDemoOffer(!session);

  if (!session) {
    // The landing page is the public front; the two cards are one click behind
    // it. An expired session skips the landing and goes straight to sign-in —
    // that person was already inside, and what they need is the door, with the
    // notice saying why they are looking at it again.
    if (door === "landing" && !notice) {
      return (
        <Landing
          onEnter={() => setDoor("signin")}
          // No `onSignUp`. The landing page asks for one thing — a demo — and
          // does not market the trial or name a plan, so it no longer needs a
          // sign-up handler. Self-serve sign-up is untouched as a path:
          // `signupOffered` still governs it and `SignInCard` still offers
          // "Create your organization" one click behind this page.
          //
          // Absent unless the deployment names a demonstration workspace,
          // because a door that always 404s is worse than no door. Entering
          // signs the visitor in on a read-only session, so it goes through
          // `signIn` exactly as the other doors do.
          onDemo={demoOffered
            ? () => { void papi.enterDemo().then(signIn).catch(() => {}); }
            : undefined}
        />
      );
    }
    const back = (
      <Button
        onClick={() => { setDoor("landing"); setNotice(null); }}
        sx={{ position: "fixed", top: 14, left: 14, color: "text.secondary" }}
      >
        ← Back
      </Button>
    );
    if (door === "signup" && signupOffered) {
      return (
        <>
          {back}
          <SignUp
            onIn={signIn}
            onSignIn={() => setDoor("signin")}
            offer={signupOffer}
          />
        </>
      );
    }
    return (
      <>
        {back}
        <SignIn
          onIn={signIn}
          notice={notice}
          onSignUp={signupOffered ? () => setDoor("signup") : undefined}
        />
      </>
    );
  }

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

  const roleShort = session.role === "SALESPERSON" ? "Salesperson" : session.role === "SALES_MANAGER" ? "Manager" : "Owner";
  // What a role may open, from one table rather than a ternary per item.
  // `ability.ts` says plainly what this is and is not: the server decides what
  // a role may *read*; this decides what the interface bothers to show, so a
  // tab that would always 403 is simply absent.
  //
  // The nav itself is no longer scoped. All five destinations are readable by
  // every role — `payments`, `data`, `quotes` and the account list carry no
  // cost — so the scoping moved down to the tabs inside Money and Setup and to
  // the Evidence index, each next to the endpoint gate it mirrors.
  const ability = abilityFor(session);

  return (
    <AppShell
      current={screen}
      // Today's badge is what is still open in this morning's queue — the
      // decisions and the approvals waiting on this person. Open, never total:
      // a badge that cannot reach zero stops being read.
      counts={{ today: openDecisions.length + pendingApprovals }}
      userName={session.name}
      roleLabel={roleShort}
      organizationName={session.organization_name}
      organizations={session.organizations}
      currentOrganizationId={session.organization_id}
      onSwitchOrganization={switchOrganization}
      onOpenCommands={() => setCommandsOpen(true)}
      onSignOut={signOut}
    >
      <CommandPalette
        open={commandsOpen}
        onClose={() => setCommandsOpen(false)}
        ability={ability}
        isOperator={isOperator}
      />
      <div>
        {/* In the shell rather than on one screen: a licence about to expire is
            true wherever the reader happens to be, and the queue it takes away
            is reached from everywhere. It is silent until the last stretch and
            silent for a salesperson — see TrialNotice. */}
        {/* Every screen, not just the ones with numbers on. A visitor who does
            not know these figures are invented is being misled by a product
            whose whole argument is that its figures are real — and this one
            says so beside a queue, a margin and a cash-cycle chart alike.
            Never dismissible for the same reason. */}
        {session.is_demo && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <AlertTitle>You are in the demonstration workspace</AlertTitle>
            Every customer, item and figure here is made up. Nothing you do is
            saved — the server refuses writes on this session. Sign up for an
            account to connect your own books.
          </Alert>
        )}
        <TrialNotice session={session} />
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
            {/* ── TODAY: the triage console ──
                This was the Commercial Storyboard, a briefing whose eight tiles
                each led into a list, into a card, into a detail page, and then
                an action. The briefing was not wrong about what belongs on the
                first screen — what changed, and what to do about it — it was
                wrong about what a first screen *is*: a place to read, when the
                only thing anybody opens this product to do is work through what
                needs them.

                So the queue is the screen, and the briefing's own material —
                what moved, and the patterns behind it — is one link away in the
                Evidence library, reached from the decision that makes somebody
                want it. */}
            <Route
              path={PATH.home}
              element={
                <>
                  {/* Above the queue, and only until the required steps are
                      done. A tenant with no connection has no queue and no
                      morning read, so every panel below it is a correct empty
                      state — and a stack of correct empty states does not tell
                      a new owner that the fix is four minutes of setup. It
                      removes itself; there is no dismiss and no stored flag. */}
                  <SetupChecklist session={session} />
                  <TodayScreen
                    session={session}
                    decisions={openDecisions
                      .map((s) => details[s.decision_id])
                      .filter((d): d is DecisionDetail => Boolean(d))}
                    loading={loading}
                    error={null}
                    onReload={load}
                    onDecisionAction={(id, kind) => {
                      // An action that must say why opens the modal that asks;
                      // the rest are recorded where they were pressed. Same
                      // vocabulary either way — `ACTION_META` is the one table.
                      if (ACTION_META[kind]?.needsNote) setModal({ id, kind });
                      else void doAction(id, kind, "");
                    }}
                    onUndoDecision={undoAction}
                    flash={flash}
                  />
                </>
              }
            />

            {/* ── THE MORNING READ ──
                The briefing this screen used to open with. An Evidence card
                now, at an address of its own. */}
            <Route path={PATH.morningRead}
                   element={<MorningReadScreen session={session} onNavigate={goViz} />} />

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
                    openPath={detailPath}
                    onOpened={recordView}
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
            <Route path={PATH.lostRevenue} element={<LostRevenueScreen session={session} />} />
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
                  <MigrationMatrix session={session} months={3} />
                </div>
              }
            />
            <Route path={PATH.simulate} element={<SimulatorScreen session={session} />} />
            <Route path={PATH.landscape} element={<LandscapeScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.composition} element={<CompositionScreen session={session} />} />
            <Route path={PATH.cadence} element={<CadenceScreen session={session} onNavigate={goViz} />} />
            {/* ── MONEY ──
                One position across three companies, as tabs over the screens
                that already answered each half. A layout route rather than a
                new screen: each tab keeps its own address, so a link anybody
                saved still works and Back still goes back. */}
            <Route element={
              <DestinationLayout
                title="Money"
                sub="All three companies as one position. Anything here that needs a person today is already in your queue."
                tabs={MONEY_TABS} current={screen} ability={ability}
              />
            }>
              <Route path={PATH.payments} element={<PaymentsScreen session={session} onNavigate={goViz} />} />
              <Route path={PATH.payables} element={<PayablesScreen session={session} onNavigate={goViz} />} />
              <Route path={PATH.orderToCash} element={<OrderToCashScreen session={session} />} />
              <Route path={PATH.cashCycle} element={<CashCycleScreen session={session} />} />
              <Route path={PATH.statutory} element={<StatutoryScreen session={session} />} />
            </Route>
            <Route path={PATH.stock} element={<StockScreen session={session} />} />
            <Route path={PATH.gmroi} element={<GmroiScreen session={session} />} />
            <Route path={PATH.supply} element={<SupplyScreen session={session} />} />
            <Route path={PATH.bonds} element={<BondsScreen session={session} />} />
            <Route path={PATH.mix} element={<MixScreen session={session} onNavigate={goViz} />} />
            <Route path={PATH.dependency} element={<DependencyScreen session={session} />} />
            <Route path={PATH.targets} element={<TargetWallScreen session={session} />} />
            <Route path={PATH.quoteOutcomes} element={<QuoteOutcomesScreen session={session} />} />
            <Route path={PATH.unrecordedQuotes} element={<UnrecordedQuotesScreen session={session} />} />

            {/* ── WHAT PIE CHANGED ──
                The value ledger, and what it could not measure. Routed for
                every role rather than gated here: the screen itself says why
                a salesperson cannot read it, which is a closed door rather
                than a broken link for anyone who follows one. */}
            <Route path={PATH.attribution} element={<AttributionScreen session={session} />} />

            {/* The other half of that pair, and the earlier one: what the book
                already held when it arrived. Same role gate — a count of margin
                findings is a count of products whose margin fell. */}
            <Route path={PATH.retrospective} element={<RetrospectiveScreen session={session} />} />

            {/* ── QUOTES ──
                The workspace — every draft in the organization — and, under
                an id, the Quote Builder open on one of them. The builder used
                to be a second application behind a button here: clicking
                through replaced the whole shell, asked for a second sign-in,
                and showed a different name in a different brand bar. It is a
                screen like any other now, on this session, and it opens on a
                draft the whole desk can see rather than on the one copy this
                browser kept. */}
            <Route path={PATH.quotes} element={<QuoteWorkspace session={session} />} />
            <Route path={PATTERN.quote} element={<QuoteBuilder session={session} />} />

            {/* ── SETUP ──
                Where the figures come from, and the policy the rest of the
                product obeys. Nine screens, four tabs and an overflow — see
                `destinations.ts` for why the other five are not tabs.

                Every one of these is routed unconditionally, tab or not: a
                salesperson who follows a link to a manager's screen should land
                on it and read the server's own refusal, rather than being
                bounced home by a client-side guess about what they may see. */}
            <Route element={
              <DestinationLayout
                title="Setup"
                sub="Where the figures come from, and the policy the rest of the product obeys."
                tabs={SETUP_TABS} current={screen} ability={ability} isOperator={isOperator}
              />
            }>
              <Route path={PATH.data} element={<DataScreen session={session} onSynced={load} />} />
              <Route path={PATH.decodedCatalog} element={<CatalogScreen session={session} />} />
              <Route path={PATH.catalogue} element={<CatalogueScreen session={session} />} />
              <Route path={PATH.identity} element={<IdentityScreen token={session.token} />} />
              <Route path={PATH.observability}
                     element={<ObservabilityDashboard session={session} />} />
              <Route path={PATH.trust} element={<TrustScreen session={session} />} />
              <Route path={PATH.settings} element={
                <SettingsScreen session={session} onToken={adoptToken}
                                onSignedOutEverywhere={forgetSession} />} />
              <Route path={PATH.states} element={<StatesScreen />} />
              {/* PIE's own, and refused by every endpoint behind it for a
                  tenant. It is a Setup tab only for an operator; the route is
                  unconditional like its neighbours. */}
              <Route path={PATH.monetization} element={<MonetizationScreen session={session} />} />
            </Route>

            <Route path={PATH.approvals} element={<ApprovalsScreen session={session} />} />

            {/* ── EVIDENCE ──
                The one door to the analysis screens, indexed by the question
                each answers. The screens themselves stay where they were. */}
            <Route path={PATH.evidence} element={<EvidenceLibrary ability={ability} />} />

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

/** One label-and-value on a narrow customer card.
 *
 *  Four of these wrap into whatever width the phone gives them. A `<dl>` would
 *  be more correct semantically and wraps far worse — these are chips of fact,
 *  not a definition list somebody reads through. */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="overline" color="text.secondary"
                  sx={{ display: "block", lineHeight: 1.2 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ lineHeight: 1.3 }}>{value}</Typography>
    </Box>
  );
}

function CustomerRoute({
  session, details, openPath, onOpened, onNavigate,
}: {
  session: PlatformSession;
  details: Record<string, DecisionDetail>;
  /** Where a decision card goes, and what to record when one is opened from
   *  here. Two props because the card is a link now: the anchor carries the
   *  destination and the handler carries only the trail entry. */
  openPath: (id: string) => string;
  onOpened: (id: string) => void;
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
      openPath={openPath}
      onOpened={onOpened}
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

/** The morning read: what moved in the book, and the movement the queue's
 *  decisions were detected against.
 *
 * This was the first screen, and its own comments were already arguing with it
 * — "the briefing, demoted to what it is". A briefing is worth having and is
 * not worth being the place a working day starts, so Today is the queue now and
 * this is one of the questions in the Evidence library: *what changed, and
 * why*. It keeps the two panels that answered that and has given up the head of
 * the queue it used to carry, which is the whole of Today's screen.
 */
function MorningReadScreen({
  session, onNavigate,
}: {
  session: PlatformSession;
  onNavigate: (route: string) => void;
}) {
  // Same subject as `CashProjection`'s gate, and for the same reason: the
  // morning read carries what we owe suppliers alongside what is owed to us.
  // `supply` mirrors `require_manager_or_owner`, which is the dependency on the
  // endpoint — see ability.ts.
  const mayReadDaily = abilityFor(session).can("read", "supply");

  return (
    <div>
      <InlineLink to={PATH.evidence}>← Evidence</InlineLink>
      <SectionHeader
        title="The morning read"
        sub="What moved in the book over the period, and the movement this morning's queue was detected against."
      />

      {/* Offered only to the roles that can load it. `GET /insight/daily` is
          `require_manager_or_owner`, half of it being what we owe suppliers, so
          rendering it for a salesperson put an amber "The morning read did not
          load — Manager or owner role required" at the top of the screen.
          Omitted rather than rendered and then 403'd, for the reason
          `PaymentsScreen` gives about `CashProjection`: a panel that always
          fails teaches people the product is broken. */}
      {mayReadDaily && (
        <Suspense fallback={<LoadingState rows={2} label="Reading this morning…" />}>
          <DailyScreen session={session} />
        </Suspense>
      )}

      <Suspense fallback={<LoadingState rows={3} />}>
        <Storyboard session={session} onNavigate={onNavigate} />
      </Suspense>
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
        <Alert severity="error" sx={{ mt: 1 }}>The chain could not be loaded: {error}</Alert>
      )}
      {open && !trace && !error && <LoadingState rows={1} height={44} />}
      {open && trace?.unavailable && (
        <Alert severity="info" sx={{ mt: 1 }}>{trace.unavailable}</Alert>
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
  if (loading && !d) return <LoadingState rows={2} />;
  if (!d) {
    return (
      <EmptyState
        title="Decision not found in this view"
        reason="It may have been closed, or it may belong to somebody else's queue." />
    );
  }
  // Distinct from "not found", because it is a different fact and the wrong
  // explanation is worse than none: this decision exists and is yours, and the
  // server could not build its detail. Without this branch the card rendered
  // every panel blank, which reads as a decision with nothing behind it.
  if (d.detail_unavailable) {
    return (
      <EmptyState
        title="This decision's detail could not be loaded"
        reason="The decision is in your queue; the facts and interpretation behind it failed to build. Reload, and if it persists the server log names the decision." />
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
          {/* The fourth hand-built copy of the panel mark, and the ink twin of
              the interpretation panel's: `PanelMark` for the label, and the
              square `.facts-mark::before` used to draw, at `tokens.mark` so it
              stays the size of the accent one in ui.tsx. Ink rather than the
              kit's muted default, because that is what the class drew and the
              tint is the only thing separating "a model wrote this" from "this
              is arithmetic". */}
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1 }}>
            <Box
              aria-hidden
              sx={{
                width: tokens.mark, height: tokens.mark,
                flex: "0 0 auto", bgcolor: "text.primary",
              }}
            />
            <PanelMark sx={{ color: "text.primary" }}>
              Facts · what the data shows
            </PanelMark>
          </Stack>
          {d.facts.length === 0 ? (
            <EmptyState
              title="No facts to show"
              reason="No numeric facts are exposed at your permission level for this decision."
            />
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
  openPath,
  onOpened,
  onOpenItem,
  onNavigate,
}: {
  session: PlatformSession;
  details: Record<string, DecisionDetail>;
  customerId: string | null;
  setCustomerId: (id: string | null) => void;
  /** Passed straight to `DecisionCard` — see its props for why a card takes a
   *  destination rather than a handler. */
  openPath: (id: string) => string;
  onOpened: (id: string) => void;
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
          <EmptyState
            title={needle ? "No match" : "Nothing here yet"}
            reason={needle
              ? `No ${status === "all" ? "" : status + " "}customer matches “${q}”.`
              : status === "inactive"
                ? "No customer is marked inactive."
                : "No customers are assigned to you yet."}
          />
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
              /* On a phone this grid used to reduce to a column of names.
                 Every other column declares a `minGridWidth` above the ~350px
                 a 390px viewport gives it, so last order, twelve-month value
                 and "needs you" all dropped — and this is the screen a
                 salesperson opens standing in somebody's factory. The card
                 carries the four facts the row is read for, which is what
                 `renderNarrow` is for; `LineGrid`'s `LineCard` is the model. */
              renderNarrow={(a) => (
                <Card
                  variant="outlined"
                  role="listitem"
                  key={a.customer_id}
                  sx={{ p: 1.5 }}
                >
                  <Box
                    component="button"
                    type="button"
                    onClick={() => setCustomerId(a.customer_id)}
                    sx={{
                      ...TOUCH,
                      width: "100%", textAlign: "left", background: "none",
                      border: 0, p: 0, cursor: "pointer", color: "inherit", font: "inherit",
                    }}
                  >
                    <EntityName
                      name={a.name}
                      origin={a.origin}
                      show={Boolean(a.sources_differ)}
                      sub={(a.status || "").toUpperCase() !== "ACTIVE"
                        ? <span className="acct-flag">inactive</span>
                        : undefined}
                    />
                  </Box>
                  <Stack direction="row" useFlexGap
                         sx={{ flexWrap: "wrap", gap: 1.5, mt: 1 }}>
                    <Fact label="Last order"
                          value={a.last_order ? formatDate(String(a.last_order)) : "never ordered"} />
                    <Fact label="Value (12m)" value={money(a.revenue_12m)} />
                    <Fact label="Orders (12m)" value={String(a.orders_12m)} />
                    <Fact label="Covered by" value={a.assigned_to || "unassigned"} />
                  </Stack>
                  {Number(a.open_decisions) > 0 && (
                    <Box sx={{ mt: 1 }}>
                      <StatusChip
                        label={`${a.open_decisions} needs you`}
                        tone="warn"
                        dense
                        tip="Open decisions flagged on this account."
                      />
                    </Box>
                  )}
                </Card>
              )}
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
          <MigrationMatrix session={session} months={3} />
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
      <Stack direction="row" sx={{ alignItems: "flex-start", justifyContent: "space-between", gap: 2 }}>
        <div className="dp-head">
          <h1 style={{ marginBottom: 2 }}>{name}</h1>
          <EntitySource origin={account?.origin} show={Boolean(account?.sources_differ)} />
          <p>Trading facts and what we read from them.</p>
        </div>
        {/* The thing this screen prepares somebody to do.
          *
          * Reading an account and then quoting it used to mean leaving for
          * Quotes, starting a draft, and finding the same customer again in a
          * dialog — five presses and the name typed twice, to do the one thing
          * the page you were on is background for.
          *
          * A link, not a button (§9): it goes somewhere, so it opens in a new
          * tab like anything else, and the customer travels in the query rather
          * than in component state so the destination is the whole instruction.
          * The workspace does the creating — it already owns the company
          * chooser for an organization with more than one set of books, and a
          * second copy of that here is how two screens start disagreeing about
          * which book a quote belongs to. */}
        <Button
          component={RouterLink}
          to={`${PATH.quotes}?customer=${encodeURIComponent(customerId)}`
              + `&name=${encodeURIComponent(name)}`}
          variant="outlined"
          size="small"
          sx={{ ...TOUCH, flexShrink: 0 }}
        >
          Start a quote
        </Button>
      </Stack>
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
        <EmptyState
          title="Nothing flagged"
          reason="Nothing is flagged on this account right now. That is a fact about the data, not a judgement about the relationship."
        />
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
          <DecisionCard
            key={d.decision_id}
            d={d}
            openPath={openPath}
            onOpened={onOpened}
          />
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
