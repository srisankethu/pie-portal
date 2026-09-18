import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import type { ReactNode } from "react";
import {
  FactTable, FieldLabel, FormDialog, Meta, PanelMark, StatusChip, TOUCH,
} from "../platform/kit";
import type { Tone } from "../platform/kit";

/**
 * One quote line's commercial diagnosis, as a salesperson sees it.
 *
 * **Every string on this card came from the server already written.** The
 * headline, the reason, the note and the qualification are assembled by
 * `commercial/quote_diagnosis/render.py` from values the deterministic engine
 * computed; nothing here composes a sentence, formats a figure, or decides what
 * a code means. That is not tidiness — a rule worded one way on the server and
 * another way in the browser is two rules, and the one people read is the one
 * that was never reviewed.
 *
 * **Two cards, picked by the payload, never by a role check in the browser.**
 * The server already chose: a salesperson is served `view: "OPERATIONS"`, built
 * from a record type with no cost field on it, and a manager or owner is served
 * `view: "OWNER"`, built from one that has them. `DiagnosisCard` draws the
 * first and `OwnerDiagnosisCard` the second, and the union below is discriminated
 * on that field — so picking the wrong one is a type error rather than a leak.
 * A single component branching on a role prop is exactly the shape this
 * repository keeps being bitten by, and it would put the decision in the one
 * place that cannot see the policy.
 *
 * **There is nothing to withhold here.** The payload behind this component is
 * built from a record type with no cost, margin, opportunity or peer field on
 * it, so the component has no `{mgmt && …}` guard and needs none. `MFLOOR` was
 * a guard that was right with one line below it that was not; this card cannot
 * have that shape because the data never arrives.
 *
 * **And the one thing that is withheld is withheld by the type.** The owner
 * projection gained an `attribution` — how much of a line's margin movement the
 * price decision owns and how much the cost level does — and every figure in it
 * is derived from purchase cost. It is declared on `OwnerDiagnosisView` alone,
 * so a salesperson's card cannot read one without a compile error. The desk's
 * whole vocabulary for this stays the sentence it already had: margin
 * compressed by supply cost, no number attached.
 *
 * **Silent by default.** `renders` is the server's answer to "is this worth
 * interrupting somebody for", and the component returns nothing when it is
 * false. A caller that drew the card anyway would be overriding a threshold
 * decision made against a versioned policy, from a place that has no idea what
 * the policy is.
 */

/** What both projections answer, under the same names.
 *
 *  `renders` was `surfaces` on the owner half until both halves had a card to
 *  draw. One answer published under two names is what let a manager be served
 *  two flagged lines, match neither, and be told the quote was clean.
 */
type DiagnosisCommon = {
  quote_diagnosis_id: string | null;
  line_id: string;
  /** The server's answer to "is this worth interrupting somebody for". A
   *  caller that drew the card anyway would be overriding a threshold decision
   *  made against a versioned policy, from a place that has no idea what the
   *  policy is. */
  renders: boolean;
  /** Whether the engine had comparable evidence for this line at all.
   *
   *  Not the same as `renders`, and the gap between them is what a reader of a
   *  finished document needs: a line that does not render may have been judged
   *  and found ordinary, or may never have been comparable to anything. Only
   *  the first is good news. */
  comparable: boolean;
  headline: string;
  /** Qualifiers the engine attached, allowlist-filtered for the desk.
   *
   *  `EVIDENCE_WITHHELD` is the one a reader of an empty panel needs: it means
   *  rows were found and left out because it is not clear when they became
   *  visible, which is a different fact from this customer never having bought
   *  the item — and only one of the two is something somebody can fix. */
  context: string[];
  /** The grade as a word — "Strong", "Moderate", "Weak", "Not enough" — from
   *  `render.strength_word`, so both cards spell it the one way. */
  strength_word: string;
  qualification: string;
  actions: string[];
};

/** A salesperson's line. No cost, margin, opportunity or peer field exists on
 *  the record type this is built from, so there is none to withhold here. */
