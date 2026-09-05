// Small shared presentation pieces for the Decision Platform.
//
// **Where the line is drawn in this file.** `docs/ui-standards.md` §2 makes
// `Paper` the surface for anything on a dashboard, and §10 lists the kit
// components that replace a hand-rolled pattern by name. So the *surfaces* here
// — the interpretation panel, the impact panel, the ranking box — are `Paper`
// now, built from theme tokens rather than from bespoke `.interp` / `.impact` /
// `.ranking` rules that had to be kept in step with the theme by hand. That is
// the move `Bp` already made below, applied to its three siblings.
//
// What deliberately did *not* move: the marks and lists with no kit equivalent
// (`.factchip`, `.actions-list`, `.facttable`) and the `.dcard*` classes, which
// `styles.css` styles from outside — `.dp-cards.tight .dcard` is a parent
// screen's rule and dropping the class would silently un-tighten the compact
// queue. Those classes read `var(--color-*)`, which `theme.ts` emits, so they
// already follow the palette; converting them would be churn, not alignment.
//
// **Money here is a decimal string, and `CurrencyValue` takes one now.**
// `DecisionImpact.financial`, `DecisionRanking.financial` and
// `rupees_per_point` are serialized as decimal *strings* so the server's
// `Decimal` never round-trips through a float, and the kit prop was typed
// `number` only — which is the whole reason every figure in this file was a
// bare `money()` call and §10's "CurrencyValue replaces bare `money()` in JSX"
// could not reach it. The prop is `number | string` now, so they are
// `CurrencyValue`: one place decides that a column of rupees lines up, and the
// em dash it renders for anything that is not a finite number is the same
// refusal `money()` already made rather than a new one.
//
// The `<table className="facttable">` in `WhyPanel` stays a `<table>` on
// purpose. It is a fact panel — a label and a value, one row per state field on
// one decision — and its row count is the shape of the decision type, never the
// size of the business. `ui-standards.md` names `facttable` as correct by
// construction; §3's test is the row count, and this one does not grow with the
// book.
import Button from "@mui/material/Button";
import type { ReactNode } from "react";
import type { DecisionDetail, Fact } from "./types";
import { CONF_LABEL, TYPE_LABEL, aiState, factLabel, factValue, isPrimaryFact,
         stateFieldLabel, stateFieldValue } from "./format";
import type { DecisionAction, DecisionImpact, DecisionRanking } from "./types";

// Shared with the Quote Builder — see src/Tip.tsx. Imported as well as
// re-exported: the panels below use it, and a module cannot read its own
// re-export.
import { Labelled } from "../Tip";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import type { SxProps, Theme } from "@mui/material/styles";
import { Link as RouterLink } from "react-router-dom";
import { CurrencyValue, PanelMark, PriorityChip, SectionHeader, StatusChip, TOUCH } from "./kit";
// Border widths and the mark square, so the two panels that say "a model wrote
// this" and "this is arithmetic" cannot drift apart in a literal.
import { tokens } from "../theme";
export { Tip, Labelled } from "../Tip";

type BpProps = {
  children: ReactNode;
  className?: string;
  /** The blueprint corner marks. On by default — they are how this product has
   *  looked since it shipped, and turning them off across twenty-nine surfaces
   *  is a redesign nobody asked for. Off is now one prop away when somebody
   *  does: `docs/ui-standards.md` ranks density above decoration, and these are
   *  decoration that happens to cost nothing. */
  marks?: boolean;
  sx?: object;
} & React.HTMLAttributes<HTMLDivElement>;

/** The surface almost everything sits on.
 *
 * A `Paper`, per the standard: the border, radius, elevation and background now
 * come from the theme instead of from a bespoke `.bp` rule that had to be kept
 * in step with it by hand. Converting the *implementation* rather than the
 * twenty-nine call sites is deliberate — one edit moved every screen, and a
 * second surface component beside this one is how a design system forks.
 *
 * `Paper`, never `Card`: this holds panels, filters and figures. `Card` is
 * reserved for something with an identity you could open or act on.
 */
