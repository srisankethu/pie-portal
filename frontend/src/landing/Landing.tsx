import { useEffect, useState } from "react";
import { demoLinkProps } from "./cta";
import {
  detectRegion,
  heldToFloor,
  heldToRecommended,
  lineTotal,
  pricingFor,
  unitPrice,
  type Region,
} from "./pricing";
import "./landing.css";

/**
 * The public front door — what a signed-out visitor sees before the sign-in
 * card.
 *
 * The second CTA is "See it on sample data" where a deployment offers one, and
 * the wording is held to the same honesty rule as everything else here: it is
 * sample data, said plainly, rather than "see it live" or "try it free" over a
 * book that belongs to nobody. Absent where no demonstration workspace is
 * configured, in which case the pricing link takes the slot back. Purely presentational (no data fetch, no session), styled entirely
 * from the theme's emitted tokens so it reads as the same product as the
 * screens behind it.
 *
 * The design direction is a works drawing — the sheets this product's
 * customers live in: the page sits inside a drawing frame, sections are named
 * on dimension-line dividers (which is why they carry no eyebrow of their
 * own), and the decision card signs itself with a title block instead of a
 * caption. The frame and corner marks are decorative and aria-hidden; the
 * dividers are real text because they are the section labels.
 *
 * The honesty rule this page keeps on purpose: every claim on it is a real,
 * checkable property of the product — the determinism and audit-trail claims,
 * the trial, the plan tiers, the connector registry's actual systems — with no
 * invented customers, no testimonials and no logos. The connectors are named in
 * plain type for the same reason.
 *
 * Section F is where that rule is under the most pressure, and it does not
 * bend: the customer names, the case-study figures and the compliance statuses
 * are `{{PLACEHOLDER}}` tokens, visible as tokens, until somebody has real
 * ones with permission to print them. `prerender.test.tsx` refuses any token
 * that is not on the known list, and `scripts/prerender.mjs` prints every one
 * still in the built page at the end of a build. Filling a slot here with
 * something plausible is not a shortcut — it is the one edit that would make
 * every other claim on the page worthless.
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
 * Audited again in Aug 2026, after four changes to the product that this page
 * had not caught up with. What moved, and against what:
 *
 *   - The hero led with three capabilities. It leads with the look-back now,
 *     because `signals/retrospective.py` makes that an offer rather than a
 *     description — it runs on the history the sync just pulled.
 *   - "See what it would have caught" promised below-floor quotes on day one
 *     and could not deliver them: replaying a floor needs `QuoteDecision` rows,
 *     and a book that connected this morning has none. The decline half runs on
 *     synced history alone and is true from the first sync, so the claim is now
 *     split by when each half becomes true.
 *   - "Four steps, and PIE tracks which are done" rendered three. `onboarding`
 *     does have four, but they are the setup checklist and not this narrative —
 *     the heading was counting one list while the page showed the other.
 *   - The value ledger was fourteen words inside a pricing card. It is Section D
 *     now, and every claim in it is a property `attribution/` enforces.
 *   - The proof bar was four adjectives. Each one names its mechanism now; the
 *     specifics are free and they are the only credibility available to a
 *     vendor with no customers it can name.
 *
 *   - The pricing section named three tiers whose upper two could be reached
 *     only by somebody with a shell on the server, so it described a purchase
 *     nobody could make. `PlanChangeRequest` gave it a real mechanism and the
 *     section now says what that is. The rate lock and the yearly discount stay
 *     as written: they are commitments rather than product claims, and no
 *     module was ever going to implement them.
 *
 * Repositioned in Aug 2026, for a different buyer: a US or European
 * mid-market industrial distributor on Prophet 21, NetSuite or Acumatica,
 * buying an annual contract rather than a monthly subscription. What that
 * changed here, and what it deliberately did not:
 *
 *   - The hero leads with the financial outcome and a commitment to prove it
 *     ("see how much you kept"), where it used to lead with the look-back.
 *     The look-back is still an offer and still evidence — it is Section C's
 *     second step, which is where the narrative reaches it.
 *   - "Why it can be trusted" moved out from between Sections C and D to
 *     directly under the hero. It was reading as a co-equal pillar — one more
 *     thing the product does — when its job is to answer the question the
 *     outcome claim provokes. Nothing in it was weakened: the determinism, the
 *     policy stamp, the append-only record and the ledger that says UNKNOWN are
 *     the reason the outcome claim is sayable at all.
 *
 *   - Prices are per visitor now. A non-Indian visitor sees the dollar list
 *     and no rupee figure anywhere on the page — not in the panels, not in the
 *     worked card, not in the arithmetic under the panels — because ₹9,999 is
 *     an anchor this positioning cannot survive a reader forming. Indian
 *     visitors still see rupees. The dollar list is what the prerender bakes,
 *     so it is also what a crawler and a no-JavaScript reader get; the swap
 *     happens on mount and only for a browser whose own clock says India. See
 *     `pricing.ts`.
 *   - "Ask for this plan" became "Book a demo" on the paid panels, and the
 *     hero's primary action with it. Nobody signs an annual contract from
 *     inside a product trial, and the page had no way for a buyer who was
 *     ready to talk to say so. The trial keeps its own button.
 *   - The two panels that read as two products are one platform with a free
 *     floor now. That is a reversal of the note above about "free forever",
 *     and it is not a return to it: `PlanTier.FREE` is still not a
 *     destination anybody chooses, and the page still does not offer it as
 *     one. What it now says is the thing that is mechanically true and was
 *     missing — the desk keeps working when nothing is being paid for, which
 *     is what makes the trial safe to start.
 *
 * Three smaller corrections, made while repositioning the page (below):
 *
 *   - The zone letters are gone. See the comment on the sheet.
 *   - The decision card named "ABC Industries", which reads as a placeholder
 *     somebody forgot rather than as an anonymised customer. It is "a machine
 *     shop in Ohio" now — a description, not a name, because the invention rule
 *     on this page forbids a customer name that was never supplied.
 *   - "About 18 months of history" was a hedge on a page that is otherwise
 *     exact, and the number was never approximate: `_default_since` in
 *     `routers/connections.py` computes the first day of the month
 *     `DEFAULT_HISTORY_MONTHS` back, and the connect form lets an owner move
 *     it. The record list stays deliberately coarse — Prophet 21 declares no
 *     `customer_payments` permission and Acumatica no `quotes`, so "customers,
 *     items, invoices and bills, plus payments and orders where the system
 *     exposes them" is the sentence that is true of every connector.
 *
 * The eyebrow is unchanged on purpose. `prerender.test.tsx` pins that string, so
 * moving it is a deliberate act rather than a copy edit, and the positioning
 * question it belongs to is not settled on this branch.
 *
 * The decision card's figures are illustrative but formula-consistent:
 * floor = cost / (1 − margin floor). They live in `pricing.ts` now, in both
 * currencies, and `pricing.test.ts` re-derives every one of them — so editing
 * the card without editing the note that re-reads it fails a test rather than
 * shipping a page whose own arithmetic does not close. The card shows cost
 * because it depicts the *approver's* view — a manager sees cost, a
 * salesperson never does. Keep both properties when editing the numbers.
 *
 * That rule used to be broken by the page's own buttons. Both CTAs said "Get
 * started free" and led to a *sign-in* form, because the only way to get an
 * account was an operator running `python -m app.provision_org` — so the one
 * promise the page made twice, in its largest type, was the one thing a visitor
 * could not do. `onSignUp` is that path. It is optional because sign-up is a
 * deployment's choice (`SELF_SERVE_SIGNUP`, off by default): where it is absent
 * the CTAs fall back to sign-in, which is honest for a single-tenant install
 * and was the whole behaviour before.
 */
