# UI follow-ups from the screen sweep

Eleven agents redesigned the twenty-three screens that #225 covers. They were
barred from four shared files (`theme.ts`, `styles.css`, `kit.tsx`,
`DataGrid.tsx`) and from business logic, APIs, schema, auth and routing — so
where they wanted one of those they had to report it instead of doing it.

That constraint is why this list is worth reading rather than being eleven
private workarounds. Several entries were found independently by four separate
agents, which is corroboration rather than one model's opinion.

**87 items.** Each carries a decision, and the decision is the point:

| | |
|---|---|
| `DONE` | already landed in #225 |
| `DO` | unambiguous — implemented |
| `DEFER` | correct but blocked, usually by §7's size floor or by an incomplete migration |
| `PRODUCT` | a genuine fork; different answers give a different product, so a human picks |
| `NONE` | informational, no action implied |

A `PRODUCT` item is not a backlog entry that nobody got to. It is a question
that should not be answered by whoever happens to touch the file next.


---

## For a human to decide — `PRODUCT` (28)

Different answers give a materially different product.


### P1 · outcomes

> Should there be ONE outcome form? Today there are two by necessity, not by choice: `useQuoteIntelligence.recordOutcome(status, lossReason, lostTo)` passes `undefined` for the note, and `intelligence.documentOutcome(token, ref, status, customer, note, lossReason)` has no `lost_to` argument — so routing the Quote Builder through `RecordOutcomeDialog` would drop `lost_to`, and giving the worklist a 'who won it' field would drop that on the server. Unifying means widening the hook signature, the dialog's `onRecord` contract, and deciding whether an ERP-raised quote should record who won it. That is a contract change, not a UI change, so I did not make it.

**PRODUCT** — unifying the two outcome forms is a server contract change


### P2 · outcomes

> `RecordOutcomeDialog` renders its Outcome select even when `allowed_next` permits only one move — a DRAFT quote may be marked LOST but not WON, so the select shows one option. Whether to hide the control and state the move in prose instead is a copy/flow decision; changing it would also change what the existing test queries.

**PRODUCT** — copy/flow call


### P3 · outcomes

> In the unanswered-quotes grid the row click and the trailing "Record…" button do the same thing. If a row click should instead open the source quote (in Zoho, or in a platform view of the ERP document), that needs a route — there is none for `quote_document_ref` today — and is a routing decision.

**PRODUCT** — needs a route for quote_document_ref


### P4 · outcomes

> The unanswered-quotes screen ranks and lists but cannot say what was already chased. A 'chased on <date>, no answer' state would make the list shrink for a reason other than an outcome being recorded, but there is no field for it and inventing one would weaken what an outcome means (CLAUDE.md §1). Worth a decision, not a UI change.

**PRODUCT** — needs a new field; inventing one weakens what an outcome means


### P6 · catalog

> The "Skipped rows" grid keeps a "Line value" column for every reader, but the server serves that value to managers and owners only — so for a salesperson it renders "—" on every row, an always-empty column taking 140px. Hiding it for readers who cannot see it would mean the component deciding on a role rather than on `canExport`, which is a props/role change rather than a UI one. (This only ever reduces what is shown; nothing here would expose a purchase value.)

**PRODUCT** — component would decide on role rather than on canExport


### P8 · sources

> The "Ignored" column shows a count plus the first two commercial columns, with the full list only in a hover tooltip on a non-focusable span. A keyboard-only user cannot reach the list that makes the nomenclature-only claim checkable. Fixing it properly needs a product call: a focusable disclosure per row (adds a tab stop to every row), an expandable row, or naming the columns inline and letting the column grow. I left the MUI Tooltip as-is — swapping it for ag-grid's `tooltipValueGetter` would not have helped (this grid sets no row click, so `suppressCellFocus` is on and its cells are not focusable either) and would have cost ag-grid's two-second default delay.

**PRODUCT** — every fix adds a tab stop to every row


### P9 · sources

> The sources grid has no default sort, so files appear in server order. "Newest upload first" or "files that will refuse a build first" are both defensible, and both change what a person sees before they touch anything. Not a UI-pass decision.

**PRODUCT** — changes what a person sees before touching anything


### P10 · sources

> Whether the file-level "proposed" rule set should also be visible in the wide grid's Decoding column, not only inside the decoding dialog. Today a file with a proposal nobody has saved reads as plain NOT SAVED in the list, and the proposal is one click away. Surfacing it would change what the list asserts about an unconfirmed proposal, which is the distinction the whole screen is built around.

**PRODUCT** — changes what the list asserts about an unconfirmed proposal


### P11 · pickers

