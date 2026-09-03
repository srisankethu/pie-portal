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
// The third is per-catalogue. A company keeps one catalogue per manufacturer
// and resolves against the union of them, so each catalogue's chip and stamp
// stay inside its own section, and the union's count — the server's, from its
// manifest — is stated once per company and never added up here.
//
// The backend half is pinned in tests/decision_platform/test_catalog_surface.py.
// This is the half that a `?? 0` in a component could break with every server
// test still green.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogScreen } from "./CatalogScreen";
import { papi } from "./api";
import type { CatalogueUnion, CompanyCatalogue, CompanyCatalogueEntry,
              CompanyCatalogues, CompanySource, PlatformSession } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

/** One manufacturer's catalogue inside a company. Built nothing by default;
 *  spread `BUILT` over it for a catalogue with a stamp. */
function catalogue(over: Partial<CompanyCatalogueEntry> = {}): CompanyCatalogueEntry {
  return {
    connection_id: "conn-a",
    catalogue_key: "kennametal",
    name: "Kennametal",
    scope: "catalogue",
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
    ...over,
  };
}

/** The union the server would describe for these catalogues — its built
 *  members and their record counts. A fixture adds the counts up so it can
 *  hand the screen a consistent manifest; the screen itself never does. */
function union(entries: CompanyCatalogueEntry[],
               over: Partial<CatalogueUnion> = {}): CatalogueUnion {
  const built = entries.filter((e) => e.exists);
  const records = built.reduce((t, e) => t + (e.records ?? 0), 0);
  return {
    records,
    version: "u9c1d2e3f4a5b6c7d",
    duplicates: 0,
    duplicate_examples: [],
    catalogues: built.map((e) => ({
      catalogue_key: e.catalogue_key, name: e.name, pack_id: e.pack_id,
      built_at: e.built_at, records: e.records ?? 0,
      ruleset_checksum: e.stamp.ruleset_checksum, run_id: e.stamp.run_id,
    })),
    retrieval: { model_id: "hashed-ngram/1", dim: 262144, records,
                 current: true, min_similarity: 0.2 },
    ...over,
  };
}

/** A company with one catalogue, described by `cat`, and the union that
 *  catalogue makes — null when it is not built, which is the server's shape
 *  for a company that resolves nothing. `label` is the company's; everything
 *  else in `cat` is the catalogue's. `over` adjusts the envelope itself. */
function company(cat: Partial<CompanyCatalogueEntry> & { label?: string } = {},
                 over: Partial<CompanyCatalogue> = {}): CompanyCatalogue {
  const { label, ...catOver } = cat;
  const entry = catalogue(catOver);
  return {
    connection_id: entry.connection_id,
    label: label ?? "SLS Engineers",
    enabled: true,
    scope: "company",
    catalogues: [entry],
    union: entry.exists ? union([entry]) : null,
    ...over,
  };
}

/** The same company with facts of its union changed. */
function withUnion(c: CompanyCatalogue, over: Partial<CatalogueUnion>): CompanyCatalogue {
  return { ...c, union: { ...(c.union ?? union(c.catalogues)), ...over } };
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

const BUILT: Partial<CompanyCatalogueEntry> = {
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
    max_catalogues: 8,
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
    // It sits beside the union, so it is stated once at the company.
    expect(screen.getByText("hashed-ngram/1")).toBeTruthy();
    expect(screen.getByText(/6717 records indexed/)).toBeTruthy();
    // And the union is what the company resolves against — its own count,
    // stated once, with the chip saying the same in a word.
    expect(screen.getByText("RESOLVES 6717 RECORDS")).toBeTruthy();
    expect(screen.getByText(/6717 records from 1 catalogue/)).toBeTruthy();
  });

  it("says when a union has no retrieval index yet, and when it is behind", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([
      withUnion(company({ ...BUILT, sources: [source()] }), { retrieval: null }),
      withUnion(company({ ...BUILT, connection_id: "conn-b", label: "4U Precision",
                          sources: [source()] }),
                { retrieval: { model_id: "hashed-ngram/1", dim: 262144, records: 6000,
                               current: false, min_similarity: 0.2 } }),
    ]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("none yet")).toBeTruthy();
    expect(screen.getByText(/behind the union, rebuilt on next use/)).toBeTruthy();
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
    expect(within(other as HTMLElement).getByText("NOTHING BUILT")).toBeTruthy();
    expect(within(other as HTMLElement).queryByText(/decoded records/)).toBeNull();
    expect(within(other as HTMLElement).queryByText(/records from/)).toBeNull();
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
    expect(screen.queryByRole("button", { name: /Add a catalogue/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Rename/ })).toBeNull();
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
  });
});

