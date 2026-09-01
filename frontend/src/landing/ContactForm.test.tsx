// The form that replaced the price list, and the two ways a form like this
// fails without anybody noticing.
//
// The first is that it does not send. A page can describe three plans, ask a
// buyer to get in touch, thank them, and drop the message — and every screen
// in that sequence looks correct. So what is pinned here is the request: the
// endpoint it goes to, and that every field the visitor filled in is in it.
//
// The second is that it *claims* to have sent when it did not. A refusal from
// the server ends in a thank-you, and the enquiry is gone. Both failure
// branches therefore keep the form on screen with the server's own sentence
// above it, and the visitor's answers still in the fields.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContactForm } from "./ContactForm";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The form with the plan state its parent owns. Returns the setter's spy so a
 *  test can assert the picker reports a change back up — the panels above the
 *  form read the same value. */
function form(plan = "") {
  const onPlanChange = vi.fn();
  const { container } = render(
    <ContactForm plan={plan} onPlanChange={onPlanChange} />);
  return { onPlanChange, container };
}

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillTheUsual() {
  fill(/your name/i, "A. Buyer");
  fill(/company/i, "Acme Distributors");
  fill(/email/i, "buyer@acme.example");
  fill(/which erp/i, "Prophet 21");
  fill(/anything else/i, "Three companies, one book each.");
}

/** A `fetch` that answers `ok` and records what it was called with. */
function stubFetch(response: Partial<Response> & { ok: boolean }) {
  const fetchMock = vi.fn().mockResolvedValue({
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Submit the form element itself rather than pressing the button.
 *
 *  jsdom does not implement form submission from a click, and the button's own
 *  label changes to "Sending…" mid-flight — so a helper that found it by name
 *  would work once and fail on the second press, which is exactly the case one
 *  test below is about. */
function submit(container: HTMLElement) {
  fireEvent.submit(container.querySelector("form")!);
}

describe("sending an enquiry", () => {
  it("posts every answer to the contact endpoint", async () => {
    const fetchMock = stubFetch({ ok: true });
    const { container } = form("intelligence");
    fillTheUsual();
    submit(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/contact");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      name: "A. Buyer",
      company: "Acme Distributors",
      email: "buyer@acme.example",
      phone: "",
      erp: "Prophet 21",
      // The plan the panel preselected, carried through. This is the one field
      // the visitor answered by pressing a button rather than by typing.
      plan: "intelligence",
      message: "Three companies, one book each.",
    });
  });

  it("says 'did not say' rather than an empty answer", async () => {
    // `null`, not `""`. The server distinguishes an unanswered question from an
    // answer it does not recognise and refuses the second, so a form that sent
    // an empty string would turn "not sure yet" into a 400.
    const fetchMock = stubFetch({ ok: true });
    const { container } = form();
    fillTheUsual();
    submit(container);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).plan).toBeNull();
  });

  it("carries the header the API requires on a write", async () => {
    // Through `authInit`, which is the shared transport rather than a second
    // copy of the rule — see `src/authFetch.ts`.
    const fetchMock = stubFetch({ ok: true });
    const { container } = form();
    fillTheUsual();
    submit(container);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][1].headers["X-PIE-App"]).toBe("1");
  });

  it("thanks the visitor only after the server has it", async () => {
    const fetchMock = stubFetch({ ok: true });
    const { container } = form();
    fillTheUsual();
    // Before the response, the form is still a form.
    expect(screen.queryByText(/that reached us/i)).toBeNull();
    submit(container);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await screen.findByText(/that reached us/i)).toBeTruthy();
  });

  it("promises nothing it did not do", async () => {
    // No account, no plan, no charge — the same non-promise the row it wrote
    // makes. The thank-you is the last thing a visitor reads here, so it is
    // the place a false one would do the most damage.
    stubFetch({ ok: true });
    const { container } = form();
    fillTheUsual();
    submit(container);
    const done = await screen.findByText(/no account\s+was created/i);
    expect(done).toBeTruthy();
  });

  it("reports the plan the picker was changed to", () => {
    // The parent holds it because the panels above set it too; a picker that
    // kept its own copy would show one plan while the panel had chosen
    // another.
    const { onPlanChange } = form();
    fireEvent.change(screen.getByLabelText(/which plan/i),
                     { target: { value: "platform" } });
    expect(onPlanChange).toHaveBeenCalledWith("platform");
  });
});

describe("when it does not send", () => {
  it("shows the server's own refusal and keeps the form", async () => {
    stubFetch({
      ok: false,
      json: async () => ({ detail: "That does not look like an email address" }),
    });
    const { container } = form();
    fillTheUsual();
    submit(container);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("That does not look like an email address");
    // Still a form, and still holding what was typed: re-typing four fields
    // because the fifth was wrong is how an enquiry is abandoned.
    expect(screen.getByLabelText(/your name/i)).toHaveValue("A. Buyer");
    expect(screen.queryByText(/that reached us/i)).toBeNull();
  });

  it("says so plainly when the request never arrived", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = form();
    fillTheUsual();
    submit(container);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/did not send/i);
    expect(screen.queryByText(/that reached us/i)).toBeNull();
  });

  it("does not send twice while the first is in flight", async () => {
    // A slow reply and an impatient second press. Two rows in the queue is not
    // a defect anybody would see, which is exactly why it lasts.
    let release: (v: unknown) => void = () => {};
    const fetchMock = vi.fn().mockReturnValue(
      new Promise((resolve) => { release = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = form();
    fillTheUsual();
    submit(container);
    submit(container);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release({ ok: true, json: async () => ({}) });
    expect(await screen.findByText(/that reached us/i)).toBeTruthy();
  });
});

describe("the form itself", () => {
  it("states no price", () => {
    // The whole point of it. A currency figure anywhere in this component
    // would be the panels' price back in a different font.
    const { container } = form();
    expect(container.textContent).not.toMatch(/[$₹]\s?\d/);
    expect(container.textContent).not.toMatch(/\/\s?month|per month/i);
  });

  it("asks for a way to reply, and marks the rest optional", () => {
    form();
    expect(screen.getByLabelText(/email/i)).toBeRequired();
    expect(screen.getByLabelText(/your name/i)).toBeRequired();
    // Everything else is a question, not a gate: a buyer who will not give a
    // phone number is still a buyer.
    expect(screen.getByLabelText(/phone/i)).not.toBeRequired();
    expect(screen.getByLabelText(/which erp/i)).not.toBeRequired();
    expect(screen.getByLabelText(/anything else/i)).not.toBeRequired();
  });
});
