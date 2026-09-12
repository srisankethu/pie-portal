/** The application frame: brand bar, five destinations, content column.
 *
 * This was a 232px drawer holding thirty-four items in four collapsible groups.
 * The groups were grouped honestly — the problem was never the length, it was
 * that the list was a description of the data rather than of the work, so the
 * product computed exactly what was wrong and then made the reader go and find
 * it. `destinations.ts` has the full argument and the map of where every one of
 * those screens now lives.
 *
 * What is left is five links along the top, which is short enough to read at a
 * glance and therefore short enough to be horizontal — and a horizontal bar
 * gives the width back to the content, which is where the tables that need it
 * are. Scoping left with the drawer: all five are readable by every role, and
 * the role rules moved down to the tabs inside Money and Setup, where they
 * match the gate on the endpoints those screens call.
 *
 * Every item is still an anchor, not a button — ctrl-click, middle-click, open
 * in a new tab, and a destination on hover. Two screens side by side is the
 * ordinary way this desk is used.
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
import ListItemText from "@mui/material/ListItemText";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import MenuIcon from "@mui/icons-material/Menu";
import SearchOutlined from "@mui/icons-material/SearchOutlined";

import { Link as RouterLink } from "react-router-dom";

import AccountMenu from "./AccountMenu";
import { DESTINATIONS, destinationFor, type Destination } from "./destinations";
import type { OrganizationMembershipView } from "./types";
import { pathFor, WIDE_SCREENS, type Screen } from "./route";

/** What a destination has waiting. Only Today carries one today, and only ever
 *  an open count: a badge that never goes down stops being read. */
export type NavCounts = Partial<Record<Destination, number>>;

export default function AppShell({
  current,
  counts,
  userName,
  roleLabel,
  organizationName,
  organizations,
  currentOrganizationId,
  onSwitchOrganization,
  onOpenCommands,
  onSignOut,
  status,
  children,
}: {
  current: Screen;
  counts?: NavCounts;
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
  /** Opens the intent search. Optional: a harness that renders the shell
   *  without the palette gets a shell without the button, rather than one whose
   *  button does nothing. */
  onOpenCommands?: () => void;
  onSignOut: () => void;
  /** When the books were last read, rendered beside the account. The shell is
   *  where it belongs: a figure's age is true wherever the reader is standing,
   *  and it is the first thing anybody asks when a number looks wrong. */
  status?: ReactNode;
  children: ReactNode;
}) {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const wideLayout = WIDE_SCREENS.has(current);
  const [open, setOpen] = useState(false);
  const here = destinationFor(current);

  const links = DESTINATIONS.map((d) => ({
    ...d, current: d.key === here, count: counts?.[d.key] ?? 0 }));

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh",
               bgcolor: "background.default" }}>
      <AppBar
        position="sticky"
        elevation={0}
        color="default"
        sx={{
          bgcolor: "var(--color-neutral-100)",
          borderBottom: "1px solid var(--color-divider)",
        }}
      >
        <Toolbar variant="dense" sx={{ gap: 1, minHeight: 56 }}>
          {!wide && (
            <IconButton edge="start" size="small" onClick={() => setOpen(true)}
                        aria-label="Open navigation">
              <MenuIcon />
            </IconButton>
          )}
          <PieLogo size={30} />
          {wide && (
            <Box component="nav" aria-label="Main" sx={{ display: "flex", gap: 0.25, ml: 1 }}>
              {links.map((d) => (
                <Button
                  key={d.key}
                  component={RouterLink}
                  to={pathFor(d.screen)}
                  aria-current={d.current ? "page" : undefined}
                  sx={{
                    minHeight: 34,
                    px: 1.5,
                    fontFamily: "var(--font-heading)",
                    fontSize: 15,
                    fontWeight: 600,
                    color: d.current ? "var(--color-accent-800)" : "text.primary",
                    bgcolor: d.current ? "var(--color-accent-100)" : "transparent",
                  }}
                >
                  {d.count ? (
                    <Badge badgeContent={d.count} color="error" sx={{ pr: 1.6 }}>
                      {d.label}
                    </Badge>
                  ) : d.label}
                </Button>
              ))}
            </Box>
          )}
          <Box sx={{ flex: 1 }} />
          {/* The search says what it is for rather than what it searches.
              "What do you want to do?" is the one line on this bar addressed to
              somebody who does not yet know the product's vocabulary, and the
              palette behind it answers in verbs. */}
          {onOpenCommands && (
            <Button
              onClick={onOpenCommands}
              startIcon={<SearchOutlined sx={{ fontSize: 18 }} />}
              sx={{
                minHeight: 34,
                px: 1.25,
                border: "1px solid var(--color-divider)",
                bgcolor: "var(--color-neutral-200)",
                color: "text.secondary",
                fontWeight: 400,
                textTransform: "none",
              }}
            >
              {wide ? "What do you want to do?" : "Search"}
              {wide && (
                <Box component="span" sx={{
                  ml: 1, px: 0.6, borderRadius: 0.5,
                  border: "1px solid var(--color-divider)",
                  fontFamily: "var(--font-heading)", fontSize: 11, fontWeight: 600 }}>
                  ⌘K
                </Box>
              )}
            </Button>
          )}
          {wide && status}
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

      <Drawer
        variant="temporary"
        open={!wide && open}
        onClose={() => setOpen(false)}
        ModalProps={{ keepMounted: true }}
        sx={{ "& .MuiDrawer-paper": { width: 260, bgcolor: "var(--color-neutral-100)" } }}
      >
        <Toolbar variant="dense" sx={{ minHeight: 56 }}>
          <PieLogo size={26} />
          <Typography sx={{ ml: 1, fontFamily: "var(--font-heading)", fontWeight: 600 }}>
            {BRAND}
          </Typography>
        </Toolbar>
        <Divider />
        <List dense component="nav" aria-label="Main">
          {links.map((d) => (
            <ListItemButton
              key={d.key}
              component={RouterLink}
              to={pathFor(d.screen)}
              selected={d.current}
              // Closing the drawer is all that is left for the click to do: the
              // anchor navigates on its own, which is what makes ctrl-click and
              // middle-click work.
              onClick={() => setOpen(false)}
              sx={{ minHeight: 44 }}
            >
              <ListItemText
                primary={d.label}
                slotProps={{ primary: { sx: {
                  fontFamily: "var(--font-heading)", fontSize: 15,
                  fontWeight: d.current ? 600 : 500 } } }}
              />
              {d.count ? <Badge badgeContent={d.count} color="error" sx={{ mr: 1.4 }} /> : null}
            </ListItemButton>
          ))}
        </List>
        {status && (
          <Box sx={{ px: 2, py: 1.5, borderTop: "1px solid var(--color-divider)" }}>
            {status}
          </Box>
        )}
      </Drawer>

      <Box component="main" sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* 1180 is a reading measure and stays the default. A table-first
            screen gets 1560 instead — see `WIDE_SCREENS` in route.ts for why
            the Quote Builder is one, and what the cap was costing it. Still a
            cap rather than full bleed: a grid that runs to the edge of a
            27-inch monitor is no easier to read across than one that does
            not. */}
        <Box
          sx={{
            flex: 1,
            width: "100%",
            maxWidth: wideLayout ? 1560 : 1180,
            mx: "auto",
            p: { xs: 2, md: 3 },
          }}
        >
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