describe("one catalogue per manufacturer", () => {
  /** Two built catalogues in one company, each with its own stamp. */
  function twoBuilt() {
    const kennametal = catalogue({ ...BUILT, sources: [source()] });
    const yg1 = catalogue({
      ...BUILT,
      catalogue_key: "yg-1", name: "YG-1", records: 3000, rows_read: 3000,
      sources: [source({ source_key: "yg1-prices.xlsx",
                         filename: "yg1-prices.xlsx", corpus_id: "cor9" })],
      stamp: { ...BUILT.stamp, ruleset_checksum: "aaaa1111bbbb2222",
               run_id: "cccc3333dddd4444" },
    });
    return company({}, { catalogues: [kennametal, yg1],
                         union: union([kennametal, yg1]) });
  }

  it("renders each catalogue in its own section, and the union once", async () => {
    // Two manufacturers, two packs, two stamps — and one number the company
    // resolves against. The union's count is the server's (9717, not two
    // "decoded records" figures a reader would have to add), and it appears
    // exactly once: `getByText` throws on a second match.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([twoBuilt()]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("Kennametal", { selector: "b" })).toBeTruthy();
    expect(screen.getByText("YG-1", { selector: "b" })).toBeTruthy();
    expect(screen.getAllByText("READY")).toHaveLength(2);
    expect(screen.getByText(/6717 decoded records/)).toBeTruthy();
    expect(screen.getByText(/3000 decoded records/)).toBeTruthy();
    // Each catalogue's provenance, under its own heading.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("aaaa1111bbbb2222")).toBeTruthy();
    expect(screen.getAllByLabelText(/Per-family parse rates/)).toHaveLength(2);
    // The union, once.
    expect(screen.getByText("RESOLVES 9717 RECORDS")).toBeTruthy();
    expect(screen.getByText(/9717 records from 2 catalogues/)).toBeTruthy();
    expect(screen.getAllByText(/records indexed/)).toHaveLength(1);
  });

  it("states a part number two catalogues both claim, with the rule that resolved it", async () => {
    // Two manufacturers' price lists can carry the same code. The union keeps
    // the most recently built catalogue's row, and that is a policy somebody
    // has to be told about rather than a merge that quietly picked one.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([
      withUnion(twoBuilt(), { duplicates: 3,
                              duplicate_examples: ["1234567", "7654321"] }),
    ]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText(/3 part numbers appear in more than one catalogue/))
      .toBeTruthy();
    expect(screen.getByText(/most recently built catalogue's row was used/)).toBeTruthy();
    expect(screen.getByText(/1234567, 7654321/)).toBeTruthy();
  });

  it("adds a catalogue by name and pack, and takes the company from the response", async () => {
    const before = company({ ...BUILT, sources: [source()] });
    const after: CompanyCatalogue = {
      ...before,
      catalogues: [...before.catalogues,
                   catalogue({ catalogue_key: "yg-1", name: "YG-1" })],
    };
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([before]));
    const create = vi.spyOn(papi, "createCompanyCatalogue").mockResolvedValue(after);
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add a catalogue" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Name/ }),
                     { target: { value: "YG-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add" }));

    // With exactly one shipped pack it is pre-chosen — there is nothing to
    // choose between — so the post carries it without a menu being opened.
    expect(create).toHaveBeenCalledWith("t", "conn-a", { name: "YG-1", pack_id: "zcnc" });
    // The response is the whole company, and it replaces the one on screen:
    // the new catalogue's section appears beside the built one.
    expect(await screen.findByText("YG-1", { selector: "b" })).toBeTruthy();
    expect(screen.getByText("Kennametal", { selector: "b" })).toBeTruthy();
    expect(screen.getByText("NO EXPORT")).toBeTruthy();
    expect(screen.getByText("READY")).toBeTruthy();
  });

  it("removes a catalogue only through the confirm dialog, by its key", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })]));
    const remove = vi.spyOn(papi, "removeCompanyCatalogue").mockResolvedValue(
      company({}, { catalogues: [], union: null }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove catalogue" }));
    expect(remove).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    // What the dialog says: the company stops resolving against this
    // manufacturer now, and its files are kept rather than deleted.
    expect(within(dialog).getByText(/stops resolving against this manufacturer at once/))
      .toBeTruthy();
    expect(within(dialog).getByText(/kept and superseded/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));

    expect(remove).toHaveBeenCalledWith("t", "conn-a", "kennametal");
    // And the response — a company with nothing left — replaces it.
    expect(await screen.findByText("No catalogue yet")).toBeTruthy();
    expect(screen.getByText("NOTHING BUILT")).toBeTruthy();
  });

  it("tells an owner to add a catalogue when a company has none, and offers no build", async () => {
    // Nothing to build, so no Build button — a control that cannot succeed is
    // the interface's version of the benign default. The prompt names the
    // manufacturers as the example, because "a catalogue" alone does not say
    // that one is wanted per manufacturer.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({}, { catalogues: [], union: null })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("No catalogue yet")).toBeTruthy();
    expect(screen.getByText(/Kennametal, YG-1/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add a catalogue" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Build" })).toBeNull();
    expect(screen.getByText("NOTHING BUILT")).toBeTruthy();
    expect(screen.queryByText(/decoded records/)).toBeNull();
  });
});
