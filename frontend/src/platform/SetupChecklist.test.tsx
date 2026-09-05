// What this panel must keep doing, and — mostly — what it must keep *not*
// doing.
//
// It had no tests at all, and that is the wrong shape of gap to leave, because
// almost everything worth pinning here is a branch that renders nothing. Three
// of them return null from one line: the request failed, the request has not
// answered yet, and the required steps are done. A silent branch that starts
// firing, or stops, looks exactly like correct behaviour from the outside —
// the panel is simply absent on the morning somebody signs up, or permanent on
// every morning after, and nothing else in the suite would notice either.
//
// The fourth thing is the role split, and it is the one a tidy-up would most
// plausibly reverse. `canAct` gates the Open buttons and nothing else: the
// panel itself is shown to a salesperson on purpose, because it is the honest
// reason every screen behind it is empty. The component says so in its own
// docstring ("It does not offer what the reader cannot do"), which is the
// weakest place for a deliberate decision to live, so it is asserted here as
// intended behaviour rather than left to a comment somebody may read as an
// oversight.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { SetupChecklist } from "./SetupChecklist";
import type { OnboardingStep, OnboardingView, PlatformSession, Role } from "./types";

const { onboarding } = vi.hoisted(() => ({ onboarding: vi.fn() }));
vi.mock("./api", () => ({ papi: { onboarding } }));

