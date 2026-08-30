// "No catalogue" must not render as zero.
//
// This is the §1 rule — absence of evidence is not a pass — at the one place a
// reader actually meets it. The server keeps `records: null` and `report: null`
// distinct from a count, and `pie_service.catalog_available` exists precisely to
// separate "the pack does not cover this item" from "nobody asked the pack".
// All of that is undone by a component that renders `{records}` as 0, or a
// parse rate as 0%, when nothing has been built — the screen would then say the
// catalogue covers none of the book, which is a different and false claim.
//
// The backend half is pinned in tests/decision_platform/test_catalog_surface.py.
// This is the half that a `?? 0` in a component could break with every server
// test still green.
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogScreen } from "./CatalogScreen";
import { papi } from "./api";
import type { CatalogStatus, PlatformSession } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function status(over: Partial<CatalogStatus> = {}): CatalogStatus {
  return {
    path: "/backend/data/products.jsonl",
    scope: "deployment",
    source: {
      available: true, reason: null,
      pie_parser_root: "/pie-parser",
      corpus: "/pie-parser/corpora/corpus.csv",
      pack: "/pie-parser/packs/org/zcnc",
    },
    exists: false,
    records: null,
    built_at: null,
    size_bytes: null,
    stamp: {},
    build: null,
    report: null,
    report_missing: null,
    auto_build: false,
    loaded: { index_loaded: false, ruleset_checksum: null },
    can_rebuild: true,
    ...over,
  };
}

const BUILT: Partial<CatalogStatus> = {
  exists: true,
  records: 6717,
  built_at: "2026-08-30T05:09:00Z",
  size_bytes: 13_300_000,
  stamp: {
    pack_id: "kennametal_widia", pack_version: "0.10.0",
    org_id: "zcnc", org_version: "0.10.0",
    ruleset_checksum: "f67131512eb97513", run_id: "2b5c96f97de49436",
    engine_version: "0.10.0", schema_version: "1.0.0",
  },
  build: {
    built_at: "2026-08-30T05:09:00Z", duration_s: 1.62,
    rows_read: 6717, emitted: 6717, quarantined: 0,
  },
  report: {
    total: 6717,
    by_family: { turning_insert: 2273, milling_insert: 897 },
    by_grammar: {}, by_flag: {},
    parse_rates: { turning_insert: 1, milling_insert: 1 },
    unresolved_family: 0,
    new_tokens_top: {},
  },
  loaded: { index_loaded: true, ruleset_checksum: "f67131512eb97513" },
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the decoded catalogue screen", () => {
  it("says NOT BUILT, and never renders a count or a rate for it", async () => {
    vi.spyOn(papi, "catalogStatus").mockResolvedValue(status());
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("NOT BUILT")).toBeTruthy();
    // The words a reader needs: not built, and what that means for resolution.
    expect(screen.getByText(/answer UNKNOWN until a catalogue is built/)).toBeTruthy();

    // Nothing that reads as coverage. Asserted on the *phrase* rather than on
    // "0 …" in `document.body.textContent`: the chip abuts the count there
    // ("NOT BUILT0 decoded records"), so a `\b0` anchor finds no word boundary
    // between "T" and "0" and matches nothing — that version of this test
    // passed against a component mutated to render `{records ?? 0}`. The
    // phrase only ever accompanies a real count, so its absence is the claim.
    expect(screen.queryByText(/decoded records/)).toBeNull();
    // Likewise no per-family census or parse rate: those are the report's, and
    // there is no report.
    expect(screen.queryByLabelText("Per-family parse rates")).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/%/);
    expect(screen.queryByText("READY")).toBeNull();
  });

  it("reports the provenance a resolution is stamped with, once built", async () => {
    vi.spyOn(papi, "catalogStatus").mockResolvedValue(status(BUILT));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("READY")).toBeTruthy();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
    // The two facts that say WHICH catalogue answered — not just how many rows.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("2b5c96f97de49436")).toBeTruthy();
    expect(screen.getByText(/kennametal_widia v0\.10\.0/)).toBeTruthy();
  });

  it("names the specific reason a build is impossible, and offers no button", async () => {
    vi.spyOn(papi, "catalogStatus").mockResolvedValue(status({
      source: {
        available: false,
        reason: "PIE corpus not found at /pie-parser/corpora/corpus.csv. The "
              + "engine is present but this corpus file is not — check PIE_CORPUS.",
        pie_parser_root: "/pie-parser",
        corpus: "/pie-parser/corpora/corpus.csv",
        pack: "/pie-parser/packs/org/zcnc",
      },
    }));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("SOURCE MISSING")).toBeTruthy();
    expect(screen.getByText(/check PIE_CORPUS/)).toBeTruthy();
    // Offering a button that cannot succeed teaches people the button is broken.
    expect(screen.queryByRole("button", { name: /Build catalogue/ })).toBeNull();
  });

  it("withholds the build control from anyone the server did not clear", async () => {
    vi.spyOn(papi, "catalogStatus").mockResolvedValue(
      status({ ...BUILT, can_rebuild: false }));
    render(<CatalogScreen session={SESSION} />);

    await waitFor(() => expect(screen.getByText("READY")).toBeTruthy());
    // The state is readable — the control is not. The server refuses the POST
    // as well; this only keeps the screen honest about it.
    expect(screen.queryByRole("button", { name: /Rebuild catalogue/ })).toBeNull();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
  });
});
