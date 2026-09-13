/** The information architecture: five places, and where everything else lives.
 *
 * The nav carried thirty-four items in four groups — `Decide`, `Understand`,
 * `The book`, `Setup` — and the groups were honest about the shape: seven ways
 * to act against twenty to read. The cost was not the length. It was that the
 * list was organised around *what the data is* (Weather, Landscape, Bonds, Mix
 * shift, Cadence, GMROI, Order to cash) rather than around what somebody came
 * here to do, so "why did revenue fall" required already knowing it lives under
 * Lost revenue and not under Weather. The product computed exactly what was
 * wrong and then made the reader go and find it.
 *
 * Five destinations now, and none of them is a data noun:
 *
 *   Today     what needs a person, ranked — approvals, decisions, chases and
 *             the outcome questions, as one queue rather than four screens
 *   Quotes    the workspace and the builder
 *   Accounts  the counterparties, both sides — who buys from us and who we buy
 *             from, and the two views of how the book leans on them
 *   Money     what is owed, what we owe, the cycle, and the statutory dates
 *   Setup     where the figures come from and the policy the rest obeys
 *
 * **Nothing was deleted.** Every screen that had a nav item still has its
 * address, and this file is the map from the address to the door it is now
 * behind: the analysis screens are the Evidence library, the book screens are
 * Money tabs, the setup screens are Setup tabs, and the three queues Today
 * absorbs keep their own pages for the reader who wants the whole pile.
 *
 * One table rather than a conditional per surface: the shell, the tab strips,
 * the command palette and the Evidence index all read from here, so moving a
 * screen is one edit and cannot leave a surface behind. `route.ts` still owns
 * *which URL each screen answers on* — this owns which door it is behind.
 */
import type { AppAbility, Subject } from "./ability";
import type { Screen } from "./route";

/** The five. `today` is `home` under the name the product uses for it. */
export type Destination = "today" | "quotes" | "accounts" | "money" | "setup";

export interface DestinationSpec {
  key: Destination;
  label: string;
  /** Where the nav item points. */
  screen: Screen;
  /** Screens that make this destination current — the tabs behind it, the
   *  detail pages under it, and the queues it absorbed. A reader who followed a
   *  link into a decision should still see which of the five they are in. */
  covers: readonly Screen[];
}

/** A tab inside a destination: one existing screen, named for what it answers.
 *
 *  `need` is the ability that must hold, matching the gate on the endpoints the
 *  screen calls — a tab that always 403s teaches people the product is broken,
 *  which is the same argument the old nav table made item by item. */
export interface DestinationTab {
  label: string;
  screen: Screen;
  need?: Subject;
  /** True for the tabs that live in the strip's overflow. The four primary tabs
   *  are the ones a working day uses; the rest are real screens somebody needs
   *  twice a year, and putting all nine in one strip would rebuild the problem
   *  this file exists to fix. */
  secondary?: boolean;
  /** PIE's own screens, offered to PIE. Not an ability: `ability` reasons about
   *  what somebody may do inside a workspace, and being PIE is not a fact any
   *  workspace holds — the server answers it. */
  operatorOnly?: boolean;
}

export const DESTINATIONS: readonly DestinationSpec[] = [
  {
    key: "today", label: "Today", screen: "home",
    // `list`, `approvals` and `unrecordedQuotes` are the three queues Today now
    // holds. They keep their addresses for the reader who wants the whole pile
    // rather than this morning's head of it, and they belong to Today when
    // opened.
    covers: ["home", "list", "detail", "approvals", "unrecordedQuotes", "evidence",
             "weather", "opportunities", "lostRevenue", "landscape", "composition",
             "cadence", "mix", "targets", "gmroi", "stock", "attribution",
             "retrospective", "simulate", "journey", "morningRead"],
  },
  {
    key: "quotes", label: "Quotes", screen: "quotes",
    covers: ["quotes", "quoteOutcomes"],
  },
  {
    // Both sides of the book. `supply`, `bonds` and `dependency` were Today's
    // until they were tabs here — a screen belongs to one of the five, and
    // `destinationFor` takes the first that claims it, so listing a screen in
    // two places would silently leave it in whichever comes first in this
    // array rather than in the destination whose strip it appears in.
    key: "accounts", label: "Accounts", screen: "customer",
    covers: ["customer", "customerItem", "supply", "bonds", "dependency"],
  },
  {
    key: "money", label: "Money", screen: "payments",
    covers: ["payments", "payables", "orderToCash", "cashCycle", "statutory"],
  },
  {
    key: "setup", label: "Setup", screen: "data",
    covers: ["data", "settings", "catalogue", "decodedCatalog", "groups",
             "identity", "observability", "states", "trust", "monetization"],
  },
] as const;

