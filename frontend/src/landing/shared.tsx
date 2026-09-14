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
