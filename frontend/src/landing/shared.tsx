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

/** The footer's one-line statement of what PIE is. */
export function FooterBlurb() {
  return (
    <div>
      <b>PIE</b> — the commercial intelligence layer for distributors.
      Deterministic numbers, auditable decisions, your data provably yours.
    </div>
  );
}
