/** The other door: create the organization rather than sign in to one.
 *
 * Four fields, because four things are genuinely needed and none of them can be
 * inferred — the company (which names the tenant), the person (who becomes its
 * owner), the address (which is the sign-in identity), and a password they
 * choose. There is deliberately no "confirm password" field: it catches a typo
 * at the cost of a field on every sign-up, and the same typo is already
 * recoverable through a password reset, which this product has.
 *
 * The password rule is stated **before** it is broken rather than after. The
 * server's floor is ten characters, and a form that accepts a short one and
 * then refuses it has spent a round trip to teach a rule it could have shown.
 * The client check is a courtesy; `passwords.password_problem` is the rule.
 *
 * ── the plan question, and what it is not ───────────────────────────────────
 *
 * The form asks which plan the business wants, and every answer creates the
 * same account: the free Quote Desk. That is not a bug in the picker, it is the
 * product — `entitlements.py` is explicit that plans are set by the operator
 * and never by a tenant, "an owner who could set their own plan would not have
 * one", and there is no billing in this product at all. So the choice is
 * *recorded* (`organizations.requested_plan`, read by nothing that decides what
 * may be used) and an operator acts on it.
 *
 * Which makes the copy the load-bearing part. `TrialNotice.test.tsx` names the
 * failure to avoid: a control that implies a purchase is "a dead end shipped to
 * the one person most likely to press it". So the card says what actually
 * happens on submit, next to the control that does it, and the button says
 * "Create account" rather than anything that sounds like a checkout.
 *
 * The ladder itself comes from the server (`GET /api/v1/signup`) rather than
 * being written here, for the reason `entitlements.describe` gives about
 * `loses_on_expiry`: a client-side copy of the plan map goes stale the first
 * time a feature moves between tiers. Prices are not in it either — those are
 * marketing copy and live in exactly one place, the landing page.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormLabel from "@mui/material/FormLabel";
import Link from "@mui/material/Link";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { AuthCard } from "./AuthCard";
import type { SignupOffer } from "./platform/types";

/** Mirrors `passwords.MIN_PASSWORD_LENGTH`. The server still decides. */
const MIN_PASSWORD = 10;

export interface SignUpDetails {
  company: string;
  name: string;
  email: string;
  password: string;
  /** Which plan they asked for. Recorded, never granted — see the header. */
  plan: string;
}

