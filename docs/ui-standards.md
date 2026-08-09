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

AG Grid Community, for all tabular data — through `platform/DataGrid.tsx`, which
is the wrapper that carries this app's theme, its pagination defaults, its
responsive column hiding and its empty state. Keep column sizing, filtering,
sorting and virtualization at enterprise grade, and keep every grid responsive
and performant.

**Where the line is.** A `<table>` is right for a *fact panel* — a label and a
value, four rows, sized by the shape of the screen — and for the accessible
table under a chart (§13). It is wrong the moment the row count is set by the
size of the business: an RFQ, a customer list, a user directory. That is the
test to apply, and `platform/DataGrid.tsx` states it at the top of the file.

**Extend the wrapper; do not open `AgGridReact` beside it.** Selection, row
identity, row classes, editable cells and Enter-to-open all live on
`DataGridProps` because the quote grid needed them — the next grid that needs
one of them gets it for free, and a second wrapper is how two grids end up
disagreeing about what a selected row looks like.

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
| `FilterPanel` | the controls above a list | `.acct-controls`, `.stock-filters`, `.seg-controls` |
| `EmptyState` | nothing to show, and **why** | `.dp-empty`, `Panel`'s empty branch |
| `LoadingState` | shaped skeletons that reserve the height | `.skeleton`, `.viz-skeleton` divs |
| `ErrorState` | it did not load, what went wrong, how to retry | `LoadFailed`, `.state-panel` |
| `StatusChip` | any state word — sync, approval, evidence, health | `.conf`, `.cx-badge`, `.st-*` spans |
| `PriorityChip` | a decision's band | `.pri` span |
| `CurrencyValue` | money, tabular, with an optional sign | bare `money()` in JSX |
| `PercentageValue` | a ratio as a percentage | bare `pct()` in JSX |
| `VarianceIndicator` | a movement, as an arrow **and** a word | `.wf-row-value.pos/.neg`, `.story-hero-value.up/.down` |
| `HumanLog` | the trail of what a person did to a decision, and who | the inline `human_action` block in `PlatformApp` |

Two rows of this table used to name components that were never written —
`ChartContainer` and `AuditTimeline`. A standard that lists a component nobody
can import is worse than one that lists nothing: the next person looks for the
pattern, is told it already exists, cannot import it, and writes it by hand
anyway. A standard that cannot be trusted on its easy claims does not get read on
the hard ones. `kit.contract.test.ts` now parses this table and asserts every row
against `kit.tsx`, so adding a row before the export fails the gate.

The chart surface stays `viz/Panel.tsx`'s `Panel` + `Figure`, which is where it
belongs — a titled surface with an accessible fallback is a visualization
concern, and moving it into `kit.tsx` for symmetry would put chart code in the
file every screen imports. `ChartTip` is in `kit.tsx` because the tooltip *is*
shared. `HumanLog` is the trail component, written when the decision card needed
it.

Where a surface wraps a business entity with an identity of its own, it is a
`Card`, per §2. This used to name `RiskCard`, `InsightCard`, `ActionCard` and
`TrendCard` in the present tense; none of the four has ever existed, which read
as a set of components a newcomer should go and find. The app's one `Card` today
is the Quote Builder's narrow-screen line card (`components/LineGrid.tsx`) —
correct, because a quote line is an entity — and everything else that looks like
a card is a `Paper` surface, also correct. Name a component here once it exists.

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

**Not yet aligned — nothing clear-cut, three worth arguing about.** The previous
two revisions of this section each said "nothing the current audit can name", and
each was wrong within a release, so this one does not say it either. Check the
current set in a minute:

```bash
rg -n '<table' frontend/src --glob '*.tsx'
```

The list below used to enumerate every one of them and mark each correct. It
drifted, exactly as the two revisions before it did: it said `facttable` appeared
three times when there were five, and it never mentioned `ci-table`
(`CommercialScreens`) or the two `class="grid"` accessible twins in
`viz/BookFlow` and `viz/Mix`. So this is now a **rule you can apply to a table
you are looking at**, plus the short list of cases still worth an argument. A
rule cannot go stale between releases; a census of eighteen tables always will.

**Correct by construction — do not convert these:**

- a **fact panel**: a label and a value, a fixed handful of rows (`facttable`)
- the **accessible twin under a chart**: same data, table form, for a screen
  reader (`viz-table`, and the `class="grid"` twins in `viz/BookFlow` and
  `viz/Mix`) — §13 requires it
- a **fixed matrix**: an n×n whose n is a property of the business's own
  structure, not of its size (`mig-grid`, the `viz/Mix` matrix)
- a **fixed shape belonging to the screen**: the OAuth scopes on one connection
  (`cx-scopes`), a few reference rows in a drawer (`qi-refs`)

**Convert these:** anything whose row count is set by the size of the business —
customers, items, invoices, quote lines, users, signals. That is §3, and it is
the only test that matters.

| Still arguable | What it holds | Why it is open |
|---|---|---|
| `dp-table` (`DataScreen`) | a capped *sample* of skipped records | Bounded by the sample, not by the book — so it never grows, but it is a list of records. |
| `id-table` (`IdentityScreen`) | the records behind one identity | A handful per identity today. Grows with connectors, not with the book. |
| `ci-table` (`CommercialScreens`) | volume against margin, a few periods | Fixed period count, so probably a fact panel in table clothing. |

Two tables have been converted since this table was written, and both were the
same defect at different sizes. The Quote Builder's line grid was the worse one:
its row count is the size of the RFQ, so a forty-line tender was forty rows with
no sort, no way to bring the thin-margin lines together, and a rate field that
saved on blur. `AdminScreens`' people-and-roles table followed — smaller, but
the row count is the size of the team, and "who can see cost?" and "who has
never signed in?" are both sort questions.

Both are `DataGrid` now, which is also how `DataGridProps` grew the things a
list of *records* needs rather than a list of numbers: `getRowId`, a row class,
controlled selection, and an editable cell. That growth is the point — the
second grid needing selection got it for free, and neither had to open
`AgGridReact` beside the wrapper.

The lesson is in how to check, not what was missed. Twice the miss has been in
the Quote Builder — first `.toast`, a fixed-position div on a 2.4s timer that
this document forbids in as many words (`notistack` now), then this table —
because it arrived before the standard and every audit swept for *known* legacy
class names, which by construction cannot find the one nobody wrote down. Grep
the standard's categories — feedback, loading, status, surfaces, **tables** —
against what the screens actually render, and write down what you found rather
than that you found nothing.

The digest in `CLAUDE.md` is the other half of this. It is what people read
before writing a screen, and the reason a hand-written quote table survived
three UI passes is that the digest listed six rules and tables was not among
them. When a rule earns a place in this document, put a clause in that
paragraph too.

The rule stands regardless: **new UI follows this document, and any screen being
changed for another reason moves toward it.**