> The `/api/v1/accounts` payload carries `last_order`, `orders_12m` and `revenue_12m`, and the endpoint's docstring says they exist so the directory 'can be chosen from rather than only searched' — but CustomerPicker shows none of them. A 'last ordered 12 Aug 2026' / 'never ordered' line under each option would disambiguate identically-named customers and warn that pricing has no history to read. I did not add it: `components/CustomerPicker.test.tsx` (not a file I own) asserts an option's exact `textContent`, so it needs a one-line test update. No API, schema or logic change — just a call on whether the extra line is worth halving the visible option count.

**PRODUCT** — halves the visible option count


### P12 · pickers

> Should Enter on a sole match go straight to creating the quote, skipping the 'Use this customer' button? It would remove a whole step from the highest-traffic path. I did not: it moves the commit point of quote creation, which is a workflow decision rather than a UI one.

**PRODUCT** — moves the commit point of quote creation


### P16 · forms

> QuoteFieldsSection: the form now repeats the server's rules (a choice field needs at least one option) as an inline hint but never blocks Save, so the server stays the only authority. If the desk would rather be stopped before the round trip, that is a decision about where validation lives, not a styling one.

**PRODUCT** — decides where validation lives


### P17 · chrome

> The setup progress bar counts every listed step, including the two marked Recommended, while the panel disappears once only the REQUIRED steps are done — so a reader can legitimately see '2 of 4 done' and then watch the panel vanish. I kept the denominator matching the visible list (a figure measuring a different set from the list under it would be worse) and did not switch it to required-only. The server already sends `remaining_required`, so either is available; which one the bar should measure is a product call.

**PRODUCT** — which set the bar measures


### P18 · chrome

> The trial end date is shown as the server's date string ('2026-09-01') rather than '1 Sep 2026'. Formatting it client-side shifts it a day for any business zone west of Greenwich, and the suite pins the current form. If a friendlier date is wanted, the server should send it pre-formatted for the business's locale — that is an API change, so I did not make it.

**PRODUCT** — needs the server to send a pre-formatted date


### P21 · attribution

> The "Attributed value" tile's subtitle repeats `attributed_events`, which is also the whole value of the "Opportunities realized" tile sitting two tiles to its right, and is stated a third time in the Verdict alert above. Three renderings of one count in one panel. Removing one is an editorial call about what the headline row is for, not a UI-standards fix, so I left all three.

**PRODUCT** — editorial — what the headline row is for


### P22 · attribution

> The roll-up's month grid carries a floating filter row over at most 24 chronological rows. It costs vertical space for a filter nobody plausibly types into. `DataGrid` already offers `filters={false}` for exactly this, but turning it off removes a control that exists today, so it needs a human's call.

**PRODUCT** — removes a control that exists today


### P23 · attribution

> The event drill-down is a `Dialog`; the ledger row it opens from is a record with an identity and a permalink-shaped id (`value_event_id`). ui-standards §9 says anything that goes somewhere should be a link with a destination from `route.ts`. Making a value event addressable would need a route and a server read — out of scope for a UI pass, and worth deciding deliberately.

**PRODUCT** — needs a route and a server read


### P24 · attribution

> `EVENT_TYPE` holds five hand-written labels and explanations keyed off codes the server sends, with a documented fallback for a sixth. Two of the five have no detector at all. Whether the wording should come from the server alongside `event_types` (so it cannot drift from what the detectors actually do) is a contract decision.

**PRODUCT** — contract decision


### P26 · trust

> After erasure the "Take your data elsewhere" panel still renders and still offers a download. That is arguably right (the plaintext survivors are exactly what an owner may still need to take), but whether the export should be suppressed, relabelled, or left untouched once a signed receipt exists is a product call. Left exactly as it was.

**PRODUCT** — what an export means after a signed receipt


### P27 · trust

> `key_destroyed === true` with no receipt row is indistinguishable, on this screen, from "never erased": it renders the arm-and-erase form, and the POST is idempotent so the press would be a visible no-op. I did not invent a warning for a state the server treats as unreachable (erasure always writes a receipt). Worth deciding whether it is actually reachable.

**PRODUCT** — is the state reachable at all


### P28 · trust

> The confirm field's `placeholder` shows the exact organization id, so the "type your own id" friction is in practice "copy the string in front of you". Left as-is: tightening the friction on the only irreversible action in the application is a product decision, not a UI pass.

**PRODUCT** — friction on the only irreversible action


### P31 · connections

> `ConnectionCheck` carries an `untested_scopes` field this screen never reads — it derives "untested" from `granted === null` on each probed scope instead. The two could disagree. Reading the server's own field is a data-flow change, so I did not make it; somebody should decide whether that field is authoritative or vestigial.

**PRODUCT** — is untested_scopes authoritative or vestigial


### P32 · connections

