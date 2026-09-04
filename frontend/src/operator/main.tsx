/** The operator console's entry point.
 *
 * A second `createRoot` and a second bundle, not a route inside `PlatformApp`.
 * The reason is not tidiness: every screen in that app reads
 * `session.organization_id`, and this page's caller has none. Mounting the
 * console beside them would mean one application whose components sometimes
 * have a tenant and sometimes do not — the conflation
 * `docs/operator-console.md` refuses on the server, repeated on the client.
 *
 * It shares the theme and the component vocabulary and nothing else. No
 * `QueryClientProvider`: the console makes a handful of calls a session and
 * refetches on demand, so a cache would be one more thing that can be stale
 * while somebody grants a plan.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";

// The same self-hosted faces the app bundles, for the reason main.tsx gives:
// a console that falls back to Arial on a network that cannot reach
// fonts.gstatic.com looks broken rather than offline.
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-condensed/600.css";

import theme from "../theme";
import "../styles.css";
import { OperatorConsole } from "./OperatorConsole";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <OperatorConsole />
    </ThemeProvider>
  </React.StrictMode>,
);