/** Accounts: the counterparties, then what the book's position in them is.
 *
 *  This destination held one tab's worth of screen — Customers — while the
 *  other side of the same book sat in the Evidence library under `Suppliers`,
 *  and the two views that read *both* sides at once sat beside it. A
 *  distributor is exposed in two directions; a place called Accounts that
 *  knows about one of them is answering half the question and not saying so.
 *
 *  Who first, then how the book leans on them: Customers and Vendors are
 *  directories somebody looks a name up in, Relationships and Dependencies are
 *  the whole book read across those names. Vendors needs `supply` for the
 *  reason `We owe` does — the endpoint behind it is gated the same way, and a
 *  tab that always 403s is worse than no tab. `bonds` and `dependency` carry no
 *  `need`: the server drops the supplier half of each for a salesperson and
 *  answers the customer half, so both are real screens for every role. */
export const ACCOUNT_TABS: readonly DestinationTab[] = [
  { label: "Customers", screen: "customer" },
  { label: "Vendors", screen: "supply", need: "supply" },
  { label: "Relationships", screen: "bonds" },
  { label: "Dependencies", screen: "dependency" },
] as const;

/** Money: the same position across three companies, in the order a question
 *  about cash arrives in. Owed to us is first because it is the half somebody
 *  can act on today. */
export const MONEY_TABS: readonly DestinationTab[] = [
  { label: "Owed to us", screen: "payments" },
  { label: "Order to cash", screen: "orderToCash" },
  { label: "We owe", screen: "payables", need: "supply" },
  { label: "Cash cycle", screen: "cashCycle", need: "supply" },
  { label: "Statutory dates", screen: "statutory", need: "supply" },
] as const;

/** Setup: where the figures come from, then the policy, then the two
 *  irreversible things. The overflow holds the screens a desk opens when
 *  something specific is wrong. */
export const SETUP_TABS: readonly DestinationTab[] = [
  { label: "Connections", screen: "data" },
  { label: "Policy & people", screen: "settings" },
  { label: "Catalogue", screen: "decodedCatalog" },
  { label: "Your data", screen: "trust", need: "trust" },
  // Primary rather than secondary, and no `need`: reading groups is open to
  // every role — the picker on a directory is what a salesperson uses them
  // through — and a tab in the overflow is a tab nobody finds the first time.
  // Drawing one is manager and above, which the screen asks the server about
  // rather than reconstructing here.
  { label: "Groups", screen: "groups" },
  { label: "Item lines", screen: "catalogue", need: "supply", secondary: true },
  { label: "Identities", screen: "identity", need: "policy", secondary: true },
  { label: "System health", screen: "observability", need: "policy", secondary: true },
  { label: "AI states", screen: "states", secondary: true },
  { label: "Pricing model", screen: "monetization", secondary: true, operatorOnly: true },
] as const;

/** One analysis screen, and the question it answers.
 *
 *  The question is the index, not the screen's name: `Relationships` says
 *  little to somebody who has not already read it — it was called `Bonds`, which
 *  said nothing at all — and "Who is closest to this business?" is why they
 *  would open it. Written here rather than fetched — a figure on each card
 *  would be eighteen requests to render an index nobody is deciding from, and a
 *  stale figure on a card is worse than none.
 */
export interface EvidenceEntry {
  name: string;
  question: string;
  screen: Screen;
  need?: Subject;
}

