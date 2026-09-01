/** Where "Book a demo" goes, and what the button does while nobody has said.
 *
 * PLACEHOLDER — `{{DEMO_BOOKING_URL}}` is not a URL. Replace it with the real
 * scheduling link (Cal.com, Calendly, HubSpot meetings, whatever is chosen).
 * `scripts/prerender.mjs` lists every `{{…}}` token left in the built pages at
 * the end of a build, `prerender.test.tsx` refuses any token that is not on a
 * declared list, and `docs/marketing-placeholders.md` says what has to replace
 * each one — so this cannot be forgotten quietly.
 *
 * What it may *not* do is reach a visitor. The token used to be rendered
 * verbatim into `href`, on the theory that a visibly broken link is the
 * loudest possible reminder. That is true of the person maintaining the page
 * and false of everybody else: it puts a dead destination on the primary
 * action of every public page, and the reminder is already carried three other
 * ways that no visitor has to pay for.
 *
 * So the link degrades instead. Until the URL is set, each button falls back
 * to what its caller says the honest alternative is — the trial door — under a
 * label that describes *that*, not a meeting nobody can book yet. A page
 * deployed today asks for something a visitor can actually do.
 *
 * One module rather than a literal per button: there are four of these across
 * two components — the landing's hero and closing block, and each ERP page's
 * hero and closing block — and a scheduling link that is right in three places
 * and stale in the fourth is the ordinary way this goes wrong.
 *
 * The two that used to sit on the paid panels are gone, and not because the
 * fallback was wrong. The panels state no price now and the plans section ends
 * in a form that reaches the same person a meeting would, so a "Book a demo"
 * button beside it would be a second door to one room — and the one a visitor
 * cannot use while the link is unset.
 *
 * Not to be confused with the *other* demo on this page. `onDemo` opens a
 * read-only workspace of sample data inside the product ("See it on sample
 * data") and needs no scheduling; this books time with a person, which is how
 * a purchase at this size actually starts. Both are called a demo by the
 * people who ask for them, so the labels stay different in the markup.
 */

export const DEMO_BOOKING_URL = "{{DEMO_BOOKING_URL}}";

/** Whether that link has been filled in. */
export const DEMO_BOOKING_READY = !DEMO_BOOKING_URL.startsWith("{{");

/** What a caller offers instead, while there is nothing to book.
 *
 *  Both fields are required, and the label is the reason why: a button that
 *  still says "Book a demo" while pointing at a sign-in form is a smaller lie
 *  than a dead link but it is still a lie, and this page cannot afford either.
 */
export interface DemoFallback {
  href: string;
  label: string;
  onClick?: (e: React.MouseEvent) => void;
}

export interface DemoCta {
  /** True only when a real scheduling link is configured. Callers use it to
   *  drop a second button that would otherwise duplicate the fallback. */
  ready: boolean;
  label: string;
  props: {
    href: string;
    onClick?: (e: React.MouseEvent) => void;
    target?: "_blank";
    rel?: "noreferrer";
    "aria-label"?: string;
  };
}

/** The pure half, so both branches are reachable from a test without mocking
 *  a module constant. `demoCta` below is this applied to the constant. */
export function demoCtaFor(url: string, fallback: DemoFallback): DemoCta {
  if (url.startsWith("{{")) {
    return {
      ready: false,
      label: fallback.label,
      props: { href: fallback.href, onClick: fallback.onClick },
    };
  }
  // A real scheduling link opens in a new tab — a buyer half-way down the
  // page should not lose it — and says so in its accessible name,
  // because a new tab that opens unannounced is disorienting to a
  // screen-reader user and to anybody else.
  return {
    ready: true,
    label: "Book a demo",
    props: {
      href: url,
      target: "_blank",
      rel: "noreferrer",
      "aria-label": "Book a demo (opens in a new tab)",
    },
  };
}

export function demoCta(fallback: DemoFallback): DemoCta {
  return demoCtaFor(DEMO_BOOKING_URL, fallback);
}
