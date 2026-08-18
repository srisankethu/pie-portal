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
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import TextField from "@mui/material/TextField";

import { AuthCard } from "./AuthCard";

export function SignInCard({
  title,
  blurb,
  submitLabel,
  onSubmit,
  notice,
  footer,
  onSignUp,
  defaultEmail = "",
}: {
  title: string;
  blurb: string;
  submitLabel: string;
  /** Throws to show the message; resolves to hand control to the caller. */
  onSubmit: (email: string, password: string) => Promise<void>;
  notice?: string | null;
  footer?: React.ReactNode;
  /** The way to the other door, where the deployment has one.
   *
   *  This card owns the sentence rather than the caller passing it in, because
   *  every surface that shows a sign-in form needs the same one and a caller
   *  that forgets it produces exactly the screen this prop was added to fix: a
   *  form for an account you cannot get, whose only advice is to ask an owner
   *  who does not exist. The sign-up form was reachable from a single button on
   *  the landing page, so arriving here any other way — a shared link, an
   *  expired session, the "Sign in" in the nav — was a dead end.
   *
   *  Absent where sign-up is off (`SELF_SERVE_SIGNUP`, the default), and then
   *  the caller's footer stands alone: on a single-tenant install "ask whoever
   *  runs this" is the whole truth. */
  onSignUp?: () => void;
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
      footer={
        onSignUp ? (
          // Two blocks, not one paragraph. They answer different people — a
          // business that has never used PIE, and somebody joining one that
          // has — and run together they read as a single wall of small print
          // that neither reader finishes.
          <>
            <Box>
              New to PIE?{" "}
              <Link component="button" type="button" onClick={onSignUp} underline="hover">
                Create your organization
              </Link>
              {" — free, and you choose the plan you want."}
            </Box>
            {footer ? <Box sx={{ mt: 1.5 }}>{footer}</Box> : null}
          </>
        ) : (
          footer
        )
      }
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
