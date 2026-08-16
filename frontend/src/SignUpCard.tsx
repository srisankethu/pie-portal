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
 */
import { useState } from "react";
import Link from "@mui/material/Link";
import TextField from "@mui/material/TextField";

import { AuthCard } from "./AuthCard";

/** Mirrors `passwords.MIN_PASSWORD_LENGTH`. The server still decides. */
const MIN_PASSWORD = 10;

export interface SignUpDetails {
  company: string;
  name: string;
  email: string;
  password: string;
}

export function SignUpCard({
  onSubmit,
  onSignIn,
  notice,
}: {
  /** Throws to show the message; resolves to hand control to the caller. */
  onSubmit: (details: SignUpDetails) => Promise<void>;
  onSignIn: () => void;
  notice?: string | null;
}) {
  const [company, setCompany] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Shown only once the field has been left, so the rule is not an error
  // message on an empty form somebody has not started filling in yet.
  const [touched, setTouched] = useState(false);
  const tooShort = touched && password.length > 0 && password.length < MIN_PASSWORD;

  return (
    <AuthCard
      title="Create your account"
      blurb="Your own organization, on the free Quote Desk. Connect Zoho Books when you are ready — nothing is read from your books until you do."
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
        await onSubmit({ company: company.trim(), name: name.trim(), email, password });
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
    </AuthCard>
  );
}
