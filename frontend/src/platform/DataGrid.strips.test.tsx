// A strip that grows after first paint has to grow on screen too.
//
// The Quote Builder draws a line's problems as a strip under it, and the
// problems arrive in two waves: what the line itself knows, then what the
// quote checks add a moment later. ag-grid measures a row once and keeps the
// answer, and a strip's row survives the update — its id is its parent's — so
// a strip that went from one problem to three kept the one-problem height and
// drew the other two over the next line ("THIS PRICE DOES NOT COVER…" with
// "BELOW WHAT THIS CUSTOMER LAST PAID" printed across the line under it).
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataGrid } from "./DataGrid";

type R = { id: string; code: string; problems: string[] };
const STRIP = 64;

function grid(rows: R[]) {
  return (
    <div style={{ width: 1200 }}>
      <DataGrid<R>
        rows={rows}
        ariaLabel="Quote lines"
        columns={[{ field: "code", headerName: "Item", flex: 1 }]}
        getRowId={(r) => r.id}
        filters={false}
        renderRowDetail={(r) => (
          <div>{r.problems.map((p) => <div key={p}>{p}</div>)}</div>)}
        rowDetailHeight={(r) => r.problems.length * STRIP}
      />
    </div>
  );
}

const stripOf = (id: string) =>
  document.querySelector<HTMLElement>(`.ag-row[row-id="${id}:strip"]`);

describe("a row's strip", () => {
  it("is re-measured when the row gains problems after first paint", async () => {
    const one: R[] = [{ id: "a", code: "CNMG", problems: ["Below cost"] },
                      { id: "b", code: "fsdf", problems: ["Which item is this?"] }];
    const view = render(grid(one));
    await screen.findByText("Below cost");
    expect(stripOf("a")?.style.height).toBe(`${STRIP}px`);

    view.rerender(grid([
      { ...one[0], problems: ["Below cost", "Below last paid", "122 short"] }, one[1]]));
    await screen.findByText("122 short");
    await waitFor(() => expect(stripOf("a")?.style.height).toBe(`${3 * STRIP}px`));
  });
});
