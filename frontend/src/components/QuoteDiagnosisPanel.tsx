/** The diagnosis cards for a quote, under the grid.
 *
 * **This is the mount `DiagnosisCard` never had.** The engine, the five
 * endpoints and the card all shipped together and nothing imported the card, so
 * no screen has ever rendered a diagnosis. The gap was invisible from either
 * side: the backend suite passed, the component suite passed, and the only test
 * that could have caught it — one asserting somebody can *see* a diagnosis —
 * belonged to neither file.
 *
 * **Silent by default, which is most of the time — about the cards.** One card
 * per line the server said `renders` for, and none at all when it said so for
 * none. That is the engine's own threshold decision, made against a versioned
 * policy; drawing a card anyway would override it from a place that cannot know
 * what the policy is.
 *
 * **The quote as a whole is not silent, and that is a change.** This paragraph
 * used to end "a quote whose lines are all ordinary shows nothing here, and that
 * is the correct screen rather than an empty one", and it was wrong in the one
 * direction this repository keeps being wrong in: a panel that draws nothing
 * reads as "all clear", which covers both "every line was judged and none was
 * unusual" and "half of them could not be compared against anything". Only the
 * first is good news. So `coverage` — what was checked and what could not be, in
 * the server's own sentence — draws whenever the server answered at all, on both
 * screens and for both roles. The ERP quote page had already reached this
 * conclusion for itself and passed its own sentence in; what is new is that the
 * engine now writes one, so the Quote Builder gets it too.
 *
 * **And the owner is told what the quote comes to, with the lines its total does
 * not show.** A quote at 22% with one line at −14% is the ordinary case this
 * whole line-level engine exists for, and that line frequently renders no card:
 * its price can be exactly what this customer has always paid. Reading the cards
 * alone would miss it. `rollup.loss_lines` is on the owner's payload whatever the
 * total says, and it is drawn here whatever the cards say.
 *
 * **A failure is not silence.** When the request fails the panel says so. A
 * panel that is quiet because it could not ask looks exactly like a panel that
 * is quiet because there was nothing to say, and only one of those is good news
 * — which is the `absence of evidence is not a pass` rule applied to a screen
 * rather than to a calculation.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { DiagnosisCard, OwnerDiagnosisCard } from "./DiagnosisCard";
import type { DismissReason } from "./DiagnosisCard";
import { FactTable, FieldLabel, Meta, PanelMark } from "../platform/kit";
import type { QuoteDiagnosisState } from "../useQuoteDiagnosis";

/** What was checked on this quote and what could not be. **No money anywhere.**
 *
 *  Served to both roles under one key, and it is safe there because of the type
 *  it is built from rather than because of anything this file does:
 *  `rollup.QuoteCoverage` declares no cost, margin or value field at all — not
 *  withheld, absent — so there is nothing here for a `{mgmt && …}` to guard.
 */
export type CoverageView = {
  quote_id: string;
  /** **True on every quote**, including the one where nothing was diagnosed.
   *  That is the point rather than a quirk — see `QuoteDiagnosisPanel`. */
  renders: boolean;
  /** What was checked and what could not be, **written server-side**. Never
   *  empty, and rendered verbatim: `CLAUDE.md` §3 puts every sentence a screen
   *  renders in `commercial/`. */
  headline: string;
  /** Counts, already spelled. A fixed handful of rows. */
  figures: { label: string; value: string }[];
};

/** One line that loses money at the price quoted. RESTRICTED. */
export type LossLineView = {
  line_id: string;
  product_id: string;
  /** What it loses and against what, **already formatted server-side** in the
   *  tenant's own currency. Rendered verbatim. */
  sentence: string;
};

/** What the quote comes to, and the lines its total does not show.
 *
 *  **Declared here and reached only from the owner's branch of the payload**,
 *  which is where the guarantee lives: `routers.quote_diagnosis._quote_level`
 *  puts `rollup` on one branch and there is no key on the other, so a
 *  salesperson's response carries none of this rather than carrying it hidden.
 */