export type OperationsDiagnosisView = DiagnosisCommon & {
  view: "OPERATIONS";
  quoted: string;
  historical: string;
  evidence: string;
  evidence_detail: string;
  why: string;
  note: string;
};

/** One attributed factor: what it is, how much it matters, how much to believe
 *  it, and why it is attributable — the browser-side face of `drivers.Driver`.
 */
export type AttributionDriverView = {
  /** The engine's own vocabulary — `PRICE_POSITION_EFFECT`, `COST_LEVEL_EFFECT`.
   *  No name is served for it and none is invented here: a term worded one way
   *  on the server and another in the browser is two terms, and the one people
   *  read is the one that was never reviewed. The underscores are opened out
   *  and nothing else. */
  code: string;
  /** `MAJOR` / `MINOR` / `NEGLIGIBLE` — how much this factor matters, graded
   *  against versioned thresholds. */
  severity: string;
  /** `Strong` / `Moderate` / `Weak` / `Not enough` — how much to believe it.
   *  The same four words `DiagnosisCommon.strength_word` grades the whole
   *  diagnosis on, deliberately: one evidence ladder, not a second one. */
  strength_word: string;
  /** **Already formatted, server-side** — money or percentage points, written
   *  by `render_owner`. Rendered verbatim. Nothing in this file formats a
   *  figure, picks a currency symbol, or does arithmetic; `CLAUDE.md` §3 puts
   *  every number a screen renders in `commercial/`. */
  effect: string;
  /** The counterfactual this number answers, in words — not a restatement of
   *  it. */
  basis: string;
};

/** The split between the price decision and the cost level, or a refusal to
 *  assert one.
 *
 *  **Declared on the owner projection and nowhere else, and that is the whole
 *  guarantee.** Every figure in here is derived from purchase cost — which is
 *  why `drivers.py` opens by saying an `Attribution` has no place on
 *  `OperationsDiagnosis`. Here that is a type error rather than a review
 *  comment: `d.attribution` on an un-narrowed `DiagnosisView` does not compile,
 *  and an operations literal carrying the key is an excess-property error.
 *
 *  Deliberately **not** written as `attribution?: never` on the operations
 *  half. That would put the property on both members of the union, typed
 *  `… | undefined`, and a reader could then reach it with no narrowing at all.
 *  Absent is stronger than absent-and-declared.
 *
 *  Two shapes, exactly as the server has: either `drivers` holds the factors
 *  and `headline` is the split in one sentence, or `drivers` is empty and
 *  `note` says what stopped it. There is no third state, and an empty one is
 *  not silence — see `AttributionBlock`.
 */
export type AttributionView = {
  renders: boolean;
  /** The split in one sentence; `""` when nothing is asserted. */
  headline: string;
  drivers: AttributionDriverView[];
  /** The qualification, or the refusal, in words; `""` when there is none. */
  note: string;
};

/** A manager's or owner's line. RESTRICTED: `lines` and `opportunity` are
 *  written by `render_owner` and do carry cost and margin, which is why the
 *  server only ever builds this for a principal whose role may see them. The
 *  browser does not re-decide that — it draws what arrived. */
/** What the cash tied up in one line costs, or a refusal to say.
 *
 *  **Declared on the owner projection and nowhere else**, for the reason
 *  `AttributionView` is — only blunter. `capital_per_unit` *is* the purchase
 *  cost, carried rather than derived, so this is not a boundary somebody could
 *  walk towards the number; it is the number. Reaching it on an un-narrowed
 *  `DiagnosisView` does not compile, and an operations literal carrying the key
 *  is an excess-property error.
 *
 *  Two shapes, exactly as the server has: either `figures` holds the reading
 *  and `headline` states the money, or `figures` is empty and `note` says what
 *  stopped it. There is no third state, and an empty one is not silence — see
 *  `WorkingCapitalBlock`.
 */
