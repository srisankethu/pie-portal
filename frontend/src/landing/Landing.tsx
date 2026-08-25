import { useState } from "react";
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
 * customers live in: the page sits inside a drawing frame with zone letters,
 * sections are named on dimension-line dividers (which is why they carry no
 * eyebrow of their own), and the decision card signs itself with a title
 * block instead of a caption. The frame, zones and corner marks are
 * decorative and aria-hidden; the dividers are real text because they are the
 * section labels.
 *
 * The honesty rule this page keeps on purpose: every claim on it is a real,
 * checkable property of the product — the determinism and audit-trail claims,
 * the trial, the plan tiers, the connector registry's actual systems — with no
 * invented customers, no testimonials and no logos. The connectors are named in
 * plain type for the same reason.
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
 * The eyebrow is unchanged on purpose. `prerender.test.tsx` pins that string, so
 * moving it is a deliberate act rather than a copy edit, and the positioning
 * question it belongs to is not settled on this branch.
 *
 * The decision card's figures are illustrative but formula-consistent:
 * floor = cost / (1 − margin floor), so cost ₹381 at a 15% floor gives ₹448.
 * And the card shows cost because it depicts the *approver's* view — a
 * manager sees cost, a salesperson never does. Keep both properties when
 * editing the numbers.
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
        <div className="lp-zones" aria-hidden="true">
          <span>A</span><span>B</span><span>C</span><span>D</span><span>E</span>
        </div>

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
              <h1>Stop quoting away your <em>margin.</em></h1>
              {/* Led with three capabilities, which is a description. It now
                  leads with the offer, because the offer is evidence: the
                  look-back runs on the history the sync just pulled and needs
                  nothing the reader has to take on trust. The capabilities are
                  still here — they are the second sentence, where a description
                  belongs. */}
              <p className="lp-sub">
                Connect the books you already keep and PIE reads your own history
                back to you — <b>which accounts went quiet</b>,{" "}
                <b>where margin drifted</b>, and how much of it the platform
                could not judge. Then it checks every new quote line against your
                own floor before it goes out.
              </p>
              <div className="lp-ctas">
                <a className="lp-btn solid" href="#signin" onClick={start}>Get started free</a>
                {onDemo
                  ? <a className="lp-btn" href="#demo" onClick={demo}>See it on sample data</a>
                  : <a className="lp-btn" href="#pricing">See pricing</a>}
              </div>
              <p className="lp-fine">
                Free quote desk forever · your first month includes the full
                intelligence layer
              </p>
            </div>

            <div
              className="lp-card"
              role="img"
              aria-label="A PIE decision card drawn like an engineering sheet: a quote priced below the margin floor, showing cost, margin floor and recommended price, with an approval action and a title block naming the policy that computed it."
            >
              <span className="lp-corner tl" aria-hidden="true" />
              <span className="lp-corner tr" aria-hidden="true" />
              <span className="lp-corner bl" aria-hidden="true" />
              <span className="lp-corner br" aria-hidden="true" />
              <div className="lp-card-top">
                <span className="lp-chip alert">Below floor</span>
                <span className="lp-chip kind">Quote Q-1147 · line 3</span>
              </div>
              <h3>This line is priced under your own floor</h3>
              <p className="lp-card-body">
                <b>ABC Industries</b> asked for 200 units at ₹412 — below the
                floor your margin policy sets for this item. It routes for a
                manager's sign-off: the platform holds it, not the salesperson.
              </p>
              <div className="lp-facts">
                <div className="lp-fact">
                  <div className="k">Cost</div>
                  <div className="v lp-num">₹381</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Margin floor</div>
                  <div className="v lp-num">₹448</div>
                </div>
                <div className="lp-fact">
                  <div className="k">Recommended</div>
                  <div className="v lp-num">₹487</div>
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
            <span className="lp-sys">Zoho Books</span>
            <span className="lp-sys">NetSuite</span>
            <span className="lp-sys">Dynamics 365 BC</span>
            <span className="lp-sys">Acumatica</span>
            <span className="lp-sys">Prophet 21</span>
            <span className="lp-sys">Sage X3</span>
            <span className="lp-sys">Sage 100</span>
            <span className="lp-sched-note">
              — your ERP records what happened; PIE helps you decide what to do next
            </span>
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
                  Prophet 21 or Sage — and PIE pulls <b>about 18 months of
                  history</b>: customers, items, invoices, bills, payments.
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

        <div className="lp-dim"><b>Section F — Pricing</b></div>
        <section id="pricing">
          <div className="lp-wrap">
            <div className="lp-sec-head">
              <h2>Thirty days free. Then priced per organization.</h2>
              <p>
                Unlimited users on every plan — nothing here counts seats.
                Early-adopter rates, locked for 24 months; yearly billing gets
                two months free.
              </p>
              {/* Added once the ask existed. Before it, the page named three
                  tiers and the only way to reach the upper two was somebody
                  with a shell on the server — so the section described a
                  purchase nobody could make. It now describes what actually
                  happens, which is not a checkout and should not be dressed as
                  one: `PlanChangeRequest` records the ask, an operator applies
                  it, and an invoice follows. Saying "no card" is worth more to
                  this buyer than a payment page would be.

                  The two terms above are commitments rather than mechanisms —
                  nothing in the code enforces a rate lock or a yearly
                  discount — which is fine for a price list and is why they sit
                  in the sentence about what we will do rather than among the
                  claims about what the product does. */}
              <p className="lp-pricing-how">
                Every organization starts on a full 30-day trial — no card, no
                conversation. To subscribe you ask from inside the product and a
                person confirms it; there is still no checkout. If the trial
                ends without one, the decision layer locks and{" "}
                <b>everything you have put in stays exactly where it is</b>.
              </p>
            </div>
            <div className="lp-grid3">
              {/* Not a tier any more. This panel used to sell "Quote Desk,
                  free forever" as somewhere a business could choose and stay,
                  which made the trial a month of extra on top of a permanent
                  free product. There is one free thing now and it is the
                  trial, so the panel describes that — and says plainly what
                  happens after it, because a price list that goes quiet about
                  the end of the free period is the one people feel cheated by. */}
              <div className="lp-panel lp-plan">
                <h3>Trial</h3>
                <div className="p">30 days free</div>
                <p>
                  <b>All of it, from the day you sign up.</b> Quoting, margin
                  floors, approvals, the attention list and the insight screens.
                  No card. When it ends the decision layer locks and your data
                  is untouched.
                </p>
                <a className="lp-btn solid" href="#signin" onClick={startOn("intelligence")}>
                  Start your trial
                </a>
              </div>
              <div className="lp-panel lp-plan mid">
                <h3>Commercial Intelligence</h3>
                <div className="p lp-num">₹9,999<small> /month</small></div>
                <p>
                  <b>Quote more profitably.</b> The attention list, customer
                  health, collections — and the value ledger in Section D, which
                  is how you decide whether to keep paying for this.
                </p>
                <a className="lp-btn" href="#signin" onClick={startOn("intelligence")}>
                  Ask for this plan
                </a>
              </div>
              <div className="lp-panel lp-plan">
                <h3>Platform</h3>
                <div className="p lp-num">₹19,999<small> /month +</small></div>
                <p>
                  <b>Commercial intelligence across the business.</b> Several
                  companies, one view — with all catalog builds included and a
                  named person who knows your setup.
                </p>
                <a className="lp-btn" href="#signin" onClick={startOn("platform")}>
                  Ask for this plan
                </a>
              </div>
            </div>
            {/* The arithmetic, done on the page's own numbers rather than left
                for the reader to do or, worse, asserted as a round claim.

                It uses ₹7,200 and not the ₹15,000 the recommended price would
                have made, and the difference is the point: the ledger counts a
                movement only up to the floor, because clearing a floor by more
                than it asked for is the salesperson's judgement and not
                something the guardrail did. Quoting the bigger number here
                would be the page contradicting the module it is describing,
                two sections after promising it does not. */}
            <p className="lp-pricing-note">
              The card at the top of this page is one line: 200 units asked at
              ₹412 against a floor of ₹448. Held to that floor it is{" "}
              <span className="lp-num">₹7,200</span> — about three weeks of the
              middle plan, from one line. The ledger would count exactly that
              and not the ₹15,000 the recommended price would have made, because
              clearing a floor by more than it asked for is your judgement, not
              ours.
            </p>
            <p className="lp-pricing-note">
              One-time catalog builds from <span className="lp-num">₹4,999</span>,
              yours permanently. Every organization starts on the 30-day trial
              and works the same day — the paid plans are enabled with you, and
              nothing is charged when you sign up.
            </p>
          </div>
        </section>

        <div className="lp-final">
          <div className="lp-wrap">
            <h2>Your books already know where the margin went.</h2>
            <p>PIE turns that history into better commercial decisions.</p>
            <a className="lp-btn solid" href="#signin" onClick={start}>Get started free</a>
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
