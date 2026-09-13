// What the group filter does that `CompanyFilter` deliberately does not.
//
// The two controls look alike and behave oppositely, which is exactly the
// confusion worth pinning. `CompanyFilter` narrows rows already in the browser
// and must never touch a server-computed total. This one holds a slug, hands it
// to the server, and the screen refetches — so every figure behind it is
// recomputed inside the group.
//
// Three properties, each the sort that would fail quietly:
//
// - **A selection the control is not rendering must not still be filtering.**
//   Harmless in `CompanyFilter`, where a stale value only hides rows. Here the
//   value is a request parameter, so a stale one keeps narrowing an answer with
//   no control on screen to reset it — a screen showing three accounts of four
//   hundred and nothing saying why.
// - **A failed load leaves the screen working.** The directory behind this does
//   not need the filter, and an error banner over a working screen because an
//   optional control could not load is how people learn to ignore banners.
// - **The size is on every option.** A group is a set; "which set" is half of
//   any answer computed over it.

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ALL_GROUPS, GroupFilter, useGroupFilter } from "./GroupFilter";
import type { EntityGroup } from "./types";

const listGroups = vi.fn();
vi.mock("./api", () => ({ papi: { listGroups: (...a: unknown[]) => listGroups(...a) } }));

afterEach(() => listGroups.mockReset());

function group(over: Partial<EntityGroup> = {}): EntityGroup {
  return {
    slug: "aerospace", name: "Aerospace", description: null,
    entity_kind: "CUSTOMER", entity_label: "Customers", membership: "ROSTER",
    visibility: "OPERATIONAL", group_version: "gr_abc1234567", members: 12,
    created_by: "M. Rao", archived: false, updated_at: null,
    ...over,
  };
}

describe("useGroupFilter", () => {
  it("offers the groups the server returned", async () => {
    listGroups.mockResolvedValue({ groups: [group()], kinds: [], may_edit: true,
                                   empty_reason: null });
    const { result } = renderHook(() => useGroupFilter("tok", "CUSTOMER"));

    await waitFor(() => expect(result.current.show).toBe(true));
    expect(result.current.options).toHaveLength(1);
    expect(listGroups).toHaveBeenCalledWith("tok", "CUSTOMER");
  });

  it("reports nothing to show when the workspace has drawn no groups", async () => {
    listGroups.mockResolvedValue({ groups: [], kinds: [], may_edit: true,
                                   empty_reason: "none yet" });
    const { result } = renderHook(() => useGroupFilter("tok", "VENDOR"));

    await waitFor(() => expect(listGroups).toHaveBeenCalled());
    expect(result.current.show).toBe(false);
    // And the value it would send is the whole book, not a stale slug.
    expect(result.current.group).toBe(ALL_GROUPS);
  });

  it("keeps the screen working when the group list cannot be loaded", async () => {
    listGroups.mockRejectedValue(new Error("503"));
    const { result } = renderHook(() => useGroupFilter("tok", "PRODUCT"));

    await waitFor(() => expect(listGroups).toHaveBeenCalled());
    expect(result.current.show).toBe(false);
    expect(result.current.group).toBe(ALL_GROUPS);
  });

  it("stops filtering when there is no control to reset", async () => {
    // The property that matters most, and the one the browser-side filter does
    // not need: the selection is a server parameter.
    listGroups.mockResolvedValue({ groups: [], kinds: [], may_edit: true,
                                   empty_reason: null });
    const { result } = renderHook(() => useGroupFilter("tok", "CUSTOMER"));
    await waitFor(() => expect(listGroups).toHaveBeenCalled());

    act(() => result.current.setGroup("aerospace"));

    expect(result.current.group).toBe(ALL_GROUPS);
  });
});

describe("GroupFilter", () => {
  it("renders nothing when there is nothing to choose from", () => {
    const { container } = render(
      <GroupFilter value="" onChange={() => {}} options={[]} show={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names each group and how many are in it", () => {
    render(<GroupFilter value="aerospace" onChange={() => {}} show
                        options={[group({ members: 12 })]} />);
    expect(screen.getByText(/Aerospace/)).toHaveTextContent("12");
  });
});