export type WorkingCapitalView = {
  /** Whether a figure was produced at all. **Read this, never `figures.length`
   *  on its own:** a line whose supplier credit covers the wait is assessed and
   *  charged exactly nothing, and a component that asked "is there a number"
   *  would report that real answer as a refusal. The server knows which it is
   *  and says so. */
  assessed: boolean;
  /** Whether this reading should interrupt somebody — the line's own surfacing
   *  gate narrowed by the reading's. It decides the register the headline is
   *  printed in and nothing else: the figures are on the card either way,
   *  because a card somebody opened is not an interruption. */
  interrupts: boolean;
  renders: boolean;
  /** The money in one sentence; `""` when nothing is asserted. */
  headline: string;
  /** Label and value, **already formatted server-side** — days, rupees and a
   *  percentage. Nothing in this file formats a figure, picks a currency
   *  symbol, or does arithmetic; `CLAUDE.md` §3 puts every number a screen
   *  renders in `commercial/`. */
  figures: { label: string; value: string }[];
  /** `MAJOR` / `MINOR` / `NEGLIGIBLE`, or `""` on a refusal. */
  severity: string;
  /** How much to believe it, or `""` on a refusal. */
  strength_word: string;
  /** The engine's own sentence — what the number answers, or the refusal and
   *  the Settings field that would finish it. */
  note: string;
};

export type OwnerDiagnosisView = DiagnosisCommon & {
  view: "OWNER";
  /** The report, one claim per sentence, already worded. */
  lines: string[];
  opportunity: string;
  /** A sentence about the evidence, not a word — the word is `strength_word`. */
  evidence: string;
  codes: string[];
  /** How much of this line's margin movement the price decision owns and how
   *  much the cost level does. Owner-only, by construction — see
   *  `AttributionView`. */
  attribution: AttributionView;
  /** What the cash tied up in this line costs. Owner-only, by construction —
   *  see `WorkingCapitalView`. */
  working_capital: WorkingCapitalView;
};

export type DiagnosisView = OperationsDiagnosisView | OwnerDiagnosisView;

export type DismissReason = { code: string; label: string };

/** Evidence strength, as a `Chip` rather than coloured text — `ui-standards` §6.
 *
 *  Strong is not "good" and weak is not "bad": they say how much the band is
 *  worth believing, so the tone ramps with confidence and stops short of
 *  success/error, which would read as a verdict on the price. */
const STRENGTH_TONE: Record<string, Tone> = {
  Strong: "good",
  Moderate: "info",
  Weak: "warn",
};

/** How much a driver matters, as a `Chip` — `ui-standards` §6.
 *
 *  **A ramp of attention, not a verdict.** Severity is symmetric by design:
 *  `drivers.severity` grades a five-point *gain* as hard as a five-point loss,
 *  so a success/error hue would pass judgement on a sign this chip cannot see —
 *  `effect` arrives already written and the component does not read it.
 *
 *  **It is the only chip in a driver row, and that is the point.** Severity is
 *  how much a factor moved margin; strength is how much evidence stands behind
 *  it. A nine-point effect off a two-row band is severe and unbelievable at
 *  once, so the two cannot share a ramp — and two chips stepping through the
 *  same five tones beside each other would be read as one whatever the words
 *  said. So severity is a chip attached to the figure it grades, strength is a
 *  labelled word attached to the sentence it grades, and each says in words
 *  which question it answers.
 */
const SEVERITY_TONE: Record<string, Tone> = {
  MAJOR: "warn",
  MINOR: "info",
  NEGLIGIBLE: "neutral",
};

