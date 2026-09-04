/** The form the price list used to be, and the demo request it is now.
 *
 * The plans section named three tiers and printed a figure under two of them.
 * What a distributor actually pays turns on how many companies they run, which
 * ERP each of those sits on, and how much catalogue there is to build — none
 * of which a panel knows, and all of which it was answering anyway. The
 * figures went first and the panels followed: the public site names no plan at
 * all now, and this is what every "Book a demo" button on it opens.
 *
 * The fields barely changed, because they were already the right ones — who
 * you are, how to reach you, and what you run. What went is the question that
 * only made sense beside three panels ("Which plan are you asking about?"),
 * and the lead paragraph that answered a pricing question the page no longer
 * raises.
 *
 * It is rendered by `ContactModal`, which owns *how it opens* and nothing else:
 * the fields, the copy and the request are here, once. A dialog with its own
 * copy of a contact form is two forms that answer the same question
 * differently, and the second one is the one nobody updates.
 *
 * Three things about it are deliberate:
 *
 * **It posts to a real endpoint.** `POST /api/v1/contact` records the enquiry
 * where an operator reads it (`python -m app.contact`, and the plan queue in
 * `python -m app.entitlements requests`). A form that opened a mail client, or
 * one that swallowed the message and said thank you, would be the same defect
 * as the "Book a demo" button that pointed at a placeholder — invisible to
 * whoever maintains the page and the whole experience for whoever used it.
 *
 * **It stays renderable on the server.** `prerender.tsx` bakes this page as
 * static markup. The dialog means these fields are no longer *in* that
 * document — nothing of them is rendered until somebody opens it — but the
 * rule still holds and is worth keeping: nothing here may read `window` or
 * fetch while rendering, because the day this form goes back on the page is
 * not the day to discover it cannot. The fields are ordinary inputs inside a
 * real `<form>`; state only ever changes in response to a person, and the
 * request happens on submit.
 *
 * **It says what it is.** Submitting creates no account, licenses no plan,
 * charges nothing and connects nothing to the sender's books — and the copy
 * beside the button says so rather than leaving a stranger to wonder what they
 * just agreed to.
 *
 * It uses `authInit` from `src/authFetch.ts` rather than `platform/api.ts`.
 * That module is the *signed-in* app's transport: it carries session storage,
 * a 401 handler and the currency side effects of a login, none of which a
 * public page has any use for and all of which would land in the prerender's
 * import graph. `authFetch` is the half both apps share, which is exactly why
 * it sits outside `platform/`.
 */
import { useState } from "react";

import { authInit } from "../authFetch";

/** The plans are deliberately not here any more.
 *
 *  This form asked "Which plan are you asking about?" over a list of three
 *  tiers, because it used to be the foot of a plans section. The public site
 *  states no price and names no plan now — that conversation happens with a
 *  person, after the demo, when there is something to base it on — so the
 *  picker went with the section.
 *
 *  The wire field survives and is always `null`. The server takes
 *  `plan: Optional[str]` and distinguishes "did not say" from an answer it
 *  does not recognise, so sending nothing is a value it already understands;
 *  a visitor who does have a plan in mind writes it in the message box, where
 *  a person reads it.
 */

/** What the form is doing. `failed` keeps the server's own sentence: it is
 *  written for a person and is more useful than anything this component could
 *  say instead. */
type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent" }
  | { kind: "failed"; message: string };

