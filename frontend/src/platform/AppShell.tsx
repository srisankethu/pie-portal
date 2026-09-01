/** The application frame: brand bar, grouped navigation, content column.
 *
 * Replaces eighteen undifferentiated text buttons in a `flex-wrap` row, where
 * finding "Suppliers" meant reading all eighteen labels and a laptop width
 * pushed the page heading under the fold.
 *
 * Grouped by the question the screens answer, not by the code that serves them:
 *
 *   Decide      the queue, the desk, and the things that block a quote
 *   Understand  where the money moved and why
 *   The book    what is actually held — customers, stock, suppliers, cash
 *   Setup       connections, identity, and the policy that governs the rest
 *
 * Role scoping lives with the caller: `AppShell` renders whatever it is handed,
 * and a screen a role cannot read is omitted upstream rather than 403'd.
 *
 * Every item is an anchor, not a button — the reason the router moved to React
 * Router. A `<button onClick>` cannot be ctrl-clicked into a new tab and shows
 * no destination on hover, so comparing the queue against one account meant
 * losing one of them.
 */
import { useState, type ReactNode } from "react";
import AppBar from "@mui/material/AppBar";
import Badge from "@mui/material/Badge";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import ListSubheader from "@mui/material/ListSubheader";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import MenuIcon from "@mui/icons-material/Menu";
import AutoStoriesOutlined from "@mui/icons-material/AutoStoriesOutlined";
import FlagOutlined from "@mui/icons-material/FlagOutlined";
import HandshakeOutlined from "@mui/icons-material/HandshakeOutlined";
import ScienceOutlined from "@mui/icons-material/ScienceOutlined";
import RequestQuoteOutlined from "@mui/icons-material/RequestQuoteOutlined";
import FactCheckOutlined from "@mui/icons-material/FactCheckOutlined";
import CloudOutlined from "@mui/icons-material/CloudOutlined";
import LightbulbOutlined from "@mui/icons-material/LightbulbOutlined";
import TrendingDownOutlined from "@mui/icons-material/TrendingDownOutlined";
import ScatterPlotOutlined from "@mui/icons-material/ScatterPlotOutlined";
import DonutSmallOutlined from "@mui/icons-material/DonutSmallOutlined";
import ScheduleOutlined from "@mui/icons-material/ScheduleOutlined";
import GroupsOutlined from "@mui/icons-material/GroupsOutlined";
import Inventory2Outlined from "@mui/icons-material/Inventory2Outlined";
import SavingsOutlined from "@mui/icons-material/SavingsOutlined";
import LocalShippingOutlined from "@mui/icons-material/LocalShippingOutlined";
import HubOutlined from "@mui/icons-material/HubOutlined";
import GridViewOutlined from "@mui/icons-material/GridViewOutlined";
import AccountTreeOutlined from "@mui/icons-material/AccountTreeOutlined";
import TrackChangesOutlined from "@mui/icons-material/TrackChangesOutlined";
import CategoryOutlined from "@mui/icons-material/CategoryOutlined";
import PaymentsOutlined from "@mui/icons-material/PaymentsOutlined";
import ReceiptLongOutlined from "@mui/icons-material/ReceiptLongOutlined";
import TimelineOutlined from "@mui/icons-material/TimelineOutlined";
import StorageOutlined from "@mui/icons-material/StorageOutlined";
import ManageSearchOutlined from "@mui/icons-material/ManageSearchOutlined";
import FingerprintOutlined from "@mui/icons-material/FingerprintOutlined";
import PsychologyOutlined from "@mui/icons-material/PsychologyOutlined";
import ScoreboardOutlined from "@mui/icons-material/ScoreboardOutlined";
import PendingActionsOutlined from "@mui/icons-material/PendingActionsOutlined";
import CurrencyExchangeOutlined from "@mui/icons-material/CurrencyExchangeOutlined";
import GavelOutlined from "@mui/icons-material/GavelOutlined";
import ShieldOutlined from "@mui/icons-material/ShieldOutlined";
import TuneOutlined from "@mui/icons-material/TuneOutlined";
import InsightsOutlined from "@mui/icons-material/InsightsOutlined";
import HistoryOutlined from "@mui/icons-material/HistoryOutlined";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";

import { Link as RouterLink } from "react-router-dom";

import AccountMenu from "./AccountMenu";
import type { OrganizationMembershipView } from "./types";
import { pathFor, type Screen } from "./route";

export const DRAWER_WIDTH = 232;

/** The four sections, in the order a working day uses them. */
export type NavGroup = "decide" | "understand" | "book" | "setup";

export const GROUP_LABEL: Record<NavGroup, string> = {
  decide: "Decide",
  understand: "Understand",
  book: "The book",
  setup: "Setup",
};

