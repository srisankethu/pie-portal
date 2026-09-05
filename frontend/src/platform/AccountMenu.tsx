/** Who you are signed in as, and the one thing you can do about it.
 *
 * The header used to spend its whole right-hand side on this: a two-line name
 * and role block, then an outlined "Sign out" button beside it. Three problems,
 * and the third is the one that matters.
 *
 * It gave the loudest control on every screen to the action nobody comes here
 * to take — an outlined button sits at the same weight as a screen's own
 * primary action, and §5 of `docs/ui-standards.md` is explicit that a surface
 * with no obvious primary action has none. It also read as a caption with a
 * button that happened to be next to it, rather than as one account control.
 *
 * And on a phone the name block was simply deleted (`display: { xs: "none" }`),
 * which left a bare "Sign out" and no way to tell which account was signed in —
 * exactly the question worth answering on a shared shop-floor tablet where
 * three people use one browser. So the identity now lives *inside* the menu,
 * where it survives every width, and the avatar that opens it is the one thing
 * on screen at any size.
 *
 * The trigger is a button rather than an anchor because it opens a menu; the
 * navigation rule about anchors in `AppShell` is about destinations, and an
 * account menu is not one.
 *
 * **Everything in the menu is a direct child of the list, and that is not a
 * formatting preference.** MUI moves keyboard focus by walking the DOM
 * siblings of the `<ul>` (`moveFocus` in `MenuList`), skipping anything
 * without a `tabindex` — it does not descend. The workspace items used to sit
 * inside a wrapping `<Box>`, so every one of them was unreachable by arrow key
 * and the menu's initial focus landed on the identity block instead of on the
 * workspace you are standing in. A section label is therefore a
 * `ListSubheader` and the rules are `component="li"`: both are real list
 * children, both are skipped by focus for the right reason, and the `<ul>`
 * holds only what a `<ul>` may hold.
 */
import { useState } from "react";

import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import ListSubheader from "@mui/material/ListSubheader";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import type { SxProps, Theme } from "@mui/material/styles";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import LogoutOutlined from "@mui/icons-material/LogoutOutlined";
import CheckRounded from "@mui/icons-material/CheckRounded";

import { TOUCH, TOUCH_TARGET } from "./kit";
import type { OrganizationMembershipView } from "./types";

/** The role, as a person reads it. The same three words the rest of the shell
 *  uses; here rather than imported because this component takes its own props
 *  and `PlatformApp` maps the session's role for the trigger already. */
const ROLE_LABEL: Record<string, string> = {
  OWNER: "Owner",
  SALES_MANAGER: "Manager",
  SALESPERSON: "Salesperson",
};

/** The gutter an icon takes in this menu. MUI's own is 56px, which is sized
 *  for a settings list rather than for a menu whose widest row is a company
 *  name — at that width the tick and the name stop reading as one row. */
const ICON_GUTTER: SxProps<Theme> = { minWidth: 32 };

/** The monogram on the avatar.
 *
 *  First letter of the first and last word, so "S. Menon" is SM rather than S.
 *  and "Sanketh" is S. Punctuation is stripped first — an initial of "." is a
 *  smudge, and names arrive from the connectors with all of "S.", "S" and
 *  "(S)" in them. Empty in, empty out: the avatar falls back to its icon
 *  rather than rendering a placeholder character that looks like a name.
 */
export function initials(name: string): string {
  const words = name.split(/\s+/).map((w) => w.replace(/[^\p{L}\p{N}]/gu, "")).filter(Boolean);
  if (!words.length) return "";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toUpperCase();
}

/** The disc the monogram sits on, at whatever size its surface needs.
 *
 *  One component because it appears twice — on the trigger and at the head of
 *  the menu — and two copies are two places for the colour pair to drift out
 *  of step. 32px in the toolbar rather than MUI's 40 because the bar is 52px
 *  tall; 36 in the menu, where the identity is the thing you should see first.
 *  The letters scale with the disc rather than being a second size to choose.
 *
 *  Hidden from assistive technology in both places: the trigger carries an
 *  explicit `aria-label` and the menu prints the name directly beside it, so
 *  announcing "S M" first is the same fact twice in a worse form.
 */
function Monogram({ mark, size }: { mark: string; size: number }) {
  return (
    <Avatar
      aria-hidden
      sx={{
        width: size,
        height: size,
        fontFamily: "var(--font-heading)",
        fontSize: size * 0.4,
        fontWeight: 700,
        bgcolor: "primary.main",
        color: "primary.contrastText",
      }}
    >
      {mark}
    </Avatar>
  );
}

