import { filled } from "./content";
import { demoCta } from "./cta";
import { DEMO_LENGTH, FooterBlurb, TrustBand } from "./shared";
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

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <nav className="lp-nav">
          <div className="lp-wrap lp-nav-inner">
            <a className="lp-logo" href="/">PIE<span>.</span></a>
            {/* The menu is a `<details>`, and that is the whole point: a
                disclosure opens without JavaScript, so a page that ships none
                can still have the same collapsed bar the landing page has.

                It used to be a flat row carrying `lp-nav-static`, written that
                way because "a burger that cannot open is worse than four links
                that wrap" — the ordinary `.lp-nav-links` is display:none below
                640px until a JS toggle adds `.open`, so without a modifier a
                phone got the logo and nothing else. The premise was wrong: a
                burger *can* open with no script behind it. What the flat row
                cost, measured in Chromium at 320–430px, was a sticky bar
                **131px tall** against the landing page's 71px — a fifth of a
                phone screen, on every one of these pages — whose links were
                **17px** high, where the landing page's own mobile menu was
                already giving each one 44+ and `e2e/.shots/a11y.mjs` holds the
                signed-in app to the same number. Both panels are one rule, so
                both are 44 now; `e2e/.shots/public-a11y.mjs` measures it.

                Above 640px the summary is hidden and the panel is forced
                visible, so the bar is the row it always was. If a browser ever
                refused that override the page degrades to a menu button that
                opens — not to a broken nav. */}
            {/* These are cross-document links, which is why they are worth a
                note: the landing page's section ids are this file's
                dependency, and nothing here fails when one of them is renamed.
                `/#product` and `/#plans` both dangled for exactly that reason
                — the first was renamed to `#outcomes` and the second deleted
                with the plans section — and a sub-page whose nav scrolls to
                the top of the front page is a dead link that looks like a
                working one. */}
            <details className="lp-nav-menu">
              <summary className="lp-nav-toggle" aria-label="Menu">
                <span className="lp-burger" aria-hidden="true" />
              </summary>
              <div className="lp-nav-links">
                <a href="/#outcomes">Outcomes</a>
                <a href="/#how">How it works</a>
                <a href="/#worth">What it&rsquo;s worth</a>
                <a className="lp-nav-signin" href="/#signin">Sign in</a>
                <a className="lp-btn solid lp-nav-cta" href="/#talk">Book a demo</a>
              </div>
            </details>
          </div>
        </nav>

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
          </div>
        </footer>
      </div>
    </div>
  );
}
