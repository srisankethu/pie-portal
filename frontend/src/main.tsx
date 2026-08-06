import React from "react";
import { createRoot } from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";

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

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Root />
    </ThemeProvider>
  </React.StrictMode>,
);
