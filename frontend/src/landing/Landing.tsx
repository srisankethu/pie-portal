import "./landing.css";

/**
 * The public front door — what a signed-out visitor sees before the sign-in
 * card. Purely presentational (no data fetch, no session), styled entirely
 * from the theme's emitted tokens so it reads as the same product as the
 * screens behind it.
 *
 * The honesty rule this page keeps on purpose: every figure on it is a real,
 * checkable property of the product (the corpus numbers, the determinism
 * claims, the plan prices) — no invented customers, no testimonials, no logos.
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
export function Landing({ onEnter, onSignUp }: {
  onEnter: () => void;
  onSignUp?: () => void;
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

  return (
    <div className="pie-landing">
      <nav className="lp-nav">
        <div className="lp-wrap lp-nav-inner">
          <a className="lp-logo" href="#top">PIE<span>.</span></a>
          <div className="lp-nav-links">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
            <a href="#pricing">Pricing</a>
            <a className="lp-btn solid" href="#signin" onClick={enter}>Sign in</a>
          </div>
        </div>
      </nav>

      <header className="lp-hero" id="top">
        <div className="lp-wrap lp-hero-grid">
          <div className="lp-hero-copy">
            <p className="lp-eyebrow">For distributors and B2B traders on Zoho Books</p>
            <h1>Stop quoting away your <em>margin.</em></h1>
            <p className="lp-sub">
              PIE connects to the books you already keep and watches the two
              places trading businesses lose money: <b>quotes going out too
              cheap</b> and <b>customers quietly slipping away</b>. Every figure
              is computed, versioned and auditable — <b>AI phrases, it never
              prices</b>.
            </p>
            <div className="lp-ctas">
              <a className="lp-btn solid" href="#signin" onClick={start}>Get started free</a>
              <a className="lp-btn" href="#pricing">See pricing</a>
            </div>
            <p className="lp-fine">
              Free quote desk forever · your first month includes the full
              intelligence layer
            </p>
          </div>

          <div
            className="lp-card"
            role="img"
            aria-label="A PIE decision card: a quote priced below the margin floor, with computed facts and an approval action."
          >
            <div className="lp-card-top">
              <span className="lp-chip alert">Below floor</span>
              <span className="lp-chip kind">Quote Q-1147 · line 3</span>
            </div>
            <h3>This line is priced under your own floor</h3>
            <p className="lp-card-body">
              <b>ABC Industries</b> asked for 200 units; the proposed price sits
              below the negotiation floor your margin policy sets for this item.
              Sending it needs a manager's sign-off — the platform holds it, not
              the salesperson.
            </p>
            <div className="lp-facts">
              <div className="lp-fact">
                <div className="k">Proposed</div>
                <div className="v lp-num down">₹412</div>
              </div>
              <div className="lp-fact">
                <div className="k">Floor</div>
                <div className="v lp-num">₹448</div>
              </div>
              <div className="lp-fact">
                <div className="k">Recommended</div>
                <div className="v lp-num">₹487</div>
              </div>
            </div>
            <div className="lp-actions"><span>Request approval</span><span>Reprice to floor</span></div>
            <p className="lp-prov lp-num">computed by policy ci_4f2a · thresholds v18 · every figure traceable</p>
          </div>
        </div>
      </header>

      <div className="lp-proof">
        <div className="lp-wrap lp-proof-grid">
          <div><div className="v lp-num">100%</div><div className="k">of a 6,717-SKU catalog decoded, zero rows skipped</div></div>
          <div><div className="v">Deterministic</div><div className="k">identical input, byte-identical output — reruns prove it</div></div>
          <div><div className="v lp-num">0</div><div className="k">prices computed by AI — models phrase, policy prices</div></div>
          <div><div className="v">Yours</div><div className="k">your data, your catalog, your AI account — provably</div></div>
        </div>
      </div>

      <section id="problem">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <p className="lp-eyebrow">The problem</p>
            <h2>Margin doesn&rsquo;t vanish in one bad deal. It leaks.</h2>
            <p>
              A busy quote desk makes hundreds of small pricing decisions a
              month. Nobody sees the pattern — because the pattern lives across
              two years of ledger, and nobody has time to read the ledger.
            </p>
          </div>
          <div className="lp-two">
            <div className="lp-panel">
              <span className="lp-tag">Leak one — outbound</span>
              <h3>Quotes below your own floor</h3>
              <p>
                A discount to close the month, a price copied from last year, a
                code quoted from memory. Each defensible; together they compound
                into points of gross margin. PIE checks every line against{" "}
                <b>your</b> margin policy at the moment of quoting — and holds
                what breaches it.
              </p>
            </div>
            <div className="lp-panel">
              <span className="lp-tag">Leak two — inbound</span>
              <h3>Customers quietly buying less</h3>
              <p>
                No angry email, no cancelled contract — just a customer whose
                orders thinned out three months ago, noticed at year end. PIE
                watches every account&rsquo;s pattern and raises the decline
                while there is still a relationship to save.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section id="product">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <p className="lp-eyebrow">The product</p>
            <h2>A quote desk with a commercial brain behind it</h2>
            <p>
              Free to start, and your whole team can use it — unlimited named
              accounts, each seeing exactly what their role allows. Salespeople
              quote confidently <b>without ever seeing your cost</b>.
            </p>
          </div>
          <div className="lp-grid3">
            <div className="lp-feat">
              <h3>Quote from a pasted email</h3>
              <p>
                Drop a customer&rsquo;s RFQ in as they wrote it; PIE reads it
                into quote lines and resolves each code against your catalog.{" "}
                <b>Free forever.</b>
              </p>
            </div>
            <div className="lp-feat">
              <h3>Margin floors &amp; approvals</h3>
              <p>
                Your policy sets the floor per line. A breach doesn&rsquo;t send
                quietly — it routes for sign-off, and the sign-off is on record.{" "}
                <b>Free forever.</b>
              </p>
            </div>
            <div className="lp-feat">
              <h3>Your catalog, decoded</h3>
              <p>
                We decode your suppliers&rsquo; price lists so every code, size
                and grade is understood — proven to 100% coverage. Pay once,{" "}
                <b>own it permanently</b>.
              </p>
            </div>
            <div className="lp-feat">
              <h3>Alternatives across brands</h3>
              <p>
                When the asked-for item is slow or thin, PIE proposes
                equivalents from the other brands you carry — deterministically,
                with the geometry to justify it.
              </p>
            </div>
            <div className="lp-feat">
              <h3>A daily attention list</h3>
              <p>
                Signals from your own numbers — margin drift, customer decline,
                payments slipping — each written up in plain words, each
                traceable to the figures that raised it.
              </p>
            </div>
            <div className="lp-feat">
              <h3>Authentic price lists</h3>
              <p>
                Every edition is fingerprinted on arrival and validated
                structurally, so every quoted price traces to a genuine, current
                manufacturer price list — never a stale or doctored file.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section id="how">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <p className="lp-eyebrow">How it works</p>
            <h2>Live in about a week. Six hours of your team&rsquo;s time.</h2>
            <p>Most of it is deciding your own margin floors — not fighting software.</p>
          </div>
          <div className="lp-grid3 lp-steps">
            <div className="lp-step">
              <h3>Connect your books</h3>
              <p>
                Link Zoho Books and PIE syncs up to <b>two years of history</b> —
                customers, items, invoices, bills, payments. New connectors as
                they ship.
              </p>
            </div>
            <div className="lp-step">
              <h3>See what it would have caught</h3>
              <p>
                Your first month includes the full intelligence layer. PIE looks
                back across your own history and shows the below-floor quotes
                and quiet declines <b>you already paid for</b>.
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
                Every price, margin, floor and priority is calculated
                deterministically from your persisted records, stamped with the
                version of the policy that produced it. A model reads those
                facts and phrases them — it may never invent one.
              </p>
              <p>
                That is what makes every card on screen auditable: same inputs,
                same answer, every time, with a paper trail.
              </p>
            </div>
            <ul>
              <li>
                Figures computed by <b>versioned policy</b> — change the policy
                and past numbers still say which rules judged them
              </li>
              <li>
                Model output is <b>validated on the way out</b>; a narrative
                that doesn&rsquo;t cite the real figures is refused
              </li>
              <li>
                AI runs on <b>your own</b> Anthropic, OpenAI or Google account
                at your rates — zero markup, and we set it up with you
              </li>
              <li>
                <b>Skip AI entirely</b> and every number still works — the
                intelligence is deterministic, the prose is optional
              </li>
              <li>
                Thin evidence is reported as <b>unknown</b>, never papered over
                — an empty screen beats a confident guess
              </li>
            </ul>
          </div>
        </div>
      </div>

      <section id="trust">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <p className="lp-eyebrow">Ownership</p>
            <h2>What you own — and we can prove it</h2>
            <p>Not policy-page promises: each of these is a mechanism in the product you can test.</p>
          </div>
          <div className="lp-grid3">
            <div className="lp-panel">
              <h3>Your data. Export any time, erase provably.</h3>
              <p>
                Full export on request. On exit, your data is destroyed —
                backups included — and you receive a signed erasure receipt you
                can verify.
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

      <section id="pricing">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <p className="lp-eyebrow">Pricing</p>
            <h2>Free to quote. Cheap to know. Priced per organization.</h2>
            <p>
              Unlimited users on every plan. Early-adopter rates, locked for 24
              months. Yearly billing gets two months free.
            </p>
          </div>
          <div className="lp-grid3">
            <div className="lp-panel lp-plan">
              <h3>Quote Desk</h3>
              <div className="p">Free</div>
              <p>
                The quote desk, RFQ reading, margin floors and approvals — for
                as long as you like, no card.
              </p>
            </div>
            <div className="lp-panel lp-plan mid">
              <h3>Commercial Intelligence</h3>
              <div className="p lp-num">₹9,999<small> /month</small></div>
              <p>
                Everything watched: the attention list, customer health,
                collections, cross-brand alternatives, authenticity checks.
              </p>
            </div>
            <div className="lp-panel lp-plan">
              <h3>Platform</h3>
              <div className="p lp-num">₹19,999<small> /month +</small></div>
              <p>
                Several companies, one view — with all catalog builds included
                and a named person who knows your setup.
              </p>
            </div>
          </div>
          <p className="lp-pricing-note">
            One-time catalog builds from <span className="lp-num">₹4,999</span>,
            yours permanently.
          </p>
        </div>
      </section>

      <div className="lp-final">
        <div className="lp-wrap">
          <h2>Your books already know where the margin went.</h2>
          <p>Connect them, and let PIE show you — the first month of intelligence is included.</p>
          <a className="lp-btn solid" href="#signin" onClick={start}>Get started free</a>
        </div>
      </div>

      <footer className="lp-footer">
        <div className="lp-wrap">
          <div>
            <b>PIE</b> — the Commercial Decision Platform. Deterministic
            numbers, auditable decisions, your data provably yours.
          </div>
          <div>
            Already have an account?{" "}
            <a href="#signin" onClick={enter}>Sign in</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
