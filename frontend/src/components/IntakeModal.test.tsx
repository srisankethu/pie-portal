// The document an RFQ arrived as, collected but not uploaded here.
//
// The dialog holds a `File` and hands it up on submit. That ordering is the
// whole of what this file pins: an upload on selection would store a document
// every time somebody opened the picker and changed their mind, and there is no
// undo for a stored document — it can be withdrawn, which is a row that says
// somebody withdrew it, not a row that never existed.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IntakeModal } from "./IntakeModal";

function open(onSubmit = vi.fn()) {
  render(<IntakeModal onClose={vi.fn()} onSubmit={onSubmit} />);
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

const PDF = new File(["%PDF-1.7 ..."], "customer rfq.pdf",
                     { type: "application/pdf" });

describe("IntakeModal attachment", () => {
  it("submits the file beside the text", () => {
    const onSubmit = open();

    fireEvent.change(screen.getByLabelText(/RFQ text/i),
                 { target: { value: "2001174, 20" } });
    fireEvent.change(fileInput(), { target: { files: [PDF] } });
    fireEvent.click(screen.getByRole("button", { name: /Resolve & add/i }));

    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", PDF);
  });

  it("submits null when nothing was attached", () => {
    const onSubmit = open();

    fireEvent.change(screen.getByLabelText(/RFQ text/i),
                 { target: { value: "2001174, 20" } });
    fireEvent.click(screen.getByRole("button", { name: /Resolve & add/i }));

    expect(onSubmit).toHaveBeenCalledWith("2001174, 20", "", null);
  });

  it("names the attached file so the desk can see what will be stored", () => {
    open();

    fireEvent.change(fileInput(), { target: { files: [PDF] } });

    expect(screen.getByText("customer rfq.pdf")).toBeInTheDocument();
  });

  it("lets an attachment be taken back before anything is stored", () => {
    const onSubmit = open();

    fireEvent.change(screen.getByLabelText(/RFQ text/i),
                 { target: { value: "2001174, 20" } });
    fireEvent.change(fileInput(), { target: { files: [PDF] } });
    fireEvent.click(screen.getByRole("button", { name: /Remove/i }));
    fireEvent.click(screen.getByRole("button", { name: /Resolve & add/i }));

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
