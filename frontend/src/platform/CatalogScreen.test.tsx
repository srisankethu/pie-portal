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
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogScreen } from "./CatalogScreen";
import { papi } from "./api";
import type { CompanyCatalogue, CompanyCatalogues, CompanySource,
              PlatformSession } from "./types";

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
    sources: [],
    ingest: null,
    built_from: null,
    stale: false,
    retrieval: null,
    ...over,
  };
}

/** One uploaded file, with the ingest report the server stores beside it.
 *
 *  `commercial_columns_dropped` is populated in the default because the screen
 *  claiming "nomenclature only" has to be checkable: the test below asserts the
 *  price column is *named* as ignored, which is the difference between a
 *  promise and evidence. */
function source(over: Partial<CompanySource> = {}): CompanySource {
  return {
    source_key: "item-master.csv",
    corpus_id: "cor1",
    filename: "item-master.csv",
    content_type: "text/csv",
    size_bytes: 2_400_000,
    sha256: "abc",
    uploaded_at: "2026-08-30T05:00:00Z",
    uploaded_by: "s.menon@pie.example",
    mapping: { record_id: "MM#", description: "Material Description",
               grade: "Grade" },
    ingest: {
      columns: ["MM#", "Material Description", "Grade", "New ZCNC Price"],
      mapped: { record_id: "MM#", description: "Material Description",
                grade: "Grade" },
      dropped_columns: ["New ZCNC Price"],
      commercial_columns_dropped: ["New ZCNC Price"],
      rows_read: 6717, rows_kept: 6717,
      rows_skipped_blank_key: 0, sampled: false,
    },
    ...over,
  };
}

/** Report a narrow viewport, so `DataGrid` draws its cards rather than lazily
 *  fetching ag-grid. jsdom has no `matchMedia` at all, and MUI's fallback
 *  answers `false` to everything — which would put every test on the wide
 *  path and load a chunk these assertions do not need. */
function narrowViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"), media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
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
  built_from: [{ source_key: "item-master.csv", corpus_id: "cor1",
                 filename: "item-master.csv", sha256: "abc" }],
  retrieval: { model_id: "hashed-ngram/1", dim: 262144, records: 6717,
               current: true, min_similarity: 0.2 },
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
  narrowViewport();
});

