import { useEffect, useState } from "react";
import { demoCta } from "./cta";
import { ERP_PAGES } from "./erp";
import { caseStudy, complianceRows, hasProof, namedCustomers } from "./proof";
import { FooterBlurb, TrustBand } from "./shared";
import { ContactModal } from "./ContactModal";
import {
  detectRegion,
  exampleFor,
  heldToFloor,
  heldToRecommended,
  lineTotal,
  unitPrice,
  type Region,
} from "./worked-example";
import "./landing.css";

/** The books PIE reads, in the order the strip shows them.
 *
 *  Zoho Books first because it is the one this platform was built against and
 *  the only connection with no missing stage; the rest are the connector
 *  registry in `ingestion/erp/`. A name here that matches an `ERP_PAGES.short`
 *  renders as a link to that page — see the strip below. */
const SYSTEMS = [
  "Zoho Books", "NetSuite", "Dynamics 365 BC", "Acumatica",
  "Prophet 21", "Sage X3", "Sage 100",
];

/**
 * The public front door — what a signed-out visitor sees before the sign-in
 * card.
 *
 * Purely presentational (no data fetch, no session), styled entirely from the
 * theme's emitted tokens so it reads as the same product as the screens behind
 * it.
 *
 * The design direction is a works drawing — the sheets this product's
 * customers live in: the page sits inside a drawing frame, sections are named
 * on dimension-line dividers (which is why they carry no eyebrow of their
 * own), and the decision card signs itself with a title block instead of a
 * caption. The frame and corner marks are decorative and aria-hidden; the
 * dividers are real text because they are the section labels.
 *
 * ── The honesty rule ────────────────────────────────────────────────────────
 *
 * Every claim on this page is a real, checkable property of the product — the
 * determinism and audit-trail claims, the role scoping, the connector
 * registry's actual systems — with no invented customers, no testimonials and
 * no logos. The connectors are named in plain type for the same reason.
 *
 * Section {letter("proof")} is where that rule is under the most pressure, and
 * it does not bend: the customer names, the case-study figures and the
 * compliance statuses are `{{PLACEHOLDER}}` tokens, visible as tokens, until
 * somebody has real ones with permission to print them. `prerender.test.tsx`
 * refuses any token that is not on the known list, and `scripts/prerender.mjs`
 * prints every one still in the built page at the end of a build. Filling a
 * slot here with something plausible is not a shortcut — it is the one edit
 * that would make every other claim on the page worthless.
 *
 * That rule was audited against the backend in Aug 2026 and had drifted in six
 * places, so the specifics are worth keeping — every one of them read as good
 * news, which is why none had been noticed:
 *
 *   - "100% of a 6,717-SKU catalog decoded" was pie-parser's own *bundled*
 *     corpus, not any customer's book. Its source document measures only 21.1%
 *     of that corpus as reachable from the real item master — a coverage
 *     warning the page had inverted into a coverage boast.
 *   - The erasure panel promised data "destroyed — backups included".
 *     `trust/erasure.py` documents the opposite for plaintext columns, and its
 *     docstring predicts this exact overclaim losing a security review.
 *   - "Live in about a week. Six hours of your team's time." No duration is
 *     modelled anywhere; `onboarding.py` has a four-step checklist and no ETA
 *     field.
 *   - "Two years of history" where `connections.DEFAULT_HISTORY_MONTHS` offers
 *     18 — the page and the product's own checklist told a customer different
 *     numbers.
 *   - "Authentic price lists" described price-list ingestion that does not
 *     exist: no upload surface, and the one real hash covers nomenclature, not
 *     prices.
 *   - "Alternatives across brands" — one manufacturer pack is indexed, and
 *     `docs/reviews/positioning-review-2026-08.md` measures a second as
 *     strictly worse today. Cross-brand substitution is deferred, not shipped.
 *
 * Marketing copy drifts from the code silently, because nothing compiles it and
 * no test fails. Check a claim against the module that would implement it
 * before putting it on this page.
 *
 * ── Sep 2026: rebuilt as an enterprise front page ───────────────────────────
 *
 * The brief was the register the buyer already knows — Proton.ai, Pricefx,
 * Zilliant — and one conversion path: **book a demo**. What that changed, and
 * what it deliberately did not:
 *
 *   - **The page states no price and names no plan, anywhere.** The plans
 *     section is gone, not softened. It had already stopped printing a figure
 *     (what this costs turns on how many companies are connected, which ERP
 *     each sits on and how much catalogue there is to build, so any number on
 *     a panel is wrong for somebody) — but three named tiers is still a
 *     pricing conversation, held with a reader who has not yet been told what
 *     the product does. None of the three reference sites has one. The whole
 *     of that conversation now happens with a person, after the demo.
 *   - **The trial is not marketed here either.** "Start free / 30 days" is a
 *     plan discussion wearing a button, and it split the page's attention
 *     between two destinations. Sign-up is untouched as a *product* path —
 *     `SELF_SERVE_SIGNUP` still governs it and `SignInCard` still offers
 *     "Create your organization" one click behind the door — it is simply no
 *     longer what this page asks for. `onSignUp` therefore left this
 *     component's props; nothing here calls it.
 *   - **Sections are named for outcomes, not for features.** This is
 *     Zilliant's move and it is the right one for a reader who is scanning:
 *     "Control every quote before it leaves" tells them whether to stop,
 *     "Margin floors & approvals" does not. The six feature cards became four
 *     outcome cards; nothing true was dropped, it moved inside the outcome it
 *     serves.
 *   - **"Who it's for" is new, and it is Pricefx's audience split.** It earns
 *     its place here for a reason that is specific to this product rather than
 *     borrowed: the three readers genuinely see three different screens, and
 *     the salesperson's is missing cost by construction (§1 of `CLAUDE.md`).
 *     A competitor can copy the section; it cannot copy the guarantee.
 *   - **What replaces the reference sites' proof furniture.** All three lean
 *     on logo walls, testimonials and recovered-margin statistics. This page
 *     may not, and the substitute is not a weaker version of the same thing:
 *     it is the mechanism band under the hero, where each claim names the code
 *     that enforces it. Specificity is the only credibility available to a
 *     vendor with no customers it can name, and it is free.
 *
 * What did not move, and why:
 *
 *   - **"Why it can be trusted" stays directly under the hero.** It reads as
 *     one more pillar anywhere else; its job is to answer the question the
 *     outcome claim provokes, which means it has to arrive with the question.
 *   - **The value ledger keeps a section of its own.** It is the one thing on
 *     this page none of the three reference sites offers, and burying it
 *     inside an outcome card would trade the differentiator for symmetry.
 *   - The hero's worked card, its arithmetic and the region swap are
 *     unchanged. See `worked-example.ts`.
 *
 * The eyebrow is unchanged on purpose. `prerender.test.tsx` pins that string,
 * so moving it is a deliberate act rather than a copy edit.
 *
 * The decision card's figures are illustrative but formula-consistent:
 * floor = cost / (1 − margin floor). They live in `worked-example.ts`, in both
 * currencies, and its test re-derives every one of them — so editing the card
 * without editing the note that re-reads it fails a test rather than shipping a
 * page whose own arithmetic does not close. The card shows cost because it
 * depicts the *approver's* view — a manager sees cost, a salesperson never
 * does. Keep both properties when editing the numbers.
 */
