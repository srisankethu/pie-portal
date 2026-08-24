// What the working capital sheet is allowed to draw.
//
// The geometry is pinned in `channel-layout.test.ts`; what is pinned here is
// the thing a layout test cannot reach — whether the panel *draws a channel it
// was not given*. That is the one failure mode of this screen: a width on this
// sheet is a number somebody funds a week against, and a book whose parties
// have too little settled history has no measured width at all. Inventing one
// would be indistinguishable, on screen, from measuring one.
//
// The rest is the arrangement the brief asked for and the invariants the panel
// inherits: the datum, the travelled route behind it, the callout on a real
// week, a table twin carrying both halves, and no score anywhere.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { CashJourney } from "./CashJourney";
import type { PlatformSession, Role } from "../types";

const { cashflow } = vi.hoisted(() => ({ cashflow: vi.fn() }));
vi.mock("../api", () => ({ papi: { cashflow } }));

// jsdom has neither, and the sheet measures its own container before it draws.
// Stubbed to a real width so the drawing renders rather than sitting at the
// zero-width placeholder every measured chart falls back to.
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
  Element.prototype.getBoundingClientRect = function rect() {
    return { width: 900, height: 420, top: 0, left: 0, right: 900, bottom: 420,
             x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});

function session(role: Role = "OWNER"): PlatformSession {
  return {
    token: "t", role, name: "O", user_id: "u1", organization_id: "org_x",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

const week = (startsOn: string, inflow: number, outflow: number,
              cumulative: number) => ({
  starts_on: startsOn, week: startsOn, inflow, outflow, net: inflow - outflow,
  cumulative, inflow_documents: 2, outflow_documents: 1,
});

/** Three committed weeks, three travelled ones, and a measured band. */
function response(over: Record<string, unknown> = {}) {
  const committed = [
    week("2026-09-07", 400000, 120000, 280000),
    week("2026-09-14", 60000, 700000, -360000),
    week("2026-09-21", 500000, 40000, 100000),
  ];
  return {
    currency: "INR",
    weeks: 3,
    as_of: "2026-09-04",
    horizon_ends_on: "2026-09-27",
    buckets: committed,
    net_over_horizon: 100000,
    lowest_cumulative: -360000,
    lowest_week_starts_on: "2026-09-14",
    requirement: -520000,
    scenarios: {
      best: { buckets: [
        week("2026-09-07", 400000, 0, 400000),
        week("2026-09-14", 60000, 500000, -40000),
        week("2026-09-21", 500000, 0, 460000)],
        lowest_cumulative: -40000, lowest_week_starts_on: "2026-09-14" },
      expected: { buckets: committed, lowest_cumulative: -360000,
                  lowest_week_starts_on: "2026-09-14" },
      worst: { buckets: [
        week("2026-09-07", 100000, 320000, -220000),
        week("2026-09-14", 0, 300000, -520000),
        week("2026-09-21", 300000, 40000, -260000)],
        lowest_cumulative: -520000, lowest_week_starts_on: "2026-09-14" },
    },
    basis: {
      share_measured: 0.82, outflow_share_measured: 0.4, outflow_shifted: true,
      customers_measured: 7, vendors_measured: 3, vendors_retimed: 0,
    },
    actual: {
      weeks: 3,
      starts_on: "2026-08-17",
      ends_on: "2026-09-04",
      observed_from: "2026-08-18",
      before_window: { inflow: 0, outflow: 0 },
      buckets: [
        { ...week("2026-08-17", 220000, 90000, -260000), partial: false },
        { ...week("2026-08-24", 90000, 40000, -210000), partial: false },
        { ...week("2026-08-31", 30000, 240000, 0), partial: true },
      ],
      empty_reason: null,
    },
    overdue: { inflow: 180000, outflow: 0 },
    undated: { inflow: 0, outflow: 0 },
    beyond_horizon: { inflow: 0, outflow: 0 },
    unscheduled: { open_sales_value: 0, open_purchase_value: 0 },
    unattributed: { inflow: 0, outflow: 0 },
    empty_reason: null,
    ...over,
  };
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CashJourney session={session()} />
    </QueryClientProvider>,
  );
}

const sheet = (container: HTMLElement) =>
  container.querySelector("svg.wcj-sheet") as SVGElement | null;

describe("the working capital journey", () => {
  it("draws the past, the datum and the committed channel", async () => {
    cashflow.mockResolvedValue(response());
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    const drawing = sheet(container) as SVGElement;
    // The route that arrived: cash that actually moved, heaviest line on the
    // sheet.
    expect(drawing.querySelector(".wcj-travelled")).not.toBeNull();
    // The reference the whole drawing is dimensioned from.
    expect(drawing.querySelector(".wcj-datum")).not.toBeNull();
    expect(drawing.textContent).toContain("NOW");
    // The committed channel, and the two edges bounding it.
    expect(drawing.querySelector(".wcj-channel")).not.toBeNull();
    expect(drawing.querySelectorAll(".wcj-edge")).toHaveLength(2);
    // Everybody at their own median, inside it.
    expect(drawing.querySelector(".wcj-expected")).not.toBeNull();
  });

  it("draws no channel at all when nothing has been measured", async () => {
    // The failure this file exists for. With no scenarios the server is saying
    // it cannot place these parties anywhere but their due dates; a width drawn
    // from that would be a funding figure with nothing behind it.
    cashflow.mockResolvedValue(response({
      scenarios: {},
      basis: { share_measured: 0, outflow_share_measured: 0,
               outflow_shifted: false, customers_measured: 0,
               vendors_measured: 0, vendors_retimed: 0 },
    }));
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    const drawing = sheet(container) as SVGElement;
    expect(drawing.querySelector(".wcj-channel")).toBeNull();
    expect(drawing.querySelectorAll(".wcj-edge")).toHaveLength(0);
    // The route on the documents' own dates survives — it is the one reading
    // that asserts nothing beyond what the source says.
    expect(drawing.querySelector(".wcj-terms")).not.toBeNull();
    expect(screen.getByText(/No channel is drawn/)).toBeInTheDocument();
    // And the envelope metric refuses rather than printing a zero width.
    expect(screen.getAllByText(/too little has settled/i).length)
      .toBeGreaterThan(0);
  });

  it("draws no route behind the datum when the server refused a history", async () => {
    // The same failure as the channel, on the other half of the sheet. Those
    // weeks come back as real zeros, so rendering them puts a flat, confident
    // route across the past asserting that no cash moved all quarter — which
    // is a claim, and not the one the server made.
    cashflow.mockResolvedValue(response({
      actual: {
        weeks: 3, starts_on: "2026-08-17", ends_on: "2026-09-04",
        observed_from: null, before_window: { inflow: 0, outflow: 0 },
        buckets: [
          { ...week("2026-08-17", 0, 0, 0), partial: false },
          { ...week("2026-08-24", 0, 0, 0), partial: false },
          { ...week("2026-08-31", 0, 0, 0), partial: true },
        ],
        empty_reason: "No customer receipt or supplier payment has synced.",
      },
    }));
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    expect(sheet(container)?.querySelector(".wcj-travelled")).toBeNull();
    expect(screen.getByText(/Nothing is drawn behind the datum/))
      .toBeInTheDocument();
    // The committed half is untouched by it.
    expect(sheet(container)?.querySelector(".wcj-channel")).not.toBeNull();
  });

  it("prints the server's own deepest point, with its week", async () => {
    cashflow.mockResolvedValue(response());
    mount();

    await waitFor(() => expect(
      screen.getByText("Next pressure point")).toBeInTheDocument());
    // −₹5,20,000 is `requirement`, taken from the response rather than
    // recomputed on the client.
    // Twice: in the headline sentence and on the metric. Both read the same
    // field, which is the point — a second answer would show up here.
    expect(screen.getAllByText("−₹5,20,000").length).toBeGreaterThan(0);
    expect(screen.getByText(/Deepest in the week of/)).toBeInTheDocument();
  });

  it("opens the callout on a real week and dimensions its envelope", async () => {
    cashflow.mockResolvedValue(response());
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    // The deepest committed week, which is where the sheet opens.
    expect(screen.getByText(/COMMITTED · WEEK OF/)).toBeInTheDocument();
    const facts = container.querySelector(".wcj-callout-facts") as HTMLElement;
    expect(facts.textContent).toContain("Expected");
    expect(facts.textContent).toContain("Envelope");
    expect(container.querySelector(".wcj-dimension")).not.toBeNull();
  });

  it("says movement, never a position", async () => {
    // The claim the whole panel rests on. PIE reads payments, not bank
    // balances; a sheet that implied a level would be the one defect here that
    // nobody could see.
    cashflow.mockResolvedValue(response());
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    expect(screen.getByText(/Movement, not a balance/)).toBeInTheDocument();
    expect(sheet(container)?.textContent).toContain("MOVEMENT");
  });

  it("carries both halves into the table twin, with the phase on the row", async () => {
    cashflow.mockResolvedValue(response());
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    const figure = container.querySelector(".viz-figure") as HTMLElement;
    // The accessible description is the whole series, both sides of the datum.
    const described = figure.querySelector("[role='img']");
    expect(described?.getAttribute("aria-label")).toContain("Travelled:");
    expect(described?.getAttribute("aria-label")).toContain("Committed:");
  });

  it("offers no score, no grade and no recommendation", async () => {
    // PIE is providing visual intelligence here, not an ERP's verdict. The
    // sheet reports measurements with dates on them; the moment one of them
    // becomes a rating, the drawing has been replaced by a summary of itself.
    cashflow.mockResolvedValue(response());
    const { container } = mount();

    await waitFor(() => expect(sheet(container)).not.toBeNull());
    expect(container.textContent).not.toMatch(
      /\b(risk score|score|rating|approved|recommend)/i);
  });
});
