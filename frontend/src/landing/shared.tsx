import { type FaqItem } from "./faq";
import { marginPct, unitPrice, type RegionExample } from "./worked-example";

import { hasProof } from "./proof";

/** The blocks the landing page and the ERP pages both carry.
 *
 * They were written twice — once in `Landing.tsx` and once in `ErpPage.tsx` —
 * and they had drifted before the second file was a day old: the trial line
 * said different things about what 30 days includes, and the trust band's
 * fourth bullet was "Your rules" on one page and "Provenance" on the other for
 * no reason anybody chose. That is the exact failure `CLAUDE.md` §2 is about,
 * and the exact failure this page's own history is about — a claim in two
 * places is a claim that will disagree with itself, and on this site the
 * disagreement is a customer reading two different promises.
 *
 * So: one definition each, here, with the differences that are real expressed
 * as props and the differences that were accidents removed.
 */

/** How long the demo is, said once.
 *
 *  It was "30–45 minutes" on the front page and "Thirty minutes" on all seven
 *  ERP pages — one meeting, two lengths, told to the same visitor if they read
 *  both. That is the defect this file exists for, arriving in the one shape a
 *  shared *component* cannot catch: a phrase inside two different sentences.
 *  A number a reader could hold us to is worth a constant. */
export const DEMO_LENGTH = "30–45 minutes";

/** The determinism band — the reason-to-believe, on every public page.
 *
 * `system` names the ERP a sub-page is about, so the sentence reads "Your
 * Prophet 21 data and your rules determine the number" there and "Your
 * business data" on the landing page. Everything else is identical by
 * construction, including the provenance bullet, which used to appear only on
 * the ERP pages and is true everywhere: every row carries the connector, the
 * connection and the id it came from.
 */