export function Landing({ onEnter, onDemo }: {
  onEnter: () => void;
  onDemo?: () => void;
}) {
  const enter = (e: React.MouseEvent) => {
    e.preventDefault();
    onEnter();
  };
  /** The third door, and the only one that asks for nothing. Optional because
   *  a deployment without a demonstration workspace must show no such button
   *  rather than one that leads nowhere. */
  const demo = (e: React.MouseEvent) => {
    e.preventDefault();
    onDemo?.();
  };

  /** Which currency the worked example is written in.
   *
   *  There is no price list on this page — it states nothing about what
   *  anything costs — but the example quote line is still money, and money that
   *  is not the reader's own is money they have to convert before the
   *  arithmetic means anything.
   *
   *  INTL until proven otherwise, and deliberately so: `useEffect` does not run
   *  during the static prerender, so the HTML baked into `dist/index.html` — the
   *  document a crawler reads, and the one a US visitor sees before the bundle
   *  arrives — carries dollars and no rupee figure anywhere in it. The swap
   *  happens on mount, for the visitors whose own clock says they are in India.
   *
   *  State rather than a call in the render body because `detectRegion` reads
   *  `Intl` and `navigator`, neither of which exists on the server, and because
   *  a component that renders differently on the server and on the first client
   *  paint is the one thing `prerender.tsx` is built to avoid. */
  const [region, setRegion] = useState<Region>("INTL");
  useEffect(() => setRegion(detectRegion()), []);
  const price = exampleFor(region);
  const line = price.line;

  /** Whether the demo-request dialog is open.
   *
   *  A boolean now, where it used to be the plan a visitor had pressed. There
   *  is one thing to ask for on this page and one form to ask it with, so
   *  there is nothing left for the state to carry. */
  const [asking, setAsking] = useState(false);

  /** `/#talk` is what the ERP sub-pages link into, and what every "Book a
   *  demo" button on this page falls back to without JavaScript. The form is a
   *  dialog, so the hash has to open it — otherwise that link would deposit a
   *  visitor beside a button and leave them to find it.
   *
   *  The id is `talk` rather than something the new copy would suggest because
   *  it is a published address: the ERP sub-pages hard-code `/#talk` and so
   *  does anything else that has ever linked here. Renaming it would break
   *  those for the sake of a string no visitor reads.
   *
   *  In an effect because it reads `location`, which does not exist during the
   *  prerender, and once because a visitor who closes the dialog with the hash
   *  still in the address bar must not have it reopened underneath them. */
  useEffect(() => {
    if (window.location.hash === "#talk") setAsking(true);
  }, []);

  /** Open the demo-request dialog.
   *
   *  Still an anchor to `#talk`, and the handler still has to stop the jump.
   *  Both halves are deliberate: with JavaScript the dialog opens over the
   *  block being read and the page does not move, and without it the press
   *  falls back to the browser's own scroll to a block that says what to do —
   *  which is the whole of what a visitor without a bundle can be given here,
   *  since the form could not have sent anything either. */
  const ask = (e: React.MouseEvent) => {
    e.preventDefault();
    setAsking(true);
  };

  /** Which lettered sections exist on this render, in order — and therefore
   *  what each one's letter is.
   *
   *  Computed rather than written out because Section {letter("proof")} is
   *  conditional: it appears when there is a customer, a case study or a
   *  compliance status to show, and disappears when there is none. Hard-coded
   *  letters would then skip one, and a reader who noticed would be right to
   *  wonder what was removed and why nobody checked.
   *
   *  The body copy references one of these by letter as well ("the value
   *  ledger in Section E"), so that reads from the same list. A cross-
   *  reference that survives the section it points at moving is the only kind
   *  worth writing. */
  const proofShown = hasProof();
  const sections = ["problem", "outcomes", "roles", "how", "worth", "ownership",
                    ...(proofShown ? ["proof"] : []), "demo"];
  const letter = (name: string) => String.fromCharCode(65 + sections.indexOf(name));

  const customers = namedCustomers();
  const study = caseStudy();
  const compliance = complianceRows();

  /** The page's one action, in the four places it appears.
   *
   *  `demoCta` opens a real scheduling link where one is configured. Where
   *  none is — and none is, today: `DEMO_BOOKING_URL` is still a placeholder —
   *  it falls back to what the caller says the honest alternative is, and here
   *  the honest alternative is *also* booking a demo: the dialog posts to
   *  `POST /api/v1/contact`, an operator reads the queue, and a person comes
   *  back to arrange one. So the label is the same in both branches, which it
   *  could not be while the fallback was a sign-in form. See `cta.ts`. */
  const bookDemo = () => demoCta({ href: "#talk", label: "Book a demo", onClick: ask });
  const heroDemo = bookDemo();
  const closingDemo = bookDemo();
  const navDemo = bookDemo();

  // The mobile nav collapses the section links behind a menu button. Closed on
  // first render, which is also the state the prerenderer bakes into the static
  // HTML — a signed-out visitor without JS sees the logo and the menu button,
  // and the toggle comes alive when the bundle mounts. Tapping a link closes it
  // so the panel is not left covering the section it just jumped to.
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        <nav className="lp-nav">
          <div className="lp-wrap lp-nav-inner">
            <a className="lp-logo" href="#top" onClick={closeMenu}>PIE<span>.</span></a>
            <button
              type="button"
              className="lp-nav-toggle"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              aria-expanded={menuOpen}
              aria-controls="lp-nav-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <span className={`lp-burger${menuOpen ? " open" : ""}`} aria-hidden="true" />
            </button>
            <div
              className={`lp-nav-links${menuOpen ? " open" : ""}`}
              id="lp-nav-menu"
            >
              <a href="#outcomes" onClick={closeMenu}>Outcomes</a>
              <a href="#roles" onClick={closeMenu}>Who it&rsquo;s for</a>
              <a href="#how" onClick={closeMenu}>How it works</a>
              <a href="#worth" onClick={closeMenu}>What it&rsquo;s worth</a>
              {proofShown && <a href="#proof" onClick={closeMenu}>Proof</a>}
              {/* Sign in is a quiet link and Book a demo is the button. On
                  every page of this kind the existing customer knows where the
                  door is; the visitor who has not decided yet is the one the
                  bar has to make an offer to. */}
              <a className="lp-nav-signin" href="#signin" onClick={(e) => { closeMenu(); enter(e); }}>
                Sign in
              </a>
              <a
                className="lp-btn solid lp-nav-cta"
                {...navDemo.props}
                onClick={(e) => {
                  closeMenu();
                  navDemo.props.onClick?.(e);
                }}
              >
                {navDemo.label}
              </a>
            </div>
          </div>
        </nav>

        {/* Everything between the bar and the footer is one region. Without
            it a screen reader's landmark list held a nav, a banner and a
            footer, and the page itself — every section of it — was outside all
            three. The hero is inside `main` on purpose: it is this document's
            content, not a site-wide banner. */}
        <main>
        <header className="lp-hero" id="top">
          <div className="lp-wrap lp-hero-grid">
            <div className="lp-hero-copy">
              <p className="lp-eyebrow">The commercial intelligence layer for distributors</p>
              {/* Named for the outcome the buyer is trying to reach, in the
                  register the category has taught them to scan for — and
                  narrow enough to be checkable, which is the half the category
                  usually drops. */}
              <h1>Make every quote defend your <em>margin</em>.</h1>
              <p className="lp-sub">
                PIE checks every quote line against <b>your own floor</b> before
                it goes out, holds what breaches it for a manager, and reports{" "}
                <b>the margin that held</b> — every figure openable, and
                re-derivable from the rows your desk already wrote. It reads the
                ERP you already run and replaces none of it.
              </p>
              <div className="lp-ctas">
                <a className="lp-btn solid" {...heroDemo.props}>{heroDemo.label}</a>
                {onDemo
                  ? <a className="lp-btn" href="#demo" onClick={demo}>See it on sample data</a>
                  : <a className="lp-btn" href="#how">See how it works</a>}
              </div>
              {/* What the button actually buys, so the press is not a
                  commitment the reader has to guess at. Nothing here promises
                  a duration the product does not model. */}
              <p className="lp-fine">
                A working session on your numbers, not a slide deck. No account,
                no card, and nothing connected until you say so.
              </p>
            </div>

            <div
              className="lp-card"
              role="img"
              /* The figures are *in* the label. `role="img"` makes every
                 descendant presentational, so a screen-reader user got "showing
                 cost, margin floor and recommended price" and none of the three
                 — on the one worked example the whole page argues from. Built
                 from the same values the card renders, so the two cannot say
                 different things. */
              aria-label={
                "An illustrative PIE decision card, drawn like an engineering sheet. "
                + `A customer asked for ${line.units} units at ${unitPrice(price, line.asked)}, `
                + `below the margin floor of ${unitPrice(price, line.floor)} that this `
                + `organization's policy sets for the item; it costs ${unitPrice(price, line.cost)} `
                + `and the recommended price is ${unitPrice(price, line.recommended)}. `
                + "The line routes for a manager's approval, and a title block names the "
                + "policy version that computed it."
              }
            >
              <span className="lp-corner tl" aria-hidden="true" />
              <span className="lp-corner tr" aria-hidden="true" />
              <span className="lp-corner bl" aria-hidden="true" />
              <span className="lp-corner br" aria-hidden="true" />
              <div className="lp-card-top">
                <span className="lp-chip alert">Below floor</span>
                <span className="lp-chip kind">Quote Q-1147 · line 3</span>
              </div>
              {/* Not a heading. The card is one `role="img"` with a full
                  aria-label, so nothing inside it is exposed to a screen
                  reader anyway — but it sits between the page's h1 and its
                  first h2, and an h1 followed by an h3 is a skipped level in
                  the document outline that every accessibility checker will
                  find and that no reader benefits from. Styled identically. */}
              <p className="lp-card-title">This line is priced under your own floor</p>
              <p className="lp-card-body">
                <b>A machine shop in Ohio</b> asked for {line.units} units at{" "}
                {unitPrice(price, line.asked)} — below the floor your margin
                policy sets for this item. It routes for a manager's sign-off:
                the platform holds it, not the salesperson.
              </p>
              <div className="lp-facts">
                <div className="lp-fact">
                  <div className="k">Cost</div>
                  <div className="v lp-num">{unitPrice(price, line.cost)}</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Margin floor</div>
                  <div className="v lp-num">{unitPrice(price, line.floor)}</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Recommended</div>
                  <div className="v lp-num">{unitPrice(price, line.recommended)}</div>
                </div>
              </div>
              <div className="lp-actions"><span>Request approval</span><span>Reprice to floor</span></div>
              <div className="lp-tblock lp-num">
                <div><span className="k">Computed by</span>policy ci_4f2a</div>
                <div><span className="k">Thresholds</span>v18</div>
                <div><span className="k">Rev</span>18</div>
                <div><span className="k">Sheet</span>1 / 1</div>
              </div>
            </div>
          </div>
        </header>

        {/* Where the reference sites put a logo wall and a recovered-margin
            statistic. This page may have neither, and what stands in for them
            is not a weaker version of the same thing: each of these was an
            adjective once, and an adjective is a claim. To a buyer who has
            survived one unfinished ERP implementation, "deterministic" reads
            as the same register as everything else they were promised. So each
            one names the mechanism that enforces it — the specifics are free,
            they are all true, and specificity is the only credibility
            available to a vendor with no customers to name. */}
        <div className="lp-proof">
          <div className="lp-wrap lp-proof-grid">
            <div><div className="v">Versioned</div><div className="k">every computed row is stamped with a hash of the policy that judged it</div></div>
            <div><div className="v">Deterministic</div><div className="k">identical input, byte-identical output — a test re-derives every figure from its operands</div></div>
            <div><div className="v lp-num">0</div><div className="k">prices computed by AI — enforced by a test that parses the imports, not by a convention</div></div>
            <div><div className="v">Yours</div><div className="k">your data, your catalog, your AI account — exportable, and erasable on request</div></div>
          </div>
        </div>

        {/* The row is full-bleed, its contents are not: everything on this
            page lines up on the same 1080px column, and a flex row with only
            its own padding silently opts out of it. */}
        <div className="lp-sched">
          <div className="lp-wrap lp-sched-row">
            <span className="lp-sched-label">Reads the books you already keep</span>
            {/* A system with a page of its own is a link to it; the rest are
                plain text. This is derived from `ERP_PAGES` rather than
                written out, because these links are the only route by which
                those pages are reachable from this one — a page in the
                sitemap and nowhere in the site is an orphan and reads like
                one — and a hand-maintained list is how a new page becomes an
                orphan. */}
            {SYSTEMS.map((name) => {
              const page = ERP_PAGES.find((p) => p.short === name);
              return page
                ? <a className="lp-sys" key={name} href={`/erp/${page.slug}`}>{name}</a>
                : <span className="lp-sys" key={name}>{name}</span>;
            })}
            <span className="lp-sched-note">
              — your ERP records what happened; PIE helps you decide what to do next
            </span>
          </div>
        </div>

        <TrustBand />

        {/* The pattern-match is the thing to beat, and it is silent: a
            distributor who has already decided that dashboards are things
            nobody opens files this under "another dashboard" in the first ten
            seconds and stops reading. Said once, early, as an aside rather than
            as the argument — leading with what a product is not is a weak
            opening, and not saying it at all loses the reader before the
            strong one. */}
        <div className="lp-notwhat">
          <div className="lp-wrap">
            <b>Not an ERP, not a dashboard, not a BI tool.</b> Your ERP keeps the
            records and PIE does not replace it. There is no chart to go and look
            at every morning: the platform checks each quote line as it is
            priced, and raises the handful of accounts that changed.
          </div>
        </div>

        <div className="lp-dim"><b>Section {letter("problem")} — The problem</b></div>
        <section id="problem">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Margin doesn&rsquo;t vanish in one bad deal. It leaks.</h2>
              <p>
                A busy quote desk makes hundreds of small pricing decisions a
                month. Nobody sees the pattern — because the pattern lives across
                years of ledger, and nobody has time to read the ledger.
              </p>
            </div>
            <div className="lp-two">
              <div className="lp-panel">
                <span className="lp-tag">Leak one — outbound</span>
                <h3>Quotes below your floor</h3>
                <p>
                  Salespeople don&rsquo;t always have the history and margin
                  context when quoting. PIE checks every line against <b>your</b>{" "}
                  policy at the moment of quoting — and holds what breaches it.
                </p>
              </div>
              <div className="lp-panel">
                <span className="lp-tag">Leak two — inbound</span>
                <h3>Customers quietly buying less</h3>
                <p>
                  Revenue can decline before anyone notices. PIE watches every
                  account&rsquo;s pattern and raises the decline while there is
                  still a relationship to save.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ── Outcomes ─────────────────────────────────────────────────────
            Named for what the reader gets, not for what the module is called.
            This was six feature cards ("Margin floors & approvals", "Your
            catalog, decoded") and it is four outcomes now; nothing true was
            dropped, each one moved inside the outcome it serves. A reader
            scanning headings can decide whether to stop, which is the whole
            job of this section and the one a feature name cannot do. */}
        <div className="lp-dim"><b>Section {letter("outcomes")} — Outcomes</b></div>
        <section id="outcomes">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Four things your desk can do the week after it connects</h2>
              <p>
                Each one is arithmetic on your own records, computed the same way
                every time, and stamped with the policy version that judged it.
                Unlimited named accounts, each seeing what their role allows.
              </p>
            </div>
            <div className="lp-two">
              <div className="lp-outcome">
                <p className="lp-eyebrow">Control</p>
                <h3>Control every quote before it leaves</h3>
                <p>
                  Your policy sets the floor per line, and PIE applies it at the
                  moment of quoting rather than in a report afterwards. A breach
                  doesn&rsquo;t send quietly — it routes for sign-off, and the
                  sign-off is on record with the policy version and catalog
                  edition that produced it.
                </p>
                <ul>
                  <li>Per-line margin floors, from your own policy</li>
                  <li>Breaches held for a named approver, never sent</li>
                  <li>Append-only record — last quarter&rsquo;s price still explains itself</li>
                </ul>
              </div>
              <div className="lp-outcome">
                <p className="lp-eyebrow">Speed</p>
                <h3>Answer an RFQ the day it arrives</h3>
                <p>
                  Drop a customer&rsquo;s enquiry in as they wrote it — a pasted
                  email, a line of WhatsApp — and PIE reads it into quote lines
                  and resolves each code against your catalog. Where a code has
                  no exact match it ranks alternatives on geometry and grade,
                  deterministically, and <b>abstains when nothing
                  discriminates</b> rather than inventing one.
                </p>
                <ul>
                  <li>Pasted enquiry to priced lines, in one pass</li>
                  <li>Your suppliers&rsquo; nomenclature decoded, coverage measured against your own catalog</li>
                  <li>A scored suggestion is never promoted to an identity</li>
                </ul>
              </div>
              <div className="lp-outcome">
                <p className="lp-eyebrow">Retention</p>
                <h3>See the accounts going quiet</h3>
                <p>
                  Signals from your own numbers — margin drift, customer decline,
                  payments slipping — each written up in plain words and each
                  traceable to the figures that raised it. A handful of accounts
                  a day, not a dashboard to go and interrogate.
                </p>
                <ul>
                  <li>Detectors run over persisted rows, never over a model&rsquo;s guess</li>
                  <li>Every signal opens into the rows beneath it</li>
                  <li>Thresholds are yours, versioned, and editable</li>
                </ul>
              </div>
              <div className="lp-outcome">
                <p className="lp-eyebrow">Evidence</p>
                <h3>Show the board what it was worth</h3>
                <p>
                  The value ledger counts what the platform actually changed —
                  quote lines held to a floor, declines raised in time — carrying
                  the operands each figure was computed from, so any number opens
                  into the lines that produced it. Section {letter("worth")} is
                  about the parts it refuses to count.
                </p>
                <ul>
                  <li>Every figure re-derivable from your own rows</li>
                  <li>A month with no detection reads UNKNOWN, not zero</li>
                  <li>What could not be measured prints above what could</li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* ── Who it's for ─────────────────────────────────────────────────
            Borrowed shape, unborrowable content. The reference sites split
            their audience because their readers have different *jobs*; this
            page can split it because the three readers are served three
            genuinely different screens, and the middle one is missing cost by
            construction rather than by configuration. That is the §1 invariant
            in `CLAUDE.md`, and it is the only claim on this page a competitor
            cannot simply also make. */}
        <div className="lp-dim"><b>Section {letter("roles")} — Who it&rsquo;s for</b></div>
        <section id="roles">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Three people, three screens, one set of numbers</h2>
              <p>
                What each role can see is enforced by the server, not hidden in
                the browser — the fields are absent from the response, so there
                is nothing to read out of a network tab.
              </p>
            </div>
            <div className="lp-grid3">
              <div className="lp-panel lp-role">
                <span className="lp-tag">Owner &amp; finance</span>
                <h3>You set the floors. You see everything.</h3>
                <p>
                  Cost, margin and the policy behind every decision — plus the
                  ledger of what the platform was worth, with the gaps printed
                  before the good news. Change the margin policy and every row
                  computed under the old one still says which one judged it.
                </p>
              </div>
              <div className="lp-panel lp-role">
                <span className="lp-tag">Sales desk</span>
                <h3>Quote confidently, without ever seeing cost.</h3>
                <p>
                  The floor, the recommended price and this customer&rsquo;s own
                  history — enough to negotiate well, and no cost or margin field
                  anywhere in the response. The guardrail travels with the
                  quote instead of living in a manager&rsquo;s head.
                </p>
              </div>
              <div className="lp-panel lp-role">
                <span className="lp-tag">Approvers</span>
                <h3>Decide in one screen, on the record.</h3>
                <p>
                  A held line arrives with cost, floor and recommended price on
                  it, and the rule that stopped it named. Your sign-off is
                  append-only and carries the threshold version that was in
                  force when you gave it.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>Section {letter("how")} — How it works</b></div>
        <section id="how">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Connect, look back, then decide.</h2>
              <p>Most of the work is deciding your own margin floors — not fighting software.</p>
            </div>
            <div className="lp-grid3 lp-steps">
              <div className="lp-step">
                <h3>Connect your books</h3>
                <p>
                  Link Zoho Books — or NetSuite, Business Central, Acumatica,
                  Prophet 21 or Sage — and the first pull reads{" "}
                  <b>18 months</b>, from the first day of that month:
                  customers, items, invoices and bills, plus payments and orders
                  where the system exposes them. Set an earlier date before it
                  runs and it reads from there instead.
                </p>
              </div>
              <div className="lp-step">
                {/* This promised below-floor quotes on day one and could not
                    deliver them: replaying a floor needs quote decisions PIE
                    recorded, and a book that connected this morning has none.
                    The decline half runs on the synced history alone and is
                    real from the first sync. So the claim is split by when each
                    half becomes true, rather than averaged into one that is
                    half wrong on the day somebody checks. */}
                <h3>See what your history already holds</h3>
                <p>
                  PIE reads the history it has just pulled and shows the accounts
                  that were quietly declining and the margins that drifted —{" "}
                  <b>and how much of your book it could not judge</b>, before
                  what it found. Below-floor quoting is measured from the quotes
                  you price here, so that arrives as you use it.
                </p>
              </div>
              <div className="lp-step">
                <h3>Decide with the numbers on</h3>
                <p>
                  Set your floors, add your catalog, and quote. Every figure on
                  every screen from here is arithmetic over your own rows,
                  openable down to the operands — including the ones that say
                  the platform found nothing.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>Section {letter("worth")} — What it was worth</b></div>
        <section id="worth">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>A ledger of what this platform was worth. Including when it was nothing.</h2>
              <p>
                Every intervention PIE claims credit for is arithmetic over rows
                your quote desk already wrote, carrying the operands it was
                computed from — so you can open any figure and re-derive it. What
                makes it worth reading is what it refuses to do.
              </p>
            </div>
            <div className="lp-grid3">
              <div className="lp-panel">
                <h3>An empty month says UNKNOWN, not zero</h3>
                <p>
                  &ldquo;Nothing was detected&rdquo; and &ldquo;no detection ran&rdquo;
                  are the same silence from outside and mean opposite things, so
                  the ledger refuses to pick one. A measured zero is reported as a
                  zero; an absence is reported as an absence.
                </p>
              </div>
              <div className="lp-panel">
                <h3>Time saved is counted, never priced</h3>
                <p>
                  Approvals turned round and quotes priced come back as counts
                  with no money figure anywhere near them. Your business holds no
                  hourly rate, and multiplying a count by an invented one is a
                  made-up number that happens to have been computed carefully.
                </p>
              </div>
              <div className="lp-panel">
                <h3>The gaps sit above the good news</h3>
                <p>
                  What the platform could not measure is printed before what it
                  did — on the renewal screen, where the incentive runs the other
                  way. Return on investment stays UNKNOWN until you type in what
                  you are paying; PIE will not assume its own figure.
                </p>
              </div>
            </div>
            {/* The arithmetic, done on the page's own numbers rather than left
                for the reader to do or, worse, asserted as a round claim.

                It uses the figure held to the *floor* and not the one the
                recommended price would have made, and the difference is the
                point: the ledger counts a movement only up to the floor,
                because clearing a floor by more than it asked for is the
                salesperson's judgement and not something the guardrail did.
                Quoting the bigger number here would be the page contradicting
                the module it is describing, in the section that describes it.

                Both figures come from `worked-example.ts`, where a test
                re-derives them from the card's own cost and floor. */}
            <p className="lp-worked-note">
              The card at the top of this page is one worked line:{" "}
              {line.units} units asked at {unitPrice(price, line.asked)} against
              a floor of {unitPrice(price, line.floor)}. Held to that floor it
              is <span className="lp-num">{lineTotal(price, heldToFloor(price))}</span>,
              from a single line. The ledger would count exactly that and not
              the {lineTotal(price, heldToRecommended(price))} the recommended
              price would have made, because clearing a floor by more than it
              asked for is your judgement, not ours.
            </p>
          </div>
        </section>

        <div className="lp-dim"><b>Section {letter("ownership")} — What you own</b></div>
        <section id="trust">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>What you own — and we can prove it</h2>
              <p>
                Your ERP stays yours. PIE works on top of it — and each of these
                is a mechanism in the product you can test, not a policy-page
                promise.
              </p>
            </div>
            <div className="lp-grid3">
              <div className="lp-panel">
                <h3>Your data. Export any time, erase provably.</h3>
                <p>
                  Full export on request. On exit your tenant key is destroyed,
                  making everything encrypted under it permanently unreadable —
                  live tables, replicas and backups alike. The signed receipt
                  lists exactly what that reached and what it did not, and you
                  can verify it.
                </p>
              </div>
              <div className="lp-panel">
                <h3>Your catalog. Decoded for you, for you alone.</h3>
                <p>
                  Your decoded catalog is your property and exports with your
                  data. It is never shared with, shown to, or reused for any
                  other customer.
                </p>
              </div>
              <div className="lp-panel">
                <h3>Your exit. Off-ramps defined, not discovered.</h3>
                <p>
                  Stop at any time and your data leaves with you, in a format
                  you can read. The quote desk keeps working, and your decoded
                  catalog keeps working as delivered.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ── Proof ───────────────────────────────────────────────────────
            Three blocks, each rendered only when its own content exists, and
            the section itself only when at least one of them does. Nothing
            here is ever an empty panel or a visible `{{TOKEN}}`: a page
            missing a section reads as a page about a product, and a page
            showing `{{CASE_STUDY_MARGIN_RECOVERED}}` reads as a building
            site — to the one visitor whose opinion is worth the most.

            The content and the rules about it are in `proof.ts`. The rule
            that matters: nothing here may be invented to make a block appear.
            Hiding a section costs a section; filling it with something
            plausible costs the argument every other claim on this page
            depends on. */}
        {proofShown && (
          <>
            <div className="lp-dim"><b>Section {letter("proof")} — Proof</b></div>
            <section id="proof">
              <div className="lp-wrap">
                <div className="lp-sec-head">
                  <h2>What it was worth, on somebody else&rsquo;s book</h2>
                  <p>
                    One distributor, one ERP, one figure — and the ledger entry
                    behind it. The number below is not a case-study estimate: it
                    is what PIE&rsquo;s own value ledger computed from rows that
                    customer&rsquo;s quote desk had already written, carrying the
                    operands it was computed from, so it opens into the lines
                    that produced it. The same screen is in the product,
                    reporting on your book, from the day you start.
                  </p>
                </div>

                {/* The systems row's shape, reused: a labelled band of plates
                    in plain type. Not `<img>` — the stylesheet has no image
                    rule, the site carries no images at all, and names a buyer
                    recognises say more than grey marks. However many are
                    cleared is however many render. */}
                {customers.length > 0 && (
                  <div className="lp-sched">
                    <div className="lp-sched-row">
                      <span className="lp-sched-label">Running PIE today</span>
                      {customers.map((name) => (
                        <span className="lp-sys" key={name}>{name}</span>
                      ))}
                      <span className="lp-sched-note">
                        — named with written permission, or not named at all
                      </span>
                    </div>
                  </div>
                )}

                {study && (
                  <div className="lp-panel lp-case">
                    <div className="lp-card-top">
                      <span className="lp-chip kind">Value ledger · {study.window}</span>
                      <span className="lp-chip kind">{study.erp}</span>
                    </div>
                    <h3>{study.profile}</h3>
                    <p>{study.narrative}</p>
                    <div className="lp-facts">
                      <div className="lp-fact">
                        <div className="k">Margin recovered</div>
                        <div className="v lp-num">{study.marginRecovered}</div>
                      </div>
                      <div className="lp-fact">
                        <div className="k">Quote lines checked</div>
                        <div className="v lp-num">{study.linesChecked}</div>
                      </div>
                      <div className="lp-fact">
                        <div className="k">Below floor, held</div>
                        <div className="v lp-num">{study.linesHeld}</div>
                      </div>
                    </div>
                    {/* The provenance strip, exactly as the hero card signs
                        itself. It is the difference between a case study and a
                        claim: the figure names the ledger that produced it and
                        the policy version in force while it did, and both are
                        re-derivable from that customer's own rows. */}
                    <div className="lp-tblock lp-num">
                      <div><span className="k">Source</span>PIE value ledger</div>
                      <div><span className="k">Thresholds</span>{study.policyVersion}</div>
                      <div><span className="k">Window</span>{study.window}</div>
                      <div><span className="k">Re-derivable</span>yes</div>
                    </div>
                  </div>
                )}
              </div>

              {/* Compliance, as statuses rather than assertions, and only the
                  ones that are known. A distributor's IT or finance function
                  asks these in the first meeting; answering "in progress" is
                  better than silence and far better than a certification that
                  does not exist, which is the claim diligence takes apart. */}
              {compliance.length > 0 && (
                <div className="lp-proof lp-compliance">
                  <div className={`lp-wrap lp-proof-grid${compliance.length === 3 ? " lp-cols-3" : ""}`}>
                    {compliance.map((row) => (
                      <div key={row.name}>
                        <div className="v">{row.name}</div>
                        <div className="k">{row.status}{row.note}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </>
        )}

        {/* ── Book a demo ──────────────────────────────────────────────────
            Where the plans section was. It named three tiers and ended in this
            same form, which meant the page spent its closing section holding a
            pricing conversation with a reader who had not yet agreed the
            product was for them. What a plan costs turns on how many companies
            are connected, which ERP each sits on and how much catalogue there
            is to build — so it was never a conversation a panel could hold
            anyway. It happens with a person now, after this.

            The id is `talk` and stays `talk`: it is the address the ERP
            sub-pages link into (`/#talk`), and the effect at the top of this
            component opens the dialog for anybody who arrives on it. */}
        <div className="lp-dim"><b>Section {letter("demo")} — Book a demo</b></div>
        <section id="talk">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>See it on your own numbers</h2>
              <p>
                Tell us what you run and we will come back to arrange a working
                session — the quote desk, your margin floors and the look-back
                over your own history, in the time it takes to price a real
                enquiry. What it would cost is a conversation we have after
                that, when we know what your setup is.
              </p>
            </div>
            <div className="lp-demo-panel lp-panel">
              <div className="lp-demo-copy">
                {/* Kept as an h3 rather than promoted: the section already has
                    its h2 directly above, and two headings of the same rank in
                    one section is an outline that says they are peers when one
                    introduces the other. */}
                <h3>Tell us about your business</h3>
                <p>
                  How many companies you run, what each of them sits on, and
                  what you are trying to fix. It reaches a person, not a
                  sequence — we read every one of these ourselves and come back
                  at the address you give, usually within a working day.
                </p>
                <div className="lp-ctas">
                  <a className="lp-btn solid" href="#talk" onClick={ask}>Book a demo</a>
                </div>
                <p className="lp-fine">
                  No card, no account, nothing charged, and nothing connected to
                  your ERP — this sends us a message and nothing else.
                </p>
              </div>
              {/* What the session actually is, so the press is not a leap. A
                  list rather than prose because a reader deciding whether to
                  give up half an hour is scanning for the shape of it. */}
              <ul className="lp-demo-list">
                <li><b>30–45 minutes</b>, with whoever prices your quotes</li>
                <li><b>A real enquiry</b> of yours, priced on the desk in front of you</li>
                <li><b>Your margin policy</b>, set up as you would actually set it</li>
                <li><b>The look-back</b> — what your own history holds, and what it cannot say</li>
                <li><b>No connection</b> to your books until you decide to make one</li>
              </ul>
            </div>
          </div>
        </section>

        <div className="lp-final">
          <div className="lp-wrap">
            <h2>Your books already know where the margin went.</h2>
            <p>PIE turns that history into better commercial decisions — and reports what that was worth.</p>
            <div className="lp-ctas lp-ctas-centred">
              <a className="lp-btn solid" {...closingDemo.props}>{closingDemo.label}</a>
            </div>
          </div>
        </div>

        </main>

        <footer className="lp-footer">
          <div className="lp-wrap">
            <FooterBlurb />
            <div>
              Already have an account?{" "}
              <a href="#signin" onClick={enter}>Sign in</a>
            </div>
          </div>
        </footer>
      </div>

      {/* Outside `.lp-sheet` and not through a portal: a sibling of the sheet
          keeps the page's own `.lp-panel` / `.lp-field` / `.lp-btn` rules over
          the form — one set of styles for one form — while sitting outside the
          drawing frame's borders and above the sticky header. Nothing is
          rendered while it is shut, so the prerender is unchanged. */}
      {asking && <ContactModal onClose={() => setAsking(false)} />}
    </div>
  );
}
