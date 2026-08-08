# Field phase 0 — what was measured, and how

Screenshots and measurements for the branch `claude/field-phase-0`. They exist
because "the price field is reachable now" is a claim and a screenshot is not.

Everything here was taken against the app running locally — the Vite dev server
on `:5173` for the screens, the production build served from `frontend/dist` for
the payload figures — signed in through the sign-in form as `r.nair@sanketh.in`,
role `SALESPERSON`, in Chromium at a **412 × 915 Pixel 7 viewport** with
`isMobile`, touch and a 2× device pixel ratio.

`before-*` is `origin/main` at `9f9b7e8`; `after-*` is this branch. The same
script produced both, so the pair differs only in the code.

| File | What it shows |
|---|---|
| `before-home.png` / `after-home.png` | the landing screen for a salesperson |
| `before-decisions.png` / `after-decisions.png` | the decision queue's filter chips |
| `before-quote-lines.png` / `after-quote-lines.png` | the quote's lines at 412px |
| `after-quote-lines-viewport.png` | the same, at real viewport height rather than as one long page |
| `before-quote-lines-1440.png` / `after-quote-lines-1440.png` | the desktop grid, which this branch does not change |
| `before-measurements.json` / `after-measurements.json` | the numbers behind the screenshots |
| `before-payload.json` / `after-payload.json` | JS bytes a phone downloads, from the production build |

## The resolution engine is stubbed in these shots

`pie-parser` is a private submodule this container cannot clone, so every RFQ
line resolves to `PIE_DOWN` and the supply column reads "awaiting PIE". That is
a fixture problem and not the behaviour under test — the finding is about which
*columns* reach the screen — but a screenshot of five unresolved lines is weak
evidence about a screen whose job is resolved ones.

So the backend serving these screenshots ran with `PieService.resolve` replaced
by a stub returning a canned `EXACT` match against the four products
`app.demo` seeds. Nothing in the repository is patched: the stub lives in the
throwaway harness, and `before` and `after` were both taken against it.

## What `*-measurements.json` records

Read out of the live DOM at 412px, per screen:

- `gridContentWidth` / `gridBoxWidth` — how wide the grid's columns are against
  the box they are in.
- `priceHeader` — the bounding box of the **Quoted ₹** column header, and
  whether it is within the viewport at all.
- `gridPriceCells` / `gridPriceCellsOnScreen` — editable rate cells in the DOM,
  and how many of them a person could actually reach.
- `lineCards` / `cardPriceInputs` / `cardPriceInputsOnScreen` — the same
  question for the card rendering.
- `controlsUnder44` — every visible chip, button, icon button and input field
  shorter than 44px, with its label.
- `priceTyped` — a rate typed into the first line and read back, so the claim is
  exercised rather than inspected.
