/** Where each screen lives, and how a screen becomes a link.
 *
 * This used to be a hand-rolled hash router: a `switch` that parsed
 * `location.hash` into a `{screen, id}` object, a `toHash` that built it back,
 * and an assignment to `window.location.hash` to move. It worked, and it cost
 * one thing that is hard to notice and impossible to work around — **nothing
 * was a link**. Every nav item was a `<button onClick>`, so ctrl-click,
 * middle-click and "open in new tab" did nothing at all, and a salesperson who
 * wanted the queue and one account side by side could not have both. Hovering
 * showed no destination either, because there was no `href` to show.
 *
 * React Router now owns matching and history; this file keeps the one thing
 * that is genuinely ours — **which URL each screen answers on** — so a path is
 * still written exactly once, and `<Route path=…>`, `<Link to=…>` and the nav
 * highlight all read it from here.
 *
 * Still hash URLs (`#/decisions`), deliberately. The API and the built bundle
 * are served by the same FastAPI app, and a browser-path router would need
 * every unknown path rewritten to `index.html` there — a deployment concern
 * traded for a cosmetically shorter URL. Hash routing also keeps every link
 * already sent to somebody working.
 */
import { matchPath } from "react-router-dom";

export type Screen =
  | "home" | "list" | "detail" | "customer" | "quotes" | "states" | "data"
  /** The visualization layer. `home` is the Storyboard; these are the screens
   *  its beats link out to, each answering one question in depth. */
  | "weather" | "opportunities" | "lostRevenue" | "journey" | "simulate"
  /** Patterns: position, mix and rhythm. Each is one screen serving two of the
   *  specified views, because the pairs differ only in which measure is on the
   *  vertical or which quantity is summed. */
  | "landscape" | "composition" | "cadence"
  /** The book itself: the shelf, the suppliers and the cash — the three things the book
   *  always knew and the platform did not read until it ingested them. */
  | "payments" | "payables" | "stock" | "supply"
  /** Who is actually close to this book, on both sides of it, over time. */
  | "bonds"
  /** Which lines of the business each customer takes, and which they do not. */
  | "mix"
  /** What the book leans on, at both ends: principals and customers. */
  | "dependency"
  /** Where each principal's number stands. */
  | "targets"
  /** Which line of the business each item belongs to. */
  | "catalogue"
  /** The negotiation desk: the one screen a salesperson uses to decide rather
   *  than to read. */
  | "negotiate"
  /** The approval queue, and organization settings (owner is super admin). */
  | "approvals" | "settings"
  /** Which connector records describe the same customer or item. */
  | "identity"
  /** One customer's relationship with one item — needs two ids, so it carries
   *  an extra `itemId` alongside the customer in `id`. */
  | "customerItem";

/** The parameterless URL for each screen.
 *
 * Two entries are *aliases* rather than addresses: a decision with no id is the
 * decision list, and a customer-item pair with no ids is the account picker.
 * That is what the old parser did too, and it is what a link that lost its id
 * should land on — the place the missing thing is chosen from.
 */
export const PATH: Record<Screen, string> = {
  home: "/",
  list: "/decisions",
  detail: "/decisions",            // alias — see above
  customer: "/customers",
  customerItem: "/customers",      // alias — see above
  quotes: "/quotes",
  states: "/states",
  data: "/data",
  approvals: "/approvals",
  settings: "/settings",
  identity: "/identity",
  weather: "/weather",
  opportunities: "/opportunities",
  lostRevenue: "/lost-revenue",
  journey: "/journey",
  simulate: "/simulate",
  landscape: "/landscape",
  composition: "/composition",
  cadence: "/cadence",
  payments: "/payments",
  payables: "/payables",
  stock: "/stock",
  supply: "/supply",
  bonds: "/bonds",
  mix: "/mix",
  dependency: "/dependency",
  targets: "/targets",
  catalogue: "/item-lines",
  negotiate: "/negotiate",
};