> The check result's "This sign-in also reaches these companies" list is read-only, while the visually identical list in the add form is pickable. That is correct today (there is nothing to connect from a card), but if connecting a sibling company directly from a card is wanted, that is a routing/flow decision.

**PRODUCT** — routing/flow


### P33 · connections

> The pooling note now renders at `warning` severity because that is what its CSS (`--caution-bg`, `--warn`) was already drawing. If pooling is meant to read as neutral information rather than a caution, that is a content decision for whoever owns `view.pooling_note`.

**PRODUCT** — content decision


### P35 · data

> `SyncLogLine.logger` is fetched, typed, documented as "half of reading an interleaved log", and never rendered. Showing it would help when two subsystems interleave and would add noise to every one of up to 2,000 lines — a product call, so I left it unrendered.

**PRODUCT** — noise on up to 2000 lines


### P36 · data

> The "Last sync" panel now shows a "Sample source" chip when the run read fixtures; `SyncStatusCard` states the same fact in prose for the run it is watching. If that reads as duplication one of the two should go — I kept both because the fact panel is the block that gets screenshotted and its counters are the fixture rows.

**PRODUCT** — editorial


### P38 · data

> A salesperson (canSync false) sees the connections list and both fact panels but not the skipped-rows or run-log panels, and the "Pull the books" panel is a single refusal sentence. Whether they should see the run counters at all is a role question, not a UI one; nothing was changed about the gating.

**PRODUCT** — role question


### P40 · shared

> Whether a signal decision should be able to arrive with no `confidence.evidence_sufficiency` at all. The chip now says "Evidence not stated" rather than "— evidence", which is the honest reading of a missing value, but the better answer may be that the server always stamps one.

**PRODUCT** — should the server always stamp one


### P41 · shared

> The fact-chip cap on `DecisionCard` is four, and which four is "the first four after the primary-fact filter". On a decision with six facts the card now admits it is holding two back, but nothing chooses the two that are most worth seeing (e.g. the ones that differ from the neighbouring cards). Picking them would need a server-side notion of salience.

**PRODUCT** — needs a server-side notion of salience


---

## Implemented — `DO` (36)

Unambiguous: a defect, or a standard already written down.


### S1 · outcomes

> frontend/src/platform/kit.tsx — a `MetricRow`/`TileGrid` wrapper for the `Grid container spacing={2}` + `Grid size={{xs:12,sm:6,md:3}}` tile row. It is now written out verbatim in at least four screens (RetrospectiveScreen, UnrecordedQuotes, MonetizationScreen, ObservabilityDashboard); ui-standards §10 says a pattern appearing twice becomes a component. I wrote it out a fifth and sixth time rather than touch kit.tsx.

**DO** — kit: TileGrid — 6 verbatim copies


### S2 · outcomes

> frontend/src/platform/DataGrid.tsx — an `actionColumn(...)` helper beside `numeric`/`text` that sets `sortable:false, filter:false, headerName:'', context:{noRowClick:true}`. That trio is what every trailing action column needs, and the one in UnrecordedQuotes had two of the three and a comment claiming the third. A helper makes the opt-out impossible to forget.

**DO** — DataGrid: actionColumn() helper


### S3 · outcomes

> frontend/src/platform/kit.tsx — a `FieldLabel` (overline + optional `Tip`, `display:block`) if a third screen wants it. Kept local in QuoteOutcomeBar for now, per §7's size floor. If it is promoted, docs/ui-standards.md §10's component table needs a row too — `kit.contract.test.ts` parses that table and fails on a row without an export (and vice versa is the drift it was written to stop).

**DO** — kit: FieldLabel


### S4 · outcomes

> docs/ui-standards.md — nothing is wrong with it, but its "Still arguable" table does not mention any of these four files, and the `rg -n '<table' frontend/src` census it points at is the check I ran. No edit needed unless one of the kit additions above lands.

**DO** — §10 table rows for the new kit components


### S7 · catalog

> frontend/src/platform/kit.tsx — `ErrorState` has no `onClose`. CatalogScreen's two failure alerts are dismissible (one refused request beside controls that still work, not a screen that failed to load), so they cannot use it; I wrote a local `ProblemAlert` instead. An optional `onClose` on `ErrorState` would let both use kit.

**DO** — kit: ErrorState gains onClose


### S8 · catalog

> frontend/src/platform/kit.tsx — `SectionHeader` can only place a `StatusChip` in `actions`, which puts the state word at the far right beside the buttons. Three headers in this cluster need the chip immediately after the name (CatalogScreen's company and catalogue rows, and CatalogSources' file header), and all three hand-roll the row. A `badge` slot rendered directly after the title would make that a kit pattern; I hand-rolled `HeadingRow` locally rather than add it.

**DO** — kit: SectionHeader gains a badge slot


### P5 · catalog

