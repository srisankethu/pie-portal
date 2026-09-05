/* The capture harness, on its own config.
 *
 * `playwright.config.ts` ignores `e2e/.shots/` so that `npm run e2e` stays the
 * two role-scoping assertions and nothing else. That ignore applies to an
 * explicit path too — `npx playwright test e2e/.shots` reports "No tests
 * found" — so the harness needs a config of its own rather than a flag.
 *
 * Everything that makes the app real is inherited: the same seeded backend and
 * the same dev server, started by `playwright.config.ts`'s `webServer`.
 *
 *   npx playwright test -c playwright.shots.config.ts
 */
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testDir: "./e2e/.shots",
  testIgnore: undefined,
  // Fourteen screens photographed twice over, per role: the default 60s is a
  // per-test budget written for a two-assertion spec.
  timeout: 600_000,
});