export function DiagnosisCard({
  diagnosis, reasons, onReviewPrice, onDismiss, dismissing = false,
}: {
  diagnosis: OperationsDiagnosisView;
  /** Served by `GET /api/v1/quote-diagnosis/reasons`, never hardcoded here: a
   *  front end offering a reason the service refuses is a dead button somebody
   *  discovers in front of a customer. */
  reasons: DismissReason[];
  onReviewPrice?: (lineId: string) => void;
  onDismiss?: (diagnosisId: string, reasonCode: string, note: string) => void;
  dismissing?: boolean;
}) {
  return (
    <DiagnosisShell
      diagnosis={diagnosis}
      detail={diagnosis.evidence_detail}
      reasons={reasons}
      onReviewPrice={onReviewPrice}
      onDismiss={onDismiss}
      dismissing={dismissing}
    >
      {/* A label and a value, two rows — the case `ui-standards` §3 keeps a
          `<table>` for. Its row count is fixed by the card, not by the size
          of the business, so a DataGrid here would be a grid for two rows. */}
      <FactTable
        label={`Price comparison for line ${diagnosis.line_id}`}
        rows={[
          ["Quoted", diagnosis.quoted],
          ["Historical", diagnosis.historical],
        ]}
      />

      <Box>
        <FieldLabel>Why?</FieldLabel>
        <Typography variant="body2" sx={{ mt: 0.5 }}>{diagnosis.why}</Typography>
        {diagnosis.note && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {diagnosis.note}
          </Typography>
        )}
      </Box>
    </DiagnosisShell>
  );
}

/**
 * The same line for a manager or an owner, from the projection that has the
 * economics on it.
 *
 * **This is the card a manager had never been shown.** The server had been
 * building the owner report since the engine landed; nothing in the front end
 * drew it, so every manager on the Quote Builder and on an ERP quote saw no
 * card at all and read only the one-line summary underneath — which, until
 * recently, told them the quote was clean.
 *
 * **It is prose, not a fact table, because the report is prose.** `render_owner`
 * writes one claim per sentence — the band and where the quote sits in it, what
 * was trimmed as an outlier and why, the cost baseline or a refusal to estimate
 * one, the peer band, and any observed price resistance. Laying those out as
 * labelled figures would mean parsing sentences the server wrote, which is the
 * one thing `DiagnosisCard`'s own docstring says not to do. The opportunity is
 * separated out because it is the part a manager acts on.
 */
export function OwnerDiagnosisCard({
  diagnosis, reasons, onReviewPrice, onDismiss, dismissing = false,
}: {
  diagnosis: OwnerDiagnosisView;
  reasons: DismissReason[];
  onReviewPrice?: (lineId: string) => void;
  onDismiss?: (diagnosisId: string, reasonCode: string, note: string) => void;
  dismissing?: boolean;
}) {
  return (
    <DiagnosisShell
      diagnosis={diagnosis}
      detail={diagnosis.evidence}
      reasons={reasons}
      onReviewPrice={onReviewPrice}
      onDismiss={onDismiss}
      dismissing={dismissing}
    >
      {/* First, because it is the thing an owner opened this card for. The
          shell above says the line is above or below its band; this says which
          half of that the price decision owns and which half the cost level
          does — the question the engine could not answer until now. The report
          and the opportunity keep their order underneath. */}
      <AttributionBlock attribution={diagnosis.attribution} />

      {/* Under it, because it answers a different question: the split above is
          about the price that was decided, this is about the money that leaves
          the bank before the customer pays it back. An owner reads the first
          and then asks the second. */}
      <WorkingCapitalBlock capital={diagnosis.working_capital} />

      <Box>
        <FieldLabel>What the evidence says</FieldLabel>
        <Stack spacing={1} sx={{ mt: 0.5 }}>
          {diagnosis.lines.map((line, i) => (
            <Typography key={i} variant="body2">{line}</Typography>
          ))}
        </Stack>
      </Box>

      <Box>
        <FieldLabel>Opportunity</FieldLabel>
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          {diagnosis.opportunity}
        </Typography>
      </Box>
    </DiagnosisShell>
  );
}

/** The one sentence on this card the server did not write — and it is about
 *  this card's own input, not about the business.
 *
 *  A refusal arrives with a `note` saying what was missing. When even that is
 *  empty, or the key is absent altogether because the payload predates it, the
 *  block still has to say that nothing was asserted. It names the absence and
 *  claims nothing else.
 */
const NOTHING_ASSERTED =
  "No split between the price decision and the cost level was asserted for "
  + "this line, and no reason was given.";

