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
 */
import { useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

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
  footer?: string;
  defaultEmail?: string;
}) {
  const [email, setEmail] = useState(defaultEmail);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!password) return setError("Enter your password.");
    setBusy(true);
    try {
      await onSubmit(email, password);
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

        {/* `role="alert"` so a failed sign-in is announced. The plain div this
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
          {busy ? "Signing in…" : submitLabel}
        </Button>

        {footer && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 3 }}>
            {footer}
          </Typography>
        )}
      </Paper>
    </Box>
  );
}