/** Three of these cards open a screen that is also a tab somewhere else —
 *  `Vendors`, `Relationships` and `Dependencies`, all under Accounts. That is
 *  deliberate and not a duplicate entry: a tab is where you go when you
 *  already know you want the vendor side, and a card here is where you land
 *  when what you have is the question. The screen is one screen at one address
 *  either way.
 *
 *  This said four, and counted `Won & lost` under Quotes. Quotes has no tab
 *  strip — `DestinationLayout` is instantiated three times, for Accounts,
 *  Money and Setup — so that card is `quoteOutcomes`'s only door, not its
 *  second one. The name-agreement test below repeated the same count and could
 *  not catch it: it walks the strips, so a screen that is a card and nothing
 *  else never enters the loop. Corrected rather than deleted, because the
 *  count is what a reader checks this paragraph against. */
export const EVIDENCE: readonly EvidenceEntry[] = [
  { name: "The morning read", question: "What moved in the book, and why?",
    screen: "morningRead" },
  { name: "Lost revenue", question: "Where did the revenue go?",
    screen: "lostRevenue", need: "economics" },
  // Directly under Lost revenue, because it is the half of that question a
  // salesperson may read. `/lost-revenue` is `require_manager_or_owner` and
  // `/insight/journey` is not, which `story.py` already knows: the
  // lost-revenue beat swaps its call-to-action to `journey` for exactly the
  // role that cannot open the other one. That made this the screen the
  // storyboard sends a salesperson to and the only screen with an address
  // that no nav item, tab, card or ⌘K row led back to — reachable once, from
  // the beat, and never again. No `need` for the same reason the endpoint has
  // no gate: it is customer counts and order dates, and nothing in it is
  // derived from cost.
  { name: "Customer journey",
    question: "Is the customer base growing, holding, or turning over?",
    screen: "journey" },
  { name: "Rhythm", question: "Which accounts have broken their own pattern?",
    screen: "cadence" },
  { name: "Landscape", question: "Which accounts are big and thin at the same time?",
    screen: "landscape", need: "economics" },
  { name: "Mix shift", question: "Why is margin down when revenue is up?",
    screen: "composition" },
  { name: "Weather", question: "What is the state of the book in one look?",
    screen: "weather", need: "economics" },
  { name: "Opportunities", question: "What does the book support doing?",
    screen: "opportunities", need: "economics" },
  { name: "Dependencies", question: "What does the book lean on?",
    screen: "dependency" },
  { name: "Relationships", question: "Who is closest to this business?",
    screen: "bonds" },
  { name: "Product mix", question: "Which lines does an account not take?",
    screen: "mix" },
  { name: "Stock", question: "What is actually on the shelf?",
    screen: "stock" },
  { name: "Return on stock", question: "What is the cash on the shelf earning?",
    screen: "gmroi", need: "economics" },
  { name: "Vendors", question: "Who do we buy from, and on what terms?",
    screen: "supply", need: "supply" },
  { name: "Vendor targets", question: "How far off is each principal's number?",
    screen: "targets", need: "supply" },
  { name: "Won & lost", question: "Which quotes were won, and why were the rest lost?",
    screen: "quoteOutcomes" },
  { name: "What your books hold",
    question: "What did the book already know when PIE arrived?",
    screen: "retrospective", need: "economics" },
  { name: "What PIE changed", question: "Has any of this been worth it?",
    screen: "attribution", need: "economics" },
  { name: "Simulator", question: "What would a different floor have done?",
    screen: "simulate", need: "simulation" },
] as const;

/** Which of the five a screen is in. `today` is the fallback for the same
 *  reason `screenAt` falls back to `home`: an address nobody claims is shown in
 *  the place the catch-all route sends it. */
export function destinationFor(screen: Screen): Destination {
  return DESTINATIONS.find((d) => d.covers.includes(screen))?.key ?? "today";
}

/** The tabs of a group this reader may actually open. */
export function visibleTabs(
  tabs: readonly DestinationTab[], ability: AppAbility, isOperator = false,
): DestinationTab[] {
  return tabs.filter((t) => (!t.need || ability.can("read", t.need))
                         && (!t.operatorOnly || isOperator));
}

/** The evidence this reader may actually open. A card that 403s is worse here
 *  than in a nav: the index promises the question is answerable. */
export function visibleEvidence(ability: AppAbility): EvidenceEntry[] {
  return EVIDENCE.filter((e) => !e.need || ability.can("read", e.need));
}
