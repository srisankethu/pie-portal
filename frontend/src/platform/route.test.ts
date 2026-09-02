// URLs, in both directions.
//
// `route.ts` is the one place a screen name becomes a path and a path becomes a
// nav highlight, and its own comments name three ways that goes wrong: an id
// pasted into a template unencoded, a longer pattern shadowed by a shorter one,
// and a destination whose query string swallowed the path. None of the three
// fails loudly — each produces a link that goes somewhere plausible, which is
// the harder kind to notice.
//
// Written against the exported table rather than a list of paths copied here.
// A second copy of `PATH` in a test file is a second thing to keep in step,
// and it would agree with the first right up until somebody renames a screen.
import { describe, expect, it } from "vitest";

// The server's own source, inlined by Vite at transform time (`?raw`). Read
// rather than restated, because a copy of the destination list is a copy that
// stops matching the day somebody adds a tile.
import dailySource from "../../../backend/app/commercial/insight/daily.py?raw";

import type { Screen } from "./route";
import { LEGACY_ACCOUNTS, PATH, PATTERN, pathFor, screenAt, vizPath } from "./route";

describe("pathFor", () => {
  it("returns the plain path for a screen that carries no id", () => {
    expect(pathFor("home")).toBe(PATH.home);
    expect(pathFor("quotes")).toBe(PATH.quotes);
  });

  it("builds the id-carrying paths in the shape their patterns declare", () => {
    // The pattern and the link are the same shape stated once; this is the
    // assertion that they still are.
    expect(pathFor("detail", "sig_1")).toBe(PATTERN.detail.replace(":id", "sig_1"));
    expect(pathFor("customer", "cst_1")).toBe(PATTERN.account.replace(":id", "cst_1"));
    expect(pathFor("customerItem", "cst_1", "prd_2")).toBe(
      PATTERN.customerItem.replace(":id", "cst_1").replace(":itemId", "prd_2"));
    expect(pathFor("quotes", "q_1")).toBe(PATTERN.quote.replace(":id", "q_1"));
  });

  it("percent-encodes an id, because customer ids are not all url-safe", () => {
    // A Zoho id is opaque. The moment one arrives with a slash in it, an
    // unencoded template silently produces a different route that still
    // resolves to *something*.
    expect(pathFor("customer", "a/b")).toBe("/account/a%2Fb");
    expect(pathFor("customer", "a b&c")).toBe("/account/a%20b%26c");
    expect(pathFor("customerItem", "a/b", "c/d")).toBe("/account/a%2Fb/item/c%2Fd");
  });

  it("falls back to the screen's own path when the id it needs is missing", () => {
    // Half a route is worse than the list: `/decision/undefined` renders an
    // error, where the list renders the thing the reader was looking for.
    expect(pathFor("detail")).toBe(PATH.detail);
    expect(pathFor("customer")).toBe(PATH.customer);
    // customerItem needs both, and one is not enough.
    expect(pathFor("customerItem", "cst_1")).toBe(PATH.customerItem);
  });
});

describe("screenAt", () => {
  it("matches the longest parameterised pattern first", () => {
    // `/account/x/item/y` must not be read as `/account/:id` — the bug the
    // ordering in PARAMETERISED exists to prevent, and the one a Record
    // iteration order would reintroduce silently.
    expect(screenAt("/account/cst_1/item/prd_2")).toBe("customerItem");
    expect(screenAt("/account/cst_1")).toBe("customer");
    expect(screenAt("/decision/sig_1")).toBe("detail");
  });

  it("does not read an alias path as the screen it aliases", () => {
    // `/decisions` is the queue, not a decision. Both share a nav item, and the
    // reverse lookup has to answer with the one whose URL this actually is.
    expect(screenAt(PATH.list)).toBe("list");
    expect(screenAt(PATH.customer)).toBe("customer");
  });

  it("answers home for a path it does not recognise", () => {
    // The same place the catch-all route sends it — the nav highlight and the
    // rendered screen agreeing matters more than either being clever.
    expect(screenAt("/nothing-here")).toBe("home");
    expect(screenAt(LEGACY_ACCOUNTS)).toBe("home");
    expect(screenAt("")).toBe("home");
  });

  it("round-trips every screen that has a path of its own", () => {
    // The property, rather than a list: every non-aliased screen's path must
    // come back as that screen. A new screen added to PATH is covered the day
    // it lands.
    const aliases: Screen[] = ["detail", "customerItem"];
    for (const screen of Object.keys(PATH) as Screen[]) {
      if (aliases.includes(screen)) continue;
      expect(screenAt(pathFor(screen)), `${screen} did not round-trip`).toBe(screen);
    }
  });
});

