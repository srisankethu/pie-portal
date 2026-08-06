import React from "react";
import { createRoot } from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

// Self-hosted, not the Google Fonts CDN. The <link> in index.html failed on any
// network that cannot reach fonts.gstatic.com — an air-gapped shop floor, a
// captive portal, a plant with the domain blocked — and the failure is silent:
// every heading falls back to Arial and the screens look broken rather than
// offline. Bundling costs ~40kB and removes the dependency.
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "@fontsource/barlow-condensed/700.css";

import Root from "./Root";
import theme from "./theme";
// After the theme: `CssBaseline` emits the design tokens, and these rules read
// them. Import order decides nothing about custom properties at runtime, but it
// states the dependency for whoever reads this next.
import "./styles.css";

/** Query defaults, chosen against what this data actually is.
 *
 * Every screen here reads a projection that is rebuilt at the end of a sync,
 * not a live feed. A one-minute `staleTime` is therefore not a compromise on
 * freshness — a shorter one re-asks a question whose answer provably cannot
 * have moved, and re-scans the book to get the same number back.
 *
 * `retry: 1` rather than the default 3. A failed insight request is usually a
 * 401 after a token expires or a 503 from a database mid-migration, and
 * retrying either three times delays the honest error the screens are designed
 * to show — `LoadFailed` and the empty-state panels exist precisely so a
 * failure is stated rather than spun on.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        {/* Toasts stack rather than replace each other. The undo offer after
            an action is the one that must not be swallowed: acting on two
            decisions quickly used to leave only the second one undoable. */}
        <SnackbarProvider
          maxSnack={3}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
          autoHideDuration={6000}
        >
          <Root />
        </SnackbarProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