export function TrustBand({ system }: { system?: string }) {
  return (
    <div className="lp-invariant">
      <div className="lp-wrap">
        <div className="lp-cols">
          <div>
            <p className="lp-eyebrow">Why it can be trusted</p>
            <h2>The AI never computes <em>a single number.</em></h2>
            <p>
              Your {system ?? "business"} data and your rules determine the
              number. AI explains it.
            </p>
            <p>Same inputs, same answer, every time — with a paper trail.</p>
          </div>
          <ul>
            <li>
              <b>Deterministic calculations</b> — every figure is arithmetic on
              your own records; turn AI off and every number still works
            </li>
            <li>
              <b>Auditable decisions</b> — each number names the policy that
              produced it, so a price you quoted last quarter still explains
              itself
            </li>
            <li>
              <b>Your data</b> — read from your own books, used for you alone,
              exportable and erasable on request; AI runs on your own account
            </li>
            <li>
              <b>Your rules</b> — you set the floors and thresholds, and PIE
              holds every quote to them
            </li>
            <li>
              <b>Provenance</b> — every row carries the connector, the
              connection and the id it came from, so nothing in PIE claims a
              source it did not have
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}

/* `TrialFinePrint` used to be here — one sentence saying what the free 30 days
   actually are, shared by the landing page and the ERP pages because it had
   already drifted into two versions that promised different things.
   
   It is gone rather than unused. No public page markets the trial now: the
   front door asks for a demo and nothing else, and the sub-pages follow it.
   The sentence was correct to the last word (a trial lifts an organization to
   Commercial Intelligence and never to Platform — `entitlements.effective_plan`
   says so in as many words), which is exactly why it should not sit here
   waiting: a correct, tested, importable component is the one somebody adds
   back to a page without asking whether the page should be making the offer.
   `entitlements` is where that rule lives; this was only ever its shop window. */

/** The section links in the bar, once, for both public pages.
 *
 * They were two hand-kept lists — one in `Landing.tsx` as `#id`, one in
 * `ErpPage.tsx` as `/#id` — and they had already drifted: the ERP bar omitted
 * "Who it's for" for no reason anybody chose, and neither bar offered "What you
 * own" or the FAQ at all, so two substantial sections were reachable only by
 * scrolling past everything above them.
 *
 * The worse half is the failure mode `ErpPage.tsx` documents against itself:
 * those hrefs are cross-document, so a renamed section id does not break a
 * build, a type-check or a test — it produces a link that scrolls to the top of
 * the front page and looks like it worked. `/#product` and `/#plans` both
 * dangled that way, and both were found by a person rather than a check.
 *
 * One array fixes the drift; `nav.test.tsx` fixes the dangling, by rendering the
 * landing page and asserting every id below is a section on it. That test is the
 * point of this constant — a shared list that nothing verifies would dangle in
 * both bars at once instead of one.
 */
export interface NavItem {
  /** The landing page's section id. `#id` on the landing, `/#id` from an ERP
   *  page — the only difference between the two bars, and the reason this is a
   *  list of ids rather than a list of hrefs. */
  id: string;
  label: string;
}

/** Which sections earn a place in the bar.
 *
 * Not every section does, and the ones left out are left out for a reason:
 *
 * - `problem` opens the page directly under the hero. A link that scrolls a
 *   reader to what is already on their screen teaches them the bar is decorative.
 * - `talk` is in the bar already, as the "Book a demo" button. A text link to
 *   the same anchor beside it would be the same offer made twice, and the
 *   weaker of the two would be the one people read.
 * - `proof` is here but conditional — `navItems()` drops it when there is
 *   nothing to show, which is the same `hasProof()` the section itself asks.
 *   A bar advertising a section that did not render is the dangling link this
 *   file exists to prevent, arriving from the inside.
 *
 * `trust` and `faq` are the two additions. Data ownership — export, erasure,
 * what a customer keeps — is a question a buyer asks in the first meeting, and
 * the FAQ answers seven more; neither had any route from the bar.
 */
const ALL_NAV_ITEMS: NavItem[] = [
  { id: "outcomes", label: "Outcomes" },
  { id: "roles", label: "Who it\u2019s for" },
  { id: "how", label: "How it works" },
  { id: "worth", label: "What it\u2019s worth" },
  // `trust`, not `ownership`: the DOM id is what an href needs, and the id on
  // that section has always been `trust` even though the page letters it "What
  // you own". Naming the constant after the heading would have produced a link
  // that compiles, reads correctly, and goes nowhere.
  { id: "trust", label: "What you own" },
  { id: "proof", label: "Proof" },
  { id: "faq", label: "Questions" },
];

/** The bar's links for this render. Both pages call it, so neither can offer a
 *  route to a section the other's content decisions removed. */
export function navItems(): NavItem[] {
  return ALL_NAV_ITEMS.filter((item) => item.id !== "proof" || hasProof());
}

/** The footer's one-line statement of what PIE is. */
export function FooterBlurb() {
  return (
    <div>
      <b>PIE</b> — the commercial intelligence layer for distributors.
      Deterministic numbers, auditable decisions, your data provably yours.
    </div>
  );
}

/** The manager's decision card — the one worked example this site argues from.
 *
 * Lifted out of `Landing.tsx` when the industry pages arrived, for the reason
 * everything else in this file was: it is ninety lines of markup that three
 * documents now need, and three copies of a worked example is three chances
 * for the arithmetic on one of them to stop agreeing with the rule printed
 * beside it. `worked-example.ts` already refused to let the *figures* exist
 * twice; this refuses the same for the markup around them.
 *
 * `item` is the only thing that varies, and the reason it varies is worth
 * stating because it looks like a violation of the rule directly above it.
 * The landing page passes `EXAMPLE_ITEM`, a code that decodes to nothing in
 * any trade, and `prerender.test.tsx` holds the *front page* to that. An
 * industry page passes a code from the trade the page is about, which is not
 * an exception to the neutrality rule but the point of it: the front page must
 * not pick a trade because it is addressed to all of them, and
 * `/industries/cutting-tools` may pick one because its address says so. The
 * test is scoped to `slug === ""` for exactly this reason.
 *
 * `price` carries the currency, the locale and the line. The landing page
 * swaps it on mount from the browser's own clock; a prerendered sub-page ships
 * no JavaScript and therefore no clock, so it passes `exampleFor("INTL")` and
 * bakes one currency. That is not a limitation being worked around — a rupee
 * figure on a statically served page is the one thing
 * `prerender.test.tsx` forbids outright, because a reader who finds a price
 * anchors to it whether or not it was the offer being made to them.
 */
export function DecisionCard(
  { item, price }: { item: string; price: RegionExample },
) {
  const line = price.line;
  return (
      <div className="lp-card-cell">
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
          `A PIE decision card for ${item}, with sample figures. `
          + `A customer asked for ${line.units} units at ${unitPrice(price, line.asked)}, `
          + `which is ${marginPct(price)} margin — below the floor of `
          + `${unitPrice(price, line.floor)} that this organization's policy `
          + `sets for the item. It costs ${unitPrice(price, line.cost)} and the `
          + `recommended price is ${unitPrice(price, line.recommended)}. The line `
          + "routes for a manager's approval, and every fact names the record it "
          + "came from."
        }
      >
        <span className="lp-corner tl" aria-hidden="true" />
        <span className="lp-corner tr" aria-hidden="true" />
        <span className="lp-corner bl" aria-hidden="true" />
        <span className="lp-corner br" aria-hidden="true" />
        {/* Below this line the card is the product's own decision screen,
            element for element: the type-and-priority row, the subject as
            the heading, who it is routed to, "Facts · what the data
            shows" over a table whose every row names the record it came
            from, then the evidence and the actions that screen actually
            offers (`platform/PlatformApp.tsx`, the decision detail).

            It was a drawing of a card before — its own invented layout,
            its own invented identifiers, and a disclaimer chip shouting
            that none of it was real, which is a worse answer than the
            problem it fixed. A page that will not show its own product is
            a page arguing that the product is not worth showing. So: the
            real screen, sample figures, said once and quietly under the
            card rather than stamped across it. */}
        <div className="lp-card-top">
          <span className="lp-chip alert">Below floor</span>
          <span className="lp-chip kind">Approval needed</span>
        </div>
        {/* Not a heading. The card is one `role="img"` with a full
            aria-label, so nothing inside it is exposed to a screen
            reader anyway — but it sits between the page's h1 and its
            first h2, and an h1 followed by an h3 is a skipped level in
            the document outline that every accessibility checker will
            find and that no reader benefits from. Styled identically. */}
        <p className="lp-card-title">{item}</p>
        <p className="lp-card-body">
          {line.units} units for <b>{price.customer}</b>, quoted at{" "}
          {unitPrice(price, line.asked)} — routed to a sales manager, no
          individual owner. The platform holds it, not the salesperson.
        </p>

        <div className="lp-facts-mark">Facts &middot; what the data shows</div>
        <table className="lp-facttable">
          <tbody>
            <tr>
              <td>Quoted unit price<span>this quote</span></td>
              <td className="lp-fv lp-num">{unitPrice(price, line.asked)}</td>
            </tr>
            <tr>
              <td>Effective unit cost<span>bill</span></td>
              <td className="lp-fv lp-num">{unitPrice(price, line.cost)}</td>
            </tr>
            <tr>
              <td>Margin floor<span>your margin policy</span></td>
              <td className="lp-fv lp-num">{unitPrice(price, line.floor)}</td>
            </tr>
            <tr>
              <td>Recommended<span>this customer&rsquo;s own history</span></td>
              <td className="lp-fv lp-num">{unitPrice(price, line.recommended)}</td>
            </tr>
            <tr>
              <td>Margin at this price<span>computed</span></td>
              <td className="lp-fv lp-num warn">{marginPct(price)}</td>
            </tr>
          </tbody>
        </table>

        {/* The real screen names the connector each record came from —
            "zoho · invoice", because that is the book that answered. On
            this page there is no such book yet: the reader has not
            connected one, and the strip below offers seven. Naming one
            of them here told six distributors the card was drawn for
            somebody else's stack, which is the same defect as the
            carbide part number two rows up. The record types are the
            part that is true of every connection. */}
        <div className="lp-facts-mark">Evidence used</div>
        <div className="lp-evi"><span>your ERP &middot; invoice</span><span>the price history</span></div>
        <div className="lp-evi"><span>your ERP &middot; bill</span><span>the cost</span></div>

        <div className="lp-actions"><span>Request approval</span><span>Full analysis &rarr;</span></div>
        {/* The title block stays, because what it says about the
            product is true and is the argument: a number here is
            computed by a policy you set and stamped with the version of
            it that judged the row. What it may not do is print a
            *particular* policy's hash and revision for a quote that does
            not exist. It names the mechanism now instead of forging an
            instance of it. */}
        <div className="lp-tblock lp-num">
          <div><span className="k">Computed by</span>your margin policy</div>
          <div><span className="k">Stamped with</span>its version</div>
          <div><span className="k">AI&rsquo;s part</span>none</div>
          <div><span className="k">Figures</span>sample</div>
        </div>
      </div>
      <p className="lp-card-note">
        The manager&rsquo;s decision card, as the product draws it — with
        sample figures, because no customer&rsquo;s numbers belong on a
        public page.
      </p>
      </div>

  );
}

