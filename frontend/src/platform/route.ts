/** Minimal hash router.
 *
 * The app previously held the current screen in component state only, so the
 * URL never changed: the browser Back button left the application entirely,
 * a reload always returned to the home screen, and a decision could not be
 * linked to a colleague. This maps the screen onto `location.hash`, which
 * gives back/forward, reload-in-place and shareable links without pulling in
 * a routing dependency.
 */
export type Screen = "home" | "list" | "detail" | "customer" | "quotes" | "states" | "data";

export interface Route {
  screen: Screen;
  id?: string;
}

const PATHS: Record<Screen, string> = {
  home: "/",
  list: "/decisions",
  detail: "/decision",
  customer: "/accounts",
  quotes: "/quotes",
  states: "/states",
  data: "/data",
};

export function toHash(r: Route): string {
  if (r.screen === "detail" && r.id) return `#/decision/${encodeURIComponent(r.id)}`;
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
    case "accounts":
      return { screen: "customer" };
    case "account":
      return parts[1]
        ? { screen: "customer", id: decodeURIComponent(parts[1]) }
        : { screen: "customer" };
    case "quotes":
      return { screen: "quotes" };
    case "states":
      return { screen: "states" };
    case "data":
      return { screen: "data" };
    default:
      return { screen: "home" };
  }
}

export function navigate(r: Route): void {
  const next = toHash(r);
  if (window.location.hash !== next) window.location.hash = next;
}
