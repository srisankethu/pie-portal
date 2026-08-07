// An imported entity, with where it came from. One component, every surface.
//
// The problem this exists for: a user can connect several companies, from the
// same ERP or from different ones, and "ABC Industries" can legitimately exist
// in three of them. A flat list of names cannot be acted on — somebody picks
// one and finds out later it was the wrong company's account.
//
// **Written once, deliberately.** A customer picker that names the company and
// an item picker that does not is exactly how the ambiguity comes back after
// being fixed. Every dropdown, grid cell, search result and detail heading that
// shows an imported record goes through this, so there is one answer to "how do
// we show where something is from" and one place to change it.
//
// **The name stays primary.** Source is secondary and immediately visible, not
// competing: a smaller line beneath, not a coloured row or a heavy chip. And it
// is never colour alone — the connector shows a mark *and* a word, because a
// colour-only badge is unreadable to a substantial minority of users and
// meaningless in a printed report.
//
// **Shown only when it distinguishes.** With one connected company every badge
// says the same thing, and a column of identical badges is decoration that
// costs width on every screen. The server sends `sources_differ`; below two
// companies this renders the name alone.

import type { EntityOrigin } from "./types";

export function connectorMark(origin: EntityOrigin | null | undefined): string {
  return origin?.icon || "◇";
}

/** The source line: company first, connector second.
 *
 *  That order is deliberate. Somebody working across three connected companies
 *  thinks in companies — "the Chennai book", "SLS" — and only needs the
 *  connector when two companies come from different systems and behave
 *  differently. Connector-first would put the less useful word in the position
 *  the eye lands on. */
export function sourceLabel(origin: EntityOrigin | null | undefined): string {
  if (!origin || origin.unknown) return "source not recorded";
  // A missing company is *named*, not dropped. Falling back to the connector
  // alone renders "Zoho" in the position a company belongs, which reads as an
  // answer — and "Zoho" is not an answer to "which of my three books is this
  // shelf in?". The row cannot be attributed until it carries a connection,
  // and the screen should say which of the two it is.
  const company = origin.company || "company not recorded";
  const parts = [company, origin.connector_short].filter(Boolean);
  return parts.join(" · ") || "source not recorded";
}

/** True when the row names its connector but not the company it belongs to.
 *
 *  Its own state because it has its own cause and its own fix: the record was
 *  written before per-connection provenance existed, and only a re-sync that
 *  claims it can attribute it. */
export function companyMissing(origin: EntityOrigin | null | undefined): boolean {
  return Boolean(origin) && !origin!.unknown && !origin!.company;
}

export function EntitySource({
  origin, show = true,
}: {
  origin: EntityOrigin | null | undefined;
  /** Usually the payload's `sources_differ`. False renders nothing at all. */
  show?: boolean;
}) {
  if (!show || !origin) return null;
  const incomplete = origin.unknown || companyMissing(origin);
  return (
    <span
      className={`ent-src${incomplete ? " unknown" : ""}`}
      title={companyMissing(origin)
        ? "This record was imported before its connected company was recorded, "
          + "so it cannot say which book it belongs to. A sync that claims it "
          + "will attribute it."
        : undefined}
    >
      <span className="ent-mark" aria-hidden="true">{connectorMark(origin)}</span>
      {sourceLabel(origin)}
    </span>
  );
}

/** An entity name with its source underneath. The default for grids, lists,
 *  search results and detail headings. */
export function EntityName({
  name, origin, show = true, sub, strong = true,
}: {
  name: string;
  origin?: EntityOrigin | null;
  show?: boolean;
  /** An extra secondary line — a SKU, a status — shown before the source. */
  sub?: React.ReactNode;
  strong?: boolean;
}) {
  return (
    <span className="ent">
      {/* Always classed, and always carrying the full name as a tooltip.
          `strong` used to decide whether the name got a class at all, which
          left the unstyled variant with nothing for the stylesheet to hold on
          to — so it could not be told to truncate, and a long one was cut
          mid-word with no ellipsis and no way to read the rest. Weight is a
          modifier; truncation belongs to every name. */}
      <span className={`ent-name${strong ? "" : " ent-name-plain"}`} title={name}>
        {name}
      </span>
      {(sub || (show && origin)) && (
        <span className="ent-meta">
          {sub}
          {sub && show && origin ? " · " : null}
          <EntitySource origin={origin} show={show} />
        </span>
      )}
    </span>
  );
}

/** One `<option>`'s text. A native select cannot hold two lines, so the source
 *  is appended in parentheses — the same information, degraded honestly, rather
 *  than a bare name that is ambiguous in exactly the case this is for. */
export function optionLabel(
  name: string, origin: EntityOrigin | null | undefined, show: boolean,
): string {
  return show && origin ? `${name} — ${sourceLabel(origin)}` : name;
}

/** Rank search matches so the least ambiguous answer comes first.
 *
 *  Three tiers, and nothing cleverer: an exact name beats a prefix beats a
 *  substring. Within a tier, an entity from the company the user is already
 *  working in comes first — that is the one they meant far more often than
 *  not, and it costs nothing to be right about it. */
export function rankMatches<T extends { name: string; origin?: EntityOrigin | null }>(
  rows: T[], query: string, preferConnectionId?: string | null,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  const tier = (r: T): number => {
    const n = r.name.toLowerCase();
    if (n === q) return 0;
    if (n.startsWith(q)) return 1;
    return n.includes(q) ? 2 : 3;
  };
  return rows
    .map((r, i) => ({ r, i, t: tier(r) }))
    .filter((x) => x.t < 3)
    .sort((a, b) =>
      a.t - b.t
      || Number(b.r.origin?.connection_id === preferConnectionId)
       - Number(a.r.origin?.connection_id === preferConnectionId)
      // Stable within a tier: a list that reorders itself between identical
      // searches is one nobody trusts.
      || a.i - b.i)
    .map((x) => x.r);
}
