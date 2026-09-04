// The dialog the contact form opens in. What is pinned here is the behaviour a
// dialog owes somebody who is not using a mouse — Escape, focus in and back
// out, and Tab that stays inside — because all three fail silently: the form
// looks right in every screenshot while the keyboard walks out of it into the
// page behind.
//
// The form's own behaviour (what it posts, and what it does with a refusal) is
// `ContactForm.test.tsx`'s. There is one form and it is tested once.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContactModal } from "./ContactModal";

function open() {
  const onClose = vi.fn();
  const utils = render(<ContactModal onClose={onClose} />);
  return { onClose, ...utils };
}

describe("opening the contact dialog", () => {
  it("puts the form in a dialog that says what it is", () => {
    open();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // Named by the form's own heading rather than by a label repeated here:
    // two names for one thing drift.
    expect(dialog).toHaveAccessibleName("Book a demo");
    expect(dialog.querySelector("form")).not.toBeNull();
  });

  it("moves focus onto the dialog itself, so it is announced before its fields", () => {
    open();
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
  });

  it("restores focus to whatever opened it", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = open();
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});

describe("closing it", () => {
  it("closes on Escape", () => {
    const { onClose } = open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on the close button", () => {
    const { onClose } = open();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on a press that lands on the backdrop", () => {
    const { onClose, container } = open();
    fireEvent.mouseDown(container.querySelector(".lp-modal-backdrop")!);
    expect(onClose).toHaveBeenCalled();
  });

  it("does not close on a press inside the form", () => {
    const { onClose } = open();
    // The case this is about is a drag that starts in a textarea and ends on
    // the backdrop: it bubbles, and a handler that did not check where the
    // press landed would throw away what the visitor had typed.
    fireEvent.mouseDown(screen.getByLabelText(/your name/i));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not release the page's scroll until it is gone", () => {
    const { unmount } = open();
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe("");
  });
});

describe("the keyboard inside it", () => {
  function stops() {
    return Array.from(
      screen.getByRole("dialog").querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]),'
        + ' select:not([disabled]), textarea:not([disabled])'));
  }

  it("wraps Tab from the last stop back to the first", () => {
    open();
    const all = stops();
    all[all.length - 1].focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(all[0]);
  });

  it("wraps Shift+Tab from the first stop back to the last", () => {
    open();
    const all = stops();
    all[0].focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(all[all.length - 1]);
  });

  it("enters at the first stop when focus is still on the dialog", () => {
    open();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(stops()[0]);
  });

  it("enters at the last stop on Shift+Tab from the dialog", () => {
    // The dialog is the first thing in the overlay, so an unguarded Shift+Tab
    // from it walks straight out into the page behind — which is the leak a
    // trap that only handles the two ends would still have.
    open();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    const all = stops();
    expect(document.activeElement).toBe(all[all.length - 1]);
  });

  it("leaves Tab alone everywhere else", () => {
    open();
    const all = stops();
    all[1].focus();
    fireEvent.keyDown(document, { key: "Tab" });
    // Unhandled: the browser's own order moves it, and jsdom's does nothing.
    expect(document.activeElement).toBe(all[1]);
  });
});
