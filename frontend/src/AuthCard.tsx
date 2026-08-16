/** The shell every door in this product is drawn in.
 *
 * Its own file for the reason `Tip.tsx` has one: two things import it. There
 * are two ways in now — sign in with an account somebody gave you, and sign up
 * for one — and they are the same card with different fields inside. When the
 * card was a private detail of `SignInCard.tsx`, the second form would have been
 * a copy of the surround, and the copy is where the drift starts. That has
 * already happened once here: the Quote Builder's own sign-in form was a second
 * copy and still ended with "Any password works" long after that stopped being
 * true.
 *
 * The surround is all of it: the frame, the product mark, the heading, the
 * notice slot, the announced error, the submit button and its busy label. What
 * differs between the two doors is the fields and the words, so that is what
 * they pass in.
 */
import { useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";

export function AuthCard({
  title,
  blurb,
  submitLabel,
  busyLabel,
  onSubmit,
  notice,
  footer,
  children,
}: {
  title: string;
  blurb: string;
  submitLabel: string;
  /** What the button says while the request is in flight. */
  busyLabel: string;
  /** Throws to show the message; resolves to hand control to the caller. */
  onSubmit: () => Promise<void>;
  notice?: string | null;
  /** A string, or the richer footer the sign-up card needs. */
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await onSubmit();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: 3,
      }}
    >
      <Paper
        component="form"
        onSubmit={submit}
        variant="outlined"
        sx={{
          width: "min(430px, 94vw)",
          p: 4,
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-md)",
          background: "linear-gradient(135deg, var(--color-neutral-100), var(--color-bg))",
        }}
      >
        <Typography variant="h6" color="text.secondary">PIE</Typography>
        <Typography variant="h2" sx={{ mb: 1 }}>{title}</Typography>
        <Typography color="text.secondary" sx={{ mb: 3, fontSize: 14 }}>
          {blurb}
        </Typography>

        {notice && <Alert severity="info" sx={{ mb: 2 }}>{notice}</Alert>}

        {children}

        {/* `role="alert"` so a failed attempt is announced. The plain div this
            replaces gave a screen-reader user no signal at all: focus stayed in
            the field and nothing said why nothing had happened. */}
        {error && <Alert severity="error" role="alert" sx={{ mt: 2 }}>{error}</Alert>}

        <Button
          type="submit"
          variant="contained"
          fullWidth
          disabled={busy}
          sx={{ mt: 3, minHeight: 40 }}
        >
          {busy ? busyLabel : submitLabel}
        </Button>

        {footer && (
          <Typography
            variant="caption"
            color="text.secondary"
            component="div"
            sx={{ display: "block", mt: 3 }}
          >
            {footer}
          </Typography>
        )}
      </Paper>
    </Box>
  );
}
