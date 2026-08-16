// When the quote screen re-asks the platform, and when it does not.
//
// The hook turns N lines into one request, and the property worth pinning is
// the *key* it is memoised on: product, quantity and price — the three inputs
// that can change an assessment. A quote object is rebuilt by every interaction
// on that screen, so keying on the object itself means opening a drawer,
// ticking a checkbox or filtering the grid re-asks the server a question whose
// answer cannot have changed.
//
// That failure is silent in the worst way. Nothing renders wrong; the screen
// just issues a request per keystroke against endpoints that recompute margins
// over a customer's whole history, and the first evidence of it is a slow
// quote screen nobody can reproduce.
//
// So these tests count calls rather than assert on rendered output.
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useQuoteIntelligence } from "./useQuoteIntelligence";
import type { Line, Quote } from "./types";

function line(over: Partial<Line> = {}): Line {
  return {
    id: "l1", supplyCode: "2001174", reqCode: "CNMG120408",
    reqQty: 10, quoted: 1000,
    ...over,
  } as Line;
}

function quote(over: Partial<Quote> = {}): Quote {
  return {
    id: "q1", customer: "Pitti Engineering", lines: [line()],
    ...over,
  } as Quote;
}

/** Requests the hook made, by path. */
let calls: string[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    return {
      ok: true,
      statusText: "OK",
      json: async () => ({ lines: [], quote_id: "q1", can_submit: true, requests: [] }),
    } as unknown as Response;
  }));
});

afterEach(() => vi.unstubAllGlobals());

const assessCalls = () => calls.filter((u) => u.includes("/assess"));

/** Let anything already in flight settle, inside act so a state update that
 *  lands here is not reported as an unwrapped one. Used to prove a request was
 *  *not* made, which is a wait for nothing to happen and so cannot be a
 *  `waitFor` on a condition. */
async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}

describe("when it asks", () => {
  it("asks once for a quote with lines", async () => {
    renderHook(() => useQuoteIntelligence(quote(), "tok"));
    await waitFor(() => expect(assessCalls()).toHaveLength(1));
  });

  it("asks nothing at all when there is no quote or no customer", async () => {
    // A quote with no customer cannot be priced against a history, so there is
    // no question to ask — and asking would be an error response per render.
    renderHook(() => useQuoteIntelligence(null, "tok"));
    renderHook(() => useQuoteIntelligence(quote({ customer: "" }), "tok"));
    renderHook(() => useQuoteIntelligence(quote({ lines: [] }), "tok"));
    await settle();
    expect(assessCalls()).toHaveLength(0);
  });
});

describe("when it does not ask again", () => {
  it("does not re-ask when the quote object is rebuilt with the same lines", async () => {
    // The case this hook exists for. Every interaction on the quote screen
    // produces a new object; only a changed assessment input is a new question.
    const { rerender } = renderHook(
      ({ q }) => useQuoteIntelligence(q, "tok"), { initialProps: { q: quote() } });
    await waitFor(() => expect(assessCalls()).toHaveLength(1));

    rerender({ q: quote() });          // same content, different object
    rerender({ q: quote() });
    await settle();
    expect(assessCalls()).toHaveLength(1);
  });

  it("ignores a change to a field the assessment does not read", async () => {
    // `sel`, `notes`, drawer state and selection all live on the line and all
    // change while somebody works. None of them changes what the line is worth.
    const { rerender } = renderHook(
      ({ q }) => useQuoteIntelligence(q, "tok"), { initialProps: { q: quote() } });
    await waitFor(() => expect(assessCalls()).toHaveLength(1));

    rerender({ q: quote({ lines: [line({ sel: "USER", notes: ["touched"] })] }) });
    await settle();
    expect(assessCalls()).toHaveLength(1);
  });
});

describe("when it asks again", () => {
  it.each([
    ["a new price", { quoted: 1250 }],
    ["a new quantity", { reqQty: 25 }],
    ["a different supply product", { supplyCode: "2009999" }],
  ])("re-asks for %s", async (_what, change) => {
    const { rerender } = renderHook(
      ({ q }) => useQuoteIntelligence(q, "tok"), { initialProps: { q: quote() } });
    await waitFor(() => expect(assessCalls()).toHaveLength(1));

    rerender({ q: quote({ lines: [line(change)] }) });
    await waitFor(() => expect(assessCalls()).toHaveLength(2));
  });

  it("re-asks when the quote is a different quote", async () => {
    const { rerender } = renderHook(
      ({ q }) => useQuoteIntelligence(q, "tok"), { initialProps: { q: quote() } });
    await waitFor(() => expect(assessCalls()).toHaveLength(1));

    rerender({ q: quote({ id: "q2" }) });
    await waitFor(() => expect(assessCalls()).toHaveLength(2));
  });
});

describe("what it reports", () => {
  it("surfaces a failure rather than leaving the panel silently empty", async () => {
    // An empty intelligence panel reads as "no history for this product", which
    // is a claim. A failure has to say so.
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 500, statusText: "Internal Server Error",
      json: async () => ({}),
    } as unknown as Response)));

    const { result } = renderHook(() => useQuoteIntelligence(quote(), "tok"));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("indexes the result by line id for the drawer to read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, statusText: "OK",
      json: async () => ({ lines: [{ line_id: "l1", resolved: true }] }),
    } as unknown as Response)));

    const { result } = renderHook(() => useQuoteIntelligence(quote(), "tok"));
    await waitFor(() => expect(result.current.byLineId.l1).toBeTruthy());
  });
});
