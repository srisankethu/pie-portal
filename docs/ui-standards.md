# UI standards

A clean, modern enterprise application. Information density, clarity and speed
over decoration. Every component should help somebody make a decision quickly,
and the whole application should behave the same way everywhere.

This is a standing standard: it applies whenever UI is written or changed, not
only when somebody remembers it. Where an existing screen departs from it,
improve that screen **incrementally** while it is being touched for another
reason — preserving what it does and avoiding a redesign nobody asked for.

---

## 1. Layout

Material UI is the design system.

| Use | For |
|---|---|
| `Box` | general layout |
| `Stack` | vertical and horizontal spacing |
| `Grid` | responsive page layouts |
| `Container` | only where a page-width constraint is genuinely required |

Avoid unnecessary nesting. A `Box` inside a `Box` inside a `Stack` that all
share one purpose is one element.

## 2. Surfaces — `Paper` almost always

`Paper` is the default surface for everything on a dashboard: KPI tiles,
charts, filters, widgets, tables, action panels, summary sections, risk panels,
insights.

`Card` is reserved for a **self-contained business entity** — a customer, a
product, a purchase order, a sales order, a recommendation. Something with an
identity you could open, act on, or send to somebody.

> A `Card` used as a generic container is the most common way an enterprise UI
> starts looking like a consumer one. If the thing inside has no identity of its
> own, it is a `Paper`.

## 3. Tables

AG Grid Community, for all tabular data. Keep column sizing, filtering,
sorting, grouping and virtualization at enterprise grade, and keep every grid
responsive and performant.

Wide grids scroll inside their own box; the page never scrolls sideways.

## 4. Typography

One hierarchy, used consistently:

```
Page title  →  Section header  →  Widget title  →  Label  →  Value  →  Secondary metadata
```

No excessive sizes, and no per-screen invention. If a screen needs a size the
ramp does not have, the ramp is probably wrong — change it once, in the theme.

## 5. Buttons

`Button`, `IconButton`, and `ToggleButton` where a control genuinely toggles.

Few primary buttons. A screen with six contained buttons has no primary action,
because the eye cannot pick one. The primary action should be obvious without
being read.

## 6. Status

Standardised on `Chip`, `Alert`, `Badge` and `Tooltip`.

**Never custom coloured text.** Colour alone fails for a substantial minority of
readers, in greyscale print, and under forced-colours mode. A chip carries a
shape and a word as well as a hue; coloured text carries only the hue.

## 7. Feedback

`Snackbar`, `Dialog`, `CircularProgress`, `LinearProgress`, `Skeleton`.

No custom loading implementations. A hand-rolled shimmer is a second thing to
keep in step with the theme, and it is the one that gets forgotten.

## 8. Forms

`TextField`, `Select`, `Autocomplete`, `Checkbox`, `Switch`, date pickers.
Consistent spacing, and consistent validation behaviour — a field that
validates on blur next to one that validates on submit teaches nothing.

## 9. Navigation

`Drawer`, `AppBar`, `Breadcrumbs`, `Tabs`. Consistent throughout.

## 10. Reusable components

A pattern that appears more than once becomes a component. Composition over
duplication — the same rule as `CLAUDE.md` §2, applied to the interface.

These live in `frontend/src/platform/kit.tsx`:

| Component | What it is | What it replaced |
|---|---|---|
| `SectionHeader` | page and section headings, with an optional tip and actions | `.dp-head`, `.section-h`, hand-written `<h1>`/`<h3>` pairs |
| `MetricCard` | one figure with its label and optional movement | `.dp-count`, the stock KPI row |
| `ChartContainer` | a titled chart surface with its accessible fallback | `viz/Panel.tsx` `Panel` + `Figure` |
| `FilterPanel` | the controls above a list | `.acct-controls`, `.stock-filters`, `.seg-controls` |
| `EmptyState` | nothing to show, and **why** | `.dp-empty`, `Panel`'s empty branch |
| `LoadingState` | shaped skeletons that reserve the height | `.skeleton`, `.viz-skeleton` divs |
| `ErrorState` | it did not load, what went wrong, how to retry | `LoadFailed`, `.state-panel` |
| `StatusChip` | any state word — sync, approval, evidence, health | `.conf`, `.cx-badge`, `.st-*` spans |
| `PriorityChip` | a decision's band | `.pri` span |
| `CurrencyValue` | money, tabular, with an optional sign | bare `money()` in JSX |
| `PercentageValue` | a ratio as a percentage | bare `pct()` in JSX |
| `VarianceIndicator` | a movement, as an arrow **and** a word | `.wf-row-value.pos/.neg`, `.story-hero-value.up/.down` |
| `AuditTimeline` | an ordered trail of what happened | the trace list in `PlatformApp` |

