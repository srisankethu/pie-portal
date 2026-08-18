/** The browser half of the session change: what is stored, and what the other
 *  tabs are told.
 *
 *  Two things used to be wrong here and both are pinned below. The token was
 *  written to `localStorage`, where any injected script could read it — it now
 *  rides in an httpOnly cookie and must not be written at all. And nothing told
 *  the other tabs when the session changed, so signing out in one left the
 *  others drawing a live-looking shell over a dead session.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearPlatformSession, loadPlatformSession, onSessionChangedElsewhere,
         savePlatformSession } from "./api";
import type { PlatformSession } from "./types";

const KEY = "pie_platform_session";

const SESSION: PlatformSession = {
  token: "tok_secret_value",
  user_id: "usr_menon",
  organization_id: "org_pie",
  role: "OWNER",
  name: "S. Menon",
  email: "s.menon@pie.example",
  currency: "INR",
  timezone: "Asia/Kolkata",
  must_change_password: false,
};

beforeEach(() => {
  localStorage.clear();
});

describe("what reaches storage", () => {
  it("never writes the token", () => {
    savePlatformSession(SESSION);

    const raw = localStorage.getItem(KEY) ?? "";
    // The strongest form of the assertion: the secret does not appear anywhere
    // in the stored string, whatever shape the object takes.
    expect(raw).not.toContain("tok_secret_value");
    expect(JSON.parse(raw).token).toBe("");
  });

  it("keeps the part the shell needs to draw itself", () => {
    savePlatformSession(SESSION);

    const back = loadPlatformSession();
    expect(back?.user_id).toBe("usr_menon");
    expect(back?.role).toBe("OWNER");
    expect(back?.currency).toBe("INR");
    expect(back?.timezone).toBe("Asia/Kolkata");
  });
});

describe("telling the other tabs", () => {
  /** A `storage` event as the browser delivers it to the tabs that did *not*
   *  write — which is the only place it fires. */
  function elsewhere(newValue: string | null) {
    window.dispatchEvent(new StorageEvent("storage", { key: KEY, newValue }));
  }

  it("reports a sign-out in another tab as no session", () => {
    const seen = vi.fn();
    onSessionChangedElsewhere(seen);

    elsewhere(null);

    expect(seen).toHaveBeenCalledWith(null);
  });

  it("reports a different account signing in elsewhere", () => {
    savePlatformSession({ ...SESSION, user_id: "usr_rao", name: "M. Rao", role: "SALES_MANAGER" });
    const seen = vi.fn();
    onSessionChangedElsewhere(seen);

    elsewhere(localStorage.getItem(KEY));

    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0]).toMatchObject({ user_id: "usr_rao", role: "SALES_MANAGER" });
  });

  it("ignores writes to keys that are not the session", () => {
    const seen = vi.fn();
    onSessionChangedElsewhere(seen);

    window.dispatchEvent(new StorageEvent("storage", { key: "something_else", newValue: "x" }));

    expect(seen).not.toHaveBeenCalled();
  });

  it("treats a wholesale storage clear as a sign-out", () => {
    // `key` is null when storage is cleared entirely, which is a real way for a
    // session to end and would be missed by a check for our key alone.
    const seen = vi.fn();
    onSessionChangedElsewhere(seen);

    window.dispatchEvent(new StorageEvent("storage", { key: null, newValue: null }));

    expect(seen).toHaveBeenCalledWith(null);
  });

  it("stops listening once unsubscribed", () => {
    const seen = vi.fn();
    const stop = onSessionChangedElsewhere(seen);
    stop();

    elsewhere(null);

    expect(seen).not.toHaveBeenCalled();
  });
});

describe("clearing", () => {
  it("leaves nothing behind", () => {
    savePlatformSession(SESSION);
    clearPlatformSession();
    expect(loadPlatformSession()).toBeNull();
  });
});
