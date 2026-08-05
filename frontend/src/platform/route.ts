/** Minimal hash router.
 *
 * The app previously held the current screen in component state only, so the
 * URL never changed: the browser Back button left the application entirely,
 * a reload always returned to the home screen, and a decision could not be
 * linked to a colleague. This maps the screen onto `location.hash`, which
 * gives back/forward, reload-in-place and shareable links without pulling in
 * a routing dependency.
 */
export type Screen =
  | "home" | "list" | "detail" | "customer" | "quotes" | "states" | "data"
  /** The visualization layer. `home` is the Storyboard; these are the screens
   *  its beats link out to, each answering one question in depth. */
  | "weather" | "opportunities" | "lostRevenue" | "journey" | "simulate"
  /** Tier 2: position, mix and rhythm. Each is one screen serving two of the
   *  specified views, because the pairs differ only in which measure is on the
   *  vertical or which quantity is summed. */
  | "landscape" | "composition" | "cadence"
  /** Tier 3: the shelf, the suppliers and the cash — the three things the book
   *  always knew and the platform did not read until it ingested them. */
  | "payments" | "stock" | "supply"
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

export interface Route {
  screen: Screen;
  id?: string;
  itemId?: string;
}

const PATHS: Record<Screen, string> = {
  home: "/",
  list: "/decisions",
  detail: "/decision",
  // Renamed with the screen: "Accounts" and "Customers" were two nav items for
  // one thing, and the surviving name is Customers. `/accounts` still parses,
  // so links already sent to somebody keep working.
  customer: "/customers",
  customerItem: "/customers",
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
  stock: "/stock",
  supply: "/supply",
  negotiate: "/negotiate",
};

export function toHash(r: Route): string {
  if (r.screen === "detail" && r.id) return `#/decision/${encodeURIComponent(r.id)}`;
  if (r.screen === "customerItem" && r.id && r.itemId) {
    return `#/account/${encodeURIComponent(r.id)}/item/${encodeURIComponent(r.itemId)}`;
  }
  if (r.screen === "customer" && r.id) return `#/account/${encodeURIComponent(r.id)}`;
  return `#${PATHS[r.screen]}`;
}

export function parseHash(hash: string): Route {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const parts = raw.split("/").filter(Boolean);
  if (parts.length === 0) return { screen: "home" };
  switch (parts[0]) {
    case "decisions":
      return { screen: "list" };
    case "decision":
      return parts[1] ? { screen: "detail", id: decodeURIComponent(parts[1]) } : { screen: "list" };
    case "customers":
    case "accounts":            // the old path — kept so existing links resolve
      return { screen: "customer" };
    case "account":
      if (!parts[1]) return { screen: "customer" };
      // /account/<customer>/item/<product>
      if (parts[2] === "item" && parts[3]) {
        return {
          screen: "customerItem",
          id: decodeURIComponent(parts[1]),
          itemId: decodeURIComponent(parts[3]),
        };
      }
      return { screen: "customer", id: decodeURIComponent(parts[1]) };
    case "quotes":
      return { screen: "quotes" };
    case "states":
      return { screen: "states" };
    case "data":
      return { screen: "data" };
    case "approvals":
      return { screen: "approvals" };
    case "settings":
      return { screen: "settings" };
    case "identity":
      return { screen: "identity" };
    case "weather":
      return { screen: "weather" };
    case "opportunities":
      return { screen: "opportunities" };
    case "lost-revenue":
      return { screen: "lostRevenue" };
    case "journey":
      return { screen: "journey" };
    case "simulate":
      return { screen: "simulate" };
    case "landscape":
      return { screen: "landscape" };
    case "composition":
      return { screen: "composition" };
    case "cadence":
      return { screen: "cadence" };
    case "payments":
      return { screen: "payments" };
    case "stock":
      return { screen: "stock" };
    case "supply":
      return { screen: "supply" };
    case "negotiate":
      return { screen: "negotiate" };
    default:
      return { screen: "home" };
  }
}

export function navigate(r: Route): void {
  const next = toHash(r);
  if (window.location.hash !== next) window.location.hash = next;
}