/**
 * How much of this line's margin movement the price decision owns, and how
 * much the cost level does.
 *
 * **A refusal is content here, not an empty state.** When the engine will not
 * assert a split — no cost baseline, a purchase whose visibility had to be
 * estimated, a reconciliation that did not hold — it says so in `note`, and
 * that sentence is the output. A block that drew nothing in that case would
 * read as "all clear", which is `CLAUDE.md` §1's *absence of evidence is not a
 * pass* wearing a layout instead of an `or 0`. So the section header is printed
 * either way and the refusal is an `Alert`, which is a thing on the screen
 * rather than the absence of one.
 *
 * `info` rather than `warning`: an engine declining to split a movement it
 * cannot account for is the engine being honest, not a fault.
 *
 * The prop is optional although `OwnerDiagnosisView.attribution` is not. A
 * payload served before the key existed would otherwise take the whole Quote
 * Builder down on a property read, and "nothing was asserted" is the true thing
 * to say about it — which is the same answer the refusal branch already gives.
 */
function AttributionBlock({ attribution }: { attribution?: AttributionView }) {
  const drivers = attribution?.drivers ?? [];
  const asserted = attribution?.renders === true && drivers.length > 0;
  const headline = attribution?.headline ?? "";
  const note = attribution?.note ?? "";

  return (
    <Box>
      <FieldLabel
        tip={"Severity is how much a factor moved margin. Confidence is how "
             + "much evidence stands behind it. They are different questions: "
             + "a large effect read off a thin history is severe and weakly "
             + "evidenced at the same time."}
      >
        What moved the margin
      </FieldLabel>

      {asserted ? (
        <>
          {headline && (
            <Typography variant="body2" sx={{ mt: 0.5 }}>{headline}</Typography>
          )}
          <Stack spacing={1.5} sx={{ mt: 1.5 }}>
            {drivers.map((d) => <AttributionDriver key={d.code} driver={d} />)}
          </Stack>
          {/* A qualification on an asserted split, in the same muted register
              as the card's standing one. */}
          {note && <Meta sx={{ mt: 1.5 }}>{note}</Meta>}
        </>
      ) : (
        <Alert severity="info" sx={{ mt: 0.5 }}>{note || NOTHING_ASSERTED}</Alert>
      )}
    </Box>
  );
}

/** The one sentence in this block the server did not write — and it is about
 *  this card's own input, not about the business.
 *
 *  A refusal arrives with a `note` naming what was missing, usually a Settings
 *  field one person fills in. When even that is empty, or the key is absent
 *  because the payload predates it, the block still has to say that nothing was
 *  read. It names the absence and claims nothing else.
 */
const NOTHING_READ =
  "What the cash tied up in this line costs was not read, and no reason was "
  + "given.";

/**
 * What the money on this line costs while it is out.
 *
 * **A refusal is content here, not an empty state.** The rate this reading is
 * levied at is owner-set with no default, so the commonest answer on a book
 * nobody has configured is "no annual cost of capital is set" — and the fix is
 * one person typing one number into Settings. The server's `note` names that
 * field, so the refusal is printed rather than swallowed. A block that drew
 * nothing would read as "this line ties up no cash", which is never true of a
 * line, and is `CLAUDE.md` §1's *absence of evidence is not a pass* wearing a
 * layout instead of an `or 0`.
 *
 * `info` rather than `warning`: an engine declining to price a cycle it has no
 * rate for is the engine being honest, not a fault.
 *
 * **`assessed` decides the shape, `interrupts` decides the register.** The
 * first is the server's own flag and is read rather than re-derived from
 * whether a figure is present — a line whose supplier funds it outright is
 * assessed and charged nothing, and asking "is there a number" would file that
 * under refusal. The second is the surfacing gate: a reading worth interrupting
 * somebody for leads with an `Alert`, and one that is not says the same
 * sentence in the same words, quietly. Neither ever hides a figure.
 *
 * The prop is optional although `OwnerDiagnosisView.working_capital` is not, for
 * the reason `AttributionBlock`'s is: a payload served before the key existed
 * would otherwise take the Quote Builder down on a property read.
 */