export function Bp({ children, className = "", marks = true, sx, ...rest }: BpProps) {
  return (
    <Paper
      variant="outlined"
      className={`bp ${className}`}
      sx={{ position: "relative", ...sx }}
      {...rest}
    >
      {marks && (
        <>
          <i className="corner tl" />
          <i className="corner tr" />
          <i className="corner bl" />
          <i className="corner br" />
        </>
      )}
      {children}
    </Paper>
  );
}

export function Pri({ band }: { band: string }) {
  return <PriorityChip band={band} />;
}

/** Confidence in the *recommendation*.
 *
 * When the AI degraded or failed there is no recommendation to be confident
 * about — only the deterministic reading. Showing "High confidence" beside
 * "AI output failed validation" (which the card used to do) reads as a
 * contradiction and quietly erodes trust in every other badge. */
export function Conf({ level, aiStatus }: { level?: string; aiStatus?: string }) {
  const state = aiStatus ? aiState(aiStatus) : "ok";
  if (state === "degraded" || state === "failed") {
    return (
      <StatusChip
        label="Facts only · no AI reading"
        tone="neutral"
        tip="The model did not return a usable reading, so this card shows the
             deterministic signal alone. The figures are unaffected — they were
             never computed by a model."
      />
    );
  }
  const label = level ? CONF_LABEL[level] : undefined;
  // An unknown sufficiency used to render as "— evidence", which reads as a
  // low score rather than as a missing one. Absence of evidence is not a pass:
  // say the level is not stated and let the reader treat it as unknown.
  if (!label) {
    return (
      <StatusChip
        label="Evidence not stated"
        tone="neutral"
        tip="This decision arrived without an evidence level. That is a gap in
             the record, not a low score — read it as unknown."
      />
    );
  }
  return (
    <StatusChip
      label={`${label} evidence`}
      tone={label === "High" ? "good" : label === "Medium" ? "warn" : "neutral"}
      tip="How much evidence stands behind the reading — not how sure a model is."
    />
  );
}

export function FactChip({ f }: { f: Fact }) {
  return (
    <span className="factchip">
      <span className="k">{factLabel(f.label)}</span>
      <span className="v">{factValue(f.label, f.value)}</span>
    </span>
  );
}

/** The AI interpretation region — renders the special states the design mandates
 * (AI unavailable / withheld) as distinct panels, never a hedged recommendation. */
