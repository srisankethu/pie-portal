// The two doors, and the one property that connects them: a person who arrives
// at the sign-in form with no account must be able to get one.
//
// That is not a styling detail, it is the defect these tests were written for.
// The sign-up form existed, worked, and was reachable from exactly one button
// on the landing page — so anyone who arrived at the card any other way (a
// shared deep link, an expired session, the "Sign in" in the nav) met a form
// for an account they did not have, under a sentence telling them to ask an
// owner who, for a company that had never used PIE, did not exist. Nothing in
// the suite noticed, because every piece worked on its own.
//
// The other property is the one that is easy to get wrong in the opposite
// direction. The sign-up form now asks which plan a business wants, and every
// answer creates the same free account: `entitlements.py` is explicit that
// plans are set by the operator and never by a tenant, and there is no billing
// here at all. `TrialNotice.test.tsx` names the failure mode — a control that
// implies a purchase is "a dead end shipped to the one person most likely to
// press it" — so what is pinned below is that the card *says* what it does.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SignInCard } from "./SignInCard";
import { SignUpCard } from "./SignUpCard";
import type { SignupOffer } from "./platform/types";

/** What `GET /api/v1/signup` answers where sign-up is on. Shaped by the server;
 *  the ladder is deliberately not written in the component. */
const OFFER: SignupOffer = {
  enabled: true,
  plan: "free",
  trial_days: 30,
  note: "A free Quote Desk account.",
  plans: [
    // `purchasable: false` on the floor: it is where an organization lands when
    // it stops paying, not a tier anyone chooses. Listed anyway so a screen can
    // name what an organization currently has.
    { plan: "free", label: "Quote Desk (no subscription)",
      summary: "Quoting and approvals.", purchasable: false },
    { plan: "intelligence", label: "Commercial Intelligence",
      summary: "Adds the decision layer.", purchasable: true },
    { plan: "platform", label: "Platform",
      summary: "Adds several companies.", purchasable: true },
  ],
};

/** Fill one labelled field. `fireEvent.change` rather than a keystroke library:
 *  nothing here depends on per-character behaviour, and the suite has no
 *  user-event dependency to add for it. */
