// Which company a record belongs to, and which match the reader meant.
//
// The problem these helpers exist for: with several connected companies,
// "ABC Industries" can legitimately exist in three of them, and a flat list of
// names cannot be acted on — somebody picks one and finds out later it was the
// wrong company's account.
//
// The tests worth having are therefore about the two ways that comes back.
// `sourceLabel` has to distinguish "we do not know where this came from" from
// "we know the connector but not the company", because those have different
// causes and different fixes and only one of them is repaired by a re-sync.
// `rankMatches` has to put the least ambiguous answer first, and — the part a
// scoring function usually gets wrong — keep the order stable between two
// identical searches.
import { describe, expect, it } from "vitest";

import {
  companyMissing, connectorMark, optionLabel, rankMatches, sourceLabel,
} from "./EntityName";
import type { EntityOrigin } from "./types";

function origin(over: Partial<EntityOrigin> = {}): EntityOrigin {
  return {
    connector: "zoho",
    connector_label: "Zoho Books",
    connector_short: "Zoho",
    icon: "◈",
    connection_id: "conn_1",
    company: "SLS Engineers",
    external_id: "z1",
    unknown: false,
    ...over,
  };
}

describe("sourceLabel", () => {
  it("puts the company first, because that is what people think in", () => {
    expect(sourceLabel(origin())).toBe("SLS Engineers · Zoho");
  });

  it("says the source is not recorded when nothing is known", () => {
    expect(sourceLabel(null)).toBe("source not recorded");
    expect(sourceLabel(undefined)).toBe("source not recorded");
    expect(sourceLabel(origin({ unknown: true }))).toBe("source not recorded");
  });

  it("names a missing company rather than falling back to the connector", () => {
    // "Zoho" alone in the position a company belongs reads as an answer, and it
    // is not an answer to "which of my three books is this in?". The two states
    // have different causes, so they must not render identically.
    const label = sourceLabel(origin({ company: "" }));
    expect(label).toBe("company not recorded · Zoho");
    expect(label).not.toBe("Zoho");
    expect(label).not.toBe(sourceLabel(origin({ unknown: true })));
  });
});

describe("companyMissing", () => {
  it("is true only for a row that names its connector but not its company", () => {
    // Its own state because it has its own fix: only a re-sync that can
    // attribute the row will repair it.
    expect(companyMissing(origin({ company: "" }))).toBe(true);
    expect(companyMissing(origin())).toBe(false);
    // Nothing recorded at all is a different problem, not this one.
    expect(companyMissing(origin({ unknown: true, company: "" }))).toBe(false);
    expect(companyMissing(null)).toBe(false);
  });
});

describe("connectorMark", () => {
  it("always yields a mark, so a badge is never a blank space", () => {
    expect(connectorMark(origin())).toBe("◈");
    expect(connectorMark(origin({ icon: "" }))).toBe("◇");
    expect(connectorMark(null)).toBe("◇");
  });
});

describe("optionLabel", () => {
  it("appends the source only when the source distinguishes anything", () => {
    // With one connected company every badge says the same thing, and a column
    // of identical badges is decoration that costs width on every screen.
    expect(optionLabel("ACE Designers", origin(), true))
      .toBe("ACE Designers — SLS Engineers · Zoho");
    expect(optionLabel("ACE Designers", origin(), false)).toBe("ACE Designers");
    expect(optionLabel("ACE Designers", null, true)).toBe("ACE Designers");
  });
});

describe("rankMatches", () => {
  const rows = [
    { name: "ACE Designers Pvt", origin: origin({ connection_id: "conn_2" }) },
    { name: "ACE", origin: origin({ connection_id: "conn_2" }) },
    { name: "Precision ACE", origin: origin({ connection_id: "conn_2" }) },
  ];

  it("ranks exact before prefix before substring", () => {
    expect(rankMatches(rows, "ace").map((r) => r.name))
      .toEqual(["ACE", "ACE Designers Pvt", "Precision ACE"]);
  });

  it("drops rows that do not match at all", () => {
    expect(rankMatches(rows, "reamer")).toEqual([]);
  });

  it("returns everything, untouched, for an empty query", () => {
    // An empty box is not a filter — it is the list.
    expect(rankMatches(rows, "")).toEqual(rows);
    expect(rankMatches(rows, "   ")).toEqual(rows);
  });

  it("prefers the company the reader is already working in, within a tier", () => {
    // Same tier, so only the preference decides — which is the case it is for.
    const twoBooks = [
      { name: "ABC Industries", origin: origin({ connection_id: "conn_other" }) },
      { name: "ABC Industries", origin: origin({ connection_id: "conn_mine" }) },
    ];
    expect(rankMatches(twoBooks, "abc", "conn_mine")[0].origin.connection_id)
      .toBe("conn_mine");
    // …and it must not outrank a better match from another book.
    const better = [
      { name: "ABC Industries Ltd", origin: origin({ connection_id: "conn_mine" }) },
      { name: "ABC", origin: origin({ connection_id: "conn_other" }) },
    ];
    expect(rankMatches(better, "abc", "conn_mine")[0].name).toBe("ABC");
  });

  it("keeps the same order for the same query", () => {
    // A list that reorders itself between identical searches is one nobody
    // trusts. Ties break on original position, so this is a property rather
    // than a coincidence of the sort implementation.
    const ties = ["Alpha Tools", "Alpha Works", "Alpha Metals"].map((name) => ({
      name, origin: origin({ connection_id: "conn_same" }),
    }));
    expect(rankMatches(ties, "alpha").map((r) => r.name))
      .toEqual(["Alpha Tools", "Alpha Works", "Alpha Metals"]);
    expect(rankMatches(ties, "alpha")).toEqual(rankMatches(ties, "alpha"));
  });

  it("matches without regard to case, in either direction", () => {
    expect(rankMatches(rows, "ACE").map((r) => r.name))
      .toEqual(rankMatches(rows, "ace").map((r) => r.name));
  });

  it("tolerates a row with no origin at all", () => {
    // `origin` is optional on the row type, and a search must not throw on a
    // record that predates provenance.
    const mixed = [{ name: "ACE" }, { name: "ACE Designers" }];
    expect(rankMatches(mixed, "ace", "conn_mine").map((r) => r.name))
      .toEqual(["ACE", "ACE Designers"]);
  });
});