export function SignUpCard({
  onSubmit,
  onSignIn,
  notice,
  offer,
  defaultPlan,
}: {
  /** Throws to show the message; resolves to hand control to the caller. */
  onSubmit: (details: SignUpDetails) => Promise<void>;
  onSignIn: () => void;
  notice?: string | null;
  /** What this deployment offers, straight from the server. The plan ladder and
   *  the trial length come from here so neither is written twice. */
  offer?: SignupOffer | null;
  /** Preselected plan, for arriving from a pricing panel. Ignored when it is
   *  not one the server offered — a stale link must not select nothing. */
  defaultPlan?: string;
}) {
  const [company, setCompany] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Shown only once the field has been left, so the rule is not an error
  // message on an empty form somebody has not started filling in yet.
  const [touched, setTouched] = useState(false);
  const tooShort = touched && password.length > 0 && password.length < MIN_PASSWORD;

  const plans = offer?.plans ?? [];
  // The plan every account actually starts on. From the server, because the
  // client asserting "free" would be the client deciding a plan.
  const lands = offer?.plan ?? "";
  const known = (p?: string) => (p && plans.some((o) => o.plan === p) ? p : "");
  const [plan, setPlan] = useState(() => known(defaultPlan) || known(lands));
  const chosen = plans.find((o) => o.plan === plan);
  const asksForMore = !!plan && !!lands && plan !== lands;

  return (
    <AuthCard
      title="Create your account"
      blurb={
        offer?.note ??
        "Your own organization, on the free Quote Desk. Connect Zoho Books when you are ready — nothing is read from your books until you do."
      }
      submitLabel="Create account"
      busyLabel="Creating…"
      notice={notice}
      onSubmit={async () => {
        if (!company.trim()) throw new Error("Enter your company name.");
        if (!name.trim()) throw new Error("Enter your name.");
        if (!email.trim()) throw new Error("Enter your email address.");
        if (password.length < MIN_PASSWORD) {
          throw new Error(`Password must be at least ${MIN_PASSWORD} characters.`);
        }
        await onSubmit({
          company: company.trim(), name: name.trim(), email, password, plan,
        });
      }}
      footer={
        <>
          You become the owner of this organization: you set the margin floors,
          add your team and decide what each role sees.{" "}
          <Link component="button" type="button" onClick={onSignIn} underline="hover">
            Already have an account?
          </Link>
        </>
      }
    >
      <TextField
        name="organization"
        label="Company name"
        autoComplete="organization"
        fullWidth
        autoFocus
        value={company}
        onChange={(e) => setCompany(e.target.value)}
        sx={{ mb: 2 }}
      />
      <TextField
        name="name"
        label="Your name"
        autoComplete="name"
        fullWidth
        value={name}
        onChange={(e) => setName(e.target.value)}
        sx={{ mb: 2 }}
      />
      <TextField
        name="email"
        type="email"
        label="Work email"
        autoComplete="username"
        fullWidth
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        sx={{ mb: 2 }}
      />
      <TextField
        name="password"
        type="password"
        label="Password"
        // `new-password`, not `current-password`: it is what tells a password
        // manager to offer to generate one rather than to fill the last one in.
        autoComplete="new-password"
        fullWidth
        value={password}
        onBlur={() => setTouched(true)}
        onChange={(e) => setPassword(e.target.value)}
        error={tooShort}
        helperText={
          tooShort
            ? `At least ${MIN_PASSWORD} characters.`
            : `At least ${MIN_PASSWORD} characters. A long phrase beats a short puzzle.`
        }
      />

      {/* Absent entirely on an older backend, which answers the offer without a
          ladder. A picker with no options is worse than no picker. */}
      {plans.length > 0 && (
        <FormControl sx={{ mt: 3, display: "block" }}>
          <FormLabel id="plan-label" sx={{ fontSize: 13.5 }}>
            Which plan do you want?
          </FormLabel>
          <RadioGroup
            aria-labelledby="plan-label"
            name="plan"
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            sx={{ mt: 1 }}
          >
            {plans.map((o) => (
              <FormControlLabel
                key={o.plan}
                value={o.plan}
                control={<Radio size="small" sx={{ alignSelf: "flex-start", pt: 0.5 }} />}
                sx={{ alignItems: "flex-start", mb: 1, mr: 0 }}
                label={
                  <Box sx={{ py: 0.25 }}>
                    <Typography sx={{ fontSize: 14, fontWeight: 600 }}>
                      {o.label}
                    </Typography>
                    <Typography color="text.secondary" sx={{ fontSize: 12.5 }}>
                      {o.summary}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </RadioGroup>
          {/* Said here, under the control, rather than in the small print: this
              form creates the same free account whichever rung is selected, and
              a person who reads only the thing they just clicked should still
              know that. */}
          <Typography color="text.secondary" sx={{ fontSize: 12.5, mt: 0.5 }}>
            {asksForMore
              ? `Your account starts on ${planLabel(plans, lands)} today and works `
                + `straight away. We record that you want ${chosen?.label ?? "more"} `
                + "and set it up with you — nothing is charged here."
              : "You can ask for more later. Nothing is charged here."}
          </Typography>
        </FormControl>
      )}
    </AuthCard>
  );
}

/** The label for a plan key, or the key itself if the server stopped sending it. */
function planLabel(plans: SignupOffer["plans"], key: string): string {
  return plans.find((o) => o.plan === key)?.label ?? key;
}