describe("vizPath", () => {
  it("splits the query off before reading the path", () => {
    // The bug this is written against: `stock?item=abc` read whole matches no
    // screen and lands on home — a link that goes somewhere plausible instead
    // of where it said.
    expect(vizPath("stock?item=abc")).toBe(`${PATH.stock}?item=abc`);
    expect(vizPath("payments?due=30")).toBe(`${PATH.payments}?due=30`);
  });

  it("carries a customer id into the account path, query and all", () => {
    expect(vizPath("customer/cst_1")).toBe("/account/cst_1");
    expect(vizPath("customer/cst_1?tab=items")).toBe("/account/cst_1?tab=items");
  });

  it("sends a bare customer to the Customers screen, not to a missing account", () => {
    // No id means the picker, which is a screen in its own right now.
    expect(vizPath("customer")).toBe(PATH.customer);
  });

  it("maps the insight layer's own names onto real screens", () => {
    // These names come from the server, so the mapping is a contract with it —
    // the hyphenated ones especially, which are not the screen keys.
    expect(vizPath("lost-revenue")).toBe(PATH.lostRevenue);
    expect(vizPath("revenue-flow")).toBe(PATH.home);
    expect(vizPath("opportunities")).toBe(PATH.opportunities);
    expect(vizPath("cadence")).toBe(PATH.cadence);
  });

  it("lands an unrecognised destination on home rather than nowhere", () => {
    expect(vizPath("a-beat-nobody-added")).toBe(PATH.home);
    expect(vizPath("")).toBe(PATH.home);
    // …and still keeps the query, so the fallback is a working link.
    expect(vizPath("unknown?x=1")).toBe(`${PATH.home}?x=1`);
  });

  // The fallback above is what makes this test necessary. A destination the
  // server emits but the map has never heard of does not fail loudly — it
  // resolves to home, so the link renders, clicks, and goes nowhere. Three of
  // them shipped that way: `approvals`, `list`, and a plural `customers` typo,
  // each dead exactly on the days its tile had something in it.
  //
  // Read from the server's source rather than restated here, because a copy of
  // the list is a copy that stops matching.
  it("routes every destination the Daily tiles actually emit", () => {
    const daily = dailySource;

    const emitted = new Set<string>();
    for (const m of daily.matchAll(/route=["']([a-z-]+)["']/g)) emitted.add(m[1]);
    // The `spec` rows carry their destination positionally: (key, label, route, why).
    for (const m of daily.matchAll(/\(\s*"[a-z_]+",\s*"[^"]+",\s*"([a-z-]+)",/g)) emitted.add(m[1]);

    // A tripwire on the scrape itself, not a count of tiles: if the regexes
    // stop matching — a rename, a reformat — `emitted` empties and the real
    // assertion below passes over nothing, which would read as "all fine".
    // Seven distinct destinations exist today (daily.py is the only module in
    // insight/ that emits any); this fails if that scrape ever finds fewer.
    expect(emitted.size).toBeGreaterThanOrEqual(7);

    // `revenue-flow` is deliberately home: the flow chart lives on that screen.
    const deliberatelyHome = new Set(["revenue-flow"]);
    const dead = [...emitted]
      .filter((r) => !deliberatelyHome.has(r) && vizPath(r) === PATH.home);
    expect(dead).toEqual([]);
  });
});