/** The collapsed bar every prerendered sub-page carries.
 *
 * Three page families now ship it — `/erp/*`, `/industries/*`, `/roles/*` —
 * and it was written twice before the third arrived, which is the point at
 * which this file's own opening paragraph stops being advice.
 *
 * The `<details>` is load-bearing rather than stylistic. These documents ship
 * no module script, so a menu whose behaviour is React's would render and
 * refuse to open; a disclosure is the browser's own. Below 640px the summary
 * shows and the panel collapses, above it the summary is hidden and the panel
 * is forced visible, so the bar is the ordinary row it always was. If a browser
 * ever refused that override the page degrades to a menu button that opens —
 * not to a broken nav.
 *
 * It replaced a flat row of links, and the measurements are worth keeping
 * because they are the argument: in Chromium at 320-430px that row made a
 * sticky bar **131px tall** against the landing page's 71px — a fifth of a
 * phone screen, on every sub-page — whose links were **17px** high, where the
 * landing page's own mobile menu already gave each one 44+ and
 * `e2e/.shots/a11y.mjs` holds the signed-in app to the same number. Both
 * panels are one rule now, so both are 44; `e2e/.shots/public-a11y.mjs`
 * measures it. The premise behind the flat row — "a burger that cannot open is
 * worse than four links that wrap" — was simply wrong: a burger *can* open
 * with no script behind it.
 *
 * The list itself is `navItems()`, shared with the landing page's own bar, so
 * neither can offer a route to a section the other's content decisions removed
 * — `proof` drops out of both together when there is nothing to show. This
 * component owns the *shape* of the bar and none of its contents, which is the
 * split that matters: two families of sub-page render the same collapsed bar,
 * and all three public surfaces agree about what is in it.
 *
 * Every link is absolute. A bare `#outcomes` on a sub-page is a fragment that
 * goes nowhere, and these are cross-document links to the landing page's own
 * section ids — `nav.test.tsx` renders the landing and asserts every id in the
 * list is a section on it, and `prerender.test.tsx` holds every emitted href
 * against the same thing. `/#product` and `/#plans` both dangled once for
 * exactly that reason, and both were found by a person rather than a check.
 */
