/** A destination that holds several screens: its heading, its tabs, and them.
 *
 * A layout route rather than a wrapper written around each element, so the
 * heading and the strip are stated once for the five screens behind Money and
 * the eight behind Setup. The screens themselves are untouched — they keep
 * their own addresses, their own data and their own headings below this one,
 * which is what made grouping them a routing change rather than a rewrite.
 */
import { Outlet } from "react-router-dom";

import type { AppAbility } from "./ability";
import DestinationTabs from "./DestinationTabs";
import type { DestinationTab } from "./destinations";
import { SectionHeader } from "./kit";
import type { Screen } from "./route";

export default function DestinationLayout({
  title, sub, tabs, current, ability, isOperator,
}: {
  title: string;
  /** The question this destination answers, in one line. */
  sub: string;
  tabs: readonly DestinationTab[];
  current: Screen;
  ability: AppAbility;
  isOperator?: boolean;
}) {
  return (
    <>
      <SectionHeader title={title} sub={sub} />
      <DestinationTabs
        tabs={tabs} current={current} ability={ability}
        isOperator={isOperator} label={`${title} sections`}
      />
      <Outlet />
    </>
  );
}
