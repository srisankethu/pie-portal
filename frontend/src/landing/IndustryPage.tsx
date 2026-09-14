import { demoCta } from "./cta";
import {
  DecisionCard, DEMO_LENGTH, FaqSection, FooterBlurb, LimitsSection, SubPageNav, TrustBand,
} from "./shared";
import { ERP_PAGES } from "./erp";
import { INDUSTRY_PAGES, verticalLabel, type IndustryPageData } from "./industries";
import { EXAMPLE_ITEM, exampleFor } from "./worked-example";
import "./landing.css";

/**
 * One trade's landing page — the same page for every trade, from
 * `industries.ts`.
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
 *     bare `#talk` on this document is a fragment that goes nowhere.
 *   - **Only `landing.css` may style it.** The built stylesheet contains what
 *     the client graph imports; a stylesheet imported only from here would
 *     compile during the prerender and never be emitted. Every class below is
 *     one the landing page already uses.
 *
 * Why this is a sibling of `ErpPage` rather than a mode of it: the two share
 * their chrome and none of their argument. An ERP page's body is a permission
 * list, a write capability and a set of gaps read out of a connector module; a
 * trade page's is a problem statement, a worked line and an FAQ. Parameterising
 * one component over both would give it two disjoint halves and a prop that
 * selects between them, which is the interface-segregation failure `CLAUDE.md`
 * §5 describes — every instance declaring fields only one kind of page uses.
 * What they genuinely share is in `shared.tsx` and is shared: the nav's
 * structure, `TrustBand`, `FooterBlurb`, `DEMO_LENGTH`, `DecisionCard`.
 *
 * The `speed` discipline is the one thing in here that is not editorial. A page
 * whose trade is `"matched"` gets a resolution section that describes matching
 * against the reader's own catalogue, and it may not use the vocabulary of
 * cross-referencing. `industries.test.ts` reads the rendered markup and fails
 * on it, because that is the sentence a well-meaning edit widens.
 */