export function Interpretation({ d }: { d: DecisionDetail }) {
  const state = aiState(d.interpretation.status);
  const suff = d.confidence?.evidence_sufficiency;

  if (state === "failed") {
    // `warning`, not `error`: nothing failed that stops somebody deciding. The
    // facts are all present and were never computed by a model — only the
    // reading of them is missing.
    return (
      <Alert severity="warning">
        <AlertTitle>Interpretation unavailable</AlertTitle>
        The reasoning service did not respond. The facts, history and evidence on
        the left are read straight from your systems and are all present — only
        the reading of them is missing. You can still act and record a decision.
      </Alert>
    );
  }
  if (state === "withheld" || suff === "INSUFFICIENT") {
    // `info`: a withheld judgement is the system working, not the system
    // breaking. Rendering it in red would teach people to distrust the refusal,
    // which is the one behaviour worth protecting.
    return (
      <Alert severity="info">
        <AlertTitle>Recommendation withheld</AlertTitle>
        {d.interpretation.explanation ||
          "The evidence does not support a confident recommendation. The movement is shown; a judgement is withheld rather than manufactured."}
        {d.confidence?.reasons?.length ? (
          <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2.5 }}>
            {d.confidence.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </Box>
        ) : null}
      </Alert>
    );
  }
  // Degraded = the model answered but failed validation, so what follows is the
  // deterministic reading, not a recommendation. Label it for what it is.
  if (state === "degraded") {
    return (
      <Alert severity="warning">
        <AlertTitle>Deterministic reading · no AI recommendation</AlertTitle>
        {d.interpretation.explanation}
        <Box sx={{ mt: 1, typography: "caption", color: "text.secondary" }}>
          The model responded but its answer failed validation, so it was discarded. The sentence
          above is generated from the signal's own figures.
        </Box>
      </Alert>
    );
  }
  // The reading itself. A `Paper` and not an `Alert`: the three branches above
  // are *states* of the interpretation, and this one is the interpretation —
  // content, on the default dashboard surface (§2).
  //
  // The steel tint and the accent edge are kept, and they are load-bearing
  // rather than decorative: `styles.css` recorded that this tint means "a model
  // wrote this", which is why the impact panel below is deliberately uncoloured.
  // Both halves of that distinction now read their colours from the theme.
  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2,
        bgcolor: "info.light",
        borderColor: "var(--color-accent-300)",
        // `tokens.rule`: the 2px left edge is the whole vocabulary separating
        // this panel from the impact panel below, and it was a literal in both.
        borderLeftWidth: tokens.rule,
        borderLeftColor: "primary.main",
      }}
    >
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1 }}>
        {/* `tokens.mark`, because this square has to stay the size of the ink
            one beside "Facts · what the data shows" in `PlatformApp`: the same
            mark, and the tint is the only thing that separates "a model wrote
            this" from "this is arithmetic". */}
        <Box
          aria-hidden
          sx={{
            width: tokens.mark, height: tokens.mark,
            flex: "0 0 auto", bgcolor: "primary.main",
          }}
        />
        <PanelMark sx={{ color: "var(--color-accent-800)" }}>AI recommendation</PanelMark>
      </Stack>
      {d.interpretation.explanation && (
        <Typography variant="body2" sx={{ maxWidth: "68ch" }}>
          {d.interpretation.explanation}
        </Typography>
      )}
      {/* The recommendation is the sentence somebody acts on, so it carries the
          weight. Same size as the explanation above it — a bigger recommendation
          would read as a stronger claim than the evidence behind it supports. */}
      {d.interpretation.recommendation && (
        <Typography variant="body2" sx={{ mt: 1, maxWidth: "68ch", fontWeight: 600 }}>
          {d.interpretation.recommendation}
        </Typography>
      )}
      {/* Named, not merely tinted. The caveat used to be accent-coloured small
          print and nothing else, so a reader in greyscale — or one who simply
          does not know the tint — could not tell it qualified the sentence
          above. The word carries it now, and the type matches the degraded
          branch's small print, which says the same kind of thing. */}
      {d.interpretation.caveat && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1, maxWidth: "68ch" }}
        >
          <Box component="strong" sx={{ fontWeight: 600 }}>Caveat</Box>
          {" · "}
          {d.interpretation.caveat}
        </Typography>
      )}
    </Paper>
  );
}

export function typeLabel(t: string): string {
  return TYPE_LABEL[t] || t;
}

/* ── the state-derived decision card ──────────────────────────────────────────
 *
 * A different card from the interpreted one, because it makes a different kind
 * of claim. An AI card says "here is a reading of the evidence, and here is how
 * confident we are in it". This one says "here is what this is worth, here is
 * the arithmetic, and here is where every number came from" — so the reader
 * checks it rather than trusting it.
 *
 * Nothing here is a recommendation. The actions are what the situation
 * *permits*; choosing between them is the reason a person is paid.
 *
 * These panels are deliberately NOT given the accent tint the interpretation
 * panel above uses. That tint means "a model wrote this"; these numbers are
 * arithmetic, and colouring them the same way would teach the reader the wrong
 * thing about both. The rule down the left edge is `text.primary` — ink, not
 * accent — for exactly that reason.
 */

/** A label written to be read as the tail of a clause — "1,000 on hand", not
 *  "1,000 On hand". The same labels are sentence-cased in the evidence table,
 *  where they are row headings rather than the end of a sentence. */
function lowerFirst(s: string): string {
  return s.charAt(0).toLowerCase() + s.slice(1);
}