export type RollupView = {
  quote_id: string;
  renders: boolean;
  /** What this quote comes to, in one sentence — **the loss first where there
   *  is one**, whatever the total says. */
  headline: string;
  /** Whether reading the total alone would miss a line that loses money. The
   *  server's own flag, not `loss_lines.length && margin >= 0` rebuilt here: a
   *  predicate re-derived downstream from published fields is a guess about
   *  what the producer meant. */
  total_hides_a_loss: boolean;
  /** **Every loss-making line, worst first, and the key is always present.** An
   *  empty list means checked and none found; a missing one would be read as the
   *  same thing and means something else entirely. */
  loss_lines: LossLineView[];
  figures: { label: string; value: string }[];
  /** The largest factor across the quote, or the refusal to name one, in words.
   *  Never blank — a roll-up read back from the store totals money perfectly
   *  well and can name no factor at all. */
  dominant: string;
  /** What the totals rest on and what is left out of them, with the loss lines
   *  named first. */
  note: string;
};

/** The dismissal vocabulary, served rather than hardcoded.
 *
 *  A front end offering a reason the service refuses is a dead button somebody
 *  discovers in front of a customer, which is why `/reasons` exists at all.
 *  Fetched once per session-token rather than per card: the list is the same
 *  for every line on every quote.
 */
const DEFAULT_TITLE = "What this customer has paid before";

export function useDismissReasons(token: string): DismissReason[] {
  const [reasons, setReasons] = useState<DismissReason[]>([]);

  useEffect(() => {
    let live = true;
    fetch("/api/v1/quote-diagnosis/reasons",
          { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : { reasons: [] }))
      .then((r) => { if (live) setReasons(r.reasons ?? []); })
      // A failure here costs the dismissal, not the diagnosis. The cards still
      // render; the Dismiss button has nothing to offer and stays disabled,
      // which is better than a card nobody sees because its footer failed.
      .catch(() => { if (live) setReasons([]); });
    return () => { live = false; };
  }, [token]);

  return reasons;
}

export function QuoteDiagnosisPanel({
  lineIds, diagnosis, dismissReasons, onReviewPrice, title, coverage,
}: {
  /** The ids of the lines currently on screen, in order. Read so the cards
   *  follow the grid's own filter — a diagnosis for a line somebody has
   *  filtered away is a card about something they cannot see.
   *
   *  Ids rather than the draft `Line` objects this used to take: the ERP quote
   *  page has lines of a different type and this component read exactly one
   *  field off them. */
  lineIds: string[];
  diagnosis: QuoteDiagnosisState;
  dismissReasons: DismissReason[];
  /** Absent where there is no price to review — an issued document cannot be
   *  re-priced here, and a card offering it would be a dead control. */
  onReviewPrice?: (lineId: string) => void;
  title?: string;
  /** How much of the quote the check could actually judge — **an override, and
   *  no longer the only source of this sentence.**
   *
   *  The server now writes one: `rollup.QuoteCoverage.basis`, on every response
   *  and for both roles, and the panel prints that by default. So a caller
   *  passes this only when it knows something the engine cannot. The ERP quote
   *  page is the one that does: it counts lines that were never put to the
   *  engine at all, because they carry no item code or no price, and a roll-up
   *  over diagnosed lines has no way to see those. Where both exist this one
   *  wins and the server's is not printed as well — two sentences about how
   *  much of a quote was checked is two answers to one question.
   *
   *  **It shows above the cards as well as instead of them.** It used to render
   *  only when nothing was flagged, which made its truth depend on this
   *  component's filter rather than on its own arithmetic — and a sentence that
   *  is true because of what some other component decided to draw is a sentence
   *  that goes wrong the next time that decision changes. Coverage is also
   *  worth reading *with* two cards in front of you: two lines flagged out of
   *  four judged out of eight is a different quote from two out of eight. */
  coverage?: ReactNode;
}) {
  const shown = lineIds
    .map((id) => diagnosis.byLineId[id])
    .filter((d) => d && d.renders);

  if (diagnosis.error) {
    return (
      <Box sx={{ mt: "var(--space-4)" }}>
        <Alert severity="warning">
          This quote could not be checked against what this customer has paid
          before. Nothing is wrong with the quote — the check itself did not
          run. {diagnosis.error}
        </Alert>
      </Box>
    );
  }

  if (diagnosis.loading && shown.length === 0) {
    return (
      <Box sx={{ mt: "var(--space-4)" }}>
        <Skeleton variant="rectangular" height={96} />
      </Box>
    );
  }

  // What was checked, in the engine's own words — or the caller's, where the
  // caller knows something the engine does not. The ERP quote page counts lines
  // that were never put to the engine at all, because they carry no item code
  // or no price, and no roll-up over diagnosed lines can see those.
  const checked = coverage ?? diagnosis.coverage?.headline;

  // Nothing worth interrupting anybody about, nothing to say about the quote as
  // a whole, and no coverage line asked for. That is a panel nobody asked a
  // question of — an unsaved quote, or one with no priced line on it — and not
  // a quote that came back clean.
  if (shown.length === 0 && !checked && !diagnosis.rollup) return null;

  return (
    <Box sx={{ mt: "var(--space-4)" }}>
      <FieldLabel>{title ?? DEFAULT_TITLE}</FieldLabel>
      {checked && <Box sx={{ mt: 0.5 }}><Meta>{checked}</Meta></Box>}
      <QuoteRollup rollup={diagnosis.rollup} />
      <Stack spacing={2} sx={{ mt: 1 }}>
        {/* The server already made the role decision and said so in `view`.
            This switch reads that answer; it does not re-take the decision from
            a role prop, which would put it in the one place that cannot see the
            policy — and would be the `{mgmt && …}` shape both of this
            repository's boundary leaks had. */}
        {shown.map((d) => (
          d.view === "OWNER" ? (
            <OwnerDiagnosisCard
              key={d.line_id}
              diagnosis={d}
              reasons={dismissReasons}
              onReviewPrice={onReviewPrice}
            />
          ) : (
            <DiagnosisCard
              key={d.line_id}
              diagnosis={d}
              reasons={dismissReasons}
              onReviewPrice={onReviewPrice}
            />
          )
        ))}
      </Stack>
    </Box>
  );
}

