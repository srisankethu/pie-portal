/** The sign-in card, shared by both surfaces.
 *
 * Lives outside `platform/` for the same reason `Tip.tsx` does: the Quote
 * Builder and the Decision Platform both need it, and two copies would explain
 * the same thing two ways. They already had — the Quote Builder's copy still
 * ended with "Any password works", which stopped being true when real password
 * authentication landed and is exactly the kind of sentence nobody re-reads
 * once it is on a screen they sign past every morning.
 *
 * What differs between the two is text and which API is called, so that is what
 * the props are. The field layout, the busy state, the error announcement and
 * the autocomplete hints are the same in both because there is no reason for
 * them to differ.
 *
 * The surround — frame, heading, notice, error, submit — is `AuthCard.tsx`,
 * because `SignUpCard.tsx` needs the identical one and a second copy of it is
 * how the drift above starts again.
 */
import { useState } from "react";
import TextField from "@mui/material/TextField";

import { AuthCard } from "./AuthCard";

export function SignInCard({
  title,
  blurb,
  submitLabel,
  onSubmit,
  notice,
  footer,
  defaultEmail = "",
}: {
  title: string;
  blurb: string;
  submitLabel: string;
  /** Throws to show the message; resolves to hand control to the caller. */
  onSubmit: (email: string, password: string) => Promise<void>;
  notice?: string | null;
  footer?: React.ReactNode;
  defaultEmail?: string;
}) {
  const [email, setEmail] = useState(defaultEmail);
  const [password, setPassword] = useState("");

  return (
    <AuthCard
      title={title}
      blurb={blurb}
      submitLabel={submitLabel}
      busyLabel="Signing in…"
      notice={notice}
      footer={footer}
      onSubmit={async () => {
        if (!password) throw new Error("Enter your password.");
        await onSubmit(email, password);
      }}
    >
      <TextField
        name="email"
        type="email"
        label="Email"
        autoComplete="username"
        fullWidth
        autoFocus
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        sx={{ mb: 2 }}
      />
      <TextField
        name="password"
        type="password"
        label="Password"
        autoComplete="current-password"
        fullWidth
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
    </AuthCard>
  );
}
