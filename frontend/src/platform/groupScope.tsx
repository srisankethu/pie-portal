/** One group selection per page, in the URL, read by every panel on it.
 *
 * **What this replaces, and why it had to be replaced.** Each panel used to own
 * a `useGroupFilter` of its own. Ten of them across eight screens, each holding
 * its own slug in its own `useState`, each drawing its own select — and on the
 * Payments page two of them, because `PaymentsScreen` and the `CreditPanel`
 * below it are separate components that each needed a customer group. Setting
 * one moved the settlements and left the exposure table answering about the
 * whole book, under a control that looked like it had been applied. Two
 * controls for one question is not a preference about layout; it is two answers
 * on one screen with nothing saying which is which.
 *
 * So the selection is the page's, not the panel's. It is declared once in
 * `SCOPED_BY` below, rendered once by `GroupScopeProvider`, and read by
 * `useGroupScope(kind)` wherever a request needs it.
 *
 * **It lives in the URL, and that is the whole of it.** `route.ts` opens by
 * explaining what this application gave up when nothing was a link and what it
 * got back; the same argument applies to a scope. In the query string,
 * "Composition for the aerospace book" is a link somebody can send, a bookmark
 * that still means what it meant, and a state Back returns to. Held in a
 * provider's `useState` instead, it would be none of those and would disagree
 * with the address bar besides.
 *
 * Two consequences, both deliberate:
 *
 * - **A selection does not follow you to the next screen.** The nav links are
 *   `pathFor(screen)` and carry no query, so walking from Cadence to
 *   Composition lands on the whole book. The alternative is a scope that
 *   survives navigation without appearing in the URL, which is the hidden state
 *   this file exists to avoid — and a link that does not reproduce what the
 *   sender was looking at is worse than one more click.
 * - **A slug the server does not recognise is an error on screen, not a quiet
 *   fall back to everything.** The endpoints 404 an unknown or unreadable slug
 *   (`routers/group_scope.py` says why it is a 404 and not a 403), and the
 *   screen shows that refusal. Answering a question about a group that does not
 *   exist with the whole book's figures is the benign default CLAUDE.md §1
 *   names. The control still shows the slug so it can be cleared — see
 *   `GroupFilter`, which renders an option for a value it was not given.
 *
 * Changing a selection **replaces** the history entry rather than pushing one.
 * Pushing would make Back mean "undo the last filter change", so leaving a page
 * somebody had narrowed three times would take four presses.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
         type ReactNode } from "react";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { useLocation, useSearchParams } from "react-router-dom";

import { papi } from "./api";
import { ALL_GROUPS, GroupFilter } from "./GroupFilter";
import { PATH, routeFor } from "./route";
import type { EntityGroup, GroupKind } from "./types";

/** Which kinds of group narrow each page, keyed by the address it answers on.
 *
 * **Keyed by route rather than by `Screen`, because two screens share a name
 * and only one of them takes a group.** `/customers` is the directory, which is
 * narrowed to a set of accounts; `/account/:id` is one account, which is not —
 * and `screenAt` calls both of them `customer`, correctly, because they share a
 * nav highlight. `routeFor` answers the other question, "which address is this
 * with the ids taken out", which is exactly the question a page-level scope
 * turns on. Its own header says so.
 *
 * The keys are written as `PATH.<screen>` rather than as literals so a screen
 * that moves address moves here with it.
 *
 * This table sits here rather than beside `WIDE_SCREENS` in `route.ts`, which
 * is the same shape of fact. The reason is narrow: `route.ts` imports nothing
 * but `matchPath` today, and a `GroupKind` from `types.ts` would make the
 * routing table depend on the API's vocabulary to state something only this
 * file reads.
 */
export const SCOPED_BY: Readonly<Record<string, readonly GroupKind[]>> = {
  /** The customer directory. */
  [PATH.customer]: ["CUSTOMER"],
  /** Revenue mix and order flow — the one page that crosses two kinds. "Which
   *  customers buy this line" is a customer question with an item group. */
  [PATH.composition]: ["CUSTOMER", "PRODUCT"],
  [PATH.cadence]: ["CUSTOMER"],
  [PATH.lostRevenue]: ["CUSTOMER"],
  [PATH.journey]: ["CUSTOMER"],
  /** Settlements, the owner books beneath them and the credit panel below
   *  that — three panels, one selection. This is the page the duplicate was
   *  on. */
  [PATH.payments]: ["CUSTOMER"],
  [PATH.supply]: ["VENDOR"],
  /** Item lines. */
  [PATH.catalogue]: ["PRODUCT"],
};

/** The query parameter each kind is carried on.
 *
 *  Named for the thing rather than for the enum member, for the reason
 *  `group_scope.py` names its dependencies `item_group` and not
 *  `product_group`: the reader of a URL is asking which set this page is about.
 */
const PARAM: Readonly<Record<GroupKind, string>> = {
  CUSTOMER: "customers",
  VENDOR: "vendors",
  PRODUCT: "items",
};

/** No accepted kinds. Module level so its identity is stable — it is a `useEffect`
 *  dependency, and a fresh `[]` each render is a refetch each render. */
const NONE: readonly GroupKind[] = [];

interface ScopeValue {
  /** What this page accepts, in the order the controls are drawn. */
  kinds: readonly GroupKind[];
  /** The selected slug for one kind, or `ALL_GROUPS` for the whole book. */
  slugOf: (kind: GroupKind) => string;
}

const ScopeContext = createContext<ScopeValue | null>(null);

/** The group this page is scoped to, for one kind.
 *
 *  Returns a slug to put in a request, or `ALL_GROUPS` (`""`) for the whole
 *  book. Callers pass it straight to `papi`, which omits an empty one.
 *
 *  A kind this page does not declare in `SCOPED_BY` returns `ALL_GROUPS` and
 *  complains in development. That is a wiring mistake — a panel asking for a
 *  scope with no control on screen to set it — and the honest behaviour is the
 *  unscoped answer it would have given before, said out loud rather than
 *  silently.
 */
