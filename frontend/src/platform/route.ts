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
  /** The cycle the two payment screens sit inside: order → invoice → payment,
   *  reported by stage so a delay can be attributed rather than argued about. */
  | "orderToCash"
  /** What each line on that shelf returns on the cash it ties up. Its own
   *  screen rather than a column on Stock, because it is the only view here
   *  whose window is set by how long the platform has been writing stock
   *  readings down rather than by what the reader asks for. */
  | "gmroi"
  /** How long a rupee is tied up, per legal entity — the composite Zoho
   *  reports one period of, for one company, and never as a trend. */
  | "cashCycle"
  /** Deadlines the tax code sets: the MSME payment cliff, whose status is still
   *  unknown, and the 194Q threshold. Dates and amounts, never advice. */
  | "statutory"
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
  /** The decoded nomenclature catalogue resolution runs against: whether one
   *  exists, which pack and ruleset checksum built it, and the control that
   *  rebuilds it. Distinct from `catalogue` above and deliberately not named
   *  alike — that screen assigns an item to a business line, this one is the
   *  pie-parser decode of the manufacturer's own part numbers. */
  | "decodedCatalog"
  /** Which quotes were won, which were lost, and why — the outcome half of the
   *  quoting loop, which the platform recorded and never read. */
  | "quoteOutcomes"
  /** The other three quarters of that loop: the quotes the ERP holds no
   *  outcome for at all, and the one screen on which a person can say why one
   *  was lost. Its own address rather than a panel on `quoteOutcomes`, because
   *  a win rate is a summary over answered quotes and this is a worklist over
   *  unanswered ones. */
  | "unrecordedQuotes"
  /** What the platform itself changed: the value ledger, its evidence gaps and
   *  — for the owner — the 30-day report against the pre-trial baseline. */
  | "attribution"
  /** The other half of that pair, and the earlier one: what the book already
   *  held when it arrived, and how much of it could be judged at all. */
  | "retrospective"
  /** PIE's own pricing model — what to charge, and why. Not a tenant screen at
   *  all: the endpoints behind it sit on an allowlist outside every workspace,
   *  and the nav item exists only for an identity the server has confirmed. */
  | "monetization"
  /** The platform's own vitals rather than the book's: component health,
   *  capacity headroom, and whether the last sync finished. Manager and owner,
   *  mirroring `require_manager_or_owner` on every `/internal/observability/*`
   *  route — a stalled sync is the first thing a desk asks about when the
   *  numbers stop moving, and it is a question about the platform. */
  | "observability"
  /** The approval queue, and organization settings (owner is super admin). */
  | "approvals" | "settings"
  /** Which connector records describe the same customer or item. */
  | "identity"
  /** What leaves for a model, who has opened this tenant, and the two
   *  irreversible things an owner can do with their own data. Owner only. */
  | "trust"
  /** One customer's relationship with one item — needs two ids, so it carries
   *  an extra `itemId` alongside the customer in `id`. */
  | "customerItem"
  /** The analysis screens, indexed by the question each one answers.
   *
   *  They used to be twelve nav items in two groups, which made a library of
   *  evidence look like twelve places to start work — and nobody starts a
   *  Tuesday on Bonds. The screens are unchanged and keep their own addresses;
   *  this is the one door to them, reached from a decision that made somebody
   *  want the pattern behind it. */
  | "evidence"
  /** What moved in the book, and what the queue was detected against. This was
   *  the home screen — the briefing above the queue — and it is an answer to a
   *  question rather than a place to start a day, so it has an address of its
   *  own and a card in the library. */
  | "morningRead";

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
  observability: "/system-health",
  trust: "/trust",
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
  orderToCash: "/order-to-cash",
  cashCycle: "/cash-cycle",
  statutory: "/statutory",
  stock: "/stock",
  gmroi: "/gmroi",
  supply: "/supply",
  bonds: "/bonds",
  mix: "/mix",
  dependency: "/dependency",
  targets: "/targets",
  catalogue: "/item-lines",
  decodedCatalog: "/decoded-catalogue",
  quoteOutcomes: "/quote-outcomes",
  unrecordedQuotes: "/unanswered-quotes",
  attribution: "/what-pie-changed",
  retrospective: "/what-your-books-hold",
  monetization: "/pricing-model",
  evidence: "/evidence",
  morningRead: "/morning-read",
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
  /** One draft in the quote workspace — the Quote Builder open on it. Bare
   *  `/quotes` is the workspace itself: every draft, and the way to start one. */
  quote: "/quotes/:id",
} as const;

/** Screens whose content is a wide table rather than something to read.
 *
 * The shell caps content at 1180px, which is a reading measure and right for
 * almost everything here — a paragraph 1600px wide is worse, not better.
 * The Quote Builder is not that: its content is a grid whose columns are set
 * by what a quote line has to say, and the cap was silently deciding which of
 * them a salesperson never sees.
 *
 * Measured before this existed: the grid stopped growing at **1137px** at
 * every viewport from 1280 to 1920, while its columns needed **1246px** — so
 * `Line total`, `Avail.` and `Short.` had thresholds above the ceiling and had
 * never rendered on any screen, and `Recommended` had nowhere to go.
 *
 * Stated here rather than as a prop, because "is this screen a table" is a
 * fact about the route, and this file is where route facts live. Both the
 * workspace and an open draft are `quotes` — `/quotes/:id` resolves to the
 * same screen, in PARAMETERISED below — so naming the screen once covers the
 * builder too.
 */
export const WIDE_SCREENS: ReadonlySet<Screen> = new Set<Screen>(["quotes"]);

/** The path that used to serve the account picker. Redirected rather than
 *  quietly aliased, so a link somebody saved lands on the current URL instead
 *  of showing the right screen under a name the product no longer uses. */
export const LEGACY_ACCOUNTS = "/accounts";

/** Longest first: `/account/x/item/y` must not be read as `/account/:id`. */
const PARAMETERISED: readonly (readonly [string, Screen])[] = [
  [PATTERN.customerItem, "customerItem"],
  [PATTERN.account, "customer"],
  [PATTERN.detail, "detail"],
  [PATTERN.quote, "quotes"],
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
  if (screen === "quotes" && id) return `/quotes/${enc(id)}`;
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
    "order-to-cash": "orderToCash",
    "cash-cycle": "cashCycle",
    statutory: "statutory",
    stock: "stock",
    gmroi: "gmroi",
    supply: "supply",
    "quote-outcomes": "quoteOutcomes",
    "unanswered-quotes": "unrecordedQuotes",
    // The two names the *needs-you* tiles carry. They were missing, and the
    // fallback below sends an unknown name to home — so "Approvals waiting"
    // and "Decisions in the queue" navigated to the screen the reader was
    // already standing on, and only ever on the days those tiles had work in
    // them, which is when the link renders at all.
    approvals: "approvals",
    list: "list",
    // Bare `customer` — no id — is the Customers screen with its own picker.
    // It routes here now that Customers is a nav destination in its own right
    // rather than only ever a link carrying an account.
    customer: "customer",
  };
  return pathFor(map[head] ?? "home") + search;
}