/** One icon per screen. Kept here rather than at the call site so the nav table
 *  upstream stays about role scoping, which is the part with rules in it. */
const ICON: Partial<Record<Screen, typeof MenuIcon>> = {
  home: AutoStoriesOutlined,
  list: FlagOutlined,
  negotiate: HandshakeOutlined,
  simulate: ScienceOutlined,
  quotes: RequestQuoteOutlined,
  quoteOutcomes: ScoreboardOutlined,
  // A clipboard still waiting on its clock: these are quotes lapsed without a
  // win or a loss recorded, not quotes that were lost. The scoreboard above is
  // the settled half of the same pair.
  unrecordedQuotes: PendingActionsOutlined,
  approvals: FactCheckOutlined,

  weather: CloudOutlined,
  opportunities: LightbulbOutlined,
  lostRevenue: TrendingDownOutlined,
  landscape: ScatterPlotOutlined,
  composition: DonutSmallOutlined,
  cadence: ScheduleOutlined,

  customer: GroupsOutlined,
  stock: Inventory2Outlined,
  gmroi: SavingsOutlined,
  supply: LocalShippingOutlined,
  bonds: HubOutlined,
  mix: GridViewOutlined,
  dependency: AccountTreeOutlined,
  targets: TrackChangesOutlined,
  catalogue: CategoryOutlined,
  payments: PaymentsOutlined,
  payables: ReceiptLongOutlined,
  orderToCash: TimelineOutlined,
  cashCycle: CurrencyExchangeOutlined,
  statutory: GavelOutlined,

  // The before-and-after pair, and the icons say which is which: a clock hand
  // for what the book already held, an upward line for what the platform
  // changed about it.
  retrospective: HistoryOutlined,
  attribution: InsightsOutlined,

  data: StorageOutlined,
  // A lookup, not a second catalogue: `catalogue` above is the book of items,
  // this is the decode that says which pack and ruleset answered for one.
  decodedCatalog: ManageSearchOutlined,
  identity: FingerprintOutlined,
  states: PsychologyOutlined,
  trust: ShieldOutlined,
  settings: TuneOutlined,
};

export interface NavItem {
  key: Screen;
  label: string;
  group: NavGroup;
  /** Rendered as a badge. Open counts only — a badge that never goes down
   *  stops being read. */
  count?: number;
  /** Marks this item current for a screen that has no nav entry of its own,
   *  e.g. a decision detail page belongs to the queue it was opened from. */
  alsoCurrentFor?: Screen[];
}

const ORDER: NavGroup[] = ["decide", "understand", "book", "setup"];

/** The two groups a reader may fold away, and why nothing is folded for them.
 *
 *  This nav carries thirty-four items and its own grouping admits the shape:
 *  seven ways to act, twenty to read. The first attempt at that treated the
 *  length as the problem and reduced it two ways — collapsing these groups by
 *  default, and withholding them entirely until an organization had synced
 *  books.
 *
 *  Both were wrong, and the withholding was wrong in the way that matters: the
 *  analysis screens are where this product's visualisations live, and they are
 *  the best argument it makes for itself. Hiding them from somebody who has not
 *  seen the product yet removes the case exactly when it would have landed. A
 *  long nav is a much smaller problem than a nav missing the thing worth
 *  looking at.
 *
 *  So nothing folds unless the reader folds it. `Decide` is the working day and
 *  never folds; `Setup` is where you go when something is wrong, so folding it
 *  would hide the exits. The two analysis groups *can* be folded by somebody who
 *  has decided they do not use them, that choice persists, and a group holding
 *  the current screen stays open regardless — a nav that hides the page you are
 *  reading has lost you.
 *
 *  The affordance is for the reader who wants a shorter list. It is not a
 *  judgement about what they should be looking at. */
const FOLDABLE: ReadonlySet<NavGroup> = new Set<NavGroup>(["understand", "book"]);

//: Which foldable groups this reader has *collapsed*. Stored the way round it
//: is because the default is open: an empty preference must mean "show me
//: everything", so the thing worth persisting is the exception.
//:
//: A new key rather than reusing the old one. The previous release stored the
//: opposite list under `pie.nav.open-groups`, and reading that as a collapse
//: list would fold exactly the groups somebody had chosen to open — the worst
//: possible misreading of a stored preference.
const FOLD_KEY = "pie.nav.collapsed-groups";


/** Which foldable groups this reader has opened. Persisted so the answer
 *  survives a reload; a failure to read or write it is not worth a broken nav,
 *  so both sides degrade to the default rather than throwing (private-mode
 *  browsers make `localStorage` throw on access, not merely return null). */
function loadCollapsedGroups(): Set<NavGroup> {
  try {
    const raw = window.localStorage.getItem(FOLD_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as NavGroup[]);
  } catch {
    return new Set();
  }
}

