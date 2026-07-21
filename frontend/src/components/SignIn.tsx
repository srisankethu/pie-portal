import { useState } from "react";
import { api } from "../api";
import type { Session } from "../types";

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [email, setEmail] = useState("r.nair@sanketh.in");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!password) {
      setError("Enter your password.");
      return;
    }
    setBusy(true);
    try {
      const s = await api.login(email, password);
      onSignedIn(s);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin-wrap">
      <form className="signin" onSubmit={submit}>
        <h6 className="text-muted">Sanketh</h6>
        <h2>Quote Builder</h2>
        <p className="text-muted" style={{ marginBottom: "var(--space-6)" }}>
          Enter your credentials. Your account determines your view.
        </p>
        <div className="field" style={{ marginBottom: "var(--space-3)" }}>
          <label>Email</label>
          <input
            className="input"
            value={email}
            autoFocus
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <div className="err">{error}</div>}
        <button className="btn btn-primary" style={{ width: "100%", marginTop: "var(--space-4)" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="demo">
          Demo accounts — <b>r.nair@sanketh.in</b> (salesperson) · <b>s.menon@sanketh.in</b>{" "}
          (management). Any password.
        </div>
      </form>
    </div>
  );
}
