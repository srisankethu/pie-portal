/** Where "Book a demo" goes.
 *
 * PLACEHOLDER — `{{DEMO_BOOKING_URL}}` is not a URL. Replace it with the
 * founder's real scheduling link (Cal.com, Calendly, HubSpot meetings, or
 * whatever is chosen) before this page is deployed. It is rendered verbatim so
 * an unreplaced token is obvious in the browser's status bar and in the built
 * HTML rather than plausible; `scripts/prerender.mjs` also lists every `{{…}}`
 * token it finds at the end of a build, so a deploy cannot ship one quietly.
 *
 * One constant rather than a literal per button: there are six of these
 * across the two components — four on the landing page (the hero, both paid
 * panels and the closing block) and one in each ERP page's hero and closing
 * block — and a scheduling link that is right in five places and stale in the
 * sixth is the ordinary way this goes wrong.
 *
 * Not to be confused with the *other* demo on this page. `onDemo` opens a
 * read-only workspace of sample data inside the product ("See it on sample
 * data") and needs no scheduling; this books time with a person, which is how
 * a purchase at this size actually starts. Both are called a demo by the
 * people who ask for them, so the labels stay different in the markup.
 */
export const DEMO_BOOKING_URL = "{{DEMO_BOOKING_URL}}";

/** Whether that link has been filled in. Used only to keep an unreplaced
 *  placeholder out of `rel="noreferrer" target="_blank"` semantics that would
 *  open a blank tab on the site's own 404 — the button still renders, and
 *  still shows the token, because hiding it would hide the thing that has to
 *  be fixed. */
export const DEMO_BOOKING_READY = !DEMO_BOOKING_URL.startsWith("{{");

/** The anchor props every "Book a demo" button uses.
 *
 * Written once because there are six of these buttons across two components,
 * and a link that opens in a new tab on five of them and not the sixth is the
 * kind of inconsistency nobody reports and everybody notices.
 *
 * A real scheduling link opens in a new tab — a buyer half-way down a pricing
 * page should not lose it — and says so in its accessible name, because a new
 * tab that opens unannounced is disorienting to a screen-reader user and to
 * anybody else. An unreplaced placeholder stays in this tab, where a broken
 * destination is noticed rather than left open behind the page.
 */
export function demoLinkProps(): {
  href: string;
  target?: "_blank";
  rel?: "noreferrer";
  "aria-label"?: string;
} {
  if (!DEMO_BOOKING_READY) return { href: DEMO_BOOKING_URL };
  return {
    href: DEMO_BOOKING_URL,
    target: "_blank",
    rel: "noreferrer",
    "aria-label": "Book a demo (opens in a new tab)",
  };
}
