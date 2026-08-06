import Button from "@mui/material/Button";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import App from "./App";
import PlatformApp from "./platform/PlatformApp";

/**
 * Single application, two surfaces. The Commercial Decision Platform is the
 * primary experience; the Quote Builder is preserved as the quote-intelligence
 * surface, reached from the platform's "Quotes" area. Not a second app — one
 * build, one shell decision here.
 *
 * The Quote Builder is a *mode*, not a route: it has no URL of its own because
 * it is one screen with a long-lived draft on it, and a back button that
 * discarded a half-priced quote would be worse than no back button. What it
 * *can* do is send you into the platform at a named account and item, which is
 * a real navigation and goes through the router.
 */
export default function Root() {
  const [mode, setMode] = useState<"platform" | "quotes">("platform");
  const navigate = useNavigate();

  if (mode === "quotes") {
    return (
      <div>
        <div
          style={{
            padding: "8px 20px",
            borderBottom: "1px solid var(--color-divider)",
            background: "var(--color-neutral-100)",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <Button variant="text" size="small" onClick={() => setMode("platform")}>
            ← Back to decisions
          </Button>
          <span className="text-muted" style={{ fontSize: 12 }}>
            Quote intelligence · Quote Builder
          </span>
        </div>
        <App
          onOpenPlatform={(path) => {
            navigate(path);
            setMode("platform");
          }}
        />
      </div>
    );
  }
  return <PlatformApp onOpenQuotes={() => setMode("quotes")} />;
}