> Record counts on the catalogue screen render ungrouped — "6717 decoded records", "RESOLVES 6717 RECORDS", "9717 records from 2 catalogues". `money.count()` already exists and would render "6,717" with the organization's own grouping. I did not change it because CatalogScreen.test.tsx asserts the raw digits in four places and that test file is not mine to edit. Grouping them is a one-line change plus a test update, and someone should decide whether to take it.

**DO** — money.count() grouping + the four test assertions


### P7 · catalog

> When the full skipped-rows list fails to fetch, the panel falls back to the run row's sample and now says so plainly — but there is still no way to retry short of leaving the screen. `kit.ErrorState` has an `onRetry` slot for exactly this; wiring one would mean re-running the effect, which is data fetching and outside a UI pass.

**DO** — wire ErrorState onRetry once the kit gains it


### S9 · sources

> frontend/src/platform/kit.tsx — needs to own the secondary-text component (`Meta`: a caption-variant, text.secondary span/div for the second line of a cell or card). This is now the SECOND local copy: `SkippedRowsPanel.tsx` wrote one for the identical reason and said the shared answer belongs in kit. §10 says a pattern appearing twice becomes a shared component; I could not edit kit.tsx, so I wrote a third-party-free local one and documented why.

**DO** — kit: Meta


### S11 · pickers

> theme.ts (or kit.tsx): `kit.TOUCH` is spread by hand at every call site, which is exactly how it went missing from `CompanyScope` while `CompanyFilter` had it. A theme-level `components.MuiSelect`/`MuiListItemButton` minHeight default, or a `TouchTarget` wrapper in kit.tsx, would make the floor the default rather than something each new control has to remember. I applied it locally in all three files instead.

**DO** — theme: touch floor as a component default


### S12 · pickers

> kit.tsx: I wrote a private `Meta` (muted trailing metadata: caption + text.secondary, aria-hidden glyph, `·` separator) in CompanyFilter.tsx. `EntityName`'s `.ent-meta` is the same idea in CSS. If a third caller appears this is a kit.tsx component, not a third copy.

**DO** — same as S9


### S15 · forms