function type(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function signIn(props: Partial<React.ComponentProps<typeof SignInCard>> = {}) {
  render(
    <SignInCard
      title="Commercial Decisions"
      blurb="One product, three doors."
      submitLabel="Sign in"
      onSubmit={async () => {}}
      footer="An owner creates accounts and sets roles from Settings."
      {...props}
    />,
  );
}

describe("the sign-in card", () => {
  it("offers the way to sign up where the deployment has one", () => {
    const onSignUp = vi.fn();
    signIn({ onSignUp });

    fireEvent.click(screen.getByRole("button", { name: /create your organization/i }));
    expect(onSignUp).toHaveBeenCalledOnce();
  });

  it("keeps the caller's own footer alongside it", () => {
    // Both sentences are true at once and answer different people: a business
    // that has never used PIE, and a person joining one that has. Replacing one
    // with the other sends half the readers to the wrong place.
    signIn({ onSignUp: vi.fn() });
    expect(screen.getByText(/an owner creates accounts/i)).toBeInTheDocument();
  });

  it("says nothing about signing up where the deployment does not offer it", () => {
    // `SELF_SERVE_SIGNUP` is off by default, and on a single-tenant install
    // "ask whoever runs this" is the whole truth. A link to a form that always
    // 404s is worse than no link.
    signIn();
    expect(screen.queryByRole("button", { name: /create your organization/i }))
      .not.toBeInTheDocument();
  });
});

describe("the sign-up card", () => {
  it("draws the plan ladder the server sent, not one of its own", () => {
    render(<SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} offer={OFFER} />);

    for (const p of OFFER.plans) {
      // Escaped: "Quote Desk (free)" is a plan label, not a capture group.
      const label = new RegExp(p.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      expect(screen.getByRole("radio", { name: label })).toBeInTheDocument();
    }
    // Landed-on plan preselected, from the server's `plan` rather than a
    // hardcoded "free" — the client asserting a plan is the client deciding one.
    expect(screen.getByRole("radio", { name: /quote desk/i })).toBeChecked();
  });

  it("opens on the plan the visitor was reading about", async () => {
    render(
      <SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} offer={OFFER}
                  defaultPlan="intelligence" />,
    );
    expect(screen.getByRole("radio", { name: /commercial intelligence/i })).toBeChecked();
  });

  it("ignores a plan the server did not offer rather than selecting nothing", () => {
    render(
      <SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} offer={OFFER}
                  defaultPlan="enterprise" />,
    );
    expect(screen.getByRole("radio", { name: /quote desk/i })).toBeChecked();
  });

  it("says the account starts on the trial when a paid plan is chosen", () => {
    render(<SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} offer={OFFER} />);

    fireEvent.click(screen.getByRole("radio", { name: /platform/i }));
    // The load-bearing sentence, and it changed with the pricing model rather
    // than with the wording: this used to say the account starts on the free
    // Quote Desk, which is now wrong in the direction that matters. An
    // organization starts with *everything* for `trial_days` and only falls to
    // the unsubscribed floor afterwards, so telling somebody they are starting
    // on the floor would undersell the thing they are about to try.
    expect(screen.getByText(/starts with everything for 30 days/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing is charged here/i)).toBeInTheDocument();
    // And the submit is still a sign-up, not a checkout.
    expect(screen.getByRole("button", { name: "Create account" })).toBeInTheDocument();
  });

  it("carries the chosen plan to the server", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SignUpCard onSubmit={onSubmit} onSignIn={vi.fn()} offer={OFFER} />);

    type(/company name/i, "Acme Distributors");
    type(/your name/i, "A. Owner");
    type(/work email/i, "owner@acme.in");
    type(/^password$/i, "a-long-enough-password");
    fireEvent.click(screen.getByRole("radio", { name: /commercial intelligence/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      company: "Acme Distributors", email: "owner@acme.in", plan: "intelligence",
    })));
  });

  it("carries the currency to the server, because nothing can change it later", async () => {
    // The defect this pins was an omission, not a wrong value: the form asked
    // no currency question and sent no currency field, so the API's default
    // applied and every self-serve organization in the world was created in
    // rupees — including the US distributors the landing page quotes in
    // dollars. There is no admin endpoint and no screen that moves an
    // organization's currency afterwards, so the omission was permanent.
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SignUpCard onSubmit={onSubmit} onSignIn={vi.fn()} offer={OFFER} />);

    type(/company name/i, "Ohio Fastener Co.");
    type(/your name/i, "D. Reyes");
    type(/work email/i, "dana@ohiofastener.example");
    type(/^password$/i, "a-long-enough-password");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    // USD without touching the field: the suite's clock is America/New_York
    // (pinned in vite.config.ts), and the default follows the same region
    // signal the pricing panels read.
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      currency: "USD",
    })));
  });

  it("sends the currency the visitor actually picked", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SignUpCard onSubmit={onSubmit} onSignIn={vi.fn()} offer={OFFER} />);

    type(/company name/i, "Hyderabad Tools");
    type(/your name/i, "S. Kumar");
    type(/work email/i, "s@hyderabadtools.example");
    type(/^password$/i, "a-long-enough-password");
    fireEvent.change(screen.getByLabelText(/currency/i), { target: { value: "INR" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      currency: "INR",
    })));
  });

  it("says the currency is permanent, because it is", () => {
    // The one answer on this form nothing in the product can undo. A field
    // that does not say so is a trap for the person filling it in.
    render(<SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} offer={OFFER} />);
    expect(screen.getByText(/cannot be changed later/i)).toBeInTheDocument();
  });

  it("drops the picker entirely against a backend that sends no ladder", () => {
    // An older server answers the offer without `plans`. A radio group with no
    // options is worse than no radio group.
    render(<SignUpCard onSubmit={async () => {}} onSignIn={vi.fn()} />);
    expect(screen.queryByText(/which plan do you want/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create account" })).toBeInTheDocument();
  });
});
