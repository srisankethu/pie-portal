import { useState } from "react";
import App from "./App";
import PlatformApp from "./platform/PlatformApp";

/**
 * Single application, two surfaces. The Commercial Decision Platform is the
 * primary experience; the Quote Builder is preserved as the quote-intelligence
 * surface, reached from the platform's "Quotes" area. Not a second app — one
 * build, one shell decision here.
 */
export default function Root() {
  const [mode, setMode] = useState<"platform" | "quotes">("platform");

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
          <button className="btn btn-ghost btn-sm" onClick={() => setMode("platform")}>
            ← Back to decisions
          </button>
          <span className="text-muted" style={{ fontSize: 12 }}>
            Quote intelligence · Quote Builder
          </span>
        </div>
        <App
          onOpenPlatform={(hash) => {
            window.location.hash = hash;
            setMode("platform");
          }}
        />
      </div>
    );
  }
  return <PlatformApp onOpenQuotes={() => setMode("quotes")} />;
}
