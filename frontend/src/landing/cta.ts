/** Where "Book a demo" goes.
 *
 * PLACEHOLDER — `{{DEMO_BOOKING_URL}}` is not a URL. Replace it with the
 * founder's real scheduling link (Cal.com, Calendly, HubSpot meetings, or
 * whatever is chosen) before this page is deployed. It is rendered verbatim so
 * an unreplaced token is obvious in the browser's status bar and in the built
 * HTML rather than plausible; `scripts/prerender.mjs` also lists every `{{…}}`
 * token it finds at the end of a build, so a deploy cannot ship one quietly.
 *
 * One constant rather than a literal per button: the landing page has three
 * "Book a demo" buttons and each ERP page has its own, and a scheduling link
 * that is right in four places and stale in the fifth is the ordinary way this
 * goes wrong.
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