`RiskCard`, `InsightCard`, `ActionCard` and `TrendCard` are `Card` — each wraps
a business entity with an identity, per §2.

Existing shared pieces stay and are used rather than duplicated:
`EntityName`/`EntitySource` (an imported record and its company),
`CompanyFilter` (narrowing a list to one book), `Tip`/`Labelled` (an explained
term), `DataGrid` (the AG Grid wrapper).

## 11. Styling

Use the theme. **Do not hardcode** colours, spacing, typography, border radius
or shadows. Reach for theme tokens and the `sx` prop.

`theme.ts` owns the palette and emits the `:root` custom properties that older
CSS reads, so a value changed there changes both. A literal in a component is a
value that will not follow.

## 12. Responsiveness

Every screen works on desktop, laptop and tablet. Avoid fixed widths unless the
content genuinely requires one (a chart measuring its own container does).

## 13. Accessibility

Keyboard navigation, visible focus, ARIA labels where the markup does not
already carry the meaning, sufficient contrast, and descriptive tooltips.

A chart carries a text summary and a table fallback — `ChartContainer` does
this so no chart has to remember to.

## 14. Code quality

Small, focused components. Presentation separated from business logic. Extract
a hook when logic is reused. Refactor a repeated pattern into a shared
component rather than copying it.

---

## Where this codebase stands

Honest, so the next person knows what they are walking into rather than
discovering it. Written after an audit, not from memory.

**Aligned:** the app shell (`AppBar` + `Drawer`), dialogs, snackbars, the grid
wrapper, the settings and connections forms, the theme itself. Every button is
MUI's — the `.btn` variant system is gone from the stylesheet, so there is
nothing left to fall back into. `Bp` is a `Paper` (the corner marks survive
behind a `marks` prop). The uncertainty panel is `ErrorState` or `Alert`
depending on which kind of not-knowing it is. Loading is `LoadingState`
everywhere; both hand-rolled shimmers are gone. Inline names inside charts are
`InlineLink`. Every figure whose colour came from a `viz.css` literal now takes
it from the theme, through `VarianceIndicator`.

**Where colour still carries meaning, and why that is correct.** These were
audited one by one rather than swept:

| Kept | Because |
|---|---|
| `.wf-fill`, `.viz-swatch`, `.journey-seg`, `.quad-*` | Chart marks. Colour is an *encoding* with a legend, which is the deliberate exception. |
| `.multiple-dir.up/.down` | The ▲/▼ glyph carries the direction; hue only reinforces it. |
| `.cadence-flag`, `.cadence-row.late` | "Overdue" is written out, and the flag has a border. |
| `.neg-figure-value` tone | A *level*, not a movement — an arrow would read as "rising". The minus sign in the value carries the sign; the tint is a second cue, and now a theme one. |

The distinction that decides it: **is the colour the only thing saying what this
means?** If a reader in greyscale loses the meaning, it is a defect. If they
lose only emphasis, it is fine.

**Not yet aligned:** nothing the current audit can name — with the caveat that
this has already proved to be a claim about the *audit*, not the codebase. The
pass that first said it had checked `.btn`, `.pri`, `.conf`, `.state-panel` and
`.skeleton`; the Quote Builder's `.toast`, a fixed-position div on a 2.4s timer,
was a custom feedback implementation this document forbids in as many words and
went unnoticed because it was not in that list. It is `notistack` now.

The lesson is in how to check, not what was missed: a sweep for *known* legacy
class names cannot find the one nobody wrote down. Grep the standard's
categories — feedback, loading, status, surfaces — against what the screens
actually render, rather than against a list of names from last time.

The rule stands regardless: **new UI follows this document, and any screen being
changed for another reason moves toward it.**
