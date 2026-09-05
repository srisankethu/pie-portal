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
import { setMoneyCurrency } from "../money";
import type { BindingSuggestion, CatalogueUnion, CompanyCatalogue,
              CompanyCatalogueEntry, CompanyCatalogues, CompanySource,
              DecoderArtifact, DecoderProposalResponse, SourceDecoding,
              PlatformSession } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

/** One manufacturer's catalogue inside a company. Built nothing by default;
 *  spread `BUILT` over it for a catalogue with a stamp.
 *
 *  `decoding_ready` and `awaiting_decoding` are DERIVED from the files, the
 *  way the server derives them, so a fixture cannot hand the screen a
 *  catalogue that is ready and holds a file nobody has said how to decode. */
function catalogue(over: Partial<CompanyCatalogueEntry> = {}): CompanyCatalogueEntry {
  const entry = {
    connection_id: "conn-a",
    catalogue_key: "kennametal",
    name: "Kennametal",
    scope: "catalogue",
    decoding_ready: true,
    awaiting_decoding: [],
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
  const awaiting = entry.sources.filter((x) => !x.decoding.ready)
                                .map((x) => x.source_key);
  return { ...entry, awaiting_decoding: awaiting,
           decoding_ready: entry.sources.length > 0 && awaiting.length === 0 };
}

/** The header a catalogue's — or a company's — own state chip sits in.
 *
 *  Each FILE now carries a state word of its own (its decoding config, not the
 *  catalogue's build), and one of them is also READY. So an assertion about a
 *  catalogue's state is scoped to the line its name is on rather than to the
 *  page, which is what it always meant.
 *
 *  By role rather than by `{ selector: "b" }`: a catalogue's name is a real
 *  heading now (`kit.SectionHeader level="widget"`) rather than bold text, so
 *  the eight manufacturers inside a company are reachable by heading navigation
 *  instead of being invisible to it.
 *
 *  Matched on the *start* of the accessible name rather than the whole of it.
 *  `SectionHeader` puts the state chip inside the heading — that is what its
 *  `badge` slot is for — and the chip carries a tooltip, which MUI renders as
 *  an `aria-label`, so the computed name is the manufacturer followed by a
 *  sentence of explanation. Anchoring at the front is what keeps this from
 *  matching the confirm dialog's "Stop resolving against Kennametal?", which is
 *  also a heading. */
const startsWith = (name: string) => new RegExp(`^${name}`);

function heading(name: string): HTMLElement {
  return screen.getByRole("heading", { name: startsWith(name) });
}

/** The same, awaited — for the first assertion of a test, before the fetch has
 *  landed. `findByRole` rather than `getByRole` is the only difference. */
function findHeading(name: string): Promise<HTMLElement> {
  return screen.findByRole("heading", { name: startsWith(name) });
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
      catalogue_key: e.catalogue_key, name: e.name,
      rule_sets: (e.built_from ?? []).map((f) => f.rule_set ?? "—"),
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

/** One file's saved decoding config: the columns confirmed, the rule set
 *  chosen, and the analysis that proposed both. Ready by default — the state
 *  a file reaches once somebody has checked it — because every other state is
 *  a deliberate case a test names. */
function decoding(over: Partial<SourceDecoding> = {}): SourceDecoding {
  return {
    columns: { record_id: "MM#", description: "Material Description",
               grade: "Grade" },
    rule_set: "zcnc",
    rule_set_resolved: true,
    // The rule-set path by default. A file decoded by an artifact built for
    // it is the other case, and a test that means it says so.
    decoder_id: null,
    decoder: null,
    path: "rule_set",
    analysis: {
      sample_rows: 500,
      candidates: [{ rule_set: "zcnc", rows_read: 500, classified: 431,
                     quarantined: 69 }],
      proposed: "zcnc",
      reason: null,
    },
    confirmed_at: "2026-08-30T05:02:00Z",
    confirmed_by: "s.menon@pie.example",
    ready: true,
    ...over,
  };
}

/** One uploaded file, with the ingest report and the decoding config the
 *  server stores beside it.
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
    decoding: decoding(),
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

/** A frozen decoder as a proposal returns it: two shapes, one binding the
 *  file's own text settled and three groups nobody has named. */
function artifact(over: Partial<DecoderArtifact> = {}): DecoderArtifact {
  return {
    schema_version: 1,
    decimal: "either",
    decoder_id: "6aaeccc849df9b16",
    segments: [
      {
        id: "s1-sc-drill",
        pattern: "^SC DRILL (?P<num1>\\d+)mm/(?P<num2>[.\\d]+)/ (?P<num3>\\d+)xD$",
        fields: [{ group: "num3", slot: "depth_ratio_xd", type: "integer" }],
        examples: ["SC DRILL 3mm/.1181/ 5xD"],
        counterexamples: ["GP SC End Mill 4FL"],
      },
    ],
    ...over,
  };
}

/** One group of the review, with the evidence a reviewer reads it against. */
function suggestion(over: Partial<BindingSuggestion> = {}): BindingSuggestion {
  const group = over.group ?? "num1";
  return {
    segment: "s1-sc-drill",
    group,
    slot: null,
    type: null,
    source: "none",
    candidates: ["cutting_dia_mm", "shank_dia_mm", "loc_mm"],
    reason: "AMBIGUOUS_UNIT",
    detail: "",
    evidence: {
      segment: "s1-sc-drill", group, kind: "number",
      rows_matched: 1239, occurrences: 1239, distinct: 312,
      samples: ["5", "8", "6"], left: "", right: "mm",
      all_integer: true, all_numeric: true,
    },
    ...over,
  };
}

function proposal(over: Partial<DecoderProposalResponse> = {}): DecoderProposalResponse {
  const decoder = artifact();
  return {
    proposal: {
      decoder, decoder_id: decoder.decoder_id,
      rows_read: 6717, claimed: 4342, unclaimed: 2375,
      unclaimed_samples: ["CNMG 120408-49 - TN2000"],
      coverage: { "s1-sc-drill": 1239 }, overlaps: [], reason: null,
    },
    review: {
      decoder_id: decoder.decoder_id,
      segments: [],
      suggestions: [
        suggestion(),
        suggestion({ group: "num3", slot: "depth_ratio_xd", type: "integer",
                     source: "surface", reason: "FROM_UNIT",
                     candidates: ["depth_ratio_xd"] }),
      ],
      from_surface: 1, from_model: 0, unnamed: 1,
      refused: {}, provider: "mock", model: "mock-1", reason: null,
    },
    ...over,
  };
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
                 filename: "item-master.csv", sha256: "abc",
                 rule_set: "zcnc",
                 stamp: {
                   pack_id: "kennametal_widia", pack_version: "0.10.0",
                   org_id: "zcnc", org_version: "0.10.0",
                   ruleset_checksum: "f67131512eb97513",
                   run_id: "2b5c96f97de49436",
                   engine_version: "0.10.0", schema_version: "1.0.0",
                 },
                 records: 6717, rows_read: 6717, quarantined: 0,
                 rows_kept: 6717, rows_skipped_blank_key: 0,
                 rows_emitted: 6717,
                 report: null }],
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
    rule_sets: [{ id: "zcnc", path: "/pie-parser/packs/org/zcnc" }],
    source: {
      available: true, reason: null,
      pie_parser_root: "/pie-parser",
      corpus: "/pie-parser/corpora/corpus.csv",
      seed_rule_set: "/pie-parser/packs/org/zcnc",
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
  // Every record count on this screen goes through `money.count`, which groups
  // for the organization's currency — "6,717" under `en-IN`, and "6,717" under
  // `en-US` too, but they part company at six figures. Pinned here rather than
  // left to `money.ts`'s default, so an assertion written as "6,717" says which
  // locale it is true of instead of inheriting one.
  setMoneyCurrency("INR");
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
    // Scoped to the catalogue's own line: the file below it is READY to
    // decode, which says nothing about whether the catalogue was built.
    expect(within(heading("Kennametal")).queryByText("READY")).toBeNull();
  });

  it("names what is missing rather than saying only 'not built'", async () => {
    // Nothing uploaded at all. "You have not built it" is not useful advice to
    // somebody who has nothing to build it from.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(view([company()]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("NO EXPORT")).toBeTruthy();
    expect(screen.getByText(/answer UNKNOWN — not zero coverage — until a file is uploaded/)).toBeTruthy();
    // Nothing to build from, so the build control cannot succeed and is off.
    expect(screen.getByRole("button", { name: "Build" })).toHaveProperty("disabled", true);
  });

  it("reports the provenance a resolution is stamped with, once built", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })]));
    render(<CatalogScreen session={SESSION} />);

    await screen.findByText(/6,717 decoded records/);
    expect(within(heading("Kennametal")).getByText("READY")).toBeTruthy();
    // The two facts that say WHICH catalogue answered — not just how many rows.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("2b5c96f97de49436")).toBeTruthy();
    expect(screen.getByText(/kennametal_widia v0\.10\.0/)).toBeTruthy();
    // The retrieval index is provenance too: which model found an option.
    // It sits beside the union, so it is stated once at the company.
    expect(screen.getByText("hashed-ngram/1")).toBeTruthy();
    expect(screen.getByText(/6,717 records indexed/)).toBeTruthy();
    // And the union is what the company resolves against — its own count,
    // stated once, with the chip saying the same in a word.
    expect(screen.getByText("RESOLVES 6,717 RECORDS")).toBeTruthy();
    expect(screen.getByText(/6,717 records from 1 catalogue/)).toBeTruthy();
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

    const built = (await findHeading("SLS Engineers")).closest(".bp");
    expect(built).toBeTruthy();
    expect(within(built as HTMLElement).getByText(/6,717 decoded records/))
      .toBeTruthy();

    const other = heading("4U Precision").closest(".bp");
    expect(within(other as HTMLElement).getByText("NOT BUILT")).toBeTruthy();
    expect(within(other as HTMLElement).getByText("NOTHING BUILT")).toBeTruthy();
    expect(within(other as HTMLElement).queryByText(/decoded records/)).toBeNull();
    expect(within(other as HTMLElement).queryByText(/records from/)).toBeNull();
    expect(within(other as HTMLElement).queryByText("f67131512eb97513")).toBeNull();
  });

  it("says why no rule set is available instead of offering an empty menu", async () => {
    // The defect this screen shipped with. `deploy/backend.Dockerfile` builds
    // an image without the private pie-parser submodule on purpose, so
    // `rule_sets` is legitimately empty in production — and the control
    // rendered as a dropdown that opened onto nothing, with the server's own
    // explanation sitting unused in `source.reason`. An empty control that
    // does not say why is the interface's version of the benign default §1
    // forbids.
    const reason = "pie-parser is not present at /app/pie-parser — the "
      + "private submodule is not initialised.";
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        // The file such a deployment actually holds: uploaded, analysed
        // against nothing, and naming no rule set because there was none to
        // name.
        sources: [source({ decoding: decoding({
          rule_set: null, rule_set_resolved: false, confirmed_at: null,
          confirmed_by: null, ready: false,
          analysis: { sample_rows: 500, candidates: [], proposed: null,
                      reason: "This engine ships no rule sets." },
        }) })],
      })], {
        rule_sets: [],
        source: { available: false, reason, pie_parser_root: "/app/pie-parser",
                  corpus: "/app/pie-parser/corpora/corpus.csv",
                  seed_rule_set: "/app/pie-parser/packs/org/zcnc" },
      }));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText(/No rule set is available to decode with/))
      .toBeTruthy();
    expect(screen.getByText(new RegExp(reason.slice(0, 40)))).toBeTruthy();
    // And the menu a file's config would name one in says the same, where a
    // person actually meets it: empty, but not silently.
    fireEvent.click(screen.getByRole("button", { name: "Decoding" }));
    expect(await screen.findByText(/none available — this deployment ships no rule set/))
      .toBeTruthy();
  });

  it("names the columns it ignored, so nomenclature-only is checkable", async () => {
    // "Never price, cost or stock" is a claim about a file the platform read
    // and the person did not. A count of ignored columns is not something they
    // can check against their own spreadsheet; the column's name is.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ ...BUILT, sources: [source()] })]));
    render(<CatalogScreen session={SESSION} />);

    await screen.findByText(/6,717 decoded records/);
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

    await screen.findByText(/6,717 decoded records/);
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

  it("names a file nobody has said how to decode, and will not build", async () => {
    // The state that exists because there is no default decoder: a file is
    // uploaded, analysed and proposed, and until somebody saves that proposal
    // it is not decoded. "Not built" would be true and useless — the server
    // refuses this build by name, so the screen names it first.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        sources: [source({ source_key: "yg1-prices.xlsx",
                           filename: "yg1-prices.xlsx",
                           decoding: decoding({ confirmed_at: null,
                                                confirmed_by: null,
                                                ready: false }) })],
      })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("AWAITING DECODING")).toBeTruthy();
    expect(screen.getByText(/Not decoded yet: yg1-prices\.xlsx/)).toBeTruthy();
    expect(screen.getByText(/Nothing is decoded through a default/)).toBeTruthy();
    // The file's own state says the same thing in a word, beside the file.
    expect(screen.getByText("NOT SAVED")).toBeTruthy();
    // And the control that cannot succeed is off rather than failing on click.
    expect(screen.getByRole("button", { name: "Build" }))
      .toHaveProperty("disabled", true);
  });

  it("shows the parser's counts per rule set, ranks nothing, and saves a config", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        sources: [source({ decoding: decoding({
          rule_set: null, rule_set_resolved: false, confirmed_at: null,
          confirmed_by: null, ready: false,
          analysis: {
            sample_rows: 500,
            candidates: [
              { rule_set: "zcnc", rows_read: 500, classified: 431,
                quarantined: 69 },
              { rule_set: "yg1", error: "PackError: no grammar matched" },
            ],
            proposed: "zcnc", reason: null,
          },
        }) })],
      })]));
    const save = vi.spyOn(papi, "saveSourceDecoding").mockResolvedValue(
      company({ ...BUILT, sources: [source()] }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));

    // The evidence, per rule set, as the parser counted it — including the one
    // that could not read the file at all, which is an answer rather than a
    // failure of the analysis.
    expect(await screen.findByText(/431 classified, 69 quarantined/)).toBeTruthy();
    expect(screen.getByText(/of 500 rows/)).toBeTruthy();
    expect(screen.getByText(/could not read this file/)).toBeTruthy();
    expect(screen.getByText("proposed")).toBeTruthy();
    // Counts, not a score. An "86% fit" here would be a rate this screen
    // computed — the one thing the catalogue surface never does.
    expect(document.body.textContent ?? "").not.toMatch(/%/);
    // And the copy says the file is not decoded until this is saved.
    expect(screen.getByText(/this file is not decoded until this is saved/))
      .toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Save decoding config" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(
      "t", "conn-a", "kennametal", "item-master.csv",
      { record_id: "MM#", description: "Material Description",
        grade: "Grade", rule_set: "zcnc", decoder: null }));
  });

  it("says plainly when no rule set reads a file, and saves it without one", async () => {
    // The answer no menu can fix: this manufacturer needs a rule set written.
    // Saving the columns with none is the honest state — the file's columns go
    // on record, and the build names it rather than decoding it through
    // somebody else's grammars.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        sources: [source({ decoding: decoding({
          rule_set: null, rule_set_resolved: false, confirmed_at: null,
          confirmed_by: null, ready: false,
          analysis: {
            sample_rows: 500,
            candidates: [{ rule_set: "zcnc", rows_read: 500, classified: 0,
                           quarantined: 500 }],
            proposed: null,
            reason: "No rule set this engine ships reads this file: none of "
              + "them classified a single sampled row. Its manufacturer needs "
              + "a rule set written before it can be decoded.",
          },
        }) })],
      })]));
    const save = vi.spyOn(papi, "saveSourceDecoding").mockResolvedValue(
      company({ sources: [source()] }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    expect(await screen.findByText(/needs a rule set written before it can be decoded/))
      .toBeTruthy();
    expect(screen.getByText(/0 classified, 500 quarantined/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Save decoding config" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(
      "t", "conn-a", "kennametal", "item-master.csv",
      { record_id: "MM#", description: "Material Description",
        grade: "Grade", rule_set: null, decoder: null }));
  });

  it("proposes a decoder from the file itself, and saves nothing until asked",
     async () => {
    // The half of discovery that needs no shipped grammar. Proposing must not
    // change what the file currently decodes through: a proposal nobody has
    // confirmed is not a config, and a screen that took it on would have
    // silently made it one.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({ sources: [source()] })]));
    const propose = vi.spyOn(papi, "proposeSourceDecoder")
      .mockResolvedValue(proposal());
    const save = vi.spyOn(papi, "saveSourceDecoding").mockResolvedValue(
      company({ sources: [source()] }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    fireEvent.click(screen.getByRole("button",
                                     { name: "A decoder built from this file" }));
    // Nothing is spent until somebody asks: inference reads the whole file.
    expect(propose).not.toHaveBeenCalled();
    // And there is nothing to save yet, because there is no decoder.
    expect(screen.getByRole("button", { name: "Save decoding config" }))
      .toHaveProperty("disabled", true);

    fireEvent.click(screen.getByRole("button", { name: "Propose a decoder" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(
      "t", "conn-a", "kennametal", "item-master.csv"));
    expect(save).not.toHaveBeenCalled();

    // Counts, never a score — the same rule the rule-set evidence follows.
    expect(await screen.findByText(/4,342 of 6,717 rows fall into 1 shape/))
      .toBeTruthy();
    expect(screen.getByText(/2,375 match none and would be kept unread/))
      .toBeTruthy();
    expect(screen.getByText(/1 are named by the file's own text/)).toBeTruthy();
    expect(screen.getByText(/1 are open/)).toBeTruthy();
    // The rows no shape matched, named rather than counted: a file this does
    // not understand is the finding.
    expect(screen.getByText(/CNMG 120408-49 - TN2000/)).toBeTruthy();
  });

  it("saves a reviewed decoder as bindings over the proposed artifact",
     async () => {
    // The patterns come from a proposal this deployment produced and can
    // verify; only the answers come from the screen. So the artifact goes back
    // untouched and the review travels beside it.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({ sources: [source()] })]));
    vi.spyOn(papi, "proposeSourceDecoder").mockResolvedValue(proposal());
    const save = vi.spyOn(papi, "saveSourceDecoding").mockResolvedValue(
      company({ sources: [source()] }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    fireEvent.click(screen.getByRole("button",
                                     { name: "A decoder built from this file" }));
    fireEvent.click(screen.getByRole("button", { name: "Propose a decoder" }));
    await screen.findByText(/4,342 of 6,717 rows fall into 1 shape/);

    fireEvent.click(screen.getByRole("button", { name: "Save decoding config" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(
      "t", "conn-a", "kennametal", "item-master.csv",
      expect.objectContaining({
        record_id: "MM#", description: "Material Description",
        // The two are alternatives: choosing a decoder says the rule set no
        // longer decodes this file, and a config naming both is refused.
        rule_set: null,
        decoder: expect.objectContaining({ decoder_id: "6aaeccc849df9b16" }),
        decimal: "either",
        // Only the group the file's own text named. The unnamed one
        // contributes nothing rather than a null binding — a group the decoder
        // does not read is a different thing from one it reads as nothing.
        bindings: [{ segment: "s1-sc-drill", group: "num3",
                     slot: "depth_ratio_xd", type: "integer" }],
      })));
  });

  it("says so when a file has no shape to propose a decoder from", async () => {
    // A real answer about the file, reported as one — never an empty decoder
    // that would freeze and decode nothing.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({ sources: [source()] })]));
    vi.spyOn(papi, "proposeSourceDecoder").mockResolvedValue(proposal({
      proposal: {
        decoder: null, decoder_id: null, rows_read: 20, claimed: 0,
        unclaimed: 20, unclaimed_samples: [], coverage: {}, overlaps: [],
        reason: "No shape in this file repeats 5 times.",
      },
      review: null,
    }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    fireEvent.click(screen.getByRole("button",
                                     { name: "A decoder built from this file" }));
    fireEvent.click(screen.getByRole("button", { name: "Propose a decoder" }));

    expect(await screen.findByText(/No shape in this file repeats 5 times/))
      .toBeTruthy();
    // And it cannot be saved, because there is nothing to save.
    expect(screen.getByRole("button", { name: "Save decoding config" }))
      .toHaveProperty("disabled", true);
  });

  it("says when the model could not be reached, rather than looking thin",
     async () => {
    // The floor is the file's own text, and the screen has to say that is what
    // it is showing — a review that is quietly short reads like a file with
    // little in it.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({ sources: [source()] })]));
    const base = proposal();
    vi.spyOn(papi, "proposeSourceDecoder").mockResolvedValue({
      ...base,
      review: { ...base.review!, reason: "PROVIDER_FAILED", provider: "mock" },
    });
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    fireEvent.click(screen.getByRole("button",
                                     { name: "A decoder built from this file" }));
    fireEvent.click(screen.getByRole("button", { name: "Propose a decoder" }));

    expect(await screen.findByText(/The model could not be reached/)).toBeTruthy();
  });

  it("shows a file decoded by its own decoder as ready, and names it", async () => {
    // Which of the two paths read a row is the first thing anybody asks when a
    // decoded value looks wrong, so READY alone is not enough.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({
      ...BUILT,
      sources: [source({
        decoding: decoding({ rule_set: null, rule_set_resolved: false,
                             path: "decoder", decoder_id: "6aaeccc849df9b16",
                             decoder: artifact() }),
      })],
    })]));
    render(<CatalogScreen session={SESSION} />);

    // Two chips read READY here — the catalogue's and this file's — so the
    // assertion is that the file is one of them rather than that only one
    // exists. Which of the two paths made it ready is the next two lines.
    expect((await screen.findAllByText("READY")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Decoding" }));
    // It opens on the path the file actually uses, not on the rule set.
    expect(screen.getByRole("button", { name: "A decoder built from this file" }))
      .toHaveProperty("ariaPressed", "true");
    expect(screen.getByText(/Saved: 6aaeccc849df9b16, 1 shape/)).toBeTruthy();
  });

  it("says NO DECODER when a saved config names neither path", async () => {
    // Three states rather than a boolean, because the fixes differ: NOT SAVED
    // is a proposal nobody confirmed, NO DECODER is a config that is saved and
    // still cannot run.
    vi.spyOn(papi, "companyCatalogues")
      .mockResolvedValue(view([company({
      sources: [source({
        decoding: decoding({ rule_set: null, rule_set_resolved: false,
                             path: null }),
      })],
    })]));
    render(<CatalogScreen session={SESSION} />);

    expect(await screen.findByText("NO DECODER")).toBeTruthy();
  });

  it("re-analyses a stored file rather than making somebody upload it again", async () => {
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ sources: [source()] })]));
    const analyze = vi.spyOn(papi, "analyzeSource").mockResolvedValue(
      company({ sources: [source()] }));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    fireEvent.click(await screen.findByRole("button", { name: "Re-analyse" }));
    await waitFor(() => expect(analyze).toHaveBeenCalledWith(
      "t", "conn-a", "kennametal", "item-master.csv"));
  });

  it("says what to do about a file stored before its columns were read", async () => {
    // The seeded corpus, and any upload from before the analysis existed, has
    // no ingest report — so there are no headers to offer. Three empty menus
    // and a disabled Save is a dead end; re-analysing reads the stored bytes
    // and puts its columns and the parser's counts on record.
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        sources: [source({ ingest: null, filename: "kmt_zcnc_2026-07.csv",
                           decoding: decoding({ columns: null, analysis: null,
                                                confirmed_at: null,
                                                confirmed_by: null,
                                                ready: false }) })],
      })]));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Decoding" }));
    expect(await screen.findByText(/stored before its columns were read/))
      .toBeTruthy();
    expect(screen.queryByRole("button", { name: /Save decoding config/ })).toBeNull();
    // Re-analysing reads the stored bytes again, which is what puts its
    // headers on record — no re-upload needed for that any more.
    expect(screen.getByRole("button", { name: "Re-analyse" })).toBeTruthy();
  });

  it("names each file's own rule set and counts where one catalogue needed two", async () => {
    // Each file is decoded on its own, so with two of them there is no single
    // stamp and no single run report — and stating one would name the wrong
    // rule set for half the records. The catalogue-level fields come back null
    // in that case, and these are where the facts are.
    const second = { ...(BUILT.built_from ?? [])[0],
                     source_key: "range.csv", filename: "range.csv",
                     corpus_id: "cor2", rule_set: "yg1",
                     records: 300, rows_read: 340, quarantined: 40,
                     rows_emitted: 300,
                     stamp: { ...BUILT.stamp, ruleset_checksum: "aaaa1111bbbb2222",
                              run_id: "cccc3333dddd4444" } };
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({
        ...BUILT,
        // What the server sends when two files disagree: no report of the
        // catalogue's own, and a stamp with nothing they share.
        report: null,
        stamp: { engine_version: "0.10.0", schema_version: "1.0.0" },
        built_from: [...(BUILT.built_from ?? []), second],
        sources: [source(),
                  source({ source_key: "range.csv", filename: "range.csv",
                           corpus_id: "cor2" })],
      })]));
    render(<CatalogScreen session={SESSION} />);

    await screen.findByText(/6,717 decoded records/);
    // Both rule sets, and which file went through which.
    expect(screen.getByText(/zcnc, yg1/)).toBeTruthy();
    expect(screen.getByText(/item-master\.csv → zcnc · range\.csv → yg1/))
      .toBeTruthy();
    // Per file, the parser's own counts — never a census added up here.
    expect(screen.getByLabelText(/Per-file parse counts/)).toBeTruthy();
    expect(screen.queryByLabelText(/Per-family parse rates/)).toBeNull();
    expect(screen.getByText(/40 quarantined of 340 rows read/)).toBeTruthy();
    // And the stamp says it has no single value rather than rendering blank.
    expect(screen.getAllByText("differs per file")).toHaveLength(2);
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

    await waitFor(() => expect(screen.getByText(/6,717 decoded records/)).toBeTruthy());
    // The state is readable — the controls are not. The server refuses the
    // POST as well; this only keeps the screen honest about it.
    expect(screen.queryByRole("button", { name: /Rebuild/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add a file/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add a catalogue/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Rename/ })).toBeNull();
    expect(screen.getByText(/6,717 decoded records/)).toBeTruthy();
  });

  it("attributes a refused action to the catalogue it was tried on, and lets it be dismissed", async () => {
    // Written when this file's local `ProblemAlert` became `kit.ErrorState`
    // with its `onClose`, because the path had no test at all — and it is the
    // one place the server's own words reach the screen. Two claims:
    //
    // The cause is shown verbatim. The server names the specific thing — a
    // column the file lacks, a rule set the engine no longer ships — and a
    // screen that summarised that away would send somebody looking in the
    // wrong place.
    //
    // And it closes. What failed is one request standing beside controls that
    // still work, not a screen that could not load, which is exactly the
    // distinction `ErrorState`'s `onClose` marks: a load failure must not be
    // dismissible, because there is nothing behind it.
    const cause = "item-master.csv names rule set zcnc, which this engine no "
      + "longer ships.";
    vi.spyOn(papi, "companyCatalogues").mockResolvedValue(
      view([company({ sources: [source()] })]));
    vi.spyOn(papi, "buildCompanyCatalog").mockRejectedValue(new Error(cause));
    render(<CatalogScreen session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Build" }));

    expect(await screen.findByText(cause)).toBeTruthy();
    expect(screen.getByText("That did not work")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() =>
      expect(screen.queryByText("That did not work")).toBeNull());
    // The controls it stood beside are still there to try again with.
    expect(screen.getByRole("button", { name: "Build" })).toBeTruthy();
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
      built_from: [{ ...(BUILT.built_from ?? [])[0],
                     source_key: "yg1-prices.xlsx",
                     filename: "yg1-prices.xlsx", corpus_id: "cor9",
                     rule_set: "yg1", records: 3000, rows_read: 3000,
                     rows_emitted: 3000,
                     stamp: { ...BUILT.stamp, ruleset_checksum: "aaaa1111bbbb2222",
                              run_id: "cccc3333dddd4444" } }],
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

    expect(await findHeading("Kennametal")).toBeTruthy();
    expect(heading("YG-1")).toBeTruthy();
    expect(within(heading("Kennametal")).getByText("READY")).toBeTruthy();
    expect(within(heading("YG-1")).getByText("READY")).toBeTruthy();
    expect(screen.getByText(/6,717 decoded records/)).toBeTruthy();
    expect(screen.getByText(/3,000 decoded records/)).toBeTruthy();
    // Each catalogue's provenance, under its own heading.
    expect(screen.getByText("f67131512eb97513")).toBeTruthy();
    expect(screen.getByText("aaaa1111bbbb2222")).toBeTruthy();
    expect(screen.getAllByLabelText(/Per-family parse rates/)).toHaveLength(2);
    // The union, once.
    expect(screen.getByText("RESOLVES 9,717 RECORDS")).toBeTruthy();
    expect(screen.getByText(/9,717 records from 2 catalogues/)).toBeTruthy();
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

  it("adds a catalogue by name alone, and takes the company from the response", async () => {
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

    // Nothing is decided about decoding here: each price list uploaded into
    // this catalogue brings its own config, worked out from that file alone.
    expect(create).toHaveBeenCalledWith("t", "conn-a", { name: "YG-1" });
    // The response is the whole company, and it replaces the one on screen:
    // the new catalogue's section appears beside the built one.
    expect(await findHeading("YG-1")).toBeTruthy();
    expect(heading("Kennametal")).toBeTruthy();
    expect(within(heading("YG-1")).getByText("NO EXPORT")).toBeTruthy();
    expect(within(heading("Kennametal")).getByText("READY")).toBeTruthy();
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
