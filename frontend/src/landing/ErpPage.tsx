import { filled } from "./content";
import { demoCta } from "./cta";
import { DEMO_LENGTH, FooterBlurb, SubPageNav, TrustBand } from "./shared";
import { INDUSTRY_PAGES, verticalLabel } from "./industries";
import { ERP_PAGES, type ErpPageData } from "./erp";
import "./landing.css";

/**
 * One ERP's landing page — the same page for every system, from `erp.ts`.
 *
 * These are static documents. `scripts/prerender.mjs` renders this component
 * into `dist/erp/{slug}.html` **without** the module script tag, so a
 * sub-page ships as HTML and CSS and nothing else: no bundle, no hydration,
 * no swap. That is not a compromise, it is the whole design — the main page's
 * 261 kB entry exists to run an application, and there is no application here.
 * It also sidesteps the thing that would otherwise break these pages: the app
 * mounts a `HashRouter` and `createRoot().render()` replaces whatever was in
 * `#root`, so a sub-page that loaded the bundle would have its content thrown
 * away and the landing page drawn over it.
 *
 * Two consequences to keep in mind when editing:
 *
 *   - **Nothing here may need JavaScript.** No `useState`, no handler, nothing
 *     whose behaviour is React's. Every link is an `href` that works with the
 *     bundle absent, and in-page links to the main page are absolute (`/#talk`)
 *     because a bare `#talk` on this document is a fragment that goes nowhere.
 *     The enquiry form is one of those links rather than a copy of the form:
 *     these pages ship no JavaScript, so a form here would render and refuse
 *     to send.
 *
 *     This line used to read "no menu that opens", and that was a *conclusion*
 *     rather than the rule — one drawn from the landing page's menu, which is
 *     React's. The rule is that nothing may need a script; the browser's own
 *     interactive elements are not a script. So the nav is a `<details>`, and
 *     any other UA-driven control is fair game on the same terms. What is
 *     ruled out is a handler, not an affordance.
 *   - **Only `landing.css` may style it.** The built stylesheet contains what
 *     the *client* graph imports; a new stylesheet imported only from here
 *     would compile during the prerender and never be emitted, and the page
 *     would ship unstyled. Every class below is one the landing page already
 *     uses. `prerender.test.tsx` renders these pages, so a class that does not
 *     exist is at least visible in that markup.
 *
 * The claims are the narrowest on the site and the most checkable: what PIE
 * reads out of this system, what it can write back, what it cannot see at all.
 * `erp.test.ts` holds the read list and the write capability against the
 * connector module's own source, in both directions.
 */