function WorkingCapitalBlock({ capital }: { capital?: WorkingCapitalView }) {
  const assessed = capital?.assessed === true;
  const figures = capital?.figures ?? [];
  const note = capital?.note ?? "";

  return (
    <Box>
      <FieldLabel
        tip={"The money leaves when the supplier is paid and comes back when "
             + "the customer does. What is counted is the gap between the two "
             + "— any time the goods sit on the shelf is not in it, so the "
             + "charge is a floor rather than a ceiling."}
      >
        What the cash on this line costs
      </FieldLabel>

      {assessed ? (
        <>
          {capital?.interrupts ? (
            <Alert severity="warning" sx={{ mt: 0.5 }}>{capital.headline}</Alert>
          ) : (
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              {capital?.headline}
            </Typography>
          )}

          <Stack
            direction="row"
            spacing={1}
            useFlexGap
            sx={{ mt: 1, alignItems: "center", flexWrap: "wrap" }}
          >
            <StatusChip
              label={capital?.severity ?? ""}
              tone={SEVERITY_TONE[capital?.severity ?? ""] ?? "neutral"}
              tip="How much this matters — the size of its effect on margin."
            />
            <Meta>Confidence in this: {capital?.strength_word}</Meta>
          </Stack>

          {/* A label and a value, seven rows — the case `ui-standards` §3 keeps
              a `<table>` for. Its row count is fixed by the reading, not by the
              size of the business. */}
          <Box sx={{ mt: 1.5 }}>
            <FactTable
              label="What the cash on this line costs"
              rows={figures.map((f) => [f.label, f.value])}
            />
          </Box>

          {note && <Meta sx={{ mt: 1.5 }}>{note}</Meta>}
        </>
      ) : (
        <Alert severity="info" sx={{ mt: 0.5 }}>{note || NOTHING_READ}</Alert>
      )}
    </Box>
  );
}

/**
 * One factor: the figure, how much it matters, why it is attributable, and how
 * much of the evidence stands behind it — in that order, because that is the
 * order somebody reads them in.
 *
 * Nothing here is computed. `effect` is money or percentage points the server
 * already wrote, printed verbatim; `severity` and `strength_word` are its
 * words; `code` is its vocabulary with the underscores opened out. The only
 * decisions this component makes are which tone a severity chip takes and
 * where on the row each thing sits.
 */
function AttributionDriver({ driver }: { driver: AttributionDriverView }) {
  return (
    <Box>
      {/* No square: the card already carries one on its own mark, and a second
          per driver would compete with it. */}
      <PanelMark>{driver.code.replace(/_/g, " ")}</PanelMark>

      <Stack
        direction="row"
        spacing={1}
        useFlexGap
        /* Wraps rather than overflowing: at 390px a long effect and its chip
           do not share a row, and this desk quotes from a machine shop. */
        sx={{ mt: 0.25, alignItems: "center", flexWrap: "wrap" }}
      >
        <Typography
          variant="subtitle1"
          sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
        >
          {driver.effect}
        </Typography>
        <StatusChip
          label={driver.severity}
          tone={SEVERITY_TONE[driver.severity] ?? "neutral"}
          tip="How much this factor matters — the size of its effect on margin."
        />
      </Stack>

      <Typography variant="body2" sx={{ mt: 0.5 }}>{driver.basis}</Typography>
      {/* Strength, deliberately not a chip — see `SEVERITY_TONE`. The label is
          the half that keeps it apart from severity; the word is the server's. */}
      <Meta>Confidence in this: {driver.strength_word}</Meta>
    </Box>
  );
}

/**
 * Everything the two cards genuinely share: the surface, the headline, the
 * strength chip, the standing qualification, and the two actions.
 *
 * Shared because it is one design, not to abstract over the difference — the
 * difference is the `children`, and it stays in the component that owns the
 * projection. Nothing economic passes through here: `renders` is a threshold
 * decision, `strength_word` grades the band, and `detail` is whatever sentence
 * the caller's own projection already wrote.
 */
