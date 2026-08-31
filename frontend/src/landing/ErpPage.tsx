import { DEMO_BOOKING_READY, DEMO_BOOKING_URL } from "./cta";
import type { ErpPageData } from "./erp";
import "./landing.css";

/**
 * One ERP's landing page — the same page for all three, from `erp.ts`.
 *
 * These are static documents. `scripts/prerender.mjs` renders this component
 * into `dist/erp/{slug}/index.html` **without** the module script tag, so a
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
 *   - **Nothing here may need JavaScript.** No `useState`, no menu that opens,
 *     no handler. Every link is an `href` that works with the bundle absent,
 *     and in-page links to the main page are absolute (`/#pricing`) because a
 *     bare `#pricing` on this document is a fragment that goes nowhere.
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
  const bookDemo = DEMO_BOOKING_READY
    ? { href: DEMO_BOOKING_URL, target: "_blank", rel: "noreferrer" as const }
    : { href: DEMO_BOOKING_URL };

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <nav className="lp-nav">
          <div className="lp-wrap lp-nav-inner">
            <a className="lp-logo" href="/">PIE<span>.</span></a>
            {/* No menu button: a sub-page carries no JavaScript, and a burger
                that cannot open is worse than four links that wrap. */}
            <div className="lp-nav-links">
              <a href="/#product">Product</a>
              <a href="/#how">How it works</a>
              <a href="/#pricing">Pricing</a>
              <a className="lp-btn solid lp-nav-cta" href="/#signin">Sign in</a>
            </div>
          </div>
        </nav>

        <header className="lp-hero" id="top">
          <div className="lp-wrap">
            <div className="lp-hero-copy">
              <p className="lp-eyebrow">For distributors running {page.name}</p>
              <h1>
                Stop quoting away your <em>margin</em> on {page.short}.
              </h1>
              <p className="lp-sub">
                PIE connects to the {page.name} book you already run, reads the
                trading history in it, and checks every new quote line against{" "}
                <b>your own margin floor</b> before it goes out — then reports
                the margin that held. Your ERP keeps the records; PIE decides
                nothing you did not set a policy for.
              </p>
              <div className="lp-ctas">
                <a className="lp-btn solid" {...bookDemo}>Book a demo</a>
                <a className="lp-btn" href="/#signin">Start free</a>
                <a className="lp-quiet" href="/#pricing">or see the pricing</a>
              </div>
              <p className="lp-fine">
                A 30-day trial of the whole platform — no card. The quote desk
                keeps working whether or not you subscribe.
              </p>
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
            {/* PLACEHOLDER — real language from real distributors running this
                system, with permission to print it. Replace `evidence` in
                erp.ts with research, or delete this panel entirely. Do not
                fill it with a plausible sentence: this page's argument is that
                its claims are checkable, and one invented quote ends that. */}
            <div className="lp-panel lp-evidence">
              <span className="lp-tag">What {page.short} distributors tell us</span>
              <p>{page.evidence}</p>
            </div>
          </div>
        </section>

        <div className="lp-invariant">
          <div className="lp-wrap">
            <div className="lp-cols">
              <div>
                <p className="lp-eyebrow">Why it can be trusted</p>
                <h2>The AI never computes <em>a single number.</em></h2>
                <p>
                  Your {page.short} data and your rules determine the number.
                  AI explains it.
                </p>
                <p>Same inputs, same answer, every time — with a paper trail.</p>
              </div>
              <ul>
                <li>
                  <b>Deterministic calculations</b> — every figure is arithmetic
                  on your own records; turn AI off and every number still works
                </li>
                <li>
                  <b>Auditable decisions</b> — each number names the policy that
                  produced it, so a price you quoted last quarter still explains
                  itself
                </li>
                <li>
                  <b>Your data</b> — read from your own book, used for you
                  alone, exportable, and erasable on request
                </li>
                <li>
                  <b>Provenance</b> — every row carries the connector, the
                  connection and the id it came from, so nothing in PIE claims
                  a source it did not have
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div className="lp-final">
          <div className="lp-wrap">
            <h2>Your {page.short} book already knows where the margin went.</h2>
            <p>
              Thirty minutes, your own numbers on the screen, and an honest
              answer about what PIE can and cannot see in {page.name}.
            </p>
            <div className="lp-ctas lp-ctas-centred">
              <a className="lp-btn solid" {...bookDemo}>Book a demo</a>
              <a className="lp-btn" href="/">Read the full product page</a>
            </div>
          </div>
        </div>

        <footer className="lp-footer">
          <div className="lp-wrap">
            <div>
              <b>PIE</b> — the commercial intelligence layer for distributors.
              Deterministic numbers, auditable decisions, your data provably
              yours.
            </div>
            <div>
              <a href="/erp/prophet-21">Prophet 21</a>{" · "}
              <a href="/erp/netsuite">NetSuite</a>{" · "}
              <a href="/erp/acumatica">Acumatica</a>{" · "}
              <a href="/">Everything else</a>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
