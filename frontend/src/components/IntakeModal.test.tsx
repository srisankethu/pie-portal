// What this dialog collects, and what it does with a press.
//
// Three contracts, all of them things the dialog can get wrong on its own:
//
//  * **The document is held, not uploaded.** The dialog keeps a `File` and
//    hands it up on submit. An upload on selection would store a document
//    every time somebody opened the picker and changed their mind, and there
//    is no undo for a stored document — it can be withdrawn, which is a row
//    that says somebody withdrew it, not a row that never existed.
//  * **A chosen channel can be un-chosen.** `InboundChannel` has no "Other"
//    or "Unknown" member and must not gain one, so "Not stated" submits the
//    same empty string the field opens on.
//  * **One press is one intake.** `onSubmit` is awaited, so a second press
//    while the first is in flight has a disabled button to land on, and a
//    rejection leaves the dialog — and everything typed into it — where it is.
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { IntakeModal } from "./IntakeModal";

/** Read off the component rather than written out again, so a change to the
 *  contract this file exists to pin fails here rather than drifting. */
type OnSubmit = ComponentProps<typeof IntakeModal>["onSubmit"];

/** The caller's half of the contract is a promise, so the default stand-in
 *  returns one: a bare `vi.fn()` would be a caller that resolves by accident
 *  rather than one this suite has decided about. */
function open(onSubmit: OnSubmit = vi.fn().mockResolvedValue(undefined),
              onClose = vi.fn()) {
  render(<IntakeModal onClose={onClose} onSubmit={onSubmit} />);
  return onSubmit;
}

/** `fireEvent`, not `user-event`. The latter is not a dependency of this repo
 *  and adding one for a single file is a cost the suite has so far declined to
 *  pay; nothing here needs the pointer sequences it models.
 */

/** The picker is `hidden`, which is correct — a bare file input cannot be
 *  styled to match the design system and MUI's own pattern is a button that
 *  clicks one. Testing Library will not find a hidden element by role, so the
 *  input is reached the way the component reaches it. */
function fileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]');
  expect(input).not.toBeNull();
  return input as HTMLInputElement;
}

const rfqBox = () => screen.getByLabelText(/RFQ text/i);
const submitButton = () => screen.getByRole("button", { name: /Resolve & add/i });
const channelBox = () => screen.getByRole("combobox", { name: /How did this reach you/i });

/** MUI opens a `Select` on mouse-down, not on click. */
function chooseChannel(label: RegExp) {
  fireEvent.mouseDown(channelBox());
  fireEvent.click(screen.getByRole("option", { name: label }));
}

const PDF = new File(["%PDF-1.7 ..."], "customer rfq.pdf",
                     { type: "application/pdf" });

/** The press, plus the microtask the awaited `onSubmit` resolves in — without
 *  which the state it settles lands outside `act` and after the assertion. */
async function press() {
  fireEvent.click(submitButton());
  await act(async () => {});
}

describe("IntakeModal attachment", () => {
  it("submits the file beside the text", async () => {
    const onSubmit = open();

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    fireEvent.change(fileInput(), { target: { files: [PDF] } });
    await press();

    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", PDF);
  });

  it("submits null when nothing was attached", async () => {
    const onSubmit = open();

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    await press();

    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", null);
  });

  it("names the attached file so the desk can see what will be stored", () => {
    open();

    fireEvent.change(fileInput(), { target: { files: [PDF] } });

    expect(screen.getByText("customer rfq.pdf")).toBeInTheDocument();
  });

  it("lets an attachment be taken back before anything is stored", async () => {
    const onSubmit = open();

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    fireEvent.change(fileInput(), { target: { files: [PDF] } });
    fireEvent.click(screen.getByRole("button", { name: /Remove/i }));
    await press();

    expect(screen.queryByText("customer rfq.pdf")).not.toBeInTheDocument();
    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", null);
  });

  it("does not claim the document is read", () => {
    // The lines come from the pasted text; nothing extracts from the file yet.
    // A dialog that implied otherwise would have somebody attach a BOQ, paste
    // nothing, and wonder where their lines went.
    open();

    // `document.body`, not the render container: a MUI `Dialog` renders into a
    // portal, so the container it was called with stays empty. And the whole
    // body's text rather than `getByText`, because the sentence is deliberately
    // broken across a `<strong>` — "It is **not** read" — which no single-node
    // query can span. Both details would otherwise fail on wording that is
    // exactly right.
    expect(document.body.textContent).toMatch(/is\s*not\s*read/i);
  });
});

describe("IntakeModal channel", () => {
  it("says the channel is unset rather than showing an empty box", () => {
    open();

    // The words, not a blank. This is what `displayEmpty` buys, and without it
    // the option below cannot be rendered at all.
    expect(channelBox()).toHaveTextContent("Not stated");
  });

  it("puts a mis-chosen channel back to unset, and submits it unset", async () => {
    const onSubmit = open();
    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });

    chooseChannel(/^Email$/);
    expect(channelBox()).toHaveTextContent("Email");

    chooseChannel(/Not stated/);
    await press();

    // The empty string the field opens on — not a sixth `InboundChannel`
    // member. The server reads it as "no channel stated" and keeps no wording,
    // which is the same row it would have written had nobody touched the field.
    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", null);
  });
});

describe("IntakeModal submit", () => {
  it("fires one intake however many times the button is pressed", async () => {
    // A press that never settles, so the second click meets the dialog in the
    // state a fast double-click meets it in.
    let land = () => {};
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => { land = () => resolve(); }));
    open(onSubmit);

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    fireEvent.click(submitButton());
    fireEvent.click(submitButton());

    // One upload and one intake, not two of each. The button is disabled while
    // it is loading, so the second press has nothing to land on.
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(submitButton()).toBeDisabled();

    await act(async () => { land(); });
  });

  it("keeps the dialog, the text and the attachment when the intake is refused", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("Line 3 has no quantity"));
    open(onSubmit);

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    fireEvent.change(fileInput(), { target: { files: [PDF] } });
    await press();

    // The server's own sentence, on the dialog rather than in a snackbar that
    // takes the only copy of the RFQ with it when it closes.
    expect(await screen.findByText("Line 3 has no quantity")).toBeInTheDocument();
    expect(rfqBox()).toHaveValue("2001174, 20");
    expect(screen.getByText("customer rfq.pdf")).toBeInTheDocument();
    // And it can be pressed again: the refusal is not a dead end.
    expect(submitButton()).toBeEnabled();
  });

  it("does not close itself — the caller closes it when the intake lands", async () => {
    const onClose = vi.fn();
    open(vi.fn().mockResolvedValue(undefined), onClose);

    fireEvent.change(rfqBox(), { target: { value: "2001174, 20" } });
    await press();

    // `onClose` means "the person backed out". The screen that owns the quote
    // is the one that knows the lines landed, and it is what closes this.
    expect(onClose).not.toHaveBeenCalled();
  });
});
