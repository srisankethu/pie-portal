import { demoCta } from "./cta";
import {
  DecisionCard, DEMO_LENGTH, FaqSection, FooterBlurb, LimitsSection, SubPageNav, TrustBand,
} from "./shared";
import { ERP_PAGES } from "./erp";
import {
  INDUSTRY_PAGES, verticalLabel, verticalTrail, type IndustryPageData,
} from "./industries";
import { EXAMPLE_ITEM, exampleFor } from "./worked-example";
import "./landing.css";

/**
 * One trade's landing page — the same page for every trade, from
 * `industries.ts`.
 *
 * **Problem-first, and the section order is the argument.** What goes wrong in
 * this trade, then what that costs, then what distributors do about it today,
 * and only then what PIE does. A capability here is the answer to a problem the
 * page has already stated in the reader's own vocabulary; it is never the
 * opening move.
 *
 * That is worth stating as a rule because the first version of this family broke
 * it while looking perfectly reasonable. Every page opened with the product's
 * own decision card beside the `h1` — a screenshot of software, above the fold,
 * on a page a reader had reached by searching for their own trouble — and named
 * the trade's problem in a single panel two screens further down. The card is
 * still here and it is still the one worked example this site argues from; it
 * sits in section six, where the reader has been told what it is for.
 *
 * `wrong` is the longest section on the page. If the product sections ever
 * outweigh the problem sections, this page has reverted to what it was.
 *
 * Built to the constraints `ErpPage.tsx` documents, because it is baked by the
 * same prerender and ships the same way. They are worth repeating rather than
 * cross-referencing, since breaking one of them produces a page that looks
 * right in a test and is broken in a browser:
 *
 *   - **Nothing here may need JavaScript.** No state, no handler. These
 *     documents ship without the module script, so a control whose behaviour is
 *     React's would render and do nothing. The nav is a `<details>`, which the
 *     browser opens by itself.
 *   - **In-page links to the landing page are absolute** (`/#talk`), because a
 *     bare `#talk` on this document is a fragment that goes nowhere. In-page
 *     links to *this* document's own sections are bare (`#wrong`), because they
 *     resolve here.
 *   - **Only `landing.css` may style it.** The built stylesheet contains what
 *     the client graph imports; a stylesheet imported only from here would
 *     compile during the prerender and never be emitted. Every class below is
 *     one the landing page already uses, with one exception — `.lp-crumbs`,
 *     added to `landing.css` for the trail this family renders.
 *
 * Why this is a sibling of `ErpPage` rather than a mode of it: the two share
 * their chrome and none of their argument. An ERP page's body is a permission
 * list, a write capability and a set of gaps read out of a connector module; a
 * trade page's is four named problems, a mechanism, a baseline and a worked
 * line. Parameterising one component over both would give it two disjoint
 * halves and a prop that selects between them, which is the
 * interface-segregation failure `CLAUDE.md` §5 describes. What they genuinely
 * share is in `shared.tsx` and is shared: the nav's structure, `TrustBand`,
 * `FooterBlurb`, `DEMO_LENGTH`, `DecisionCard`, `LimitsSection`, `FaqSection`.
 *
 * The `speed` discipline is the one thing in here that is not editorial. A page
 * whose trade is `"matched"` may not use the vocabulary of cross-referencing
 * anywhere above its own limits section — which is where those words belong,
 * because that section exists to disclaim them. `industries.test.ts` reads the
 * rendered markup and fails on it, so the order of the sections below is load
 * bearing as well as editorial: `LimitsSection` must stay above the FAQ.
 */