function DiagnosisShell({
  diagnosis, detail, reasons, onReviewPrice, onDismiss, dismissing, children,
}: {
  diagnosis: DiagnosisCommon;
  detail: string;
  reasons: DismissReason[];
  onReviewPrice?: (lineId: string) => void;
  onDismiss?: (diagnosisId: string, reasonCode: string, note: string) => void;
  dismissing: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  if (!diagnosis.renders) return null;

  const canDismiss = Boolean(diagnosis.quote_diagnosis_id && onDismiss);

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack spacing={2}>
        <Box>
          <PanelMark mark="ink">Quote diagnosis</PanelMark>
          <Typography variant="subtitle1" sx={{ mt: 0.5, fontWeight: 600 }}>
            {diagnosis.headline}
          </Typography>
        </Box>

        <Box>
          <FieldLabel>Evidence</FieldLabel>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, alignItems: "center" }}>
            <StatusChip
              label={diagnosis.strength_word}
              tone={STRENGTH_TONE[diagnosis.strength_word] ?? "neutral"}
            />
            <Meta>{detail}</Meta>
          </Stack>
        </Box>

        {children}

        {/* Printed on every card without exception. A reader who is not told
            that history can hold unrecorded exceptional pricing reads a band as
            a rule, and these books genuinely hold deals done outside them. */}
        <Meta>{diagnosis.qualification}</Meta>

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          /* Stacked full-width on a phone: this desk quotes from a machine
             shop, and two buttons sharing a 390px row are two 44px targets a
             thumb has to choose between. */
          sx={{ "& .MuiButton-root": { ...TOUCH } }}
        >
          {diagnosis.actions.includes("REVIEW_PRICE") && (
            <Button
              variant="contained"
              onClick={() => onReviewPrice?.(diagnosis.line_id)}
              disabled={!onReviewPrice}
            >
              Review price
            </Button>
          )}
          {diagnosis.actions.includes("DISMISS") && (
            <Button
              variant="outlined"
              onClick={() => setOpen(true)}
              disabled={!canDismiss}
            >
              Dismiss — reason?
            </Button>
          )}
        </Stack>
      </Stack>

      <DismissDialog
        open={open}
        reasons={reasons}
        busy={dismissing}
        onClose={() => setOpen(false)}
        onConfirm={(code, note) => {
          if (diagnosis.quote_diagnosis_id) {
            onDismiss?.(diagnosis.quote_diagnosis_id, code, note);
          }
          setOpen(false);
        }}
      />
    </Paper>
  );
}

/**
 * Why this card was wrong, in a vocabulary somebody can count.
 *
 * `FormDialog` rather than `Dialog`: this is a form somebody fills in, so it is
 * full screen below `sm` — `ui-standards` §12. A centred sheet at 390px is a
 * letterbox with its own scrollbar inside the page's.
 *
 * The reason is required and the note is not, which is the whole design. A
 * dismissal is the cheapest labelled data this engine will ever get, and free
 * text nobody can aggregate tunes nothing.
 */
function DismissDialog({
  open, reasons, busy, onClose, onConfirm,
}: {
  open: boolean;
  reasons: DismissReason[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (reasonCode: string, note: string) => void;
}) {
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");

  return (
    <FormDialog open={open} onClose={onClose} maxWidth="xs">
      <DialogTitle>Dismiss this diagnosis</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Telling us why is how a rule that is wrong for this business gets
            found. It is the only thing that changes what you are shown next.
          </Typography>
          <TextField
            select
            required
            label="Reason"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            sx={{ ...TOUCH }}
          >
            {reasons.map((r) => (
              <MenuItem key={r.code} value={r.code}>{r.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="Anything else (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            multiline
            minRows={2}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ ...TOUCH }}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!code || busy}
          onClick={() => onConfirm(code, note)}
          sx={{ ...TOUCH }}
        >
          Dismiss
        </Button>
      </DialogActions>
    </FormDialog>
  );
}