/**
 * What this quote comes to, and the lines its total does not show. RESTRICTED.
 *
 * **This is not a third diagnosis card.** There are still exactly two, one per
 * role, and they are per line. This is the quote's own summary and it sits in
 * the panel's own header region, above them — the same place the coverage
 * sentence has always sat, because it answers the same kind of question about
 * the same whole.
 *
 * **The loss leads, and it is drawn whatever the cards say.** A line that loses
 * money frequently renders no card: the price engine compares a price against
 * what this customer has paid before, and a line priced exactly as always can
 * still be bought for more than it sells for. So this block does not read
 * `shown` and is not gated on it — a reader who scrolled past two cards and
 * concluded the quote was fine is the failure it exists to prevent. An `Alert`
 * rather than a sentence, because a quote about to go out with a line losing
 * money on every unit is the case `CLAUDE.md` §1 names by hand.
 *
 * **Nothing here is computed or formatted.** Every figure and every sentence
 * arrived written by `render_rollup`, in the tenant's own currency; `CLAUDE.md`
 * §3 puts every number a screen renders in `commercial/`. The only decision
 * this component makes is whether the headline goes in an `Alert` or in a line
 * of body text, and it reads the server's own flag for that rather than
 * counting the list itself.
 *
 * The prop is optional and nullable because a salesperson's payload carries no
 * `rollup` key at all — that is the guarantee, not an oversight — and because a
 * response served before the key existed would otherwise take the Quote Builder
 * down on a property read.
 */
function QuoteRollup({ rollup }: { rollup?: RollupView | null }) {
  if (!rollup?.renders) return null;

  return (
    <Box sx={{ mt: 1 }}>
      {rollup.loss_lines.length > 0 ? (
        <Alert severity="warning" sx={{ mt: 0.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {rollup.headline}
          </Typography>
          {/* One sentence per line, named and costed, because the whole point
              is that the total is not where these are visible. */}
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {rollup.loss_lines.map((line) => (
              <Typography key={line.line_id} variant="body2">
                {line.sentence}
              </Typography>
            ))}
          </Stack>
        </Alert>
      ) : (
        <Typography variant="body2" sx={{ mt: 0.5 }}>{rollup.headline}</Typography>
      )}

      {/* A label and a value, seven fixed rows — the case `ui-standards` §3
          keeps a `<table>` for. Its row count is a property of the roll-up, not
          of the size of the business, so a DataGrid here would be a grid for
          seven rows. */}
      <Box sx={{ mt: 1.5 }}>
        <PanelMark>What this quote comes to</PanelMark>
        <FactTable
          label="What this quote comes to"
          rows={rollup.figures.map((f) => [f.label, f.value])}
        />
      </Box>

      {/* The factor behind it, or the refusal to name one, and then what the
          totals rest on — both the engine's own sentences, in the same muted
          register as the card's standing qualification. Neither is ever
          dropped: the second is where "this line was counted in the value and
          left out of the margin" is said. */}
      <Meta sx={{ mt: 1.5 }}>{rollup.dominant}</Meta>
      <Meta sx={{ mt: 0.5 }}>{rollup.note}</Meta>
    </Box>
  );
}