export function useGroupScope(kind: GroupKind): string {
  const scope = useContext(ScopeContext);
  if (!scope) {
    throw new Error("useGroupScope outside a GroupScopeProvider");
  }
  if (!scope.kinds.includes(kind) && import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.error(
      `useGroupScope("${kind}") on a page that does not declare it. ` +
      "Add the kind to SCOPED_BY in platform/groupScope.tsx.");
  }
  return scope.kinds.includes(kind) ? scope.slugOf(kind) : ALL_GROUPS;
}

/** Holds the page's selection and draws the control for it, above the page.
 *
 *  Renders nothing of its own on a page that takes no group, and nothing on one
 *  whose workspace has drawn no groups of the kinds it takes — the same rule
 *  the per-panel control followed, and for the same reason: a select with only
 *  "All" in it is a control that cannot do anything.
 */
export function GroupScopeProvider({
  token, children,
}: { token: string; children: ReactNode }) {
  const { pathname } = useLocation();
  const kinds = SCOPED_BY[routeFor(pathname)] ?? NONE;
  const [params, setParams] = useSearchParams();
  const [options, setOptions] = useState<Partial<Record<GroupKind, EntityGroup[]>>>({});
  /** Which `(token, kind)` lists have already been asked for, so walking
   *  between two customer-scoped pages does not re-fetch the same list. A ref
   *  rather than state: it is a record of requests made, and rendering must not
   *  depend on it. */
  const asked = useRef<Set<string>>(new Set());

  // A different token is a different workspace, whose groups are different
  // groups. Forget both the lists and the record of having fetched them.
  useEffect(() => {
    asked.current = new Set();
    setOptions({});
  }, [token]);

  useEffect(() => {
    let live = true;
    for (const kind of kinds) {
      const key = `${token}:${kind}`;
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      // A failure is silent, which is the one judgement call here and the same
      // one the per-panel hook made: the page behind this control works without
      // it, and an error banner over a working screen because an optional
      // filter would not load teaches people to ignore banners. The control
      // does not render.
      papi.listGroups(token, kind)
        .then((r) => { if (live) setOptions((p) => ({ ...p, [kind]: r.groups })); })
        .catch(() => { if (live) setOptions((p) => ({ ...p, [kind]: [] })); });
    }
    return () => { live = false; };
  }, [token, kinds]);

  const slugOf = useCallback(
    (kind: GroupKind) => params.get(PARAM[kind]) ?? ALL_GROUPS, [params]);

  const setSlug = useCallback((kind: GroupKind, slug: string) => {
    // Built from the current query rather than from scratch: `?item=` on Stock
    // and `?scenario=` on the simulator belong to their screens, and a scope
    // change is not a reason to drop them.
    const next = new URLSearchParams(params);
    if (slug) next.set(PARAM[kind], slug);
    else next.delete(PARAM[kind]);
    setParams(next, { replace: true });
  }, [params, setParams]);

  const value = useMemo<ScopeValue>(() => ({ kinds, slugOf }), [kinds, slugOf]);

  const drawn = kinds.filter((k) => (options[k]?.length ?? 0) > 0);
  const chosen = kinds.filter((k) => slugOf(k) !== ALL_GROUPS);

  return (
    <ScopeContext.Provider value={value}>
      {drawn.length > 0 && (
        // A surface, and a sentence saying what it does. Drawn once without
        // either, it was a bare select floating above the page's own heading
        // with a rule under it, and it read as a stray control belonging to the
        // brand bar rather than as a statement about the page. The lead-in is
        // the fix: a reader arriving at "Narrow this page · Customer group ·
        // Aerospace · 3" knows what the figures below are computed over before
        // reading any of them.
        <Paper
          component="section"
          variant="outlined"
          aria-label="Group scope"
          sx={{ display: "flex", flexWrap: "wrap", alignItems: "center",
                gap: { xs: 1, sm: 1.5 }, px: { xs: 1.5, md: 2 }, py: 1.25, mb: 2,
                bgcolor: "var(--color-neutral-100)" }}
        >
          <Typography
            component="span"
            sx={{ fontFamily: "var(--font-heading)", fontSize: 12,
                  fontWeight: 700, letterSpacing: ".06em",
                  textTransform: "uppercase", color: "text.secondary" }}
          >
            Narrow this page
          </Typography>
          {drawn.map((kind) => (
            <GroupFilter
              key={kind}
              label={LABEL[kind]}
              value={slugOf(kind)}
              onChange={(slug) => setSlug(kind, slug)}
              options={options[kind] ?? []}
              show
              minWidth={190}
            />
          ))}
          {chosen.length > 0 && (
            // One control clears the page, however many kinds narrow it. On
            // Composition, "back to the whole book" is otherwise two selects
            // set back to All, and somebody will set one.
            <Button
              size="small"
              onClick={() => {
                const next = new URLSearchParams(params);
                for (const kind of kinds) next.delete(PARAM[kind]);
                setParams(next, { replace: true });
              }}
              sx={{ textTransform: "none", color: "text.secondary" }}
            >
              Whole book
            </Button>
          )}
        </Paper>
      )}
      {children}
    </ScopeContext.Provider>
  );
}

/** What each control is called. The server sends `entity_label` per group, but
 *  the control is named before any group is picked and on a page that may take
 *  two of them, so the label is the kind's and it is written here. */
const LABEL: Readonly<Record<GroupKind, string>> = {
  CUSTOMER: "Customer group",
  VENDOR: "Vendor group",
  PRODUCT: "Item group",
};
