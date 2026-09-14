import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
// Read with Vite's `?raw`, the same way `erp.test.ts` reads the connector
// modules — `node:fs` is not in this project's type graph, and a test that
// reached for it would typecheck nowhere but in somebody's editor.
import enumsSource from "../../../backend/app/domain/enums.py?raw";
import { ROLE_PAGES } from "./roles";
import { RolePage } from "./RolePage";

const rendered = ROLE_PAGES.map((page) => ({
  page,
  html: renderToStaticMarkup(RolePage({ page })),
}));

/** The roles the product actually enforces, out of `domain/enums.Role`.
 *
 *  Read from the backend rather than restated here, for the reason
 *  `erp.test.ts` reads the connector modules: a page family that describes
 *  roles is worth exactly as much as its agreement with the roles that exist,
 *  and a hand-copied list agrees with them only until somebody adds one. */
function declaredRoles(): string[] {
  const block = enumsSource.match(/class Role\(str, Enum\):([\s\S]*?)\n\nclass /)?.[1] ?? "";
  return [...block.matchAll(/^\s{4}([A-Z_]+)\s*=\s*"/gm)].map((m) => m[1]);
}

describe("the role registry", () => {
  it("has exactly one page per role the product enforces", () => {
    // Both directions. A role added to the backend with no page leaves a buyer
    // with no page; a page for a role that does not exist describes a product
    // nobody can buy. Neither is visible without this.
    const roles = declaredRoles();
    expect(roles.length, "no roles parsed out of enums.py — the check is empty")
      .toBeGreaterThan(0);
    expect([...ROLE_PAGES.map((p) => p.role)].sort()).toEqual([...roles].sort());
  });

  it("gives every page a unique slug", () => {
    const slugs = ROLE_PAGES.map((p) => p.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("states no screen count or nav-item count, on any page", () => {
    // Those numbers exist in an internal document and move whenever a screen
    // is added. A count on a public page that nobody re-derives is a claim
    // that goes stale in silence — which is the whole failure mode this site's
    // tests are shaped around.
    for (const { page, html } of rendered) {
      expect(html, `/roles/${page.slug} prints a screen or nav-item count`)
        .not.toMatch(/\b\d+\s+(nav items?|screens?|menu items?)\b/i);
    }
  });
});

describe("the withheld column, which is the argument", () => {
  it("names the withholding as the server's, not a screen's", () => {
    // The claim that distinguishes this from every "role-based views" page:
    // the fields are absent from the response. A page that said "hidden" would
    // be describing a different, weaker product — and one whose control a
    // network tab defeats.
    for (const { page, html } of rendered) {
      expect(html, `/roles/${page.slug} does not say where the withholding happens`)
        .toMatch(/absent from the response/i);
    }
  });

  it("gives the salesperson page the cost and margin exclusions explicitly", () => {
    const sales = rendered.find(({ page }) => page.role === "SALESPERSON")!;
    for (const field of ["cost", "margin", "purchase spend"]) {
      expect(sales.html.toLowerCase(),
        `the salesperson page does not name ${field} as withheld`).toContain(field);
    }
    expect(sales.page.cannotSee.length).toBeGreaterThanOrEqual(3);
  });

  it("leaves the owner's withheld column empty and says so rather than hiding it", () => {
    // An empty block is worse than an absent one — `content.ts` states that
    // rule for the landing page's placeholder sections. Here the emptiness is
    // itself the fact, so it is said out loud. What must not happen is a
    // restraint invented for the owner to keep the layout symmetrical.
    const owner = rendered.find(({ page }) => page.role === "OWNER")!;
    expect(owner.page.cannotSee).toEqual([]);
    expect(owner.html).toContain("Nothing");
    expect(owner.html).toMatch(/will not invent one/i);
  });

  it("never claims a role restriction the other pages contradict", () => {
    // The salesperson is the only role cost is withheld from. If a manager or
    // owner page ever claimed the same, two pages would be describing two
    // different products to two people who talk to each other.
    for (const { page, html } of rendered) {
      if (page.role === "SALESPERSON") continue;
      const body = html.split('<section id="sees">')[1]?.split("</section>")[0] ?? "";
      expect(body, `/roles/${page.slug} claims cost is withheld from it`)
        .not.toMatch(/never reaches you[\s\S]{0,400}purchase cost/i);
    }
  });
});

describe("what a role page may promise", () => {
  it("describes the below-cost escalation as the policy setting it is", () => {
    // `approvals.authority_for` escalates to the owner only when the line is
    // below cost AND `policy.below_cost_requires_owner` is set. A page stating
    // it as a fixed rule would be describing an organization that had turned
    // it on, to one that had not.
    for (const { page, html } of rendered) {
      if (!/below[- ]cost/i.test(html)) continue;
      expect(html, `/roles/${page.slug} states the below-cost rule without its policy`)
        .toMatch(/where (your|the) (organization|policy)|policy (setting|requires)/i);
    }
  });

  it("says what it does not do, on every page", () => {
    for (const { page } of rendered) {
      expect(page.notServed.length,
        `/roles/${page.slug} lists too few limits to be believed`)
        .toBeGreaterThanOrEqual(3);
    }
  });

  it("renders every question it declares, so the FAQ schema restates the page", () => {
    for (const { page, html } of rendered) {
      expect(page.faq.length).toBeGreaterThan(0);
      for (const { question } of page.faq) {
        const escaped = question
          .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
        expect(html, `/roles/${page.slug} declares a question it does not render`)
          .toContain(escaped);
      }
    }
  });

  it("keeps every role reachable from every other", () => {
    // Three pages that only the landing page links to are three pages a reader
    // arrives at and leaves.
    for (const { page, html } of rendered) {
      for (const other of ROLE_PAGES) {
        if (other.slug === page.slug) continue;
        expect(html, `/roles/${page.slug} does not link /roles/${other.slug}`)
          .toContain(`href="/roles/${other.slug}"`);
      }
    }
  });
});

describe("the first heading names what the page is about", () => {
  // The rule the ERP family is already tested on, applied to this one.
  // `erp.ts` states it: a distributor searching for their own system should
  // land on a page that names it in the first line, because "Prophet 21" is
  // what they call their problem and a page that says "your ERP" is a page
  // about somebody else.
  //
  // This family shipped without that check and immediately broke it. The
  // cutting tools page led with "Quote without losing the margin in the
  // cross-reference" — true, well-formed, and it never said "cutting tools",
  // so the one reader it was written for could not tell it was theirs.
  it("puts the role in the h1 of every page", () => {
    expect(ROLE_PAGES.length).toBeGreaterThan(0);
    for (const page of ROLE_PAGES) {
      const html = renderToStaticMarkup(RolePage({ page }));
      // Tags stripped and whitespace collapsed: the headline puts one word in
      // an <em>, so the raw markup reads "industrial and <em>MRO</em>" and a
      // substring match on it fails for a heading that is perfectly correct.
      // The rule is about the sentence a reader sees, not the elements it is
      // built from.
      const h1 = (html.match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1] ?? "")
        .replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
      expect(h1, `/roles/${page.slug} h1 does not name ${page.noun}`)
        .toContain(page.noun);
    }
  });
});
