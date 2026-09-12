/** The tab strip inside a destination — Money's four positions, Setup's four
 *  surfaces — plus the overflow that keeps the rest reachable.
 *
 * Nine screens became one destination twice over, and the honest way to do that
 * is not to put nine tabs in a strip: that is the thirty-four-item nav again,
 * two levels down. Four tabs are what a working day uses and the rest are real
 * screens somebody opens when something specific is wrong, so they are in a
 * `More` menu — one click, named, and the strip shows them as current when you
 * are standing on one, which is the part an overflow usually gets wrong.
 *
 * Every tab is a `Link` to the screen's own address. Tabs here are navigation,
 * not local state: reload, Back, bookmark and "open in a new tab" all work
 * because the URL never stopped being the screen. It is also why these screens
 * did not have to change to be tabbed — `route.ts` already gave each one an
 * address, and this only groups them.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";
import { Link as RouterLink } from "react-router-dom";

import type { AppAbility } from "./ability";
import { visibleTabs, type DestinationTab } from "./destinations";
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

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1,
               borderBottom: "1px solid var(--color-divider)", mb: 2 }}>
      <Tabs
        value={strip.some((t) => t.screen === current) ? current : false}
        aria-label={label}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ minHeight: 40, flex: 1 }}
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