export function IndustryPage({ page }: { page: IndustryPageData }) {
  const heroDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  const closingDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  const trail = verticalTrail(page);
  // One currency, baked. There is no clock on a document that ships no script,
  // so `detectRegion` has nothing to read and the landing page's swap-on-mount
  // cannot happen here. INTL is the region these pages are addressed to, and a
  // rupee figure in a statically served document is the one thing
  // `prerender.test.tsx` forbids outright.
  const price = exampleFor("INTL");
  /** The systems this trade most often runs, and then the rest.
   *
   *  Both rows are derived from `ERP_PAGES` rather than written out, for the
   *  reason every other cross-family list on this site is: a hand-kept list is
   *  how a new connector becomes unreachable from six pages at once, silently,
   *  because the markup is still valid. `erpSlugs` decides the order of the
   *  first row; the second row is whatever is left, so the two together are
   *  always all seven and cannot drift out of that. */
  const primaryErps = page.erpSlugs
    .map((slug) => ERP_PAGES.find((erp) => erp.slug === slug))
    .filter((erp): erp is (typeof ERP_PAGES)[number] => erp !== undefined);
  const otherErps = ERP_PAGES.filter((erp) => !page.erpSlugs.includes(erp.slug));

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <SubPageNav />

        {/* The trail. Three levels, and the middle one is a real destination:
            `/#trades` is the front page's own strip of trade pages, which is
            this family's index. There is no `/industries` listing document, and
            a crumb pointing at one would be the dangling cross-document link
            `shared.tsx` documents against itself — valid markup that scrolls to
            the top of the front page and looks like it worked.

            `verticalTrail` is shared with `scripts/prerender.mjs`, which emits
            the `BreadcrumbList` node from the same array. That order is the
            site's standing rule and the reason `FAQPage` was withheld until a
            page rendered a visible FAQ: schema may restate what is on the page
            and nothing else. */}
        <nav className="lp-crumbs" aria-label="Breadcrumb">
          <div className="lp-wrap">
            <ol>
              {trail.map((crumb, i) => (
                <li key={crumb.path}>
                  {i === trail.length - 1
                    /* The current page is not a link to itself. It carries
                       `aria-current` so the trail's last item is announced as
                       where the reader is, rather than as a fourth place they
                       could go. */
                    ? <span aria-current="page">{crumb.name}</span>
                    : <a href={crumb.path}>{crumb.name}</a>}
                </li>
              ))}
            </ol>
          </div>
        </nav>

        <main>
          {/* The hero states the failure mode and asks for nothing else. No
              card, no capability, no three-column grid of what the product
              does — the next thing under this is the problem, which is what the
              reader came to read about. */}
          <header className="lp-hero" id="top">
            <div className="lp-wrap">
              <div className="lp-hero-copy">
                <p className="lp-eyebrow">{page.eyebrow}</p>
                <h1>
                  {page.headline.lead}<em>{page.headline.em}</em>
                  {page.headline.tail}
                </h1>
                <p className="lp-sub">{page.sub}</p>
                <div className="lp-ctas">
                  <a className="lp-btn solid" {...heroDemo.props}>{heroDemo.label}</a>
                  <a className="lp-btn" href="#wrong">What goes wrong here</a>
                </div>
                <p className="lp-fine">
                  A working session on your numbers, not a slide deck. No
                  account, no card, and nothing connected until you say so.
                </p>
              </div>
            </div>
          </header>

          {/* ── Section 2. The longest thing on the page, and deliberately so. */}
          <div className="lp-dim"><b>What goes wrong here</b></div>
          <section id="wrong">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Four things that go wrong on a {page.short} book</h2>
                <p>
                  Named rather than categorized. &ldquo;Margin erosion&rdquo; is
                  a heading a reader cannot check against their own week; each of
                  these is something that happened on a Tuesday, and if none of
                  them is familiar then this is the wrong page and the rest of it
                  will not improve.
                </p>
              </div>
              <div className="lp-two">
                {page.wrong.map((item) => (
                  <div className="lp-outcome" key={item.title}>
                    <p className="lp-eyebrow">{item.tag}</p>
                    <h3>{item.title}</h3>
                    <p>{item.body}</p>
                    <ul>
                      {item.notes.map((note) => <li key={note}>{note}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ── Section 3. The mechanism. No figures: the only numbers on this
                page are the worked card's, and they are labelled sample. */}
          <div className="lp-dim"><b>What it costs</b></div>
          <section id="cost">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>What it costs is the way it accumulates</h2>
                <p>{page.cost.lead}</p>
              </div>
              <div className="lp-two">
                <div className="lp-panel">
                  <h3>How it compounds</h3>
                  <p>{page.cost.compounds}</p>
                </div>
                <div className="lp-panel">
                  <h3>Why nothing catches it</h3>
                  <p>{page.cost.invisible}</p>
                </div>
              </div>
              <p className="lp-worked-note">
                No figure on this page is a claim about your book. There are no
                industry averages here and no recovered-margin percentages: the
                only numbers on the page are on the card further down, and they
                are sample figures re-derived by a test from the operands the
                card itself shows.
              </p>
            </div>
          </section>

          {/* ── Section 4. The honest baseline, and honest in both directions:
                most of these are reasonable answers to a real constraint. */}
          <div className="lp-dim"><b>What distributors do today</b></div>
          <section id="today">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>What a {page.short} desk does about it today</h2>
                <p>
                  Not a list of mistakes. Every one of these is a sensible
                  response to a real constraint, which is exactly why it has
                  survived — and the last one is usually the most rational of
                  them. A product that does not understand why the current answer
                  is the current answer has nothing useful to replace it with.
                </p>
              </div>
              <div className="lp-two">
                {page.today.map((item) => (
                  <div className="lp-panel" key={item.practice}>
                    <h3>{item.practice}</h3>
                    <p>{item.body}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* The pattern-match, said once and here rather than in the hero: a
              reader who has decided that dashboards are things nobody opens is
              about to file the next section under "another dashboard", and this
              is the paragraph before that happens. Leading a page with what a
              product is not is a weak opening; not saying it at all loses the
              reader at the top of section five. */}
          <div className="lp-notwhat">
            <div className="lp-wrap">
              <b>Not an ERP, not a dashboard, not a BI tool.</b> Your ERP keeps
              the records and PIE does not replace it. There is no chart to go
              and look at every morning: the platform checks each quote line as
              it is priced, and raises the handful of accounts that changed.
            </div>
          </div>

          {/* ── Section 5. One paragraph per capability that genuinely applies
                to this trade. The count varies per page on purpose — a
                capability that does not apply is omitted rather than stretched,
                which is why the electrical page has no floor paragraph. */}
          <div className="lp-dim"><b>Where PIE intervenes</b></div>
          <section id="intervenes">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Where PIE intervenes, and at which moment</h2>
                <p>
                  Every figure below is deterministic arithmetic over rows your
                  ERP already wrote, stamped with the version of the margin
                  policy that judged it. The AI on this platform reads those
                  numbers and phrases them; it never produces one. What is not
                  listed here does not apply to a {page.short} book, and the
                  limits further down say which of those are permanent.
                </p>
              </div>
              <div className="lp-grid3">
                {page.intervenes.map((item) => (
                  <div className="lp-feat" key={item.title}>
                    <h3>{item.title}</h3>
                    <p>{item.body}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ── Section 6. The worked line, and the card the whole site argues
                from — here rather than in the hero. */}
          <div className="lp-dim"><b>One line, start to finish</b></div>
          <section id="worked">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>One quote line, start to finish</h2>
                <p>{page.worked.lead}</p>
              </div>
              {/* The same card the front page draws, with this trade's own code
                  on it where the trade has one. See `exampleItem`. */}
              <DecisionCard item={page.exampleItem ?? EXAMPLE_ITEM} price={price} />
              <div className="lp-grid3 lp-steps">
                {page.worked.steps.map((step) => (
                  <div className="lp-step" key={step.title}>
                    <h3>{step.title}</h3>
                    <p>{step.body}</p>
                  </div>
                ))}
              </div>
              {/* Prose, not a second copy of the numbers: the figures are on the
                  card, `worked-example.ts` derives them and its own test
                  re-derives them. Restating them in a sentence here is how the
                  card and the paragraph beside it start to disagree — the defect
                  that module was written to prevent. */}
              <p className="lp-worked-note">
                Sample figures throughout. No customer&rsquo;s numbers appear on
                a public page, every figure on the card is re-derived by a test
                from the same operands it shows, and the floor is cost divided by
                one minus the margin floor your policy sets — nothing rounded in
                your favor, because a floor rounded down is a floor that has
                moved.
              </p>
            </div>
          </section>

          {/* ── Section 7. Above the FAQ, and that order is enforced: the
                vocabulary this page may not use about itself is disclaimed
                here, and `industries.test.ts` scans everything above this
                section for it. */}
          <LimitsSection
            label="What this does not solve"
            lead={<>
              Every item below is a thing this platform does not do on a{" "}
              {page.short} book today. They are on the page because a capability
              list is not what a distributor who has survived one implementation
              believes — and because the alternative is you discovering them
              after a pilot.
            </>}
            items={page.notServed}
          />

          <TrustBand />

          {/* ── Section 8. */}
          <FaqSection heading={`What ${page.short} distributors ask first`} faq={page.faq} />

          {/* ── The ERP routing layer. A route to seven pages and not a summary
                of them: no permission list, no write capability and no gap is
                restated here, because each of those is one page's job and a
                copy of it on seven trade pages is seven places for one fact to
                go stale. What this section adds is the thing an `/erp/` page
                cannot say, because it is about the trade rather than the
                system: which book a reader in this trade is likely to be on. */}
          <div className="lp-dim"><b>On the system you already run</b></div>
          <section id="systems">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Which of the seven this trade runs</h2>
                <p>{page.erpLead}</p>
              </div>
              <div className="lp-sched">
                <div className="lp-sched-row">
                  <span className="lp-sched-label">Most common here</span>
                  {primaryErps.map((erp) => (
                    <a className="lp-sys" key={erp.slug} href={`/erp/${erp.slug}`}>
                      {erp.name}
                    </a>
                  ))}
                </div>
                <div className="lp-sched-row">
                  <span className="lp-sched-label">Also read</span>
                  {otherErps.map((erp) => (
                    <a className="lp-sys" key={erp.slug} href={`/erp/${erp.slug}`}>
                      {erp.name}
                    </a>
                  ))}
                  <span className="lp-sched-note">
                    Each of those pages names the permissions PIE asks for, what
                    it can create back in that system, and what it cannot read at
                    all — read out of the connector that implements it. This
                    section is the route to them, not a summary of them.
                  </span>
                </div>
              </div>
            </div>
          </section>

          {/* ── Section 9. */}
          <div className="lp-final">
            <div className="lp-wrap">
              <h2>Your book already knows where the margin went.</h2>
              <p>
                {DEMO_LENGTH}, your own numbers on the screen, and an honest
                answer about what PIE can and cannot see on a {page.short} book.
              </p>
              <div className="lp-ctas lp-ctas-centred">
                <a className="lp-btn solid" {...closingDemo.props}>{closingDemo.label}</a>
                <a className="lp-btn" href="/">Read the full product page</a>
              </div>
            </div>
          </div>
        </main>

        <footer className="lp-footer">
          <div className="lp-wrap">
            <FooterBlurb />
            {/* Derived, for the reason `ErpPage`'s equivalent row is: a
                hand-written list of siblings is how a new page becomes an
                orphan, and an orphan is still valid markup. */}
            <div className="lp-footer-erp">
              {INDUSTRY_PAGES.filter((other) => other.slug !== page.slug).map((other) => (
                <span key={other.slug}>
                  <a href={`/industries/${other.slug}`}>{verticalLabel(other)}</a>
                  <span className="lp-sep" aria-hidden="true"> &middot; </span>
                </span>
              ))}
              <a href="/">Everything else</a>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