export function IndustryPage({ page }: { page: IndustryPageData }) {
  const heroDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  const closingDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  // One currency, baked. There is no clock on a document that ships no script,
  // so `detectRegion` has nothing to read and the landing page's swap-on-mount
  // cannot happen here. INTL is the region these pages are addressed to, and a
  // rupee figure in a statically served document is the one thing
  // `prerender.test.tsx` forbids outright.
  const price = exampleFor("INTL");
  const erps = page.erpSlugs
    .map((slug) => ERP_PAGES.find((erp) => erp.slug === slug))
    .filter((erp): erp is (typeof ERP_PAGES)[number] => erp !== undefined);

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <SubPageNav />

        <main>
          <header className="lp-hero" id="top">
            <div className="lp-wrap lp-hero-grid">
              <div className="lp-hero-copy">
                <p className="lp-eyebrow">{page.eyebrow}</p>
                <h1>
                  {page.headline.lead}<em>{page.headline.em}</em>
                  {page.headline.tail}
                </h1>
                <p className="lp-sub">{page.sub}</p>
                <div className="lp-ctas">
                  <a className="lp-btn solid" {...heroDemo.props}>{heroDemo.label}</a>
                  <a className="lp-btn" href="#worked">See the worked line</a>
                </div>
                <p className="lp-fine">
                  A working session on your numbers, not a slide deck. No
                  account, no card, and nothing connected until you say so.
                </p>
              </div>
              {/* The same card the front page draws, with this trade's own
                  code on it where the trade has one. See `exampleItem`. */}
              <DecisionCard item={page.exampleItem ?? EXAMPLE_ITEM} price={price} />
            </div>
          </header>

          <div className="lp-notwhat">
            <div className="lp-wrap">
              <b>Not an ERP, not a dashboard, not a BI tool.</b> Your ERP keeps
              the records and PIE does not replace it. There is no chart to go
              and look at every morning: the platform checks each quote line as
              it is priced, and raises the handful of accounts that changed.
            </div>
          </div>

          <div className="lp-dim"><b>The problem</b></div>
          <section id="problem">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>{page.problem.title}</h2>
                <p>{page.problem.body}</p>
              </div>
              <div className="lp-two">
                <div className="lp-panel">
                  <h3>Why it compounds</h3>
                  <p>{page.problem.detail}</p>
                </div>
                <div className="lp-panel">
                  <h3>Where PIE intervenes</h3>
                  <p>
                    At the moment a price is typed on a quote line, before the
                    quote is sent — not in a monthly review and not in a report.
                    A line that breaches the floor your policy sets is held for a
                    named approver, and the sign-off is append-only and carries
                    the policy version that was in force when it was given.
                  </p>
                </div>
              </div>
            </div>
          </section>

          <div className="lp-dim"><b>The worked line</b></div>
          <section id="worked">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>One line, one floor, one held quote</h2>
                <p>
                  The card above is the manager&rsquo;s decision screen as the
                  product draws it, with sample figures. The arithmetic under it
                  is the arithmetic the platform applies.
                </p>
              </div>
              <div className="lp-two">
                <div className="lp-panel">
                  <h3>How the floor is computed</h3>
                  {/* Prose, not a second copy of the numbers: the figures are
                      on the card, `worked-example.ts` derives them and its own
                      test re-derives them. Restating them here in a sentence is
                      how the card and the paragraph beside it start to
                      disagree — which is the defect that module was written
                      to prevent. */}
                  <p>
                    Cost, divided by one minus the margin floor your policy
                    sets. Nothing is rounded in your favor: a floor rounded
                    down is a floor that has moved. The cost is the one on the
                    AP-invoice line your ERP already wrote, and the policy is
                    the one you set and can edit.
                  </p>
                </div>
                <div className="lp-panel">
                  <h3>What the ledger counts</h3>
                  <p>
                    The movement <em>up to the floor</em>, and no further.
                    Clearing a floor by more than it asked for is the
                    salesperson&rsquo;s judgement and not something the
                    guardrail did, so the ledger does not claim it. That is the
                    same rule that makes a month with no detection read UNKNOWN
                    rather than zero.
                  </p>
                </div>
              </div>
              <p className="lp-worked-note">
                Sample figures throughout. No customer&rsquo;s numbers appear on
                a public page, and every figure here is re-derived by a test
                from the same operands the card shows.
              </p>
            </div>
          </section>

          <div className="lp-dim"><b>Reading the enquiry</b></div>
          <section id="resolution">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>{page.resolution.title}</h2>
                <p>{page.resolution.body}</p>
              </div>
              <ul className="lp-gaps">
                {page.resolution.points.map((point) => <li key={point}>{point}</li>)}
              </ul>
            </div>
          </section>

          <div className="lp-dim"><b>What it does with it</b></div>
          <section id="does">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Three things, on your own numbers</h2>
                <p>
                  Every figure is deterministic arithmetic over rows your ERP
                  already wrote, stamped with the version of the margin policy
                  that judged it. The AI on this platform reads those numbers and
                  phrases them; it never produces one.
                </p>
              </div>
              <div className="lp-grid3">
                {page.fit.map((item) => (
                  <div className="lp-feat" key={item.title}>
                    <h3>{item.title}</h3>
                    <p>{item.body}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <LimitsSection
            label="What PIE does not do here"
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

          <FaqSection heading="What distributors ask first" faq={page.faq} />

          <div className="lp-dim"><b>On the system you already run</b></div>
          <section id="systems">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>What PIE reads, per ERP</h2>
                <p>
                  This page is about the trade. What PIE can actually see
                  depends on the book, and each system&rsquo;s page names the
                  permissions it needs and what it cannot read at all.
                </p>
              </div>
              <div className="lp-grid3">
                {erps.map((erp) => (
                  <div className="lp-feat" key={erp.slug}>
                    <h3><a href={`/erp/${erp.slug}`}>{erp.name}</a></h3>
                    <p>
                      {/* The gap count is read off the registry, not written
                          down. It said "the seven things it cannot see",
                          which is true of Prophet 21 and was being asserted of
                          every system in this row — the exact shape of stale
                          claim the `/erp/` pages exist to avoid. */}
                      {erp.costed
                        ? `What PIE reads from ${erp.short}, what it writes back, and the ${erp.gaps.length} things it cannot see.`
                        : `${erp.short} holds no item-level purchase cost, so PIE reports margin as unknown on that book rather than estimating one.`}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </section>

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
                  <span className="lp-sep" aria-hidden="true"> · </span>
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
