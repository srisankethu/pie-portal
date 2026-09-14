import { demoCta } from "./cta";
import {
  DEMO_LENGTH, FaqSection, FooterBlurb, LimitsSection, SubPageNav, TrustBand,
} from "./shared";
import { INDUSTRY_PAGES } from "./industries";
import { ROLE_PAGES, type RolePageData } from "./roles";
import "./landing.css";

/**
 * One role's landing page — the same page for each of the three, from
 * `roles.ts`.
 *
 * Same constraints as every other prerendered sub-page: no JavaScript, absolute
 * links to the landing page's fragments, and only `landing.css`. See
 * `ErpPage.tsx` for why each of those is load-bearing rather than a style.
 *
 * **What this family renders that no other does is the withheld column.** The
 * "what you see / what you do not" pair is the whole argument, and the second
 * half is the one worth reading: on this platform the fields a role may not see
 * are absent from the API response rather than hidden by a component, and the
 * rules whose boundary is cost are withheld along with them. Saying that in
 * prose is cheap; putting it in a column with the specific fields named is the
 * version a buyer can test.
 *
 * The owner page's `cannotSee` is empty and the page says so out loud instead
 * of rendering a heading over nothing. That is the same rule `content.ts`
 * states for the landing page's placeholder sections — an empty block is worse
 * than an absent one — applied to a case where the emptiness is itself the
 * fact. Inventing a restraint for the owner so the layout stayed symmetrical
 * would be the exact failure this site spends its `{{TOKEN}}` machinery
 * avoiding.
 *
 * No page here prints a screen count or a nav-item count. `roles.ts` says why.
 */
export function RolePage({ page }: { page: RolePageData }) {
  const heroDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  const closingDemo = demoCta({ href: "/#talk", label: "Book a demo" });

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <SubPageNav />

        <main>
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
                  {heroDemo.ready
                    && <a className="lp-btn" href="/#talk">Tell us what you run</a>}
                </div>
              </div>
            </div>
          </header>

          <div className="lp-notwhat">
            <div className="lp-wrap">
              <b>What each role can see is enforced by the server.</b> The
              fields a role may not see are absent from the response, not hidden
              in the browser — so there is nothing to read out of a network tab,
              and a screen that hid a field it had been sent would be a defect
              rather than a control.
            </div>
          </div>

          <div className="lp-dim"><b>The day</b></div>
          <section id="day">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>{page.day.title}</h2>
                <p>{page.day.body}</p>
              </div>
            </div>
          </section>

          <div className="lp-dim"><b>What reaches you, and what does not</b></div>
          <section id="sees">
            <div className="lp-wrap">
              <div className="lp-two">
                <div className="lp-panel lp-role">
                  <span className="lp-tag">On your screens</span>
                  <h3>What {page.name} sees</h3>
                  <ul className="lp-gaps">
                    {page.sees.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div className="lp-panel lp-role">
                  <span className="lp-tag">Withheld by the server</span>
                  {page.cannotSee.length > 0 ? (
                    <>
                      <h3>What never reaches you</h3>
                      <ul className="lp-gaps">
                        {page.cannotSee.map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </>
                  ) : (
                    /* Empty on purpose, and said rather than hidden — see the
                       note at the top of this file. */
                    <>
                      <h3>Nothing</h3>
                      <p>
                        The owner is the role every other role is scoped
                        against, so there is no column here and this page will
                        not invent one to look even-handed. What that costs is
                        worth knowing: an owner reading a screen is reading the
                        unredacted book, and the roles below are where the
                        withholding actually happens.
                      </p>
                    </>
                  )}
                </div>
              </div>
            </div>
          </section>

          <div className="lp-dim"><b>What it gives you</b></div>
          <section id="does">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Three things, on your own numbers</h2>
                <p>
                  Deterministic arithmetic over rows your ERP already wrote,
                  stamped with the version of the policy that judged it. The AI
                  reads those numbers and phrases them; it never produces one.
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
            label="What PIE does not do for you"
            lead={<>
              Each of these is a thing the product does not do for {page.short}{" "}
              today. They are on the page because the alternative is you finding
              them after a pilot.
            </>}
            items={page.notServed}
          />

          <TrustBand />

          <FaqSection heading={`What ${page.short} ask first`} faq={page.faq} />

          <div className="lp-dim"><b>The other two</b></div>
          <section id="roles">
            <div className="lp-wrap">
              <div className="lp-sec-head">
                <h2>Three people, three screens, one set of numbers</h2>
                <p>
                  The same rows, projected differently and enforced in one
                  place. Reading the other two is the fastest way to see where
                  the line actually falls.
                </p>
              </div>
              <div className="lp-two">
                {ROLE_PAGES.filter((other) => other.slug !== page.slug).map((other) => (
                  <div className="lp-panel lp-role" key={other.slug}>
                    <h3><a href={`/roles/${other.slug}`}>PIE for {other.short}</a></h3>
                    <p>{other.description}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <div className="lp-final">
            <div className="lp-wrap">
              <h2>Your books already know where the margin went.</h2>
              <p>
                {DEMO_LENGTH}, your own numbers on the screen, and an honest
                answer about what PIE can and cannot see on your book.
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
            {/* Both other families, derived. A page reachable only from the
                sitemap is an orphan, and a hand-written list is how one is
                made. */}
            <div className="lp-footer-erp">
              {ROLE_PAGES.filter((other) => other.slug !== page.slug).map((other) => (
                <span key={other.slug}>
                  <a href={`/roles/${other.slug}`}>{other.short}</a>
                  <span className="lp-sep" aria-hidden="true"> · </span>
                </span>
              ))}
              {INDUSTRY_PAGES.map((industry) => (
                <span key={industry.slug}>
                  <a href={`/industries/${industry.slug}`}>{industry.short}</a>
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
