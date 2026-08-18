// The account control is the only way out of the app, so what it must keep
// doing is narrow and worth pinning: the account is identifiable at every
// width, and signing out takes exactly one press once the menu is open.
//
// The width part is the reason this file exists. The block this replaced was
// `display: { xs: "none", sm: "block" }` — on a phone the name and role were
// simply gone, and a shared tablet showed no way to tell whose session was
// open. Moving the identity inside the menu is what fixes that, and a jsdom
// test cannot see a media query, so the assertion is about *where the name
// lives* rather than about a viewport: it is inside the menu, which has no
// breakpoint on it.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AccountMenu, { initials } from "./AccountMenu";

describe("initials", () => {
  it("takes the first and last word", () => {
    expect(initials("S. Menon")).toBe("SM");
    expect(initials("Sanketh")).toBe("S");
    expect(initials("ravi kumar reddy")).toBe("RR");
  });

  it("ignores punctuation rather than rendering it", () => {
    // "S." must not become "S." on the avatar, and a name that is only
    // punctuation leaves the avatar to its own fallback.
    expect(initials("(S) Menon")).toBe("SM");
    expect(initials("  ")).toBe("");
    expect(initials("...")).toBe("");
  });
});

describe("AccountMenu", () => {
  function show(onSignOut = vi.fn()) {
    render(<AccountMenu userName="S. Menon" roleLabel="Owner" onSignOut={onSignOut} />);
    return { onSignOut, trigger: screen.getByRole("button", { name: /account: s\. menon, owner/i }) };
  }

  it("says who is signed in without opening anything", () => {
    show();
    // The accessible name carries it even where the text block is hidden.
    expect(screen.getByRole("button", { name: /account: s\. menon, owner/i })).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens a menu carrying the name and role", async () => {
    const { trigger } = show();
    fireEvent.click(trigger);
    const menu = await screen.findByRole("menu");
    expect(menu).toHaveTextContent("S. Menon");
    expect(menu).toHaveTextContent("Owner");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("signs out from the menu, once", async () => {
    const { trigger, onSignOut } = show();
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("is a real button, so the keyboard reaches it", () => {
    // The only exit from the app must not be mouse-only. jsdom does not turn
    // Enter into a click, so what is checkable here is that the trigger is a
    // focusable native button rather than a div with an onClick — which is the
    // thing that would actually break keyboard access.
    const { trigger } = show();
    expect(trigger.tagName).toBe("BUTTON");
    // `act` because MUI's ripple sets state on focus, and an unwrapped update
    // is a warning in every later test rather than in this one.
    act(() => trigger.focus());
    expect(trigger).toHaveFocus();
  });
});