> platform/kit.tsx: `SectionHeader`'s `tip` is typed `tip?: string`, while the `Labelled` it replaces took a ReactNode. AdminScreens' "People and roles" tip contains a `<b>`, so that section cannot convert until `tip` is widened to `ReactNode` (Tip's own `text` prop already is).

**DO** — kit: SectionHeader tip widened to ReactNode


### P14 · forms

> IntakeModal: a chosen channel cannot be un-chosen. `InboundChannel` deliberately has no "Other"/"Unknown" member and unset is the honest default, but someone who picks "Email" by mistake can only get back to unset by cancelling the dialog — and the file's own comment says the harm is a row filed under a route nobody chose. Fixing it means either a "Not stated" option submitting "" (needs `displayEmpty` so the floating label does not overlap it) or a Clear button. I left the option set exactly as it is.

**DO** — a chosen channel must be un-choosable


### P15 · forms

> IntakeModal: `onSubmit` returns `void`, so the dialog cannot show progress or block a second press, and double-clicking "Resolve & add" fires the upload+intake pair twice. The fix is to have the dialog await the caller's promise and drive a `loading` button from it, which changes the component's contract and QuoteBuilder's call site — both outside a UI pass.

**DO** — double-click fires the upload+intake pair twice


### S19 · chrome

> theme.ts: `subtitle2` is 12px UPPERCASE, which makes it a micro-label rung, not the 'widget title' rung §4's ramp names. A screen that wants a small sentence-case title has to reach for `h5` (as SetupChecklist now does) or invent a size. Worth either renaming the rung in the standard or adding one between `subtitle2` and `h5`.

**DO** — addressed by PanelMark (S43) rather than renaming a rung


### S20 · chrome

> theme.ts: no `MuiListSubheader` defaults. MUI's own are sized for a settings list (48px line-height, 16px gutters), so the account menu overrides line-height and padding at the call site; a second menu with a section label will do the same.

**DO** — theme: MuiListSubheader defaults


### P19 · chrome

> SetupChecklist has no test file at all, while the other two shell components have thorough ones. Its behaviour — the three silent branches, the owner-only Open buttons, the non-owner Alert — is exactly the kind that regresses invisibly. Worth someone deciding whether it earns one.

**DO** — write the missing test file


### S22 · attribution

> `platform/kit.tsx` should own a `FactTable`. Five raw fact panels in this file alone, and `rg -n 'facttable' frontend/src` finds more in `CommercialScreens`, `DataScreen` and elsewhere — all repeating the same `<Box component="table" className="facttable">` boilerplate, and all inheriting the same hole: `styles.css` styles `.facttable td` and never `.facttable th`. I wrote it locally because I may not edit `kit.tsx`; it is a §10 candidate the moment a second file wants it.

**DO** — kit: FactTable


### S26 · attribution

> `platform/kit.tsx` could own a `Tile`/`TileRow` pair. Eighteen copies of the same flex `Box` in this file; `rg 'flex: "1 1' frontend/src` suggests the pattern recurs on other screens. Local for now.

**DO** — folded into TileGrid (S1)


### S27 · attribution

> `MetricCard` has no way to say "this slot holds a word, not a figure". Both `UnknownValue` and `Amount`'s absent branch reach for a bare `fontSize: "0.6em"` literal, which is the one typography literal I could not remove (there is no theme token for "smaller than the surrounding h3"). A `MetricCard` `unknown` prop, or a kit-level `UnknownValue`, would retire it.

**DO** — kit: MetricCard gains unknown


### S30 · trust

> frontend/src/platform/kit.tsx — `<Paper variant="outlined" sx={{ p: 3 }}>` wrapping a `<SectionHeader level="section">` is the app's section surface: 7 uses in AttributionScreen.tsx, 5 in TrustScreen.tsx, more in RetrospectiveScreen and OperatorConsole. It should be a `Section` component in kit.tsx. I deliberately did NOT add a file-local one — a third answer to one question is worse than the second.

**DO** — kit: Section


### S31 · trust

> frontend/src/platform/DataGrid.tsx (or kit.tsx) — no way for a grid to say "these are the most recent N, not all of them". The observability screen solved this with hand-written prose per panel and TrustScreen now does the same in its section copy. A `truncated`/`showingOf` affordance on DataGridProps would stop the next screen inventing a third wording.

**DO** — DataGrid: showingOf


### P25 · trust

> `/trust/payloads` (cap 50) and `/trust/access` (cap 200) truncate server-side and return no total, so the screen physically cannot say "showing 200 of N". I reworded every claim to be true of the rows in hand, but the honest fix is a `total` alongside `events`/`summary` in routers/trust.py and api.ts — a response-shape change, so a human decision.

**DO** — serve a total so the screen can state what it is showing


### S32 · connections

> frontend/src/styles.css: these rules are now unreferenced by any .tsx and can be deleted — `.cx-badge` and its four tone modifiers; `.cx-scopegaps` (+ `li`, `li.bad`, `code`); `.cx-empty`; `.cx-lastpull`; `.cx-rotate-open`; `.cx-detail` and `.cx-detail.bad`; `.cx-pool`; `.st-bad`; `.cred-orgs` and `.cred-orgs li`; `.cx-actions .spacer`; `.cx-name .input`; `.cx-pull > label`, `.cx-pull .input`, `.cx-pull .sync-check`; `.cx-rotate .input`; `.cx-add form > label`, `.cx-add form > .input`, `.cx-add .fsrc`, `.cx-add form > button.btn-ghost`; `.cx-unused .cred-orgs li`, `.cx-unused .mono`. I verified each with a grep across frontend/src before listing it.

**DO** — sweep the dead rules, each re-verified


### S35 · connections

> frontend/src/styles.css: `.dp-screen-head` is referenced by `IdentityScreen.tsx` and `AdminScreens.tsx` (and, until this change, `ConnectionsPanel.tsx`) but has no rule anywhere in the stylesheet — a class name that does nothing. Those two remaining call sites should move to `kit.SectionHeader` as I did.

**DO** — drop the .dp-screen-head class that styles nothing


### P30 · connections

> The rename field on a connection card has no Enter-to-save — you must click Save. Adding `onKeyDown` is a behaviour change rather than presentation, so I left it. Worth deciding whether inline rename should submit on Enter and cancel on Escape; the same decision applies to `IdentityScreen`'s identical `.id-name .input` pattern.

**DO** — Enter saves, Escape cancels — additive


### S37 · data

> kit.tsx: `Meta` — the second line of a grid cell/card as a `Typography variant="caption"` — now exists identically in SkippedRowsPanel.tsx and DataScreen.tsx. §10 says a pattern that appears twice belongs in kit; I could not move it.

**DO** — same as S9


### S40 · data

> kit.tsx: `SectionHeader`'s `actions` Stack has no `flexWrap`, so a header carrying several chips plus controls can overflow on a narrow viewport. I kept the log panel's switch and download button in a separate wrapping row because of this; with `flexWrap: "wrap"` there they could sit in the header.

**DO** — kit: SectionHeader actions wrap


### S41 · data

> kit.tsx: `EmptyState` renders its own `Paper variant="outlined"`, so it nests surfaces when used inside a panel. A flat variant would let a panel show an empty state in place of its content; I worked around it by placing the log's empty state outside the `Bp`.

**DO** — kit: EmptyState flat variant


### P34 · data

> backend/app/routers/data_status.py `_log_note` builds its "This run kept no log — it ran before its log was stored with it" sentence from the total AFTER the `problems_only` filter, so a perfectly clean run is described as having kept no log. I worked around it in the client (the filtered empty state now says what the client knows instead of showing the server's sentence); the server sentence should account for the filter.

**DO** — backend: the sentence is built from a post-filter count


### P37 · data

> `defaultSince()` formats via `toLocaleDateString("en-CA")` in the BROWSER's timezone, while `when.ts` pins every other date to the business timezone precisely because six helpers used to do this. A browser west of the business can compute a start date one day early. Left untouched — it is date logic, not UI.

**DO** — browser timezone where every other date is business timezone


### S42 · shared

> frontend/src/platform/kit.tsx — `CurrencyValue`'s `value` prop is typed `number | null | undefined`. Decision money arrives as a decimal *string* on purpose (`DecisionImpact.financial`, `DecisionRanking.financial`, `DecisionRanking.rupees_per_point` — serialized that way so the server's `Decimal` never round-trips through a float), and `money()` in money.ts already accepts `number | string`. So §10's "CurrencyValue replaces bare money() in JSX" cannot reach ImpactPanel, RankingPanel or DecisionCard without a `Number()` at every call site. Widening the prop to `number | string | null | undefined` (and using the same coercion money.ts already does) would close it. I left `money()` and documented why.

**DO** — kit: CurrencyValue accepts the decimal string the server sends


### S43 · shared

> frontend/src/platform/kit.tsx — `SectionHeader` has three rungs (page/section/widget → h1/h2/h3, smallest 21px). There is no rung for the 11–12px uppercase micro-heading a panel uses for its own mark, so "AI recommendation", "Business impact" and "Why it sits here" are each hand-built here as `Typography variant="overline"` — a pattern that now appears three times in one file, which is §10's threshold. Either a `level="mark"` on SectionHeader or a small `PanelMark` in kit would make it one component. PlatformApp's `.facts-mark` is a fourth instance of the same pattern.

**DO** — kit: PanelMark


### S44 · shared

> frontend/src/theme.ts — there is no token for a border *width*. The 2px left rule is the whole vocabulary that separates "a model wrote this" (accent) from "this is arithmetic" (ink), and it is now written as `borderLeftWidth: "2px"` in two places in ui.tsx. A `tokens.rule` alongside `tokens.radius` would let it follow the theme. The 7px accent square on the interpretation mark is the same kind of value (it has to match `.facts-mark::before` in styles.css).

**DO** — theme: tokens.rule


### S45 · shared

> frontend/src/styles.css — `.interp`, `.interp-mark`, `.impact`, `.impact-mark`, `.impact-figure`, `.impact-basis`, `.impact-monthly`, `.impact-ops`, `.ranking`, `.ranking-mark`, `.ranking-sum`, `.ranking-scale` and `.why-text` now have zero call sites in the whole frontend (verified by exact `className=` grep across *.tsx). They are dead and can be swept in a later pass. Do NOT sweep `.facts-mark` — PlatformApp still renders it, and it is the visual twin of the interpretation mark. `.section-h` also stays: 11 other call sites.

**DO** — sweep the dead rules


### P39 · shared

> `DecisionCard`'s "Open →" is a `<button>`, not a link. ui-standards §9 says anything that goes somewhere must be an anchor whose path comes from `route.ts` — no ctrl-click, no middle-click, no "open in a new tab", and it is announced as a button — and §9 also says two screens side by side is the ordinary way this desk is used. The card cannot fix this alone: its prop is `onOpen: (id: string) => void`, so it has no path. Making it a link means changing a shared component's signature (e.g. accepting a `to` from `route.ts` alongside or instead of `onOpen`) and updating both call sites in PlatformApp. Out of scope for a UI pass on one file.

**DO** — §9 — navigation must be an anchor


---

## Correct, but blocked — `DEFER` (8)

Each names what unblocks it.


### S10 · sources

> frontend/src/styles.css — `.fsrc` is declared only under `.facttable`, `.sync-opts` and `.cx-add`, while roughly 55 call sites across `platform/` use it as a bare class (see the census in `SkippedRowsPanel.tsx`). Every one of those outside those three ancestors renders unstyled. Once kit owns `Meta`, `.fsrc` should be deleted rather than globalised — globalising it would re-introduce a second styling vocabulary beside the theme.

**DEFER** — delete .fsrc only once every call site uses kit Meta; the bare rule is correct until then


### S14 · forms

> styles.css: `.st-section h3 { font-size: 15px; margin: 0 0 4px }` outranks the theme ramp (specificity 0-1-1 beats emotion's 0-1-0), so `SectionHeader`'s `<h3>` renders at 15px inside a settings section rather than the theme's 21px. That is what keeps this section matching its not-yet-converted neighbours today, and I relied on it deliberately — but once every settings section uses `SectionHeader`, that rule should be deleted so §4's ramp is the only thing deciding heading size.

**DEFER** — deleting .st-section h3 needs every settings section on SectionHeader first


### S16 · forms

> platform/kit.tsx (minor): `StatusChip` is the only chip in the kit, so it is now carrying two things that are not state words — "Built-in" here, and the attached filename that IntakeModal already showed. Both read correctly as tags. If the kit ever grows a plain `Tag`, these are the two call sites.

**DEFER** — two call sites; §7 size floor


### S17 · chrome

> kit.tsx: a user-avatar/monogram component. `initials()` + the coloured disc now live in AccountMenu.tsx as a local `Monogram`, which is right for one call site — but the moment a second screen shows a person (an admin people list, a HumanLog actor), this belongs in the kit rather than being copied. `initials` is already exported from AccountMenu.tsx and is pinned by its own tests, so the move is cheap.

**DEFER** — one call site; §7 size floor says no


### S18 · chrome

> theme.ts: there is no size scale for component geometry, so avatar diameters (32/36), the menu's `minWidth: 232` and the 32px icon gutter remain numeric literals in my files. They are geometry rather than design tokens and the theme has nowhere to put them today; if a sizing scale is ever added they should follow.

**DEFER** — no geometry scale exists to add these to


### S21 · chrome

> kit.tsx: no shared 'live region for the outcome of a press' helper. TrialNotice now hand-rolls an always-rendered `aria-live="polite"` box around its ask error. If a second inline-action error appears anywhere, that pattern should be one component rather than two.

**DEFER** — one call site; §7 size floor


### P20 · chrome

> The account menu's identity row can still receive a keyboard tab stop when no workspace is selected, because MUI's MenuList gives `tabIndex={0}` to the first non-disabled child under variant="selectedMenu". Avoiding it entirely needs either a component that swallows a `disabled` prop or switching the Menu to variant="menu" (which would give up opening on the current workspace). I left MUI's default rather than introduce a hack; it is a trade-off someone may want to revisit.

**DEFER** — MUI MenuList behaviour; the alternatives are worse


### S34 · connections

> frontend/src/styles.css: `.st-section h3 { font-size: 15px }` is a per-screen override of the theme's h3 (21px) — exactly the "per-screen invention" ui-standards §4 rules out. It is why I could not use `kit.SectionHeader level="widget"` for the "Add a company" heading without either accepting a silent 21px→15px override or making my panel disagree with `DataScreen`'s "Pull the books" panel immediately below it. The fix is one rung in `theme.ts` plus deleting the rule, across every `.st-section` panel at once.

**DEFER** — same as S14


---

## Already fixed in #225 — `DONE` (10)

Listed because four of them were found independently, which is how they were trusted enough to act on.


### S5 · catalog

> frontend/src/theme.ts — `CSS_VARS` never emits `--font-mono`, although `tokens.fontMono` exists. Every `var(--font-mono, monospace)` in the codebase therefore resolves to the browser's bare `monospace`. Adding `"--font-mono": tokens.fontMono` to CSS_VARS would fix the CSS half; I worked around it locally by importing `tokens` and reading `tokens.fontMono` into a single `MONO` constant.

**DONE** — #225 — CSS_VARS emits --font-mono


### S6 · catalog

> frontend/src/styles.css or frontend/src/platform/kit.tsx — `.fsrc` (the secondary-metadata line) is declared ONLY as `.facttable .fsrc`, `.sync-opts .fsrc` and `.cx-add .fsrc`, but `className="fsrc"` appears 55 times across platform/*.tsx, most of them inside AG Grid cell renderers where none of those ancestors exists. Those all render at body size and body contrast today. Either add a bare `.fsrc` rule, or (better, and what §10 implies) export the component from kit.tsx and convert the call sites. I wrote a local `Meta` in SkippedRowsPanel only.

**DONE** — #225 — bare .fsrc rule


### S23 · attribution

> `platform/kit.tsx` should own the monospace token (`const MONO = { fontFamily: tokens.fontMono }`). This is now the THIRD file to discover independently that `.mono` is styled nowhere as monospace — `CatalogScreen.tsx:124` documents it at length, this file had two more cells, and `rg 'className="mono"' frontend/src` shows ~10 further uses in `ConnectionsPanel` and `CommercialScreens` that are still rendering in the body face.

**DONE** — #225 gave .mono a face; local MONO consts now removable


### S24 · attribution

> `theme.ts` `CSS_VARS` does not emit `--font-mono`, so `styles.css:1226` (`.cx-rotate .input { font-family: var(--font-mono, monospace) }`) silently falls back to the browser's bare monospace rather than `tokens.fontMono`. One line in `CSS_VARS` fixes every CSS-side caller. Not made — theme.ts is off limits.

**DONE** — #225


### S25 · attribution

> `styles.css` `.facttable` would benefit from a `th` rule and from scoping `white-space: nowrap` to numeric values rather than to every `.fv` cell. I worked around both locally (theme-driven `th` styling in `FactTable`, and a `FactNote` child span to re-enable wrapping) because the stylesheet's `.facttable .fv` selector is more specific than an `sx` class and cannot be overridden on the cell itself.

**DONE** — #225 — .facttable th


### S28 · trust

> frontend/src/styles.css — `.facttable` styles `td` and nothing else; there is no `.facttable th` rule anywhere. Every fact panel with a header row or `<th scope="row">` labels falls through to the browser's centred bold with no padding and no rule. I worked around it with a local `FACT_CELLS` sx const. It belongs in the stylesheet, or better as a `FactTable` component in platform/kit.tsx (5 screens use the class).

**DONE** — #225


### S29 · trust

> frontend/src/styles.css + frontend/src/theme.ts — there is no bare `.mono` rule, and `theme.ts` holds `tokens.fontMono` but `CSS_VARS` never emits `--font-mono` (styles.css:1226 reads `var(--font-mono, monospace)` and silently takes the fallback). Two screens now carry an identical local `const MONO = { fontFamily: tokens.fontMono }` — CatalogScreen.tsx:124 and now TrustScreen.tsx. Either emit `--font-mono` from theme.ts or export a `MonoText`/`Identifier` component from kit.tsx; §10 says the second occurrence is the trigger and I could not make it.

**DONE** — #225


### S36 · connections

> frontend/src/platform/kit.tsx: there is no base `.mono` rule in styles.css, so `className="mono"` is a no-op except where a context-specific selector happens to catch it (`.cred-org .mono`, `.cx-unused .mono`, `.cx-scopes td.mono`). Ids and scope strings across this app are therefore not actually monospaced. A `MonoValue` in kit (or a base `.mono` rule reading `theme.tokens.fontMono`) would fix it once; I kept the class where it was rather than inventing a local answer.

**DONE** — #225


### S38 · data

> styles.css: `.fsrc` is declared only as a descendant of `.facttable`, `.sync-opts` and `.cx-add`, so of the 33 `className="fsrc"` sites across the app, every one outside those three ancestors renders unstyled (body size, body ink). Either promote `.fsrc` to a standalone rule or finish the migration to a kit component.

**DONE** — #225


### S39 · data

> theme.ts: `CSS_VARS` has no `--font-mono` although `tokens.fontMono` exists and `styles.css:1226` reads `var(--font-mono, monospace)` — that declaration always takes its fallback. Adding the variable would make the CSS and the JS agree.

**DONE** — #225


---

## Informational — `NONE` (5)

Recorded so the absence stays deliberate.


### S13 · pickers

> styles.css: none needed — `.ent`'s `display:grid` + `min-width:0` truncates correctly inside MUI's `display:flex; overflow:hidden` option row (verified against Autocomplete.js:353). Noting only that CustomerPicker now depends on that, so `.ent` has a new consumer.

**NONE** — informational — .ent gained a consumer


### P13 · pickers

> `CompanyScope` selects now carry the 44px touch floor (previously 40px), so the headers in `viz/Mix.tsx` and `viz/Dependency.tsx` grow by 4px. That is the standard being applied, not a regression, but it is a visible change in files I do not own.

**NONE** — informational — 4px growth is the standard applying


### P29 · trust

> The `ai-metrics` fourth section the file's docstring reserves space for is still deliberately absent (AI_PROVIDER defaults to `mock`, so it would be a screen of zeros). Unchanged — noting it so the absence stays a decision rather than an oversight.

**NONE** — deliberate absence, recorded so it stays one


### S33 · connections

> frontend/src/styles.css: do NOT delete `.cred-org` / `.cred-org:disabled` / `.cred-org .mono` / `.cred-org em` — `platform/TrustScreen.tsx` still uses them. Likewise `.cx-tabs`/`.cx-tab` and `.sync-check` are still used by `platform/IdentityScreen.tsx`. Those are the same legacy patterns I converted here and are worth converting there too, but those files are not mine.

**NONE** — guard rail for S32 — these must NOT be deleted


### P42 · shared

> The impact figure moved from a bespoke 30px to the ramp's `h2` (26px), because §4 says a screen needing a size the ramp does not have means the ramp is wrong. If 30px is genuinely wanted for the hero number on a decision, the change belongs in `theme.ts` once, not in this component.

**NONE** — resolved by using the ramp