export function SubPageNav() {
  return (
    <nav className="lp-nav">
      <div className="lp-wrap lp-nav-inner">
        <a className="lp-logo" href="/">PIE<span>.</span></a>
        <details className="lp-nav-menu">
          <summary className="lp-nav-toggle" aria-label="Menu">
            <span className="lp-burger" aria-hidden="true" />
          </summary>
          <div className="lp-nav-links">
            {navItems().map((item) => (
              <a key={item.id} href={`/#${item.id}`}>{item.label}</a>
            ))}
            <a className="lp-nav-signin" href="/#signin">Sign in</a>
            <a className="lp-btn solid lp-nav-cta" href="/#talk">Book a demo</a>
          </div>
        </details>
      </div>
    </nav>
  );
}

/** One question-and-answer block, and the schema that may describe it.
 *
 * Both sub-page families that carry an FAQ render it through here, because
 * they rendered it identically twice and the only difference was the heading —
 * the near-exact duplication `CLAUDE.md` §2 names, in the shape it usually
 * arrives in: a second page copying a first that was correct.
 *
 * The order matters more than the markup. `scripts/prerender.mjs` emits
 * `FAQPage` JSON-LD from `page.faq`, the same array this maps over, and only
 * for a page that declares one. The site's standing rule is that schema may
 * restate what is on the page and nothing else — which is why the Aug 2026 SEO
 * audit recorded FAQPage as *not claimed*, no page having rendered an FAQ then.
 * A page that renders one earns the node. There is deliberately no way to
 * declare an FAQ that the page does not show.
 */
export function FaqSection(
  { heading, faq }: { heading: string; faq: FaqItem[] },
) {
  return (
    <>
      <div className="lp-dim"><b>Questions</b></div>
      <section id="faq">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <h2>{heading}</h2>
          </div>
          <div className="lp-two">
            {faq.map((entry) => (
              <div className="lp-panel" key={entry.question}>
                <h3>{entry.question}</h3>
                <p>{entry.answer}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}

/** What this page's product does *not* do, printed before the closing CTA.
 *
 * `erp.ts` argues the case at length and it holds for every family: a
 * distributor who has survived one implementation does not believe a
 * capability list, they believe a vendor who volunteers the gaps — and they
 * find out anyway in week three. So every sub-page carries one of these, and
 * the registries' own tests hold each list to a minimum length, because the
 * failure mode is not omitting the section but hollowing it out.
 *
 * `label` differs between families ("What PIE does not do here" against "…for
 * you") and the rest does not, which is exactly the split a prop is for.
 */
export function LimitsSection(
  { label, lead, items }: { label: string; lead: React.ReactNode; items: string[] },
) {
  return (
    <>
      <div className="lp-dim"><b>{label}</b></div>
      <section id="gaps">
        <div className="lp-wrap">
          <div className="lp-sec-head">
            <h2>The limits, before you find them in week three</h2>
            <p>{lead}</p>
          </div>
          <ul className="lp-gaps">
            {items.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      </section>
    </>
  );
}
