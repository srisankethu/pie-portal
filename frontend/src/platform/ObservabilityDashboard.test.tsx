// What this screen must not do, which is invent a number the server declined to
// state.
//
// Every assertion below is a regression. The screen had never been reached — no
// route, no nav entry, no importer — so none of these had ever run in a
// browser: a null bottleneck crashed the render, a null error rate drew as a
// green 0%, a null throughput as 0.0 rec/s, and two server-truncated lists drew
// as if they were the whole of what had gone wrong.
//
// The grids themselves are not asserted here. `DataGrid` lazy-loads ag-grid and
// these panels' honesty lives in the prose around it — how much of a capped
// list is on screen, and which of the two tenant scopes this is.
import { act, render, screen, fireEvent } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { papi } from "./api";
import { ObservabilityDashboard } from "./ObservabilityDashboard";
import type {
  JobFailure, ObservabilityDashboard as Dashboard, PlatformSession, RunIssue,
} from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "SALES_MANAGER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

const NOW = "2026-09-05T06:30:00Z";

function issue(n: number, status = "FAILED"): RunIssue {
  return {
    sync_run_id: `run_${n}`, connection_id: "SLS Engineers", status,
    error: "Zoho refused the refresh token", timestamp: NOW,
  };
}

/** The same row under `jobs`, where it carries the kind that produced it. */
function failure(n: number, status = "FAILED"): JobFailure {
  return { ...issue(n, status), job_kind: "erp_sync" };
}

/** The payload with everything measured and nothing truncated. Each test
 *  overrides only the block it is about. */
function payload(over: Partial<Dashboard> = {}): Dashboard {
  return {
    timestamp: NOW,
    health: {
      timestamp: NOW, status: "healthy",
      components: {
        database: { name: "database", status: "healthy", timestamp: NOW,
                    message: "Responding in 4 ms", details: {} },
      },
    },
    load: {
      timestamp: NOW,
      api: { requests_total: 120, active_requests: null, basis: "This worker's own counters." },
      database: { queries_total: 900, basis: "This worker's queries since it started." },
      background: { active_jobs: 0, active_syncs: 0, stalled: 0, total_active: 0,
                    basis: "Live sync runs for this organization." },
    },
    capacity: {
      timestamp: NOW,
      components: [{
        name: "api_requests", current: 0.2, status: "healthy", percentage: 20,
        safe_capacity_multiplier: 3.5, threshold_warning: 0.7,
        threshold_critical: 0.9, basis: "This API worker's own counters.",
      }],
      bottleneck: { component: "api_requests", current: 0.2, status: "healthy" },
      safe_capacity_headroom: { multiplier: 3.5, message: "Can handle 3.5x current load." },
      unmeasured: [],
      recommended_action: "All systems within normal parameters",
    },
    api: {
      timestamp: NOW, scope: "worker", worker: "w1", counting_since: NOW,
      observed_minutes: 30, basis: "Cumulative for this API worker.",
      requests_total: 120, errors_total: 3, error_rate: 2.5,
      latency_ms: { p50: 12, p95: 48, p99: 90 },
    },
    database: {
      timestamp: NOW, connections: { active: 2 }, queries: { total: 900, errors: 0 },
      latency_ms: { p50: 1, p95: 4, p99: 9 },
    },
    jobs: {
      timestamp: NOW, source: "sync_runs", job_kinds: ["erp_sync"],
      active: { count: 0, by_phase: {} },
      stalled: { count: 0, by_phase: {}, detail: "" },
      history: { last_run_at: NOW, last_successful_run_at: NOW, ever_run: true,
                 basis: "Most recent run of any status" },
      recent_24h: { basis: "runs that started or finished in the last 24 hours",
                    completed: 4, partial: 0, failed: 3, total_records_processed: 90 },
      failures: [failure(1), failure(2), failure(3)],
    },
    syncs: {
      timestamp: NOW, source: "sync_runs",
      active: { count: 0, by_phase: {}, by_connection: {} },
      stalled: { count: 0, by_phase: {}, detail: "" },
      history: { last_run_at: NOW, last_successful_run_at: NOW, ever_run: true,
                 basis: "Most recent run of any status" },
      recent_24h: {
        basis: "runs that started or finished in the last 24 hours",
        completed: 4, partial: 0, failed: 1, total_records_fetched: 900,
        total_records_processed: 890, throughput_records_per_sec: 12.5,
        throughput_basis: "Over 4 finished runs.",
      },
      issues: [issue(9)],
    },
    tenants: {
      timestamp: NOW, tenants: [{ organization_id: "org_pie", signals_generated: 12 }],
      total_tenants: 1, scope: "OWN_ORGANIZATION",
    },
    ...over,
  };
}

/** jsdom has no `matchMedia`, and MUI's fallback answers `false` to everything —
 *  which would leave `DataGrid` on the wide path fetching ag-grid. Nothing here
 *  asserts a grid's contents, so either path is fine; this only keeps the stub
 *  from being missing when MUI reaches for it. */
function stubMedia() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
}

async function show(data: Dashboard) {
  stubMedia();
  vi.spyOn(papi, "observability").mockResolvedValue(data);
  render(
    <ThemeProvider theme={createTheme()}>
      <ObservabilityDashboard session={SESSION} />
    </ThemeProvider>);
  // The heading only appears once the first poll has resolved.
  return screen.findByText("System health");
}

/** Move to a tab by its label. */
function openTab(label: string) {
  fireEvent.click(screen.getByRole("tab", { name: label }));
}