describe("the decoded catalogue screen", () => {
  it("says NOT BUILT, and never renders a count or a rate for it", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ corpus: BUILT.corpus, sources: [source()] })]));
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
    expect(screen.getByText(/answer UNKNOWN — not zero coverage — until a file is uploaded/)).toBeTruthy();
    // Nothing to build from, so the build control cannot succeed and is off.
    expect(screen.getByRole("button", { name: "Build" })).toHaveProperty("disabled", true);
  });

  it("reports the provenance a resolution is stamped with, once built", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("READY")).toBeTruthy();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
    // The two facts that say WHICH catalogue answered — not just how many rows.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("2b5c96f97de49436")).toBeTruthy();
    expect(screen.getByText(/kennametal_widia v0\.10\.0/)).toBeTruthy();
    // The retrieval index is provenance too: which model found an option.
    expect(screen.getByText("hashed-ngram/1")).toBeTruthy();
    expect(screen.getByText(/6717 records indexed/)).toBeTruthy();
  });

  it("says when a built catalogue has no retrieval index yet, and when it is behind", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([
      company({ ...BUILT, sources: [source()], retrieval: null }),
      company({ ...BUILT, connection_id: "conn-b", label: "4U Precision", sources: [source()],
                retrieval: { model_id: "hashed-ngram/1", dim: 262144, records: 6000,
                             current: false, min_similarity: 0.2 } }),
    ]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("none yet")).toBeTruthy();
    expect(screen.getByText(/behind this catalogue, rebuilt on next use/)).toBeTruthy();
  });

  it("keeps one company's state inside that company's own surface", async () => {
    // The whole point of the per-company move: a built catalogue for one
    // company must not read as coverage for the other. A single flat list of
    // facts would let a reader carry the first company's checksum onto the
    // second — so each company's numbers are asserted within its own panel.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([
      company({ ...BUILT, sources: [source()] }),
      company({ connection_id: "conn-b", label: "4U Precision",
                corpus: BUILT.corpus, sources: [source()] }),
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

  it("says why the pack list is empty instead of offering an empty menu", async () => {
    // The defect this screen shipped with. `deploy/backend.Dockerfile` builds
    // an image without the private pie-parser submodule on purpose, so `packs`
    // is legitimately empty in production — and the control rendered as a
    // dropdown that opened onto nothing, with the server's own explanation
    // sitting unused in `source.reason`. An empty control that does not say
    // why is the interface's version of the benign default §1 forbids.
    const reason = "pie-parser is not present at /app/pie-parser — the "
      + "private submodule is not initialised.";
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ pack_id: null, pack_resolved: false })], {
        packs: [],
        source: { available: false, reason, pie_parser_root: "/app/pie-parser",
                  corpus: "/app/pie-parser/corpora/corpus.csv",
                  pack: "/app/pie-parser/packs/org/zcnc" },
      }));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText(/No pack is available to decode with/))
      .toBeTruthy();
    expect(screen.getByText(new RegExp(reason.slice(0, 40)))).toBeTruthy();
    // And the control itself is off rather than empty: a menu with no items is
    // indistinguishable from one that failed to open.
    expect(screen.getByText("none available")).toBeTruthy();
  });

  it("names the columns it ignored, so nomenclature-only is checkable", async () => {
    // "Never price, cost or stock" is a claim about a file the platform read
    // and the person did not. A count of ignored columns is not something they
    // can check against their own spreadsheet; the column's name is.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })]));
    render(<CatalogScreen session={SESSION} />);

    await screen.findByText("READY");
    expect(screen.getByText(/New ZCNC Price/)).toBeTruthy();
  });

  it("shows every file a catalogue is built from, not just the newest", async () => {
    // One company, three exports — an item master, a range extension and a
    // price list. Reporting only the newest would say the catalogue holds a
    // fraction of what it holds, and the merge's overlaps would be invisible.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        ...BUILT,
        sources: [
          source(),
          source({ source_key: "range.csv", filename: "range.csv",
                   corpus_id: "cor2" }),
          source({ source_key: "prices.xlsx", filename: "prices.xlsx",
                   corpus_id: "cor3" }),
        ],
        ingest: {
          sources: [], rows_kept: 6900, rows_in: 6950,
          rows_skipped_blank_key: 50, collisions: 4,
          collision_examples: ["1234567", "7654321"], sampled: false,
        },
      })]));
    render(<CatalogScreen session={SESSION} />);

    await screen.findByText("READY");
    expect(screen.getByText("item-master.csv")).toBeTruthy();
    expect(screen.getByText("range.csv")).toBeTruthy();
    expect(screen.getByText("prices.xlsx")).toBeTruthy();
    // The overlap is stated, with the rule that resolved it. A merge that
    // silently kept one of two rows for the same part number would be a
    // decision nobody was told about.
    expect(screen.getByText(/4 part numbers appeared in more than one file/))
      .toBeTruthy();
    expect(screen.getByText(/newest file's row was used/)).toBeTruthy();
  });

  it("offers the pack trial even when only one pack ships", async () => {
    // The condition was `packs.length > 1` — "which of these" — which hid the
    // trial in exactly the deployment that ships one pack. With one pack the
    // question is not which of them but whether that one reads this file at
    // all, and its answer is what decides whether a pack has to be written.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ sources: [source()] })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByRole("button", { name: "Try this pack" }))
      .toBeTruthy();
  });

  it("reports the parser's counts per pack, and ranks nothing", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ sources: [source()] })]));
    vi.spyOn(papi, "companyPackFit").mockResolvedValue({
      available: true, reason: null, sample_rows: 500,
      packs: [{ pack_id: "zcnc", rows_read: 500, classified: 431,
                quarantined: 69, sampled: true }],
    });
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Try this pack" }));
    expect(await screen.findByText(/431 classified, 69 quarantined/))
      .toBeTruthy();
    // Counts, not a score. A "86% fit" here would be a rate this screen
    // computed — the one thing the catalogue surface never does.
    expect(document.body.textContent ?? "").not.toMatch(/%/);
  });

  it("says what to do about a file stored before its columns were read", async () => {
    // The seeded corpus, and any upload from before mapping existed, has no
    // ingest report — so there are no headers to offer. Three empty menus and a
    // disabled Save is a dead end; the file still builds, and re-uploading it
    // is what puts its columns on record.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        sources: [source({ mapping: null, ingest: null,
                           filename: "kmt_zcnc_2026-07.csv" })],
      })]));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Columns" }));
    expect(await screen.findByText(/stored before its columns were read/))
      .toBeTruthy();
    expect(screen.queryByRole("button", { name: /Save and re-read/ })).toBeNull();
  });

  it("shows a small file's size in a unit that is not 0.0 MB", async () => {
    // Found by looking at the screen rather than by a test: every real file —
    // a 700-byte test export, a 200 kB range extension — rendered as "0.0 MB",
    // which reads as an upload that did not work on the one screen whose job is
    // to say what was uploaded.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ sources: [source({ size_bytes: 726 })] })]));
    render(<CatalogScreen session={SESSION} />);

    // Both the file list and the fact panel name it, which is the point of
    // the fact panel row — so the assertion is on the size, not the filename.
    // Both the file list and the fact panel carry it, so `findAll`.
    expect((await screen.findAllByText(/726 B/)).length).toBeGreaterThan(0);
    expect(document.body.textContent ?? "").not.toMatch(/0\.0 MB/);
  });

  it("withholds the build controls from anyone the server did not clear", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })], { can_manage: false }));
    render(<CatalogScreen session={SESSION} />);

    await waitFor(() => expect(screen.getByText("READY")).toBeTruthy());
    // The state is readable — the controls are not. The server refuses the
    // POST as well; this only keeps the screen honest about it.
    expect(screen.queryByRole("button", { name: /Rebuild/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add a file/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
  });
});
