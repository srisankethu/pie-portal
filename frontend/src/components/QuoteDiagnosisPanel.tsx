/** The diagnosis cards for a quote, under the grid.
 *
 * **This is the mount `DiagnosisCard` never had.** The engine, the five
 * endpoints and the card all shipped together and nothing imported the card, so
 * no screen has ever rendered a diagnosis. The gap was invisible from either
 * side: the backend suite passed, the component suite passed, and the only test
 * that could have caught it — one asserting somebody can *see* a diagnosis —
 * belonged to neither file.
 *
 * **Silent by default, which is most of the time.** One card per line the
 * server said `renders` for, and nothing at all when it said so for none. That
 * is the engine's own threshold decision, made against a versioned policy;
 * drawing a card anyway would override it from a place that cannot know what
 * the policy is. A quote whose lines are all ordinary shows nothing here, and
 * that is the correct screen rather than an empty one.
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
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { DiagnosisCard, OwnerDiagnosisCard } from "./DiagnosisCard";
import type { DismissReason } from "./DiagnosisCard";
import { FieldLabel, Meta } from "../platform/kit";
import type { QuoteDiagnosisState } from "../useQuoteDiagnosis";

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
  /** How much of the quote the check could actually judge.
   *
   *  The Quote Builder passes nothing and the panel disappears when there is
   *  nothing to say, which is right while somebody is pricing: a row of
   *  furniture reading "no findings" on every ordinary quote is noise. A
   *  finished document is the opposite case — a reader asking "was this
   *  checked?" cannot tell silence from a panel that was never mounted.
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

  // Nothing worth interrupting anybody about, and no coverage line asked for.
  if (shown.length === 0 && !coverage) return null;

  return (
    <Box sx={{ mt: "var(--space-4)" }}>
      <FieldLabel>{title ?? DEFAULT_TITLE}</FieldLabel>
      {coverage && <Box sx={{ mt: 0.5 }}><Meta>{coverage}</Meta></Box>}
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
