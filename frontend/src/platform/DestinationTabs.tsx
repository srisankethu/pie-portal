/** The tab strip inside a destination — Money's five positions, Setup's five
 *  surfaces — plus the overflow that keeps the rest reachable.
 *
 * Nine screens became one destination twice over, and the honest way to do that
 * is not to put nine tabs in a strip: that is the thirty-four-item nav again,
 * two levels down. A handful of tabs is what a working day uses and the rest
 * are real screens somebody opens when something specific is wrong, so they are
 * in a `More` menu — one click, named, and the strip shows them as current when
 * you are standing on one, which is the part an overflow usually gets wrong.
 *
 * **"Four" is written here as "five" on purpose, and the count is the point.**
 * Both strips grew a fifth primary tab while this paragraph still said four,
 * and the strip that was comfortable at four is the one that broke on a phone —
 * see the note on `allowScrollButtonsMobile` below for what it broke into and
 * what it was measured with. A number in a docstring that nobody updates is how
 * a layout budget gets spent without anyone deciding to spend it.
 *
 * **Two shapes.** At `sm` and up, the strip. Below it, one button naming where
 * you are and a menu holding every destination — because a scrollable strip on
 * a 390px screen hides tabs behind a swipe that nothing advertises.
 *
 * Every tab is a `Link` to the screen's own address, in both shapes. Tabs here
 * are navigation, not local state: reload, Back, bookmark and "open in a new
 * tab" all work because the URL never stopped being the screen. It is also why
 * these screens did not have to change to be tabbed — `route.ts` already gave
 * each one an address, and this only groups them.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";
import { Link as RouterLink } from "react-router-dom";

import type { AppAbility } from "./ability";
import { visibleTabs, type DestinationTab } from "./destinations";
import { TOUCH } from "./kit";
import { pathFor, type Screen } from "./route";

export default function DestinationTabs({
  tabs,
  current,
  ability,
  isOperator = false,
  label,
}: {
  tabs: readonly DestinationTab[];
  current: Screen;
  ability: AppAbility;
  isOperator?: boolean;
  /** What this strip is a strip of, for a screen reader that cannot see the
   *  heading above it. */
  label: string;
}) {
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const open = visibleTabs(tabs, ability, isOperator);
  const primary = open.filter((t) => !t.secondary);
  const overflow = open.filter((t) => t.secondary);
  const here = open.find((t) => t.screen === current);
  // A tab from the overflow joins the strip while it is the screen being read.
  // Without this the strip would show nothing selected on a screen it owns,
  // which reads as "you are nowhere" — the state a nav exists to prevent.
  const strip = here && here.secondary ? [...primary, here] : primary;

  // Below `sm` there is no strip: one button naming where you are, one menu
  // holding everywhere you could go.
  //
  // **The scrollable strip does not survive a phone, and it was measured
  // rather than argued.** At 390px the Setup strip overflowed its scroller by
  // 98px with `Catalogue` and `Groups` clipped out of frame; turning MUI's
  // hidden scroll arrows back on made them visible and made it *worse* — the
  // arrows cost about 80px, so the overflow grew to 178px and at 360px a third
  // tab fell off. A control that needs two taps on an arrow to reveal a tab is
  // not a tab strip, it is a menu with extra steps and no label.
  //
  // So on a phone it is the menu, admitted. Every item is still a `RouterLink`
  // — §9's rule that anything going somewhere is a link, which a `Select`
  // would have broken — and the button only opens it, which is a press that
  // does not leave the screen.
  //
  // `down("sm")` is the same breakpoint `kit.FormDialog` reads, deliberately:
  // `ui-standards.md` §12 allows three in this codebase and a fourth invented
  // here would be a fourth place a phone starts.
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));

  if (phone) {
    return (
      <Box sx={{ borderBottom: "1px solid var(--color-divider)", mb: 2, pb: 1 }}>
        <Button
          fullWidth
          onClick={(e) => setMenu(e.currentTarget)}
          endIcon={<ExpandMoreOutlined />}
          aria-haspopup="menu"
          aria-label={label}
          sx={{ ...TOUCH, justifyContent: "space-between", textTransform: "none",
                fontFamily: "var(--font-heading)", fontSize: 15, fontWeight: 600,
                color: "text.primary", px: 1 }}
        >
          {here?.label ?? label}
        </Button>
        <Menu
          anchorEl={menu} open={Boolean(menu)} onClose={() => setMenu(null)}
          // Full-bleed, because a menu narrower than the button that opened it
          // reads as a different control than the one pressed.
          slotProps={{ paper: { sx: { width: "calc(100vw - 32px)" } } }}
        >
          {open.map((t) => (
            <MenuItem
              key={t.screen}
              component={RouterLink}
              to={pathFor(t.screen)}
              selected={t.screen === current}
              onClick={() => setMenu(null)}
              sx={TOUCH}
            >
              {t.label}
            </MenuItem>
          ))}
        </Menu>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1,
               borderBottom: "1px solid var(--color-divider)", mb: 2 }}>
      <Tabs
        value={strip.some((t) => t.screen === current) ? current : false}
        aria-label={label}
        variant="scrollable"
        scrollButtons="auto"
        // **The fix for "items only appear when scrolling", and it is one
        // word.** MUI renders this strip's two scroll arrows and then hides
        // both below `sm` unless asked otherwise, so on a phone the tabs past
        // the fold were reachable only by swiping a strip that gave no sign it
        // could be swiped.
        //
        // Measured rather than reasoned about, by `e2e/.shots/tabstrip.mjs`,
        // because the source reads as correct either way. At 390px the Setup
        // strip overflows its scroller by 98px with `Catalogue` and `Groups`
        // clipped out of it, and `scrollButtons=2 hidden=2` — the controls
        // existed and were invisible. At 768px and above nothing overflows at
        // all, which is why this survived: it is a defect at one end of the
        // range and perfect at the other.
        //
        // It is also the drawer's lesson a second time, and `CLAUDE.md`'s
        // digest states it: a gesture is never the only way in, because nothing
        // on screen says it is there.
        allowScrollButtonsMobile
        // `minWidth: 0` is *not* what fixed the above — the strip was measured
        // without it and neither scrolled the page sideways nor pushed `More`
        // out of frame at any width from 360px to 1440px. It stays as the floor
        // a `flex: 1` item should carry: `min-width: auto` is the default, it
        // means "never shrink below my content", and the day a sixth tab or a
        // longer label makes that bite, it bites as a page that scrolls
        // sideways rather than as a strip that scrolls itself.
        sx={{ minHeight: 40, flex: 1, minWidth: 0 }}
      >
        {strip.map((t) => (
          <Tab
            key={t.screen}
            value={t.screen}
            label={t.label}
            component={RouterLink}
            to={pathFor(t.screen)}
            sx={{ minHeight: 40, textTransform: "none",
                  fontFamily: "var(--font-heading)", fontSize: 14.5, fontWeight: 600 }}
          />
        ))}
      </Tabs>
      {overflow.length > 0 && (
        <>
          <Button
            size="small"
            onClick={(e) => setMenu(e.currentTarget)}
            endIcon={<ExpandMoreOutlined />}
            aria-haspopup="menu"
            sx={{ textTransform: "none", color: "text.secondary", flex: "none" }}
          >
            More
          </Button>
          <Menu anchorEl={menu} open={Boolean(menu)} onClose={() => setMenu(null)}>
            {overflow.map((t) => (
              <MenuItem
                key={t.screen}
                component={RouterLink}
                to={pathFor(t.screen)}
                selected={t.screen === current}
                onClick={() => setMenu(null)}
              >
                {t.label}
              </MenuItem>
            ))}
          </Menu>
        </>
      )}
    </Box>
  );
}