export function ContactForm() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (status.kind === "sending") return;
    const data = new FormData(e.currentTarget);
    const body = {
      name: String(data.get("name") ?? "").trim(),
      company: String(data.get("company") ?? "").trim(),
      email: String(data.get("email") ?? "").trim(),
      phone: String(data.get("phone") ?? "").trim(),
      erp: String(data.get("erp") ?? "").trim(),
      // Always "did not say" — see the note above. `null` rather than `""`
      // because the server distinguishes an unanswered question from an answer
      // it does not recognise, and refuses the second.
      plan: null,
      message: String(data.get("message") ?? "").trim(),
    };
    setStatus({ kind: "sending" });
    try {
      const res = await fetch("/api/v1/contact", authInit({
        method: "POST",
        body: JSON.stringify(body),
      }));
      if (!res.ok) {
        // The server's refusals are sentences meant for the person reading
        // them — "That does not look like an email address" — so they are
        // shown rather than replaced with a generic failure.
        const detail = await res.json().then(
          (j) => (typeof j?.detail === "string" ? j.detail : ""),
        ).catch(() => "");
        setStatus({
          kind: "failed",
          message: detail || "That did not send. Try again in a moment.",
        });
        return;
      }
      setStatus({ kind: "sent" });
    } catch {
      // A network failure, which is the one case where the visitor has
      // somewhere else to go: say so and give them the address.
      setStatus({
        kind: "failed",
        message: "That did not send — check your connection and try again.",
      });
    }
  };

  if (status.kind === "sent") {
    return (
      <div className="lp-panel lp-form-done" role="status">
        {/* Keeps the id the heading above it carries: the dialog in
            `ContactModal` is labelled by it, and a thank-you that dropped the
            id would leave the dialog unnamed at the one moment a screen reader
            is being told what just happened. */}
        <h3 id="lp-form-head">Thanks — that reached us.</h3>
        <p>
          We read every one of these ourselves and come back at the address you
          gave, usually within a working day, to arrange a time. Nothing was
          charged, no account was created, and nothing has been connected to
          your books.
        </p>
      </div>
    );
  }

  const sending = status.kind === "sending";
  return (
    <form className="lp-panel lp-form" onSubmit={submit}>
      <h3 id="lp-form-head">Book a demo</h3>
      <p className="lp-form-lead">
        Tell us roughly where you are and we will come back to arrange a
        working session on your own numbers — the quote desk, your margin
        floors, and what your history already holds. It reaches a person, not a
        sequence.
      </p>

      <div className="lp-form-grid">
        <label className="lp-field">
          <span>Your name</span>
          <input name="name" type="text" autoComplete="name" required
                 maxLength={255} disabled={sending} />
        </label>
        <label className="lp-field">
          <span>Company</span>
          <input name="company" type="text" autoComplete="organization"
                 maxLength={255} disabled={sending} />
        </label>
        <label className="lp-field">
          <span>Email</span>
          <input name="email" type="email" autoComplete="email" required
                 maxLength={255} disabled={sending} />
        </label>
        <label className="lp-field">
          <span>Phone <em>optional</em></span>
          <input name="phone" type="tel" autoComplete="tel" maxLength={64}
                 disabled={sending} />
        </label>
        <label className="lp-field">
          <span>Which ERP do you run? <em>optional</em></span>
          <input name="erp" type="text" list="lp-erp-list" maxLength={64}
                 disabled={sending} placeholder="Zoho Books, Prophet 21, …" />
          {/* A suggestion list, not a constraint: the point of asking is the
              books nobody has a connector for yet, and a picker with seven
              options would tell that visitor they are not wanted. */}
          <datalist id="lp-erp-list">
            <option value="Zoho Books" />
            <option value="NetSuite" />
            <option value="Dynamics 365 Business Central" />
            <option value="Acumatica" />
            <option value="Epicor Prophet 21" />
            <option value="Sage" />
          </datalist>
        </label>
      </div>

      <label className="lp-field">
        <span>Anything else we should know? <em>optional</em></span>
        <textarea name="message" rows={4} maxLength={4000} disabled={sending}
                  placeholder="How many companies, roughly how many items, what you are trying to fix." />
      </label>

      {status.kind === "failed" && (
        <p className="lp-form-error" role="alert">{status.message}</p>
      )}

      <div className="lp-form-actions">
        <button className="lp-btn solid" type="submit" disabled={sending}>
          {sending ? "Sending…" : "Send this to us"}
        </button>
        <span className="lp-fine">
          No card, no account, nothing charged, and nothing connected to your
          ERP — this sends us a message and nothing else.
        </span>
      </div>
    </form>
  );
}