describe("capacity nothing could measure", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders at all, and says the capacity is unknown", async () => {
    // The shape `get_overall_capacity` returns when no component reported a
    // usable figure. Every `.toFixed` on this screen used to run straight
    // through it, so this is the first render a fresh deployment would get.
    await show(payload({
      capacity: {
        timestamp: NOW,
        components: [{
          name: "workers", current: null, status: "unknown", percentage: null,
          safe_capacity_multiplier: null, threshold_warning: 0.7,
          threshold_critical: 0.9, basis: "No worker reported a figure.",
        }],
        bottleneck: null,
        safe_capacity_headroom: null,
        unmeasured: ["workers"],
        recommended_action: "Capacity is unknown: no component reported a usable figure.",
      },
    }));

    expect(screen.getByText("Capacity is unknown")).toBeInTheDocument();
    // The chip standing in for the bar, and the sentence naming which
    // components the headroom claim does not rest on.
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.getByText(/Not measured, and therefore not in the figure above/))
      .toBeInTheDocument();
    // The reassuring shape, in either of the two ways it used to appear.
    expect(screen.queryByText(/0\.0×/)).toBeNull();
    expect(screen.queryByText("0.0%")).toBeNull();
  });
});

describe("an error rate with no denominator", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says it is not known, never 0%", async () => {
    await show(payload({
      api: {
        timestamp: NOW, scope: "worker", worker: "w1", counting_since: NOW,
        observed_minutes: 0.5, basis: "Cumulative for this API worker.",
        requests_total: 0, errors_total: 0, error_rate: null,
        latency_ms: { p50: null, p95: null, p99: null },
      },
    }));
    openTab("Load");

    // Three figures the server declined to state, and none of them a zero.
    expect(screen.getAllByText("Not known")).toHaveLength(3);
    expect(screen.queryByText("0.00%")).toBeNull();
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.getByText(/no requests, so a rate would have no denominator/))
      .toBeInTheDocument();
  });

  it("still shows a real zero when the worker measured one", async () => {
    // The reason this uses `??` and not `||`: 0% is a measurement, and the
    // operator that stood here could not tell it from the null above.
    await show(payload({
      api: { ...payload().api, errors_total: 0, error_rate: 0,
             latency_ms: { p50: 0, p95: 4, p99: 9 } },
    }));
    openTab("Load");

    expect(screen.getByText("0.00%")).toBeInTheDocument();
    expect(screen.getByText("0 ms")).toBeInTheDocument();
  });
});

describe("a list the server truncated", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says how much of it is on screen", async () => {
    // Ten failures sent, twenty-three in the window: the panel must not read as
    // ten problems.
    const base = payload();
    await show(payload({
      jobs: {
        ...base.jobs,
        recent_24h: { ...base.jobs.recent_24h, failed: 18, partial: 5 },
        failures: Array.from({ length: 10 }, (_, i) => failure(i)),
      },
    }));
    openTab("Background jobs");

    expect(screen.getByText(
      "Showing the 10 most recent of 23 in the last 24 hours.")).toBeInTheDocument();
  });

  it("says so plainly when it is showing all of them", async () => {
    await show(payload());
    openTab("Background jobs");
    expect(screen.getByText("Showing all 3 in the last 24 hours.")).toBeInTheDocument();
  });

  it("does not claim a truncation when there is nothing to show", async () => {
    const base = payload();
    await show(payload({
      jobs: { ...base.jobs, recent_24h: { ...base.jobs.recent_24h, failed: 0, partial: 0 },
              failures: [] },
    }));
    openTab("Background jobs");
    expect(screen.getByText(
      "Showing no failed or partial runs in the last 24 hours.")).toBeInTheDocument();
  });
});

describe("a throughput no finished run could answer", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says it is not known, never 0.0 rec/s", async () => {
    const base = payload();
    await show(payload({
      syncs: {
        ...base.syncs,
        recent_24h: { ...base.syncs.recent_24h, throughput_records_per_sec: null,
                      throughput_basis: "No finished run carried both a duration and a count." },
      },
    }));
    openTab("ERP sync");

    expect(screen.getByText("Not known")).toBeInTheDocument();
    expect(screen.queryByText("0.0 rec/s")).toBeNull();
  });
});

describe("which tenant list this is", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("reads the server's scope rather than inferring it from the row count", async () => {
    // One row, and it is one row because row-level security bound the query —
    // not because the deployment has one customer. The two are the same row
    // count and different facts.
    await show(payload());
    openTab("Tenant usage");
    expect(screen.getByText(/row-level security scoped it/)).toBeInTheDocument();
  });

  it("says when the query was not scoped", async () => {
    await show(payload({
      tenants: {
        timestamp: NOW, total_tenants: 2, scope: "ALL_ORGANIZATIONS",
        tenants: [
          { organization_id: "org_pie", signals_generated: 12 },
          { organization_id: "org_other", signals_generated: 4 },
        ],
      },
    }));
    openTab("Tenant usage");
    expect(screen.getByText(/Every organization on this deployment/)).toBeInTheDocument();
  });
});

describe("a poll that fails after something is on screen", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says the figures have stopped refreshing rather than showing them as live", async () => {
    stubMedia();
    vi.spyOn(papi, "observability")
      .mockResolvedValueOnce(payload())
      .mockRejectedValue(new Error("502 Bad Gateway"));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(
      <ThemeProvider theme={createTheme()}>
        <ObservabilityDashboard session={SESSION} />
      </ThemeProvider>);
    await screen.findByText("System health");

    // Inside `act`: the poll this fires sets state, and React warns — rightly —
    // about a render nothing waited for.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(await screen.findByText("These figures are not being refreshed"))
      .toBeInTheDocument();
    // And the last good reading is still there rather than replaced by an error.
    expect(screen.getByText("Components")).toBeInTheDocument();
    vi.useRealTimers();
  });
});