export function Landing({ onEnter, onSignUp, onDemo }: {
  onEnter: () => void;
  /** Takes the plan the visitor was reading about, where they came through a
   *  pricing panel. It only preselects the radio on the sign-up form — the
   *  account created is the free one whichever panel was pressed, because
   *  nothing here sells anything. */
  onSignUp?: (plan?: string) => void;
  onDemo?: () => void;
}) {
  const enter = (e: React.MouseEvent) => {
    e.preventDefault();
    onEnter();
  };
  /** The two big CTAs: sign up where it is offered, sign in where it is not. */
  const start = (e: React.MouseEvent) => {
    e.preventDefault();
    (onSignUp ?? onEnter)();
  };
  /** A pricing panel's own CTA. Same door, opened on that plan.
   *
   *  The panels were three static blocks with nothing to press, so the one
   *  place a visitor has decided which plan they want was the one place the
   *  page stopped talking to them — they had to scroll back to a CTA that
   *  asked the question again. */
  const startOn = (plan: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    (onSignUp ?? onEnter)(plan);
  };
  /** The third door, and the only one that asks for nothing. Optional for the
   *  same reason `onSignUp` is: a deployment without a demonstration workspace
   *  must show no such button rather than one that leads nowhere. */
  const demo = (e: React.MouseEvent) => {
    e.preventDefault();
    onDemo?.();
  };

  /** Which price list this visitor sees, and in which currency every figure on
   *  the page is written.
   *
   *  INTL until proven otherwise, and deliberately so: `useEffect` does not run
   *  during the static prerender, so the HTML baked into `dist/index.html` — the
   *  document a crawler reads, and the one a US visitor sees before the bundle
   *  arrives — carries the dollar list and no rupee figure anywhere in it. The
   *  swap to the Indian list happens on mount, for the visitors whose own clock
   *  says they are in India.
   *
   *  State rather than a call in the render body because `detectRegion` reads
   *  `Intl` and `navigator`, neither of which exists on the server, and because
   *  a component that renders differently on the server and on the first client
   *  paint is the one thing `prerender.tsx` is built to avoid. */
  const [region, setRegion] = useState<Region>("INTL");
  useEffect(() => setRegion(detectRegion()), []);
  const price = pricingFor(region);
  const line = price.line;

  const bookDemo = demoLinkProps();

  // The mobile nav collapses the section links behind a menu button. Closed on
  // first render, which is also the state the prerenderer bakes into the static
  // HTML — a signed-out visitor without JS sees the logo and Sign in, and the
  // toggle comes alive when the bundle mounts. Tapping a link closes it so the
  // panel is not left covering the section it just jumped to.
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="pie-landing">
      <div className="lp-sheet">
        {/* The drawing frame used to carry zone letters along its top margin —
            A B C D E, set above the sheet in the page's own top gutter. On a
            works drawing they are a coordinate system; here there was nothing
            to reference them from, so the row read as five stray capitals
            floating over the nav. The frame keeps the motif; the letters are
            gone. The section names on the dimension lines below are the real
            navigation aid, and they read as words. */}
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
              <a href="#product" onClick={closeMenu}>Product</a>
              <a href="#how" onClick={closeMenu}>How it works</a>
              <a href="#worth" onClick={closeMenu}>What it&rsquo;s worth</a>
              <a href="#proof" onClick={closeMenu}>Proof</a>
              <a href="#trust" onClick={closeMenu}>Trust</a>
              <a href="#pricing" onClick={closeMenu}>Pricing</a>
              <a className="lp-btn solid lp-nav-cta" href="#signin" onClick={enter}>Sign in</a>
            </div>
          </div>
        </nav>

        <header className="lp-hero" id="top">
          <div className="lp-wrap lp-hero-grid">
            <div className="lp-hero-copy">
              <p className="lp-eyebrow">The commercial intelligence layer for distributors</p>
              <h1>Stop quoting away your <em>margin</em> — and see how much you kept.</h1>
              {/* The second sentence is a commitment, not a capability list.
                  "How much you kept" is only worth leading with if the reader
                  can check it, so the sentence that follows the promise says
                  where the figure comes from and that it opens — which is what
                  `attribution/` actually does, and what Section D spells out.
                  Everything else the product does is a description, and a
                  description belongs below the fold. */}
              <p className="lp-sub">
                PIE checks every quote line against <b>your own floor</b> before
                it goes out and holds what breaches it for a manager. Then it
                reports <b>the margin that held</b> — every figure openable, and
                re-derivable from the rows your desk already wrote. Connected to
                the ERP you already run.
              </p>
              {/* The primary action is a conversation now, not a signup.
                  Nobody commits a distributor to an annual contract from
                  inside a product trial, and a page that only offers the trial
                  makes the buyer who is ready to talk go and find an address.
                  The trial keeps its own button, one step quieter. */}
              <div className="lp-ctas">
                <a className="lp-btn solid" {...bookDemo}>Book a demo</a>
                <a className="lp-btn" href="#signin" onClick={start}>Start free</a>
                {onDemo
                  ? <a className="lp-quiet" href="#demo" onClick={demo}>or see it on sample data</a>
                  : <a className="lp-quiet" href="#pricing">or see the pricing</a>}
              </div>
              <p className="lp-fine">
                A 30-day trial of the whole platform — no card. The quote desk
                keeps working whether or not you subscribe.
              </p>
            </div>

            <div
              className="lp-card"
              role="img"
              aria-label="An illustrative PIE decision card, drawn like an engineering sheet: a quote priced below the margin floor, showing cost, margin floor and recommended price, with an approval action and a title block naming the policy that computed it."
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

        {/* Each of these was an adjective, and an adjective is a claim. To a
            buyer who has survived one unfinished ERP implementation,
            "deterministic" reads as the same register as everything else they
            were promised. So each one now names the mechanism that enforces it
            — the specifics are free, they are all true, and specificity is the
            only credibility available to a vendor with no customers to name. */}
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
            {/* Three of these are links, and the other four are not, because
                three have a page of their own at /erp/{system} — what PIE
                reads out of that system, what it can write back, and what it
                cannot see. They are also the only route by which those pages
                are reachable from this one: a page in the sitemap and nowhere
                in the site is an orphan, and reads like one. */}
            <span className="lp-sys">Zoho Books</span>
            <a className="lp-sys" href="/erp/netsuite">NetSuite</a>
            <span className="lp-sys">Dynamics 365 BC</span>
            <a className="lp-sys" href="/erp/acumatica">Acumatica</a>
            <a className="lp-sys" href="/erp/prophet-21">Prophet 21</a>
            <span className="lp-sys">Sage X3</span>
            <span className="lp-sys">Sage 100</span>
            <span className="lp-sched-note">
              — your ERP records what happened; PIE helps you decide what to do next
            </span>
          </div>
        </div>

        <div className="lp-invariant">
          <div className="lp-wrap">
            <div className="lp-cols">
              <div>
                <p className="lp-eyebrow">Why it can be trusted</p>
                <h2>The AI never computes <em>a single number.</em></h2>
                <p>
                  Your business data and rules determine the number. AI explains
                  it.
                </p>
                <p>
                  Same inputs, same answer, every time — with a paper trail.
                </p>
              </div>
              <ul>
                <li>
                  <b>Deterministic calculations</b> — every figure is arithmetic
                  on your records; turn AI off and every number still works
                </li>
                <li>
                  <b>Auditable decisions</b> — each number names the policy that
                  produced it, so past decisions stay explainable
                </li>
                <li>
                  <b>Your data</b> — read from your own books, used for you
                  alone; AI runs on your own account
                </li>
                <li>
                  <b>Your rules</b> — you set the floors and thresholds, and PIE
                  holds every quote to them
                </li>
              </ul>
            </div>
          </div>
        </div>

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

        <div className="lp-dim"><b>Section A — The problem</b></div>
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

        <div className="lp-dim"><b>Section B — The product</b></div>
        <section id="product">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>A quote desk with a commercial brain behind it</h2>
              <p>
                Three outcomes: <b>know what to quote</b>, <b>know what you
                cannot afford to quote</b>, and <b>know which customers need
                attention</b>. Unlimited named accounts, each seeing what their
                role allows — salespeople quote confidently{" "}
                <b>without ever seeing your cost</b>.
              </p>
            </div>
            <div className="lp-grid3">
              <div className="lp-feat">
                <p className="lp-eyebrow">Quote</p>
                <h3>Quote from a pasted email</h3>
                <p>
                  Drop a customer&rsquo;s RFQ in as they wrote it; PIE reads it
                  into quote lines and resolves each code against your catalog.{" "}
                  <b>In your 30-day trial.</b>
                </p>
              </div>
              <div className="lp-feat">
                <p className="lp-eyebrow">Margin</p>
                <h3>Margin floors &amp; approvals</h3>
                <p>
                  Your policy sets the floor per line. A breach doesn&rsquo;t send
                  quietly — it routes for sign-off, and the sign-off is on record.{" "}
                  <b>In your 30-day trial.</b>
                </p>
              </div>
              <div className="lp-feat">
                <p className="lp-eyebrow">Quote</p>
                <h3>Your catalog, decoded</h3>
                <p>
                  We decode your suppliers&rsquo; price lists so every code, size
                  and grade is understood — and the coverage is measured against
                  your own catalog, not promised. Pay once,{" "}
                  <b>own it permanently</b>.
                </p>
              </div>
              <div className="lp-feat">
                <p className="lp-eyebrow">Quote</p>
                <h3>Equivalents, scored not guessed</h3>
                <p>
                  PIE ranks alternatives from your catalog on geometry and grade
                  — deterministically — and <b>abstains when nothing
                  discriminates</b> rather than inventing a match.
                </p>
              </div>
              <div className="lp-feat">
                <p className="lp-eyebrow">Attention</p>
                <h3>A daily attention list</h3>
                <p>
                  Signals from your own numbers — margin drift, customer decline,
                  payments slipping — each written up in plain words, each
                  traceable to the figures that raised it.
                </p>
              </div>
              <div className="lp-feat">
                <p className="lp-eyebrow">Margin</p>
                <h3>Every quote, on record</h3>
                <p>
                  Each decision is stamped with the policy version and catalog
                  edition that produced it, and the record is append-only — so a
                  price you quoted last quarter still explains itself.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>Section C — How it works</b></div>
        <section id="how">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              {/* Said "Four steps" and rendered three. `onboarding.py` does have
                  four — connect, pull, floors, team — but they are the setup
                  checklist, not this narrative, and the heading was counting
                  one list while the page showed the other. Named for what is
                  below it; the checklist speaks for itself inside the product. */}
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
                  Your first month includes the full intelligence layer. PIE reads
                  the history it just pulled and shows the accounts that were
                  quietly declining and the margins that drifted —{" "}
                  <b>and how much of your book it could not judge</b>, before
                  what it found. Below-floor quoting is measured from the quotes
                  you price here, so that arrives as you use it.
                </p>
              </div>
              <div className="lp-step">
                <h3>Decide with the numbers on</h3>
                <p>
                  Set your floors, add your catalog, and quote. If the findings
                  don&rsquo;t pay for the subscription, stay on the free desk —{" "}
                  <b>everything you built stays yours</b>.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>Section D — What it was worth</b></div>
        <section id="worth">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              {/* Promoted out of a subordinate clause in the middle pricing
                  card, where it had been the strongest asset on the page
                  described in fourteen words. Every claim below is a property
                  of `backend/app/attribution/` and its own docstrings say so
                  more bluntly than this does. */}
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
                  with no rupee figure anywhere near them. Your business holds no
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
                  you are paying; PIE will not assume its own price.
                </p>
              </div>
            </div>
          </div>
        </section>

        <div className="lp-dim"><b>Section E — Ownership</b></div>
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
                  Downgrade any time: you keep the free desk, all your data, and
                  your decoded catalog keeps working as delivered.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ── Section F — Proof ──────────────────────────────────────────
            EVERY FIGURE AND EVERY MARK IN THIS SECTION IS A PLACEHOLDER, and
            the section must not be deployed while any of them is still here.
            `scripts/prerender.mjs` lists every `{{…}}` token left in the built
            page at the end of a build, so a deploy cannot ship one quietly.

            What must replace them:
              {{CUSTOMER_LOGO_1..4}}   the names of four real customers who
                                       have given written permission to be
                                       named. Names in plain type, as the
                                       systems row is — not image marks, which
                                       this stylesheet has no rule for and
                                       which would be the only images on the
                                       site.
              {{CASE_STUDY_*}}         one real customer's figures, read off
                                       their own value ledger and quoted with
                                       their permission.
              the compliance statuses  whatever is actually true on the day
                                       this ships. "In progress" is a fine
                                       answer; a certification that does not
                                       exist is not.

            This page's standing honesty rule was "no invented customers, no
            testimonials and no logos", and it is unchanged in the only sense
            that matters: nothing here may be invented. What changed is that
            the page now has a place to put the real ones, because a
            mid-market buyer being asked for an annual contract looks for
            exactly this section and reads its absence as an answer. A slot
            that is visibly empty is honest. A slot filled with a
            plausible-sounding customer is the single thing that would destroy
            the argument the rest of this page makes. */}
        <div className="lp-dim"><b>Section F — Proof</b></div>
        <section id="proof">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>What it was worth, on somebody else&rsquo;s book</h2>
              <p>
                One distributor, one ERP, one figure — and the ledger entry
                behind it. The number below is not a case-study estimate: it is
                what PIE&rsquo;s own value ledger computed from rows that
                customer&rsquo;s quote desk had already written, carrying the
                operands it was computed from, so it opens into the lines that
                produced it. The same screen is in the product, reporting on
                your book, from the day you start.
              </p>
            </div>

            {/* The systems row's shape, reused: a labelled band of plates in
                plain type. Not `<img>` — the stylesheet has no image rule, the
                page has never carried a logo, and a wall of grey marks says
                less to this reader than four names they recognise. */}
            <div className="lp-sched">
              <div className="lp-sched-row">
                <span className="lp-sched-label">Running PIE today</span>
                <span className="lp-sys">{"{{CUSTOMER_LOGO_1}}"}</span>
                <span className="lp-sys">{"{{CUSTOMER_LOGO_2}}"}</span>
                <span className="lp-sys">{"{{CUSTOMER_LOGO_3}}"}</span>
                <span className="lp-sys">{"{{CUSTOMER_LOGO_4}}"}</span>
                <span className="lp-sched-note">
                  — named with written permission, or not named at all
                </span>
              </div>
            </div>

            <div className="lp-panel lp-case">
              <div className="lp-card-top">
                <span className="lp-chip kind">Value ledger · first 90 days</span>
                <span className="lp-chip kind">{"{{CASE_STUDY_ERP}}"}</span>
              </div>
              <h3>{"{{CASE_STUDY_DISTRIBUTOR_PROFILE}}"}</h3>
              <p>
                {"{{CASE_STUDY_NARRATIVE}}"} — what the desk was doing before,
                what the floor caught, and what the owner did about it. Two or
                three sentences, in their words where possible.
              </p>
              <div className="lp-facts">
                <div className="lp-fact">
                  <div className="k">Margin recovered</div>
                  <div className="v lp-num">{"{{CASE_STUDY_MARGIN_RECOVERED}}"}</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Quote lines checked</div>
                  <div className="v lp-num">{"{{CASE_STUDY_LINES_CHECKED}}"}</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Below floor, held</div>
                  <div className="v lp-num">{"{{CASE_STUDY_LINES_HELD}}"}</div>
                </div>
              </div>
              {/* The provenance strip, exactly as the hero card signs itself.
                  It is the difference between a case study and a claim: the
                  figure names the ledger that produced it and the policy
                  version in force while it did, and both are re-derivable from
                  that customer's own rows. */}
              <div className="lp-tblock lp-num">
                <div><span className="k">Source</span>PIE value ledger</div>
                <div><span className="k">Thresholds</span>{"{{CASE_STUDY_POLICY_VERSION}}"}</div>
                <div><span className="k">Window</span>{"{{CASE_STUDY_WINDOW}}"}</div>
                <div><span className="k">Re-derivable</span>yes</div>
              </div>
            </div>
          </div>

          {/* Compliance, as three statuses rather than three assertions. The
              band is the proof strip's own grid, narrowed to three columns.

              Nothing here claims a certification. A distributor's IT or
              finance function asks these three questions in the first meeting,
              and a page that does not answer them at all reads worse than one
              answering "in progress" — but only a page that answers them
              *truthfully* survives the diligence that follows. */}
          <div className="lp-proof lp-compliance">
            <div className="lp-wrap lp-proof-grid lp-cols-3">
              <div>
                <div className="v">SOC 2 Type II</div>
                <div className="k">
                  {"{{SOC2_TYPE_II_STATUS}}"} — this line states the real status
                  and nothing more. No certification is claimed until it says
                  one exists.
                </div>
              </div>
              <div>
                <div className="v">Data residency</div>
                <div className="k">
                  {"{{DATA_RESIDENCY}}"} — where this deployment stores your
                  rows, named as a region rather than as a promise.
                </div>
              </div>
              <div>
                <div className="v">GDPR DPA</div>
                <div className="k">
                  {"{{GDPR_DPA_STATUS}}"} — the processing agreement, and how to
                  get a copy. Erasure is already a mechanism rather than a
                  clause: see Section E.
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Who built it — three sentences, and the most credible three on the
            page. This category is full of software people who interviewed a
            distributor once, and the buyer knows it; a vendor who runs the
            business he is selling to does not have to be believed on that
            point, only checked.

            Every claim here is supplied fact rather than colour: three B2B
            industrial distribution businesses, in Hyderabad, on Zoho Books —
            which is also why this repository's own connector, thresholds and
            approval model were built against that book first (see CLAUDE.md).
            No photograph, no name, no title: none was given, and a marketing
            page is the wrong place to invent any of the three. If a name and a
            link belong here, they are the founder's to add. */}
        <div className="lp-notwhat">
          <div className="lp-wrap lp-founder">
            <b>Built by a distributor, on his own quote desk.</b> PIE was not
            built by software people who interviewed a distributor once — it is
            built by one, running three B2B industrial distribution businesses
            in Hyderabad on Zoho Books, quoting cutting tools every working day.
            The margin floors, the approval routing and the rule that no model
            ever computes a number were all answers to problems on that book
            before they were features on this page, which is why they are
            specific rather than general.
          </div>
        </div>

        <div className="lp-dim"><b>Section G — Pricing</b></div>
        <section id="pricing">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              {/* This read as two products — a free quote desk and a separate
                  intelligence product — which is not what is being sold and is
                  not what the entitlements actually do. There is one platform.
                  The quote desk is the part of it that never stops working, and
                  the decision layer is the part that is paid for. Said in that
                  order, the sequence a buyer moves through is obvious; said as
                  two panels of equal weight, they had to work it out. */}
              <h2>One platform. The quote desk keeps working; the decision layer is what you buy.</h2>
              <p>
                Unlimited users on every plan — nothing here counts seats.
                Early-adopter rates, locked for 24 months; yearly billing gets
                two months free.
              </p>
              {/* The mechanism, quieter than the offer. It describes what
                  actually happens rather than dressing it as a checkout, which
                  there still is not: `PlanChangeRequest` records the ask and an
                  operator applies it. Saying "no card" is worth more to this
                  buyer than a payment page would be.

                  The two terms above are commitments rather than mechanisms —
                  nothing in the code enforces a rate lock or a yearly discount
                  — which is fine for a price list and is why they sit in the
                  sentence about what we will do rather than among the claims
                  about what the product does. */}
              <p className="lp-pricing-how">
                Every organization starts on a full 30-day trial of everything —
                no card, no conversation needed. Buying is a conversation: book
                a demo, and the plan is then requested from inside the product
                and confirmed by a person. There is no checkout. If the trial
                ends without one, the decision layer locks, the quote desk
                carries on, and{" "}
                <b>everything you have put in stays exactly where it is</b>.
              </p>
            </div>
            <div className="lp-grid3">
              {/* Not "free forever". `domain/enums.PlanTier` is explicit that
                  FREE "is **not a product** … the floor an organization sits on
                  when it is paying for nothing", and `entitlements.PURCHASABLE`
                  leaves it out precisely so it cannot be chosen as a
                  destination. What is true — and what this panel says — is that
                  the desk keeps working when nothing is being paid for, which
                  is the entry the buyer actually experiences and the promise the
                  trial has to keep. */}
              <div className="lp-panel lp-plan">
                <h3>Quote desk</h3>
                <div className="p">Free</div>
                <p>
                  Where everyone starts, and what keeps working when nothing is
                  being paid for: quoting, RFQ reading, margin floors and
                  approvals, on one connected company. Your first 30 days
                  include everything below.
                </p>
                <a className="lp-btn" href="#signin" onClick={startOn("intelligence")}>
                  Start your trial
                </a>
              </div>
              <div className="lp-panel lp-plan mid">
                <h3>Commercial Intelligence</h3>
                <div className="p lp-num">
                  {price.tierIntelligence}<small> /month</small>
                </div>
                <p>
                  <b>The decision layer.</b> The attention list, customer
                  health, collections — and the value ledger in Section D, which
                  is how you decide whether to keep paying for this.
                </p>
                <a className="lp-btn solid" {...bookDemo}>Book a demo</a>
              </div>
              <div className="lp-panel lp-plan">
                <h3>Platform</h3>
                <div className="p lp-num">
                  {price.tierPlatform}<small> /month +</small>
                </div>
                <p>
                  <b>Commercial intelligence across the business.</b> Several
                  companies, one view — with all catalog builds included and a
                  named person who knows your setup.
                </p>
                <a className="lp-btn" {...bookDemo}>Book a demo</a>
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
                the module it is describing, two sections after promising it
                does not.

                Both figures come from `pricing.ts`, where a test re-derives
                them from the card's own cost and floor. It used to say "about
                three weeks of the middle plan", which cannot be said while the
                middle plan is a placeholder — and should not be said even once
                it is not, because the ratio changes with every price. */}
            <p className="lp-pricing-note">
              The card at the top of this page is one worked line:{" "}
              {line.units} units asked at {unitPrice(price, line.asked)} against
              a floor of {unitPrice(price, line.floor)}. Held to that floor it
              is <span className="lp-num">{lineTotal(price, heldToFloor(price))}</span>,
              from a single line. The ledger would count exactly that and not
              the {lineTotal(price, heldToRecommended(price))} the recommended
              price would have made, because clearing a floor by more than it
              asked for is your judgement, not ours.
            </p>
            <p className="lp-pricing-note">
              One-time catalog builds from{" "}
              <span className="lp-num">{price.catalogBuild}</span>, yours
              permanently. Every organization starts on the 30-day trial and
              works the same day — the paid plans are enabled with you, and
              nothing is charged when you sign up.
            </p>
          </div>
        </section>

        <div className="lp-final">
          <div className="lp-wrap">
            <h2>Your books already know where the margin went.</h2>
            <p>PIE turns that history into better commercial decisions — and reports what that was worth.</p>
            <div className="lp-ctas lp-ctas-centred">
              <a className="lp-btn solid" {...bookDemo}>Book a demo</a>
              <a className="lp-btn" href="#signin" onClick={start}>Start free</a>
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
              Already have an account?{" "}
              <a href="#signin" onClick={enter}>Sign in</a>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