/** What the situation is worth, and what that number is. */
export function ImpactPanel({ impact }: { impact: DecisionImpact }) {
  if (!impact?.financial) return null;
  const operational = Object.entries(impact.operational || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <Paper
      variant="outlined"
      sx={{ p: 2, mb: 2, borderLeftWidth: tokens.rule, borderLeftColor: "text.primary" }}
    >
      <PanelMark>Business impact</PanelMark>
      {/* `component="div"`: this is the largest thing on the panel and it is a
          number, not a heading. Rendered as an `<h2>` it would join the page's
          heading outline, and a screen reader would announce "₹4,00,000" as a
          section. `h2` is the size rung; the element is a div. The tabular
          figures come from `CurrencyValue` rather than from an `sx` here, so
          the panel and the card decide alignment the same way. */}
      <Typography variant="h2" component="div">
        <CurrencyValue value={impact.financial} />
      </Typography>
      {/* The sentence matters as much as the figure: capital locked and annual
          holding cost can be the same number and are not the same claim. */}
      {impact.basis && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: "46ch" }}>
          {impact.basis}
        </Typography>
      )}
      {impact.monthly && (
        <Typography variant="body1" sx={{ mt: 1, fontWeight: 600 }}>
          <CurrencyValue value={impact.monthly} />{" "}
          <Box
            component="span"
            sx={{ typography: "caption", fontWeight: 400, color: "text.secondary" }}
          >
            a month while it sits
          </Box>
        </Typography>
      )}
      {operational.length > 0 && (
        <Stack
          direction="row"
          useFlexGap
          sx={{ flexWrap: "wrap", columnGap: 2, rowGap: 0.5, mt: 1.5 }}
        >
          {operational.map(([k, v]) => (
            <Typography key={k} variant="caption" color="text.secondary">
              <Box
                component="b"
                sx={{ color: "text.primary", fontVariantNumeric: "tabular-nums" }}
              >
                {stateFieldValue(k, v)}
              </Box>{" "}
              {lowerFirst(stateFieldLabel(k))}
            </Typography>
          ))}
        </Stack>
      )}
    </Paper>
  );
}

