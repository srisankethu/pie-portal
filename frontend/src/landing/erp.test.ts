/** The ERP pages must say what the connectors actually do.
 *
 * This is the test the landing page's own docstring asks for and never had:
 * *"Marketing copy drifts from the code silently, because nothing compiles it
 * and no test fails."* Six claims on that page had drifted by the time anyone
 * checked, and every one of them read as good news.
 *
 * The ERP pages make the most specific claims on the site — this is exactly
 * what PIE reads out of your system, this is what it can write back, this is
 * what it cannot see — and they are the claims a buyer will check first,
 * because their own administrator has to grant every one of them. So they are
 * held against the connector modules' own source, in both directions: a stage
 * declared on a page and not in the connector fails here, and a stage the
 * connector reads and the page does not list fails here too. The second
 * direction is the one that matters over time — a capability added to a
 * connector six months from now should show up on the page that sells it.
 *
 * Read with Vite's `?raw`, the same way `platform/route.test.ts` reads the
 * server's own destination list rather than restating it.
 */
import { describe, expect, it } from "vitest";

import acumaticaSource from "../../../backend/app/ingestion/erp/acumatica.py?raw";
import netsuiteSource from "../../../backend/app/ingestion/erp/netsuite.py?raw";
import prophet21Source from "../../../backend/app/ingestion/erp/prophet21.py?raw";
import zohoSource from "../../../backend/app/ingestion/zoho_client.py?raw";

import { ERP_PAGES, erpPage } from "./erp";

const SOURCE: Record<string, string> = {
  prophet21: prophet21Source,
  netsuite: netsuiteSource,
  acumatica: acumaticaSource,
  zoho: zohoSource,
};

/** Zoho Books is not a connector, and declares what it reads differently.
 *
 *  It predates `ingestion/erp/` and is the book this platform was built
 *  against, so there is no `Permission(reads=…)` to match: the equivalent
 *  declaration is `SCOPE_FOR_PATH`, the map from Zoho API path to the OAuth
 *  scope that path needs. That map is load-bearing rather than descriptive —
 *  it is what turns a 401 into "grant ZohoBooks.creditnotes.READ" instead of a
 *  symptom — so an endpoint cannot be read without appearing in it, which is
 *  exactly the property this test needs from a declaration.
 *
 *  The comparison stays both-directional for the reason the file header gives:
 *  a stage the page claims and Zoho does not read is a lie now, and a stage
 *  Zoho reads and the page omits is a capability nobody is being sold. */
const ZOHO = "zoho";

function pathsIn(source: string, table: string): Set<string> {
  const body = source.match(new RegExp(`${table}: dict\\[str, str\\] = \\{([\\s\\S]*?)\\n\\}`))?.[1];
  if (body === undefined) throw new Error(`${table} not found in zoho_client.py`);
  return new Set([...body.matchAll(/^\s*"([a-z]+)":/gm)].map((m) => m[1]));
}

/** The stages a connector declares it reads.
 *
 *  `reads=("invoices",)` on a `Permission` is the declaration, and
 *  `tests/decision_platform/test_erp_connectors.py::test_connector_permissions`
 *  already holds it against the `list_<stage>` methods that implement it — so
 *  matching this is matching the implementation, one step removed. */
function declaredReads(source: string, connector: string): Set<string> {
  if (connector === ZOHO) return pathsIn(source, "SCOPE_FOR_PATH");
  return new Set(
    [...source.matchAll(/reads=\(([^)]*)\)/g)]
      .flatMap((m) => [...m[1].matchAll(/"([a-z_]+)"/g)].map((s) => s[1])),
  );
}

function declaredWrites(source: string, connector: string): Set<string> {
  if (connector === ZOHO) return pathsIn(source, "WRITE_SCOPE_FOR_PATH");
  return new Set(
    [...source.matchAll(/writes=\(([^)]*)\)/g)]
      .flatMap((m) => [...m[1].matchAll(/"([a-z_]+)"/g)].map((s) => s[1])),
  );
}