function saveCollapsedGroups(groups: Set<NavGroup>): void {
  try {
    window.localStorage.setItem(FOLD_KEY, JSON.stringify([...groups]));
  } catch {
    /* A nav that cannot remember is still a nav. */
  }
}

export default function AppShell({
  items,
  current,
  userName,
  roleLabel,
  organizationName,
  organizations,
  currentOrganizationId,
  onSwitchOrganization,
  onSignOut,
  children,
}: {
  items: NavItem[];
  current: Screen;
  userName: string;
  roleLabel: string;
  /** Passed straight through to the account menu, which is where the shell
   *  answers "who am I and whose workspace is this". Optional so the two test
   *  harnesses that render a shell without a session keep compiling — and so a
   *  single-workspace deployment can simply not pass them. */
  organizationName?: string;
  organizations?: OrganizationMembershipView[];
  currentOrganizationId?: string;
  onSwitchOrganization?: (organizationId: string) => void;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);
  const [collapsedGroups, setCollapsedGroups] =
    useState<Set<NavGroup>>(loadCollapsedGroups);

  const isCurrent = (it: NavItem) =>
    current === it.key || (it.alsoCurrentFor || []).includes(current);

  const toggleGroup = (group: NavGroup) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group); else next.add(group);
      saveCollapsedGroups(next);
      return next;
    });
  };

  const nav = (
    <Box sx={{ overflowY: "auto", height: "100%", pb: 2 }}>
      {ORDER.map((group) => {
        const inGroup = items.filter((i) => i.group === group);
        if (!inGroup.length) return null;
        // A group holding the screen you are on is open whatever the stored
        // preference says — a nav that hides the page you are reading is a nav
        // that has lost you.
        const holdsCurrent = inGroup.some(isCurrent);
        const foldable = FOLDABLE.has(group);
        const expanded = !foldable || holdsCurrent || !collapsedGroups.has(group);
        return (
          <List
            key={group}
            dense
            disablePadding
            subheader={
              <ListSubheader
                disableSticky
                sx={{
                  bgcolor: "transparent",
                  fontFamily: "var(--font-heading)",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.09em",
                  textTransform: "uppercase",
                  color: "text.secondary",
                  lineHeight: "30px",
                  mt: 1,
                }}
              >
                {foldable ? (
                  <Box
                    component="button"
                    type="button"
                    onClick={() => toggleGroup(group)}
                    aria-expanded={expanded}
                    aria-label={`${GROUP_LABEL[group]}, ${inGroup.length} screens`}
                    sx={{
                      all: "unset",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      width: "100%",
                      font: "inherit",
                      letterSpacing: "inherit",
                      "&:focus-visible": {
                        outline: "2px solid",
                        outlineColor: "primary.main",
                        outlineOffset: 2,
                      },
                    }}
                  >
                    <ExpandMoreOutlined
                      sx={{
                        fontSize: 16,
                        transition: "transform 120ms",
                        transform: expanded ? "none" : "rotate(-90deg)",
                      }} />
                    {GROUP_LABEL[group]}
                    {/* The count is what makes a folded group legible: a
                        reader who collapsed this a month ago needs the row to
                        read as eleven screens put away, not as a heading with
                        nothing under it. */}
                    {!expanded && (
                      <Box component="span" sx={{ opacity: 0.7, ml: 0.25 }}>
                        ({inGroup.length})
                      </Box>
                    )}
                  </Box>
                ) : GROUP_LABEL[group]}
              </ListSubheader>
            }
            sx={{ px: 1 }}
          >
            {(expanded ? inGroup : []).map((it) => {
              const Icon = ICON[it.key];
              const selected = isCurrent(it);
              return (
                <ListItemButton
                  key={it.key}
                  component={RouterLink}
                  to={pathFor(it.key)}
                  selected={selected}
                  // Closing the drawer is all that is left for the click to do:
                  // the anchor navigates on its own, which is what makes
                  // ctrl-click and middle-click work.
                  onClick={() => setOpen(false)}
                  sx={{ minHeight: 34, py: 0.25, mb: "1px", color: "inherit" }}
                >
                  {/* The slot is always rendered, even with nothing in it. It
                      used to be omitted when a screen had no icon, which pulled
                      that one label flush against the edge while every
                      neighbour stayed indented — so the four screens missing
                      from ICON did not read as "no icon yet", they read as a
                      broken list. A gap keeps the column straight, and a
                      missing icon stays a small omission instead of a layout
                      fault. */}
                  <ListItemIcon sx={{ minWidth: 30, color: "inherit" }}>
                    {Icon ? <Icon sx={{ fontSize: 18 }} /> : null}
                  </ListItemIcon>
                  <ListItemText
                    primary={it.label}
                    slotProps={{
                      primary: {
                        sx: {
                          fontFamily: "var(--font-heading)",
                          fontSize: 13.5,
                          fontWeight: selected ? 600 : 500,
                        },
                      },
                    }}
                  />
                  {it.count ? (
                    <Badge
                      badgeContent={it.count}
                      color={it.key === "approvals" ? "error" : "primary"}
                      sx={{ mr: 1.4 }}
                    />
                  ) : null}
                </ListItemButton>
              );
            })}
          </List>
        );
      })}
    </Box>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar
        position="fixed"
        elevation={0}
        color="default"
        sx={{
          bgcolor: "var(--color-neutral-100)",
          borderBottom: "1px solid var(--color-divider)",
          zIndex: (t) => t.zIndex.drawer + 1,
        }}
      >
        <Toolbar variant="dense" sx={{ gap: 1.5, minHeight: 52 }}>
          {!wide && (
            <IconButton edge="start" size="small" onClick={() => setOpen(true)} aria-label="Open navigation">
              <MenuIcon />
            </IconButton>
          )}
          <PieLogo size={30} />
          <Box sx={{ flex: 1 }} />
          {/* No role switcher. A user has exactly one role, it comes from their
              account, and a control that swapped it would be a control that lets
              anyone read the cost of every line in the book. The role is shown
              in there, and only shown. */}
          <AccountMenu
            userName={userName}
            roleLabel={roleLabel}
            organizationName={organizationName}
            organizations={organizations}
            currentOrganizationId={currentOrganizationId}
            onSwitchOrganization={onSwitchOrganization}
            onSignOut={onSignOut}
          />
        </Toolbar>
      </AppBar>

      <Box component="nav" sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }}>
        <Drawer
          variant={wide ? "permanent" : "temporary"}
          open={wide || open}
          onClose={() => setOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            "& .MuiDrawer-paper": {
              width: DRAWER_WIDTH,
              boxSizing: "border-box",
              bgcolor: "var(--color-neutral-100)",
              borderRight: "1px solid var(--color-divider)",
            },
          }}
        >
          <Toolbar variant="dense" sx={{ minHeight: 52 }} />
          <Divider />
          {nav}
        </Drawer>
      </Box>

      <Box
        component="main"
        sx={{
          flex: 1,
          minWidth: 0,          // without this a wide grid stretches the shell
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Toolbar variant="dense" sx={{ minHeight: 52 }} />
        <Box sx={{ flex: 1, width: "100%", maxWidth: 1180, mx: "auto", p: { xs: 2, md: 3 } }}>
          {children}
        </Box>
      </Box>
    </Box>
  );
}