/** Why this exists, and the state fields that produced it. */
export function WhyPanel({ rationale, evidence }:
  { rationale: string | null; evidence: Record<string, unknown> }) {
  const rows = Object.entries(evidence || {})
    .filter(([k, v]) => v !== null && v !== undefined && v !== ""
                        && k !== "state" && k !== "key");
  return (
    <>
      {/* `SectionHeader`, not the `.section-h` div this used to be: §10 names it
          as the replacement, and `HumanLog` — the next block down this same
          column — is already on it. Three sibling headings on three rungs was
          the thing making the column hard to read. */}
      <SectionHeader
        level="widget"
        title="Why this exists"
        tip="Assembled from the numbers that triggered this, not written by a model. Every figure in it appears in the state below, so the sentence can be checked rather than believed."
      />
      {rationale && (
        <Typography variant="body2" sx={{ mb: 1.5, maxWidth: "62ch" }}>{rationale}</Typography>
      )}
      {rows.length > 0 && (
        <Bp sx={{ px: 2, py: 1 }}>
          {/* A fact panel: one row per state field on one decision. The row
              count is the shape of the decision type, not the size of the book,
              which is the test §3 sets — so a `<table>`, not a `DataGrid`. */}
          <table className="facttable">
            <tbody>
              {rows.map(([k, v]) => (
                <tr key={k}>
                  <td>{stateFieldLabel(k)}</td>
                  <td className="fv num">{stateFieldValue(k, v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Bp>
      )}
    </>
  );
}

/** The two figure rungs inside the ranking sum. Sizes come from the theme's
 *  ramp (`body1`, `h4`) rather than from px, so a change to the ramp reaches
 *  them — §4 and §11. */
const FIGURE: SxProps<Theme> = {
  typography: "body1", fontWeight: 600, fontVariantNumeric: "tabular-nums",
};
const TOTAL_FIGURE: SxProps<Theme> = {
  typography: "h4", fontVariantNumeric: "tabular-nums",
};

/** The ranking, shown working. A queue position nobody can check is a queue
 *  position nobody argues with, and one nobody argues with is one nobody
 *  reads. */
export function RankingPanel({ ranking }: { ranking: DecisionRanking }) {
  if (!ranking?.score && ranking?.score !== 0) return null;
  // The dashed edge is the point of this surface: it says "working", where the
  // solid panels above say "figure". Kept, and now taken from the theme's own
  // divider rather than from a `.ranking` rule.
  return (
    <Paper variant="outlined" sx={{ p: 1.5, mt: 2, borderStyle: "dashed" }}>
      <PanelMark sx={{ mb: 0.75 }}>
        <Labelled tip="Money first, lateness second, both capped. The rupee scale is a versioned setting, so re-tuning the queue does not make last quarter's ordering unexplainable.">
          Why it sits here
        </Labelled>
      </PanelMark>
      <Stack
        direction="row"
        spacing={0.75}
        useFlexGap
        sx={{ flexWrap: "wrap", alignItems: "baseline" }}
      >
        <Typography variant="body2">
          <Box component="b" sx={FIGURE}>{ranking.money_points}</Box>{" "}
          from <CurrencyValue value={ranking.financial} />
        </Typography>
        <Typography variant="body2" color="text.secondary">+</Typography>
        <Typography variant="body2">
          <Box component="b" sx={FIGURE}>{ranking.urgency_points}</Box>{" "}
          {ranking.days_past_due
            ? `from ${ranking.days_past_due} days past due`
            : "— nothing was promised"}
        </Typography>
        <Typography variant="body2" color="text.secondary">=</Typography>
        {/* The total is the one figure somebody carries away, so it is a rung
            above its own operands rather than the same size in bold. */}
        <Typography variant="body2">
          <Box component="b" sx={TOTAL_FIGURE}>{ranking.score}</Box>/100
        </Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
        one point per <CurrencyValue value={ranking.rupees_per_point} /> · money caps at{" "}
        {ranking.money_cap}, lateness at {ranking.urgency_cap}
      </Typography>
    </Paper>
  );
}

/** What can be done. Presented, never chosen — so they are rendered as a list
 *  of equals rather than one primary button and some alternatives. */
export function ActionsPanel({ actions }: { actions: DecisionAction[] }) {
  if (!actions?.length) return null;
  return (
    <>
      <SectionHeader
        level="widget"
        title="Available actions"
        tip="What this situation permits. The platform lists them and does not pick one — which of these is right depends on the customer, the supplier and the month, and none of that is in the data."
      />
      {/* `.actions-list` stays: its rule carries the "equals, not a primary and
          some alternatives" reasoning in `styles.css`, it reads the theme's own
          divider, and no kit component covers a list of inert options. */}
      <ul className="actions-list">
        {actions.map((a) => <li key={a.key}>{a.label}</li>)}
      </ul>
    </>
  );
}

/* ── the decision card ────────────────────────────────────────────────────── */

/** How many fact chips fit on a card before it stops being scannable. Beyond
 *  this the card says how many it is holding back rather than silently showing
 *  a quarter of them — the same honesty the observability panels had to learn.
 *
 *  Safe to count: `facts` is already filtered to what this reader may see, so
 *  the number never discloses that a restricted fact exists, and it does not
 *  move with a price. */
const CARD_FACTS = 4;

/** One decision, at a glance, wherever a list of them is shown.
 *
 * Extracted from `CustomerScreen`, which had the only copy, when the landing
 * screen needed the same thing. A second copy would have been the easy move and
 * the wrong one: the two producers render differently (see below), and two
 * copies of that branch is one place for a state decision to start rendering as
 * a signal one worth nothing.
 *
 * **Two origins, two claims.** A signal decision says "here is a reading of the
 * evidence" — so it shows the interpretation and the facts the detector
 * measured. A state decision says "here is what this is worth, and here is the
 * arithmetic" — so it shows the impact and the rationale assembled from the
 * numbers that triggered it, and never the interpretation, because there is no
 * model in that path at all. Read from `origin` rather than sniffed from which
 * fields happen to be populated.
 *
 * A `Paper` (through `Bp`), not a `Card`, and the `.dcard*` classes stay on it:
 * `.dp-cards.tight .dcard` is the queue screen's own rule, so the class is how
 * the compact variant gets its tighter padding from outside.
 */
export function DecisionCard({
  d, openPath, onOpened, compact = false,
}: {
  d: DecisionDetail;
  /** Where this card goes, given its decision id — `pathFor` from `route.ts`,
   *  never a URL written out here.
   *
   *  A path rather than the `onOpen: (id) => void` this used to take, because
   *  the Open below is an `<a href>` now: a handler navigates on a left click
   *  and does nothing at all on ctrl-click, middle-click or "open in a new
   *  tab", which is what `ui-standards.md` §9 is about — two screens side by
   *  side is the ordinary way this desk is used, and a button cannot open one.
   *  The shape is `viz/Dependency`'s `openPath`: the caller owns the
   *  destination, the component owns the control. */
  openPath: (id: string) => string;
  /** What a press still has to record — the VIEW trail entry — now that it no
   *  longer navigates. Optional: the anchor opens the decision with or without
   *  it, so a card rendered somewhere with nothing to record still works.
   *
   *  It cannot fire on a middle-click, which raises no `click` event at all.
   *  That leaves a gap in the trail rather than in the navigation, and the
   *  alternative is the button that made those presses do nothing whatever. */
  onOpened?: (id: string) => void;
  /** Drop the impact basis and trim the fact chips. Used where the card is one
   *  of several on a screen that is not only about decisions. */
  compact?: boolean;
}) {
  const fromState = d.origin === "STATE";
  const facts = d.facts.filter((f) => !f.restricted && isPrimaryFact(f.label));
  const held = facts.length - CARD_FACTS;
  return (
    <Bp className="dcard">
      <div className="dcard-top">
        <span className="dcard-type">{typeLabel(d.decision_type)}</span>
        <Pri band={d.priority.band} />
        <span className="dcard-subject">{d.subject_label}</span>
        <span className="dp-spacer" />
        {/* The card's row action, and on a phone the only way into the
            decision. `TOUCH` so it is hittable with a thumb — see kit.TOUCH.
            `component={RouterLink}` rather than `InlineLink`, which §9 offers
            for the same job: this is a control at the end of a row, not a name
            inside a sentence, and the underlined body-text link that component
            renders would drop both the button shape and the thumb target.
            `aria-label` because a screen reader listing the links on a queue of
            five cards otherwise hears "Open" five times with nothing to tell
            them apart — the row supplies that context only on screen. */}
        <Button
          component={RouterLink}
          to={openPath(d.decision_id)}
          variant="text"
          size="small"
          aria-label={`Open: ${typeLabel(d.decision_type)} on ${d.subject_label}`}
          sx={TOUCH}
          onClick={() => onOpened?.(d.decision_id)}
        >
          Open →
        </Button>
      </div>

      {fromState ? (
        <>
          {d.impact?.financial != null && (
            <div className="dcard-impact">
              <b>
                {/* The doubled selector is not decoration. `.dcard-impact span`
                    in `styles.css` is (0,1,1) and an `sx` class is (0,1,0), so
                    the rule written for the basis sentence beside this figure
                    would otherwise take the figure itself down to its 12.5px
                    grey. The class stays — `.dp-cards.tight .dcard` is the
                    queue screen's own rule, per the note at the top of this
                    file — and the real fix is one character in the stylesheet
                    (`.dcard-impact > span`), which is not this file's to make. */}
                <CurrencyValue
                  value={d.impact.financial}
                  sx={{ "&&": { fontSize: "inherit", color: "inherit" } }}
                />
              </b>
              {/* The sentence matters as much as the figure: capital locked and
                  revenue at risk can be the same number and are not the same
                  claim, and a reader who sums them across cards is wrong. */}
              {!compact && d.impact.basis && <span>{d.impact.basis}</span>}
            </div>
          )}
          {d.rationale && <div className="dcard-reason">{d.rationale}</div>}
        </>
      ) : (
        <>
          {/* Clamped to two lines in a list, in full on a detail page.
              A card in a list is scanned against its neighbours, so uneven
              paragraphs make the list harder to read than the same text set
              short — and when the model is degraded, or is the mock provider,
              every explanation is the same sentence and five full copies of it
              push the facts that DO differ below the fold. Clamped rather than
              hidden, because when the readings differ the first line is the most
              useful thing on the card. */}
          {d.interpretation.explanation && (
            <div className={`dcard-reason${compact ? " clamp" : ""}`}>
              {d.interpretation.explanation}
            </div>
          )}
          {facts.length > 0 && (
            <div className={`dcard-chips${compact ? " tight" : ""}`}>
              {facts.slice(0, CARD_FACTS).map((f) => <FactChip key={f.label} f={f} />)}
              {held > 0 && (
                <Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>
                  +{held} more facts
                </Typography>
              )}
            </div>
          )}
        </>
      )}
    </Bp>
  );
}
