// Where a user-facing sentence goes, and whether anything renders it.
//
// `notice` is the message the sign-in card shows. It has exactly one renderer,
// and that renderer only exists while the session is gone — so a sentence set
// on a path where the shell *survives* is set and never seen. Two were:
//
//   * a refused workspace switch ("That workspace could not be opened…"). The
//     comment above it even said "quiet beyond the toast" — there was no toast.
//     The switch failed in silence, and the stale sentence could then surface
//     at the next sign-out, describing something that happened long before.
//   * a sign-in as somebody else in another tab. The tab correctly adopted the
//     new account and said nothing, so the name and role changed under the
//     reader with no explanation.
//
// Neither was visible to the suite: every piece worked on its own, which is the
// same shape as the defect `AuthDoors.test.tsx` was written for. Pinned here as
// a property of the source, in the idiom `kit.contract.test.ts` already uses,
// because the alternative is mounting the whole application to read a toast.
/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

import dataScreen from "./DataScreen.tsx?raw";
import src from "./PlatformApp.tsx?raw";

describe("the notice is only for messages that end signed out", () => {
  it("has exactly one renderer, behind the signed-out gate", () => {
    // The guard against a vacuous pass: if `notice` stopped being rendered at
    // all, or the gate were renamed, every assertion below would still hold
    // while saying nothing.
    const gate = src.indexOf("if (!session) {");
    const rendered = src.lastIndexOf("notice={notice}");
    expect(gate).toBeGreaterThan(0);
    expect(rendered).toBeGreaterThan(gate);
    // Twice: the pass-through in the `SignIn` wrapper, and the one site that
    // renders it. A third would mean a second renderer to reason about.
    expect(src.match(/notice=\{notice\}/g)).toHaveLength(2);
  });

  it("still uses the notice for the messages that do end signed out", () => {
    // Session expiry and a sign-out elsewhere both unmount the shell, so the
    // sign-in card is exactly where their sentence belongs. Without this, the
    // two assertions below could pass by `setNotice` having been deleted.
    expect(src).toMatch(/setNotice\("Your session expired/);
    expect(src).toMatch(/setNotice\("You signed out in another tab/);
  });
});

describe("a message raised while the shell survives goes to a toast", () => {
  it("reports a refused workspace switch", () => {
    expect(src).toMatch(/flash\("That workspace could not be opened/);
    expect(src).not.toMatch(/setNotice\("That workspace could not be opened/);
  });

  it("reports a sign-in as somebody else in another tab", () => {
    expect(src).toMatch(/flash\(`Signed in as \$\{next\.name\} in another tab/);
    expect(src).not.toMatch(/setNotice\(`Signed in as/);
  });

  it("raises that toast outside the state updater", () => {
    // `setSession(fn)` may run `fn` twice under StrictMode. A toast fired from
    // inside it is a side effect in a function React is entitled to call again,
    // so the reader would see the message twice. The handler reads the current
    // session through a ref instead.
    expect(src).toMatch(/const prev = sessionRef\.current;/);
    expect(src).not.toMatch(/setSession\(\(prev\)[\s\S]{0,400}?flash\(/);
  });
});

describe("an error a salesperson can hit offers a way to act on it", () => {
  it("gives the data-status failure a retry", () => {
    // The "Refresh status" button that would otherwise re-fetch sits inside the
    // manager-only branch, so for a salesperson this error state was the end of
    // the road.
    expect(dataScreen).toMatch(
      /Could not read the connection status[\s\S]{0,120}onRetry=\{load\}/);
  });
});
