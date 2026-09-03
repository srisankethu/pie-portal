// The two learning panels: a remembered phrase is listed with its customer,
// an owner can retire it, a manager cannot, and the report's readings are the
// server's sentences rather than a judgement made here.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { papi } from "./api";
import { CatalogLearning } from "./CatalogLearning";
import type { PhraseAliases, PlatformSession, RetrievalReport } from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function aliases(canManage = true): PhraseAliases {
  return {
    can_manage: canManage,
    aliases: [{
      alias_id: "al1", identity_id: "id-pitti", customer: "Pitti Engineering",
      phrase: "12mm drill for SS", target_record_id: "4149315",
      source_ref: "quote q1 line l1", recorded_by: "usr_sales",
      created_at: "2026-09-02T10:00:00Z",
    }],
  };
}

const REPORT: RetrievalReport = {
  organization_id: "org_pie", since: null, drafts: 3,
  counts: { lines: 12, auto_selected: 4, chosen_by_person: 6, chosen_from_ranking: 3,
            chosen_from_retrieval: 2, chosen_from_confirmed_code: 0, chosen_from_phrase: 1,
            typed_unoffered: 0, left_open: 2 },
  shares: { found_beneath_ranking: 0.5, typed_unoffered: 0, auto_selected: 0.333 },
  learned: { phrase_aliases: 1, confirmed_codes: 2, customers_with_aliases: 1,
             training_pairs_floor: 2000 },
  triggers: { ranking: 0.2, meaning: 0.2 },
  readings: ["3 of 6 choices took a record found beneath the ranking: the ranking is the bottleneck, and teaching it the learned words is worth doing."],
};

describe("CatalogLearning", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(papi, "retrievalReport").mockResolvedValue(REPORT);
  });

  it("lists what is remembered, with the customer, and the report's own reading", async () => {
    vi.spyOn(papi, "phraseAliases").mockResolvedValue(aliases());
    render(<CatalogLearning session={SESSION} />);

    expect(await screen.findByText("Pitti Engineering")).toBeTruthy();
    expect(screen.getByText("12mm drill for SS")).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByText(/the ranking is the bottleneck/)).toBeTruthy();
    expect(screen.getByText(/1 phrases · 2 codes/)).toBeTruthy();
  });

  it("lets an owner retire a phrase after confirming, and reloads", async () => {
    const list = vi.spyOn(papi, "phraseAliases")
      .mockResolvedValueOnce(aliases())
      .mockResolvedValueOnce({ can_manage: true, aliases: [] });
    const retire = vi.spyOn(papi, "retirePhraseAlias").mockResolvedValue({ retired: "al1" });
    render(<CatalogLearning session={SESSION} />);

    fireEvent.click(await screen.findByRole("button", { name: "Retire" }));
    expect(screen.getByText("Stop offering this?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retire", hidden: false }));

    await waitFor(() => expect(retire).toHaveBeenCalledWith("t", "al1"));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Nothing remembered yet")).toBeTruthy();
  });

  it("shows a manager the list without a way to retire", async () => {
    vi.spyOn(papi, "phraseAliases").mockResolvedValue(aliases(false));
    render(<CatalogLearning session={SESSION} />);

    expect(await screen.findByText("Pitti Engineering")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retire" })).toBeNull();
    expect(screen.getByText(/Retiring one is an owner action/)).toBeTruthy();
  });
});
