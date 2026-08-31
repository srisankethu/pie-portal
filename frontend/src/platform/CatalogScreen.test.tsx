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
// The second rule this screen carries is per-company: one company's provenance
// must never be drawn against another's name. Two companies are rendered in
// every fixture here for that reason.
//
// The backend half is pinned in tests/decision_platform/test_catalog_surface.py.
// This is the half that a `?? 0` in a component could break with every server
// test still green.
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogScreen } from "./CatalogScreen";
import { papi } from "./api";
import type { CompanyCatalogue, CompanyCatalogues, PlatformSession } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function company(over: Partial<CompanyCatalogue> = {}): CompanyCatalogue {
  return {
    connection_id: "conn-a",
    label: "SLS Engineers",
    enabled: true,
    scope: "company",
    pack_id: "zcnc",
    pack_resolved: true,
    pack: null,
    exists: false,
    built_but_missing_on_disk: false,
    records: null,
    rows_read: null,
    quarantined: null,
    duration_s: null,
    built_at: null,
    built_by: null,
    report: null,
    stamp: {},
    corpus: null,
    stale: false,
    ...over,
  };
}

const BUILT: Partial<CompanyCatalogue> = {
  exists: true,
  records: 6717,
  rows_read: 6717,
  quarantined: 0,
  duration_s: 1.62,
  built_at: "2026-08-30T05:09:00Z",
  built_by: "s.menon@pie.example",
  stamp: {
    pack_id: "kennametal_widia", pack_version: "0.10.0",
    org_id: "zcnc", org_version: "0.10.0",
    ruleset_checksum: "f67131512eb97513", run_id: "2b5c96f97de49436",
    engine_version: "0.10.0", schema_version: "1.0.0",
  },
  corpus: {
    corpus_id: "cor1", filename: "item-master.csv", size_bytes: 2_400_000,
    sha256: "abc", uploaded_at: "2026-08-30T05:00:00Z",
    uploaded_by: "s.menon@pie.example",
  },
  report: {
    total: 6717,
    by_family: { turning_insert: 2273, milling_insert: 897 },
    by_grammar: {}, by_flag: {},
    parse_rates: { turning_insert: 1, milling_insert: 1 },
    unresolved_family: 0,
    new_tokens_top: {},
  },
};

function view(companies: CompanyCatalogue[],
              over: Partial<CompanyCatalogues> = {}): CompanyCatalogues {
  return {
    scope: "company",
    companies,
    packs: [{ id: "zcnc", path: "/pie-parser/packs/org/zcnc" }],
    source: {
      available: true, reason: null,
      pie_parser_root: "/pie-parser",
      corpus: "/pie-parser/corpora/corpus.csv",
      pack: "/pie-parser/packs/org/zcnc",
    },
    max_corpus_bytes: 33_554_432,
    can_manage: true,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the decoded catalogue screen", () => {
  it("says NOT BUILT, and never renders a count or a rate for it", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ corpus: BUILT.corpus })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("NOT BUILT")).toBeTruthy();

    // Nothing that reads as coverage. Asserted on the *phrase* rather than on
    // "0 …" in `document.body.textContent`: the chip abuts the count there
    // ("NOT BUILT0 decoded records"), so a `\b0` anchor finds no word boundary
    // between "T" and "0" and matches nothing — that version of this test
    // passed against a component mutated to render `{records ?? 0}`. The
    // phrase only ever accompanies a real count, so its absence is the claim.
    expect(screen.queryByText(/decoded records/)).toBeNull();
    // Likewise no per-family census or parse rate: those are the report's, and
    // there is no report.
    expect(screen.queryByLabelText(/Per-family parse rates/)).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/%/);
    expect(screen.queryByText("READY")).toBeNull();
  });

  it("names what is missing rather than saying only 'not built'", async () => {
    // No export uploaded, and no pack chosen. "You have not built it" is not
    // useful advice to somebody who has nothing to build it from.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ pack_id: null, pack_resolved: false })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("NO PACK CHOSEN")).toBeTruthy();
    expect(screen.getByText(/answer UNKNOWN until an export is uploaded/)).toBeTruthy();
    // Nothing to build from, so the build control cannot succeed and is off.
    expect(screen.getByRole("button", { name: "Build" })).toHaveProperty("disabled", true);
  });

  it("reports the provenance a resolution is stamped with, once built", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company(BUILT)]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("READY")).toBeTruthy();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
    // The two facts that say WHICH catalogue answered — not just how many rows.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("2b5c96f97de49436")).toBeTruthy();
    expect(screen.getByText(/kennametal_widia v0\.10\.0/)).toBeTruthy();
  });

  it("keeps one company's state inside that company's own surface", async () => {
    // The whole point of the per-company move: a built catalogue for one
    // company must not read as coverage for the other. A single flat list of
    // facts would let a reader carry the first company's checksum onto the
    // second — so each company's numbers are asserted within its own panel.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([
      company(BUILT),
      company({ connection_id: "conn-b", label: "4U Precision",
                corpus: BUILT.corpus }),
    ]));
    render(<CatalogScreen session={SESSION} />);

    const built = (await screen.findByText("SLS Engineers")).closest(".bp");
    expect(built).toBeTruthy();
    expect(within(built as HTMLElement).getByText("READY")).toBeTruthy();

    const other = (screen.getByText("4U Precision")).closest(".bp");
    expect(within(other as HTMLElement).getByText("NOT BUILT")).toBeTruthy();
    expect(within(other as HTMLElement).queryByText(/decoded records/)).toBeNull();
    expect(within(other as HTMLElement).queryByText("f67131512eb97513")).toBeNull();
  });

  it("withholds the build controls from anyone the server did not clear", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company(BUILT)], { can_manage: false }));
    render(<CatalogScreen session={SESSION} />);

    await waitFor(() => expect(screen.getByText("READY")).toBeTruthy());
    // The state is readable — the controls are not. The server refuses the
    // POST as well; this only keeps the screen honest about it.
    expect(screen.queryByRole("button", { name: /Rebuild/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /export/ })).toBeNull();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
  });
});