describe.each(ERP_PAGES.map((p) => [p.slug, p] as const))("/erp/%s", (_slug, page) => {
  const source = SOURCE[page.connector];

  it("is backed by a connector module that exists", () => {
    expect(source).toBeTruthy();
    // The page names the system; the module names itself the same way. A
    // connector in `erp/` declares its own key as a literal; `zoho_client.py`
    // predates that registry and never did, so what identifies it is the
    // vocabulary it is written in — every scope it names is a Zoho Books one.
    if (page.connector === ZOHO) expect(source).toContain("ZohoBooks.");
    else expect(source).toContain(`"${page.connector}"`);
  });

  it("lists exactly the stages that connector declares it reads", () => {
    const claimed = new Set(page.reads.map((r) => r.stage));
    const declared = declaredReads(source, page.connector);
    // Both directions, and reported as sorted arrays so a failure names the
    // stage rather than printing two Sets.
    expect([...claimed].sort()).toEqual([...declared].sort());
  });

  it("claims a write only where the connector declares one", () => {
    const writes = declaredWrites(source, page.connector);
    if (page.writes === null) {
      expect(writes.size).toBe(0);
    } else if (page.connector === ZOHO) {
      // Zoho Books is the one connection PIE creates two kinds of record in,
      // and the page has to say both. This is the drift that shipped: two
      // pages read "the only thing PIE ever creates anywhere", which was
      // written when an estimate was, and `WRITE_SCOPE_FOR_PATH` has carried
      // `items` all along. The test that would have caught it is this one,
      // and it did not exist for the connection that has the second write.
      expect([...writes].sort()).toEqual(["estimates", "items"]);
      expect(page.writes).toMatch(/estimate/i);
      expect(page.writes).toMatch(/item/i);
    } else {
      expect(writes.has("sales_quotes")).toBe(true);
      // And the copy has to say what is created, not merely that something is.
      expect(page.writes).toMatch(/quote|estimate/i);
    }
  });

  it("does not claim to create nothing anywhere else", () => {
    // "the only thing PIE ever creates anywhere" was true of no deployment
    // with Zoho Books connected — the Quote Builder creates an item there
    // too. A page may say what it is the only thing PIE creates *in this
    // system*; it may not make a claim about every other system from here.
    if (page.writes !== null) {
      expect(page.writes).not.toMatch(/creates anywhere/i);
    }
  });

  it("names a source record for every stage it claims", () => {
    for (const read of page.reads) {
      expect(read.label.length).toBeGreaterThan(0);
      expect(read.source.length).toBeGreaterThan(0);
    }
  });

  it("states its gaps rather than only its capabilities", () => {
    // Not a style rule. A page that lists what a connector reads and stops
    // there is the page a buyer discovers is incomplete in week three, and
    // CLAUDE.md §1 holds the product itself to the same standard: an absence
    // is reported as an absence.
    expect(page.gaps.length).toBeGreaterThanOrEqual(3);
  });

  it("keeps the unresearched panel a visible placeholder", () => {
    // `evidence` is the one field with no source in the code. It stays a
    // token until somebody has real language from a real distributor, with
    // permission to print it.
    expect(page.evidence).toMatch(/^\{\{[A-Z0-9_]+\}\}$/);
  });
});

describe("the set of pages", () => {
  it("has one page per slug, and every slug is URL-shaped", () => {
    const slugs = ERP_PAGES.map((p) => p.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const slug of slugs) expect(slug).toMatch(/^[a-z0-9-]+$/);
  });

  it("resolves a page by slug and refuses an unknown one", () => {
    expect(erpPage("netsuite").connector).toBe("netsuite");
    expect(() => erpPage("sage-x3")).toThrow(/no ERP page/);
  });

  it("gives every page its own title and description", () => {
    const titles = new Set(ERP_PAGES.map((p) => p.title));
    const descriptions = new Set(ERP_PAGES.map((p) => p.description));
    expect(titles.size).toBe(ERP_PAGES.length);
    expect(descriptions.size).toBe(ERP_PAGES.length);
    for (const page of ERP_PAGES) {
      // The system's name in the first words of both: a distributor searching
      // for their own ERP has to see it in the result, not after a clause.
      expect(page.title).toContain(page.name);
      expect(page.description).toContain(page.short);
      // Long enough to be a description, short enough not to be truncated.
      expect(page.description.length).toBeGreaterThan(80);
    }
  });
});
