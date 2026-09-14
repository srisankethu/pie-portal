// What the group filter does that `CompanyFilter` deliberately does not.
//
// The two controls look alike and behave oppositely, which is exactly the
// confusion worth pinning. `CompanyFilter` narrows rows already in the browser
// and must never touch a server-computed total. This one holds a slug, hands it
// to the server, and the screen refetches — so every figure behind it is
// recomputed inside the group.
//
// Two properties belong to the select itself, and both would fail quietly:
//
// - **The size is on every option.** A group is a set; "which set" is half of
//   any answer computed over it.
// - **A value the options do not contain is still shown.** The selection comes
//   from the URL now, so it can name a group that was archived since the link
//   was sent, or one this reader may not see. A select whose value matches no
//   item renders blank, which says "the whole book" over a screen showing the
//   server's refusal.
//
// The rest — who holds the selection, when the control renders at all, and that
// a failed load leaves the screen working — moved to `groupScope.test.tsx` with
// the state it tests.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GroupFilter } from "./GroupFilter";
import { aGroup } from "../test/groups";

describe("GroupFilter", () => {
  it("renders nothing when there is nothing to choose from", () => {
    const { container } = render(
      <GroupFilter value="" onChange={() => {}} options={[]} show={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names each group and how many are in it", () => {
    render(<GroupFilter value="aerospace" onChange={() => {}} show
                        options={[aGroup({ members: 12 })]} />);
    expect(screen.getByText(/Aerospace/)).toHaveTextContent("12");
  });

  it("shows a selection the options do not contain, so it can be cleared", () => {
    // A link sent last month naming a group archived since. The page behind
    // this is showing the server's "no such group"; the control must not read
    // as "the whole book" over it.
    render(<GroupFilter value="retired-set" onChange={() => {}} show
                        options={[aGroup()]} />);
    expect(screen.getByRole("combobox")).toHaveTextContent("retired-set");
  });

  it("offers the whole book beside the groups", () => {
    render(<GroupFilter value="" onChange={() => {}} show options={[aGroup()]} />);
    // The options only exist in the DOM once the select is opened, and MUI
    // opens on `mousedown` rather than on a click.
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const list = screen.getByRole("listbox");
    expect(within(list).getByRole("option", { name: "All" })).toBeTruthy();
  });
});
