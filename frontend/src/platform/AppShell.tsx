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
import Button from "@mui/material/Button";
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
import Typography from "@mui/material/Typography";
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
import FingerprintOutlined from "@mui/icons-material/FingerprintOutlined";
import PsychologyOutlined from "@mui/icons-material/PsychologyOutlined";
import ScoreboardOutlined from "@mui/icons-material/ScoreboardOutlined";
import CurrencyExchangeOutlined from "@mui/icons-material/CurrencyExchangeOutlined";
import GavelOutlined from "@mui/icons-material/GavelOutlined";
import ShieldOutlined from "@mui/icons-material/ShieldOutlined";
import TuneOutlined from "@mui/icons-material/TuneOutlined";
import InsightsOutlined from "@mui/icons-material/InsightsOutlined";

import { Link as RouterLink } from "react-router-dom";

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

  attribution: InsightsOutlined,

  data: StorageOutlined,
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

export default function AppShell({
  items,
  current,
  userName,
  roleLabel,
  onSignOut,
  children,
}: {
  items: NavItem[];
  current: Screen;
  userName: string;
  roleLabel: string;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);

  const isCurrent = (it: NavItem) =>
    current === it.key || (it.alsoCurrentFor || []).includes(current);

  const nav = (
    <Box sx={{ overflowY: "auto", height: "100%", pb: 2 }}>
      {ORDER.map((group) => {
        const inGroup = items.filter((i) => i.group === group);
        if (!inGroup.length) return null;
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
                {GROUP_LABEL[group]}
              </ListSubheader>
            }
            sx={{ px: 1 }}
          >
            {inGroup.map((it) => {
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
          <Typography
            component="span"
            sx={{
              fontFamily: "var(--font-heading)",
              fontWeight: 700,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              fontSize: 15,
              whiteSpace: "nowrap",
            }}
          >
            PIE · Decisions
          </Typography>
          <Box sx={{ flex: 1 }} />
          {/* No role switcher. A user has exactly one role, it comes from their
              account, and a control that swapped it would be a control that lets
              anyone read the cost of every line in the book. */}
          <Box sx={{ textAlign: "right", lineHeight: 1.2, display: { xs: "none", sm: "block" } }}>
            <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 13 }}>
              {userName}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {roleLabel}
            </Typography>
          </Box>
          <Button size="small" variant="outlined" color="inherit" onClick={onSignOut}>
            Sign out
          </Button>
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

/** The tooltip-wrapped brand mark, exported for the sign-in screen so the two
 *  surfaces agree on how the product names itself. */
export function BrandMark({ tip }: { tip?: string }) {
  const mark = (
    <Typography
      component="span"
      sx={{
        fontFamily: "var(--font-heading)",
        fontWeight: 700,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        fontSize: 15,
      }}
    >
      PIE · Decisions
    </Typography>
  );
  return tip ? <Tooltip title={tip}>{mark}</Tooltip> : mark;
}
