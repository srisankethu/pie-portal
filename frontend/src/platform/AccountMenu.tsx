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
 */
import { useState } from "react";

import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import LogoutOutlined from "@mui/icons-material/LogoutOutlined";

import { TOUCH, TOUCH_TARGET } from "./kit";

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

export default function AccountMenu({
  userName,
  roleLabel,
  onSignOut,
}: {
  userName: string;
  roleLabel: string;
  onSignOut: () => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const open = Boolean(anchor);
  const mark = initials(userName);

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
        <Avatar
          sx={{
            width: 32,
            height: 32,
            fontFamily: "var(--font-heading)",
            fontSize: 13,
            fontWeight: 700,
            bgcolor: "primary.main",
            color: "primary.contrastText",
          }}
        >
          {mark}
        </Avatar>
        <Box sx={{ display: { xs: "none", sm: "block" }, textAlign: "left", lineHeight: 1.2 }}>
          <Typography
            component="span"
            sx={{
              display: "block",
              fontFamily: "var(--font-heading)",
              fontWeight: 600,
              fontSize: 13,
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
          sx={{
            display: { xs: "none", sm: "block" },
            fontSize: 18,
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
            sx: { mt: 0.5, minWidth: 232, maxWidth: "calc(100vw - 16px)", borderRadius: 2 },
          },
        }}
      >
        {/* Not a `MenuItem`: it is the answer to "who am I signed in as", not
            something to choose, and a focusable row that does nothing is a
            keyboard dead stop between the trigger and the only action. */}
        <Box sx={{ px: 2, pt: 1.25, pb: 1.25, display: "flex", gap: 1.5, alignItems: "center" }}>
          <Avatar
            sx={{
              width: 36,
              height: 36,
              fontFamily: "var(--font-heading)",
              fontSize: 14,
              fontWeight: 700,
              bgcolor: "primary.main",
              color: "primary.contrastText",
            }}
          >
            {mark}
          </Avatar>
          <Box sx={{ minWidth: 0 }}>
            <Typography
              sx={{
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                fontSize: 14,
                lineHeight: 1.3,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {userName}
            </Typography>
            {/* Plain secondary text, not a status chip: a role is who you are,
                not a state that changes while you watch. There is no role
                switcher here for the reason `AppShell` gives. */}
            <Typography variant="caption" color="text.secondary">
              {roleLabel}
            </Typography>
          </Box>
        </Box>

        <Divider />

        <MenuItem
          onClick={() => {
            setAnchor(null);
            onSignOut();
          }}
          sx={{ minHeight: TOUCH_TARGET, mt: 0.5, mx: 0.5, borderRadius: 1 }}
        >
          <ListItemIcon sx={{ minWidth: 32 }}>
            <LogoutOutlined sx={{ fontSize: 19 }} />
          </ListItemIcon>
          <ListItemText
            primary="Sign out"
            slotProps={{ primary: { sx: { fontFamily: "var(--font-heading)", fontSize: 14 } } }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}