/** The three screens whose URL carries an id, as route patterns.
 *
 * `useParams` reads these back, and `pathFor` writes them — the pattern and the
 * link are the same shape stated once, which is the part a string-concatenating
 * router gets wrong first. */
export const PATTERN = {
  detail: "/decision/:id",
  account: "/account/:id",
  customerItem: "/account/:id/item/:itemId",
} as const;

/** The path that used to serve the account picker. Redirected rather than
 *  quietly aliased, so a link somebody saved lands on the current URL instead
 *  of showing the right screen under a name the product no longer uses. */
export const LEGACY_ACCOUNTS = "/accounts";

/** Longest first: `/account/x/item/y` must not be read as `/account/:id`. */
const PARAMETERISED: readonly (readonly [string, Screen])[] = [
  [PATTERN.customerItem, "customerItem"],
  [PATTERN.account, "customer"],
  [PATTERN.detail, "detail"],
] as const;

/** Screens whose `PATH` entry is an alias, so they must never be found by a
 *  reverse lookup — `/decisions` is the list, not a decision. */
const ALIASED: readonly Screen[] = ["detail", "customerItem"] as const;

/** A screen, and optionally what it is about, as a URL. */
export function pathFor(screen: Screen, id?: string, itemId?: string): string {
  const enc = encodeURIComponent;
  if (screen === "detail" && id) return `/decision/${enc(id)}`;
  if (screen === "customerItem" && id && itemId) {
    return `/account/${enc(id)}/item/${enc(itemId)}`;
  }
  if (screen === "customer" && id) return `/account/${enc(id)}`;
  return PATH[screen];
}

/** Which screen a URL is showing — the nav highlight, and nothing else.
 *
 * React Router decides what renders; this decides what looks current, which is
 * a separate question because two screens share a nav item (a decision belongs
 * to the queue it was opened from). An unrecognised path reports `home`, the
 * same place the catch-all route sends it. */
export function screenAt(pathname: string): Screen {
  for (const [pattern, screen] of PARAMETERISED) {
    if (matchPath(pattern, pathname)) return screen;
  }
  for (const screen of Object.keys(PATH) as Screen[]) {
    if (!ALIASED.includes(screen) && PATH[screen] === pathname) return screen;
  }
  return "home";
}

/** Resolve a destination named by the insight layer onto a URL.
 *
 * The server names a destination for every storyboard beat and every weather
 * front — `lost-revenue`, `opportunities`, `customer/<id>` — and this is the
 * single place those names become navigation. One table rather than a
 * conditional per call site, so adding a beat means adding a row here and
 * nothing else. A name nobody recognises lands on the storyboard rather than
 * nowhere. */
export function vizPath(route: string): string {
  // The query is split off before the path is read, not after. A token like
  // `stock?item=abc` otherwise makes the whole string the head, matches no
  // screen, and lands on the home page — a link that goes somewhere plausible
  // instead of nowhere, which is the harder kind to notice.
  const q = route.indexOf("?");
  const search = q === -1 ? "" : route.slice(q);
  const [head, id] = (q === -1 ? route : route.slice(0, q)).split("/");
  if (head === "customer" && id) return pathFor("customer", id) + search;
  const map: Record<string, Screen> = {
    "lost-revenue": "lostRevenue",
    opportunities: "opportunities",
    journey: "journey",
    weather: "weather",
    "revenue-flow": "home",
    data: "data",
    simulate: "simulate",
    landscape: "landscape",
    composition: "composition",
    cadence: "cadence",
    payments: "payments",
    payables: "payables",
    stock: "stock",
    supply: "supply",
    negotiate: "negotiate",
    // Bare `customer` — no id — is the Customers screen with its own picker.
    // It routes here now that Customers is a nav destination in its own right
    // rather than only ever a link carrying an account.
    customer: "customer",
  };
  return pathFor(map[head] ?? "home") + search;
}
