/** Where a visitor is, as far as a browser can honestly say — and what that
 *  implies about the money they trade in.
 *
 * Outside `landing/` and outside `platform/` for the reason `Tip.tsx` is: two
 * parts of this app need the same answer, and two implementations of "is this
 * person in India?" would give different ones. The landing page reads it to
 * decide which price list to show; the sign-up form reads it to decide which
 * currency to *offer* a new organization. Those must agree — a visitor shown
 * dollars who then signs up into a rupee tenant has been told two things.
 */

/** Which price list, and which currency, a visitor is offered by default. */
export type Region = "IN" | "INTL";

/** Where the visitor is, as far as a browser can honestly say.
 *
 * The clock, not the language: `navigator.language` is what somebody chose to
 * read in and travels with them, while the IANA zone is set from the machine's
 * own location and is the closest thing a static page gets to "where is this
 * person actually". A US buyer who reads in `en-IN` must not be shown rupees,
 * which is why the zone wins outright and the language is consulted only where
 * there is no zone at all.
 *
 * Never throws and never returns a maybe: any failure is INTL, which is also
 * what the prerendered HTML already says, so the worst case is that the page
 * does not change after it loads.
 */
export function detectRegion(): Region {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    // Asia/Calcutta is the older alias and is still what some browsers report.
    if (zone === "Asia/Kolkata" || zone === "Asia/Calcutta") return "IN";
    if (zone) return "INTL";
  } catch {
    /* no Intl, or a locked-down environment: fall through to the language */
  }
  try {
    const tags = [...(navigator.languages ?? []), navigator.language]
      .filter(Boolean) as string[];
    if (tags.some((tag) => /-IN\b/i.test(tag))) return "IN";
  } catch {
    /* no navigator (server render): INTL, which is what is baked in */
  }
  return "INTL";
}

/** The currencies a new organization may choose at sign-up.
 *
 * **This list is a commercial decision, not a technical one**, and it is short
 * on purpose: these are the markets the product is sold into today — the
 * repositioning's US and European mid-market, and the Indian book it was built
 * on. `money.ts` will format any ISO code the browser knows, so adding one here
 * costs nothing but saying PIE sells there.
 *
 * It matters more than a settings field usually would, because **nothing in the
 * product changes an organization's currency afterwards**: there is no admin
 * endpoint and no screen for it. Whatever is chosen here is what every figure
 * that organization ever sees is denominated in. Before this list existed the
 * form sent nothing and the API's default applied, so every self-serve tenant
 * in the world was INR — including the US distributors the site now sells to.
 */
export const SIGNUP_CURRENCIES: { code: string; label: string }[] = [
  { code: "USD", label: "US dollar (USD)" },
  { code: "EUR", label: "Euro (EUR)" },
  { code: "GBP", label: "Pound sterling (GBP)" },
  { code: "INR", label: "Indian rupee (INR)" },
];

/** What to preselect. The visitor can change it; this only decides which one
 *  is already correct for most of them. */
export function defaultCurrency(region: Region = detectRegion()): string {
  return region === "IN" ? "INR" : "USD";
}