export function ErpPage({ page }: { page: ErpPageData }) {
  /** Both "Book a demo" buttons on a sub-page. With no scheduling link
   *  configured they fall back to `/#talk` — the landing page's demo-request
   *  form, which opens on arrival because the hash says so — so the label is
   *  the same in both branches. These pages ship no JavaScript, so the
   *  fallback is a plain path and carries no handler.
   *
   *  This followed the front page rather than leading it. Both buttons said
   *  "Start free" and pointed at the sign-in card, which was correct while the
   *  landing page sold a trial; once the front door asked for a demo and
   *  nothing else, a visitor who clicked through to /erp/prophet-21 from that
   *  page's own systems strip was offered something the page they came from
   *  had stopped offering. One ask, on every public page. */
  const evidence = filled(page.evidence);
  const heroDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  const closingDemo = demoCta({ href: "/#talk", label: "Book a demo" });
  /** The trades this system's distributors most often run — the other half of
   *  the routing the trade pages do in their own ERP section.
   *
   *  Resolved from `page.industrySlugs` against `INDUSTRY_PAGES` rather than
   *  written out, so a renamed trade slug cannot leave valid markup pointing at
   *  a 404. Empty on two of the seven, deliberately: see `industrySlugs`. */
  const trades = page.industrySlugs
    .map((slug) => INDUSTRY_PAGES.find((trade) => trade.slug === slug))
    .filter((trade): trade is (typeof INDUSTRY_PAGES)[number] => trade !== undefined);

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <SubPageNav />

        {/* One region for the document's own content — see the note on the
            same element in Landing.tsx. */}
        <main>
        <header className="lp-hero" id="top">
          <div className="lp-wrap">
            <div className="lp-hero-copy">
              <p className="lp-eyebrow">For distributors running {page.name}</p>
              {/* Two headlines, and which one a page gets is not a tone
                  choice. A margin floor is arithmetic on purchase cost, and a
                  book that carries no item-level cost cannot have one — so on
                  a system where the connector reads no bills, the headline
                  claim of this whole site is false, and the page has to lead
                  with what it can actually do. See `costed` in `erp.ts`. */}
              {page.costed ? (
                <>
                  <h1>
                    Stop quoting away your <em>margin</em> on {page.short}.
                  </h1>
                  <p className="lp-sub">
                    PIE connects to the {page.name} book you already run, reads
                    the trading history in it, and checks every new quote line
                    against <b>your own margin floor</b> before it goes out —
                    then reports the margin that held. Your ERP keeps the
                    records; PIE decides nothing you did not set a policy for.
                  </p>
                </>
              ) : (
                <>
                  <h1>
                    Know what your {page.short} book <em>can</em> tell you
                    before you quote.
                  </h1>
                  <p className="lp-sub">
                    PIE connects to the {page.name} book you already run and
                    reads the trading history in it: what each customer paid
                    for each item, what they pay at this quantity, and which
                    accounts have gone quiet. What {page.short} does not hold
                    is <b>item-level purchase cost</b> — so on this book PIE
                    reports margin as unknown rather than estimating one, and
                    says so on every screen that would have used it.
                  </p>
                </>
              )}
              <div className="lp-ctas">
                <a className="lp-btn solid" {...heroDemo.props}>{heroDemo.label}</a>
                {/* Only where a scheduling link exists: without one the
                    primary button already goes to `/#talk`, and two buttons to
                    one destination is a choice that is not a choice. */}
                {heroDemo.ready
                  && <a className="lp-btn" href="/#talk">Tell us what you run</a>}
              </div>
            </div>
          </div>
        </header>

        <div className="lp-notwhat">
          <div className="lp-wrap">
            <b>PIE does not replace {page.short}.</b> It reads it. Your ERP
            stays the system of record, every row PIE holds is stamped with
            where it came from, and a full re-sync rebuilds what PIE derived
            from nothing.
          </div>
        </div>

        <div className="lp-dim"><b>How PIE connects to {page.short}</b></div>
        <section id="connect">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>One sign-in, checked when you save it</h2>
              <p>{page.connects}</p>
            </div>
            <div className="lp-two">
              <div className="lp-panel">
                <h3>What your administrator does first</h3>
                <p>{page.setup}</p>
              </div>
              <div className="lp-panel">
                <h3>What the first pull reads</h3>
                <p>
                  The first pull is offered from <b>the first day of the month
                  18 months back</b>, and you choose the date before it starts.
                  It runs as a background job that commits as it goes, so you
                  can watch it move rather than wait on a spinner — and what
                  the screens then report is the span the rows actually cover,
                  not the window that was asked for.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>What it reads, and what it writes</b></div>
        <section id="reads">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Exactly this, and nothing else</h2>
              <p>
                The list below is not a summary of the integration — it is the
                integration. Each line is a permission the {page.short}{" "}
                connector declares and a method that reads it, and a test holds
                the two against each other in both directions, so a capability
                cannot be claimed here and missing in the code.
              </p>
            </div>
            <ul className="lp-reads">
              {page.reads.map((read) => (
                <li key={read.stage}>
                  <b>{read.label}</b>
                  <span className="lp-num">{read.source}</span>
                </li>
              ))}
            </ul>
            <div className="lp-two lp-reads-note">
              <div className="lp-panel">
                <h3>{page.writes ? `What PIE creates in ${page.short}` : `PIE writes nothing to ${page.short}`}</h3>
                <p>
                  {page.writes ?? (
                    <>
                      The {page.short} connector is read-only: there is no
                      method in it that creates anything, and the platform
                      refuses to send a quote to a {page.short} customer rather
                      than pretending it can. A quote is built and approved in
                      PIE and entered in {page.short} by whoever enters them
                      today.
                    </>
                  )}
                </p>
              </div>
              <div className="lp-panel">
                <h3>What PIE does <em>not</em> read from {page.short}</h3>
                <ul className="lp-gaps">
                  {page.gaps.map((gap) => <li key={gap}>{gap}</li>)}
                </ul>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>What it does with it</b></div>
        <section id="does">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Three things, on your own numbers</h2>
              <p>
                Every figure is deterministic arithmetic over the rows above,
                stamped with the version of the margin policy that judged it.
                The AI on this platform reads those numbers and phrases them;
                it never produces one.
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
            {/* Real language from a real distributor running this system,
                with permission to print it, or nothing at all — a panel headed
                "What Prophet 21 distributors tell us" containing a token tells
                a Prophet 21 distributor exactly the wrong thing. Nothing in
                this repository knows what they report, so nothing here claims
                to until somebody has asked them. */}
            {evidence && (
              <div className="lp-panel lp-evidence">
                <span className="lp-tag">What {page.short} distributors tell us</span>
                <p>{evidence}</p>
              </div>
            )}
          </div>
        </section>

        <TrustBand system={page.short} />

        {/* Which trades run this system, and a route to each of their pages.
            The back-link half of the routing the trade pages already do — an
            `/erp/` page answers "will this work with the system I run" and a
            trade page answers "will this work for the trade I'm in", and a
            reader on one is usually about to ask the other.

            It says which trades, and nothing about what PIE does for them: that
            is each trade page's job, and a summary of seven of them on seven ERP
            pages is forty-nine places for one claim to go stale. Two systems
            name no trade at all, and the page says that rather than guessing —
            see `industrySlugs` in `erp.ts`. */}
        <div className="lp-dim"><b>Which trades run {page.short}</b></div>
        <section id="trades">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>The trades this book usually belongs to</h2>
              <p>
                {trades.length > 0 ? (
                  <>
                    Each page below is about the trade rather than the system:
                    what goes wrong on that book, what it costs, and what PIE
                    does and does not do about it. This is a route to them, not a
                    summary — and it is drawn from desk research into which
                    systems each trade typically runs, so read it as where to
                    look first rather than as a claim about market share.
                  </>
                ) : (
                  <>
                    This site&rsquo;s own research names no trade as typically
                    running {page.short}, so this page will not guess at one —
                    inventing the answer is the one thing that would make the
                    rest of the page worth less. PIE reads a {page.short} book
                    the same way it reads any of the seven. All seven trade pages
                    are linked at the foot of this page, and each states what PIE
                    does and does not do on that book.
                  </>
                )}
              </p>
            </div>
            {trades.length > 0 && (
              <div className="lp-sched">
                <div className="lp-sched-row">
                  <span className="lp-sched-label">Most often</span>
                  {trades.map((trade) => (
                    <a className="lp-sys" key={trade.slug} href={`/industries/${trade.slug}`}>
                      {verticalLabel(trade)}
                    </a>
                  ))}
                  <span className="lp-sched-note">
                    Every trade page names the limits of what PIE can do on that
                    book before it names the capabilities, which is the fastest
                    way to find out whether this is for you.
                  </span>
                </div>
              </div>
            )}
          </div>
        </section>

        <div className="lp-final">
          <div className="lp-wrap">
            <h2>
            {page.costed
              ? `Your ${page.short} book already knows where the margin went.`
              : `Your ${page.short} book already knows what your customers pay.`}
          </h2>
            <p>
              {DEMO_LENGTH}, your own numbers on the screen, and an honest
              answer about what PIE can and cannot see in {page.name}.
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
            {/* The other systems, derived rather than listed. The written-out
                version was three links on a site that had four pages — Zoho
                Books had been added and this list had not, so the only page
                that could reach it was the landing page's strip, and every
                sub-page told a reader those three were all there was. A list
                of siblings is exactly the kind that goes stale silently: it
                is still valid markup and still renders, it is simply no
                longer true. */}
            {/* Tappable, because on a phone this row *is* the navigation
                between the individual ERP pages — the bar above only goes back
                to the front page. As a run of bare links separated by a middot
                each one was 17px high and a few pixels from its neighbour;
                below 640px they are 44px-high items in a wrapped row and the
                separators, which were doing the spacing, step aside for the
                gap that now does it. */}
            <div className="lp-footer-erp">
              {ERP_PAGES.filter((other) => other.slug !== page.slug).map((other) => (
                <span key={other.slug}>
                  <a href={`/erp/${other.slug}`}>{other.short}</a>
                  <span className="lp-sep" aria-hidden="true"> · </span>
                </span>
              ))}
              <a href="/">Everything else</a>
            </div>
            {/* And the other direction: an ERP page answers "will it work with
                my system", a trade page answers "will it work for my trade",
                and a reader arriving on one is usually about to ask the other.
                Linking only one way would make the second family reachable
                from the front page alone. */}
            <div className="lp-footer-erp">
              {INDUSTRY_PAGES.map((industry) => (
                <span key={industry.slug}>
                  <a href={`/industries/${industry.slug}`}>
                    PIE for {verticalLabel(industry)}
                  </a>
                  <span className="lp-sep" aria-hidden="true"> · </span>
                </span>
              ))}
              <a href="/roles/salesperson">For your desk</a>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