/** How the product names itself, written once.
 *
 *  It was `PIE · Decisions` in two places — the shell header and `BrandMark` —
 *  even though `BrandMark` exists precisely so the surfaces cannot disagree; the
 *  header simply restated the string instead of using it. The sub-brand was also
 *  stale: it dated from when the product was "Commercial Decisions", while the
 *  landing page, the tab title and the positioning had all moved to commercial
 *  intelligence.
 *
 *  So it is just `PIE` now — a name with a category glued to it goes out of date
 *  every time the category is rethought, which is twice so far; the bare name
 *  does not. Kept as one constant so the logo's accessible name has one source. */
export const BRAND = "PIE";

/** The product logo: the `PIE` wordmark set in the accent tile — the same mark
 *  the browser tab carries (public/favicon.svg), redrawn here with theme tokens
 *  and the bundled heading font so it tracks the palette instead of the
 *  favicon's frozen hex. One in-app copy, used by the top bar and by BrandMark,
 *  so the two never drift. `textLength` pins the advance width so the wordmark
 *  stays centred in the tile even in the instant before Barlow Condensed
 *  finishes loading and a wider fallback is briefly substituted. */
export function PieLogo({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 32 32"
      role="img" aria-label={BRAND} style={{ display: "block", flex: "none" }}
    >
      <rect width="32" height="32" rx="6" fill="var(--color-accent-700)" />
      <text
        x="16" y="22.5" textAnchor="middle"
        textLength="23" lengthAdjust="spacingAndGlyphs"
        fontFamily="var(--font-heading)" fontSize="16" fontWeight="700"
        fill="var(--color-bg)"
      >
        PIE<tspan fill="var(--color-accent-400)">.</tspan>
      </text>
    </svg>
  );
}

/** The tooltip-wrapped brand mark, exported for the sign-in screen so the two
 *  surfaces agree on how the product names itself. */
export function BrandMark({ tip }: { tip?: string }) {
  const mark = <PieLogo size={30} />;
  return tip ? <Tooltip title={tip}>{mark}</Tooltip> : mark;
}