function session(role: Role = "OWNER"): PlatformSession {
  return {
    token: "t", role, name: "O", user_id: "u1", organization_id: "org_x",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

/** The four steps the server actually sends, in the order `onboarding.checklist`
 *  sends them, with `done` supplied per test.
 *
 *  Real titles and real routes rather than invented ones, because two of the
 *  assertions below are about *which* step carries an Open button and *where*
 *  it points. A fixture with made-up routes would go green while the panel sent
 *  a new owner to the wrong screen, which is the whole of what this control
 *  does. `required` is the server's flag: `policy` and `team` are the two a
 *  working platform does not need. */
function steps(done: Partial<Record<OnboardingStep["key"], boolean>> = {}): OnboardingStep[] {
  return [
    { key: "connect", title: "Connect your Zoho Books company",
      detail: "No Zoho Books company is connected yet. Everything this platform "
              + "reports is read from your books.",
      done: !!done.connect, required: true, route: "/data" },
    { key: "history", title: "Pull your trading history",
      detail: "Nothing has been pulled yet.",
      done: !!done.history, required: true, route: "/data" },
    { key: "policy", title: "Set your margin floors",
      detail: "Running on the default thresholds.",
      done: !!done.policy, required: false, route: "/settings" },
    { key: "team", title: "Add your team",
      detail: "Only your account so far.",
      done: !!done.team, required: false, route: "/settings" },
  ];
}

/** A checklist payload, with `complete` and the two counts derived exactly as
 *  `app/onboarding.py` derives them.
 *
 *  Derived rather than passed in on purpose: a hand-set `complete` would let a
 *  test assert a combination the server cannot send, and `complete` counting
 *  the *required* steps only is the whole of the branch that makes this panel
 *  disappear. A fixture free to disagree with the server about that would pin
 *  the wrong thing convincingly. */
function view(done: Partial<Record<OnboardingStep["key"], boolean>> = {}): OnboardingView {
  const all = steps(done);
  const required = all.filter((s) => s.required);
  return {
    steps: all,
    complete: required.every((s) => s.done),
    remaining: all.filter((s) => !s.done).length,
    remaining_required: required.filter((s) => !s.done).length,
  };
}

/** Mounted with its own client, so one test's cache cannot answer the next.
 *
 *  A `MemoryRouter` because every Open button is a `RouterLink` — §9 of
 *  `docs/ui-standards.md`, and the reason the assertions below ask for a `link`
 *  role rather than a `button` one. */
function mount(role: Role) {
  // Deliberately no `gcTime: 0`, for the reason `TrialNotice.test.tsx` gives at
  // length: a query whose observers are mid-transition is then collected
  // between resolving and rendering, and the component reads `undefined`.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <SetupChecklist session={session(role)} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return client;
}

/** Wait for the *query* to settle, not for the mock to have been called.
 *
 *  Most of this file asserts an absence, and an absence only means anything
 *  once the data has arrived and the component has decided against rendering:
 *  "not there yet" and "deliberately not rendered" are the same empty DOM. */
async function settled(client: QueryClient, status: "success" | "error") {
  await waitFor(() =>
    expect(client.getQueryCache().find({ queryKey: ["onboarding", "org_x"] })
      ?.state.status).toBe(status));
}

async function show(v: OnboardingView, role: Role = "OWNER") {
  onboarding.mockClear();
  onboarding.mockResolvedValue(v);
  await settled(mount(role), "success");
}

async function refused(role: Role = "OWNER") {
  onboarding.mockClear();
  onboarding.mockRejectedValue(new Error("onboarding is unreachable"));
  await settled(mount(role), "error");
}

// ── the three silent branches ────────────────────────────────────────────────
//
// One line decides all three, and each is silent for its own reason. Pinned
// separately because the reasons are separate: a change that made the failure
// loud would be wrong for a different argument than a change that made the
// wait visible.
describe("SetupChecklist, when it says nothing", () => {
  it("shows nothing at all while the checklist is still in flight", async () => {
    // No skeleton, and no reserved height. The panel's own absence is the
    // normal state for every workspace that finished setting up months ago, so
    // a placeholder here would make their home page jump on every load — the
    // cost lands on the readers this panel is not for.
    onboarding.mockClear();
    onboarding.mockReturnValue(new Promise(() => {}));
    mount("OWNER");
    // Asked, and waiting — which is what makes this the loading branch rather
    // than a component that never fetched.
    await waitFor(() => expect(onboarding).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: /finish setting up/i })).not.toBeInTheDocument();
    // Covers the progress bar and any shimmer somebody might reach for later.
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("stays silent when the checklist itself fails to load", async () => {
    // The one place in the app where swallowing a failure is right. This panel
    // is scaffolding for a tenant that has not finished setting up, so "the
    // setup checklist did not load" would sit at the top of the first screen
    // for the several hundred days after it stops applying to anybody.
    await refused();
    expect(screen.queryByRole("heading", { name: /finish setting up/i })).not.toBeInTheDocument();
    // Not even a quiet one: the panel has a real `Alert` in it for a non-owner,
    // so an alert appearing here would be a genuine change of behaviour.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disappears once the *required* steps are done, with work still outstanding", async () => {
    // It does not nag. `complete` counts the required steps only, so the panel
    // goes while the margin policy and the team are still untouched — a
    // permanent banner asking for a margin policy is one people learn to look
    // past, and then the one that matters is looked past too.
    const v = view({ connect: true, history: true });
    // Stated rather than implied: two steps are genuinely still open when it
    // vanishes. That is the tension `docs/ui-followups.md` P17 records — a
    // reader can see "2 of 4 done" and then watch the panel go — and it is a
    // product question about what the bar should measure, not a counting bug
    // for the next person to "fix" here.
    expect(v.remaining).toBe(2);
    await show(v);
    expect(screen.queryByRole("heading", { name: /finish setting up/i })).not.toBeInTheDocument();
  });

  it("speaks while a required step is open, even with a recommended one done", async () => {
    // The converse of the branch above, and the half that would go missing if
    // `complete` were ever read as "everything". A finished optional step must
    // not retire the panel.
    await show(view({ policy: true }));
    expect(screen.getByRole("heading", { name: /finish setting up/i })).toBeInTheDocument();
  });
});

// ── what it lists ────────────────────────────────────────────────────────────
describe("SetupChecklist, what it lists", () => {
  it("lists every step the server sent, with the reason each is not done", async () => {
    await show(view());
    for (const s of steps()) {
      expect(screen.getByText(s.title)).toBeInTheDocument();
    }
    // `detail` is the field that stops somebody opening the wrong screen —
    // "not done" without a reason is what sends them there.
    expect(screen.getByText(/No Zoho Books company is connected yet/)).toBeInTheDocument();
  });

  it("carries the state as a shape with a name, not only as a colour", async () => {
    // ui-standards §6. `titleAccess` is what puts the tick and the ring into
    // the accessibility tree; without it a finished step and an unfinished one
    // are the same silence, and "which of these have I done" is the only
    // question this panel asks.
    await show(view({ connect: true }));
    expect(screen.getAllByTitle("Done")).toHaveLength(1);
    expect(screen.getAllByTitle("Still to do")).toHaveLength(3);
  });

  it("chips the two steps a working platform does not need", async () => {
    await show(view());
    expect(screen.getAllByText("Recommended")).toHaveLength(2);
    // Which two, not only how many. The chip sits beside the title inside the
    // row's own heading line, so the title's parent is that line.
    expect(within(screen.getByText("Set your margin floors").parentElement!)
      .getByText("Recommended")).toBeInTheDocument();
    expect(within(screen.getByText("Connect your Zoho Books company").parentElement!)
      .queryByText("Recommended")).not.toBeInTheDocument();
  });

  it("takes optional from the server's flag rather than knowing which steps are optional", async () => {
    // The client holding its own list of which steps are optional is the
    // second copy that goes stale the first time the server moves one.
    await show({ ...view(), steps: steps().map((s) => ({ ...s, required: true })) });
    expect(screen.queryByText("Recommended")).not.toBeInTheDocument();
  });
});

// ── the owner-only actions ───────────────────────────────────────────────────
describe("SetupChecklist, who can act on it", () => {
  it("gives an owner a way into each unfinished step, and says which is which", async () => {
    await show(view());
    // A link, not a button: §9 says anything that goes somewhere is an anchor
    // whose path comes from `route.ts`, so ctrl-click and "open in a new tab"
    // work and a screen reader is told it is a destination.
    expect(screen.getAllByRole("link")).toHaveLength(4);
    // Four rows and four visible "Open"s: without the label a screen reader
    // listing the controls on this page hears the same word four times.
    expect(screen.getByRole("link", { name: "Open: Connect your Zoho Books company" }))
      .toHaveAttribute("href", "/data");
    expect(screen.getByRole("link", { name: "Open: Set your margin floors" }))
      .toHaveAttribute("href", "/settings");
  });

  it("offers nothing on a step already done", async () => {
    await show(view({ connect: true }));
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.queryByRole("link", { name: /Connect your Zoho Books company/ }))
      .not.toBeInTheDocument();
  });

  it("offers nobody a way to dismiss it, because there is nothing to store", async () => {
    // Every step is derived from real rows on every request, so a dismiss
    // would be a stored flag saying setup was finished when it was not —
    // exactly the defect `app/onboarding.py` refuses to keep a column for. The
    // panel removes itself or it stays; the only controls in it are the four
    // ways in, and those are links.
    await show(view());
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("tells an owner nothing about owner actions", async () => {
    // The "These are owner actions" alert is addressed to somebody who cannot
    // act. An owner reading it beside four working ways in would be told they
    // cannot do the thing they are about to do.
    await show(view());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still shows a salesperson the panel — that is the point of it", async () => {
    // Deliberate, and documented in the component. A salesperson whose company
    // has connected nothing is looking at a dozen correct empty states, and
    // this is the only thing on the screen that says why. Hiding it from the
    // role that cannot act would leave them with a product that looks broken.
    await show(view(), "SALESPERSON");
    expect(screen.getByRole("heading", { name: /finish setting up/i })).toBeInTheDocument();
    for (const s of steps()) {
      expect(screen.getByText(s.title)).toBeInTheDocument();
    }
  });

  it("sends a salesperson nowhere they would be refused", async () => {
    await show(view(), "SALESPERSON");
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/These are owner actions/i);
    // Information, not a warning. Nothing is wrong with this reader's screen
    // and nothing is being asked of them, so raising the severity would be
    // alarming somebody who has no lever to pull.
    expect(alert.className).toMatch(/Info/);
  });

  it("treats a manager as a reader too, because the server does", async () => {
    // `canAct` is `role === "OWNER"`, and connecting books and setting floors
    // are owner endpoints. A manager offered an Open button would be walked to
    // a screen that refuses them, which is the one thing this panel's docstring
    // says it does not do.
    await show(view(), "SALES_MANAGER");
    expect(screen.getByRole("heading", { name: /finish setting up/i })).toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/These are owner actions/i);
  });
});

// ── the progress figure ──────────────────────────────────────────────────────
//
// One count, rendered twice — as a tally beside the heading and as the bar's
// value. They are computed from the same two locals, and the reason to pin
// them together is that the tally exists precisely because the bar's figure
// used to live only in an `aria-label`, where no sighted reader could check it.
describe("SetupChecklist, the progress figure", () => {
  it("counts every step it lists, recommended ones included", async () => {
    await show(view({ connect: true }));
    expect(screen.getByText("1 of 4 done")).toBeInTheDocument();
    // The denominator is the list under it. A figure measuring a different set
    // from the rows it sits above would be worse than no figure — which is
    // what `remaining_required` would have made it, and the payload carries
    // that field for anyone who decides otherwise.
    expect(screen.getByRole("progressbar", { name: "Setup progress" }))
      .toHaveAttribute("aria-valuenow", "25");
  });

  it("counts a finished recommended step as done, because it is listed", async () => {
    await show(view({ connect: true, policy: true }));
    expect(screen.getByText("2 of 4 done")).toBeInTheDocument();
    // Bar and tally from the same pair of locals: they cannot drift, and this
    // is what would catch it if somebody gave one of them its own source.
    expect(screen.getByRole("progressbar", { name: "Setup progress" }))
      .toHaveAttribute("aria-valuenow", "50");
  });

  it("reads zero for a tenant that has just signed up", async () => {
    // A brand-new tenant. Zero is a real answer here and the bar must show it
    // — an empty bar with "0 of 4 done" beside it is the state this panel was
    // written for.
    await show(view());
    expect(screen.getByText("0 of 4 done")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Setup progress" }))
      .toHaveAttribute("aria-valuenow", "0");
  });
});