export default function AccountMenu({
  userName,
  roleLabel,
  organizationName,
  organizations,
  currentOrganizationId,
  onSwitchOrganization,
  onSignOut,
}: {
  userName: string;
  roleLabel: string;
  /** The workspace this session acts for. Shown under the name, because on a
   *  platform where one login reaches two customers "who am I signed in as" is
   *  two questions and the menu answers both or neither. */
  organizationName?: string;
  /** Every workspace this identity may open. One or none renders no switcher:
   *  a list of one is a control that teaches people it does nothing. */
  organizations?: OrganizationMembershipView[];
  currentOrganizationId?: string;
  onSwitchOrganization?: (organizationId: string) => void;
  onSignOut: () => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const open = Boolean(anchor);
  const mark = initials(userName);
  // Somewhere else to go. Asked of the memberships themselves rather than
  // taken from `organizations.length > 1`, so that a stale list still
  // containing only the current workspace renders nothing — the safe direction
  // for a control that mints a session.
  const switchable = (organizations || []).some(
    (o) => o.organization_id !== currentOrganizationId,
  );

  return (
    <>
      <Button
        id="account-menu-button"
        onClick={(e) => setAnchor(e.currentTarget)}
        aria-controls={open ? "account-menu" : undefined}
        aria-haspopup="menu"
        aria-expanded={open ? true : undefined}
        // The name is hidden below `sm`, so without this the control announces
        // itself as two initials on exactly the screens where the visible text
        // is gone.
        aria-label={`Account: ${userName}, ${roleLabel}`}
        color="inherit"
        sx={{
          ...TOUCH,
          textTransform: "none",
          borderRadius: 999,
          // Tight on a phone, where this is a bare avatar and the padding of a
          // labelled button would only push the toolbar around.
          pl: 0.5,
          pr: { xs: 0.5, sm: 1.25 },
          gap: 1,
          color: "text.primary",
        }}
      >
        <Monogram mark={mark} size={32} />
        <Box sx={{ display: { xs: "none", sm: "block" }, textAlign: "left", lineHeight: 1.2 }}>
          {/* Sizes off the ramp rather than picked here: `body2` is the 13px
              rung and `caption` the metadata one, so a change to the ramp
              moves the toolbar with everything else. */}
          <Typography
            component="span"
            sx={{
              typography: "body2",
              fontFamily: "var(--font-heading)",
              fontWeight: 600,
              display: "block",
              lineHeight: 1.25,
            }}
          >
            {userName}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", lineHeight: 1.25 }}>
            {roleLabel}
          </Typography>
        </Box>
        <ExpandMoreRounded
          fontSize="small"
          sx={{
            display: { xs: "none", sm: "block" },
            color: "text.secondary",
            transform: open ? "rotate(180deg)" : "none",
            transition: (t) => t.transitions.create("transform", { duration: t.transitions.duration.shortest }),
          }}
        />
      </Button>

      <Menu
        id="account-menu"
        anchorEl={anchor}
        open={open}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          list: { "aria-labelledby": "account-menu-button", sx: { py: 0.5 } },
          paper: {
            // `maxWidth` because a long name on a 320px phone would otherwise
            // push the menu off its own anchor edge.
            sx: { mt: 0.5, minWidth: 232, maxWidth: "calc(100vw - 16px)" },
          },
        }}
      >
        {/* Not a `MenuItem`: it is the answer to "who am I signed in as", not
            something to choose, and a focusable row that does nothing is a
            keyboard dead stop between the trigger and the only action. A list
            item all the same, because it is a child of the list. */}
        <Box
          component="li"
          sx={{ px: 2, py: 1.25, display: "flex", gap: 1.5, alignItems: "center" }}
        >
          <Monogram mark={mark} size={36} />
          <Box sx={{ minWidth: 0 }}>
            <Typography
              sx={{
                typography: "subtitle1",
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                lineHeight: 1.3,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {userName}
            </Typography>
            {/* Plain secondary text, not a status chip: a role is who you are,
                not a state that changes while you watch. There is no role
                switcher here for the reason `AppShell` gives — but there *is*
                a workspace switcher below, because that is a different kind of
                fact: a role is granted to you, a workspace is one you are
                standing in and can leave. */}
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {organizationName ? `${organizationName} · ${roleLabel}` : roleLabel}
            </Typography>
          </Box>
        </Box>

        <Divider component="li" />

        {/* Only when there is somewhere to go. One workspace renders nothing:
            a switcher listing the workspace you are already in is a control
            that teaches people the menu does not work. */}
        {switchable && (
          <ListSubheader
            disableSticky
            sx={{
              typography: "overline",
              color: "text.secondary",
              bgcolor: "transparent",
              lineHeight: 2,
              px: 2,
              pt: 1,
            }}
          >
            Switch workspace
          </ListSubheader>
        )}
        {switchable && (organizations || []).map((o) => {
          const here = o.organization_id === currentOrganizationId;
          return (
            <MenuItem
              key={o.organization_id}
              selected={here}
              // `selected` is presentational on a `menuitem` — MUI derives
              // `aria-checked` only for the checkable roles — so without this
              // the workspace you are in is marked by a tint and a tick that
              // is decorative, and a screen reader is told nothing at all.
              aria-current={here ? true : undefined}
              onClick={() => {
                setAnchor(null);
                // The current workspace is rendered so the list is a
                // complete answer to "where can I be", and clicking it does
                // nothing rather than posting a switch to where you already
                // are — which would mint a second session for no reason.
                if (!here) onSwitchOrganization?.(o.organization_id);
              }}
              sx={{ minHeight: TOUCH_TARGET, mx: 0.5, borderRadius: 1 }}
            >
              <ListItemIcon sx={ICON_GUTTER}>
                {here ? <CheckRounded fontSize="small" /> : null}
              </ListItemIcon>
              <ListItemText
                primary={o.name || o.organization_id}
                secondary={ROLE_LABEL[o.role] || o.role}
                slotProps={{
                  primary: {
                    sx: { typography: "subtitle1", fontFamily: "var(--font-heading)" },
                  },
                  secondary: { sx: { typography: "caption" } },
                }}
              />
            </MenuItem>
          );
        })}
        {switchable && <Divider component="li" sx={{ mt: 0.5 }} />}

        <MenuItem
          onClick={() => {
            setAnchor(null);
            onSignOut();
          }}
          sx={{ minHeight: TOUCH_TARGET, mt: 0.5, mx: 0.5, borderRadius: 1 }}
        >
          <ListItemIcon sx={ICON_GUTTER}>
            <LogoutOutlined fontSize="small" />
          </ListItemIcon>
          <ListItemText
            primary="Sign out"
            slotProps={{
              primary: {
                sx: { typography: "subtitle1", fontFamily: "var(--font-heading)" },
              },
            }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}
