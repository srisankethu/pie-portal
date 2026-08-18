/**
 * What your books already hold — the first-run look-back.
 *
 * The pair to `AttributionScreen`, and the earlier half of it: that one reports
 * what the platform changed, this one reports what was already there when the
 * books arrived and how much of it could be judged at all.
 *
 * **Coverage is rendered before findings, and that is the whole design.** A
 * screen that opens with "3 issues found" over a book where nothing could be
 * examined has told the reader something false while displaying only true
 * numbers, and it is the most likely first-run state there is — a distributor's
 * Zoho book usually has invoices long before it has purchase bills, so the
 * margin checks find nothing because they cannot look. So the verdict banner
 * comes first, every check shows its denominator beside its count, and a check
 * that judged nothing renders its reasons instead of a zero.
 *
 * The three verdicts turn on structural zeroes rather than a tuned coverage
 * threshold — see `signals/retrospective.py` for why there deliberately isn't
 * one. UNEXAMINED suppresses the finding counts entirely rather than showing
 * zeroes nobody should read.
 *
 * Manager or owner. A count of margin findings is a count of products whose
 * margin fell, so the server refuses a salesperson outright and there is no
 * partial view of this to offer them.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { formatDate } from "../when";
import { papi } from "./api";
import {
  EmptyState, ErrorState, LoadingState, MetricCard, SectionHeader, StatusChip,
  type Tone,
} from "./kit";
import type {
  PlatformSession, Retrospective, RetrospectiveDetector,
} from "./types";
import { useInsight } from "./viz/useInsight";

/** What each detector is called on screen, and what it looked for.
 *
 *  An unknown key falls through to its raw code rather than being dropped: a
 *  fifth detector should look ugly here, not disappear from a coverage report
 *  whose entire subject is completeness. */
const CHECK: Record<string, { label: string; what: string }> = {
  CUSTOMER_DECLINE: {
    label: "Customers buying less",
    what: "Revenue down against the same customer's own earlier period, past "
      + "an activity floor so ordinary lumpiness does not read as a decline.",
  },
  CUSTOMER_DORMANCY: {
    label: "Customers gone quiet",
    what: "Overdue against that customer's own ordering rhythm — not a fixed "
      + "number of days, which would flag every seasonal account.",
  },
  MARGIN_DETERIORATION: {
    label: "Margin slipping",
    what: "A product's margin down against its own baseline, computed only "
      + "where the recorded cost is trustworthy.",
  },
  COST_PASS_THROUGH: {
    label: "Cost rises not passed on",
    what: "A purchase cost rose and the selling price did not follow it.",
  },
};

const VERDICT: Record<string, { title: string; tone: Tone; severity: "error" | "warning" | "success" }> = {
  UNEXAMINED: {
    title: "Nothing in this history could be judged yet",
    tone: "bad",
    severity: "error",
  },
  PARTIAL: {
    title: "Part of this history could be judged",
    tone: "warn",
    severity: "warning",
  },
  EXAMINED: {
    title: "Every subject in this history was judged",
    tone: "good",
    severity: "success",
  },
};

function share(value: number | null): string {
  // "—" rather than "0%" where nothing was considered. A zero percent that
  // means "there was nothing to look at" reads as "looked at none of many".
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function Check({ row, suppressFindings }: {
  row: RetrospectiveDetector;
  suppressFindings: boolean;
}) {
  const meta = CHECK[row.detector] ?? { label: row.detector, what: "" };
  const judged = row.considered > 0 && row.judged > 0;
  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack direction="row" spacing={1} useFlexGap
             sx={{ alignItems: "center", flexWrap: "wrap", mb: 0.5 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {meta.label}
        </Typography>
        <StatusChip
          label={`${share(row.judged_share)} judged`}
          tone={judged ? "info" : "warn"} dense
          tip={`${row.judged} of ${row.considered} examined. The rest could not be judged — the reasons are below.`} />
        {/* The finding count is withheld entirely where nothing was judged.
            Rendering "0 found" beside "0% judged" invites the reader to take
            the zero as the answer, and the two zeroes mean opposite things. */}
        {!suppressFindings && judged && (
          <StatusChip
            label={row.found === 0 ? "nothing to raise" : `${row.found} to look at`}
            tone={row.found === 0 ? "good" : "warn"} dense />
        )}
      </Stack>

      {meta.what && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {meta.what}
        </Typography>
      )}

      <LinearProgress
        variant="determinate"
        value={row.judged_share === null ? 0 : row.judged_share * 100}
        aria-label={`${meta.label}: ${share(row.judged_share)} of subjects judged`}
        sx={{ mb: row.withheld.length ? 1.5 : 0, height: 6, borderRadius: 3 }} />

      {row.withheld.length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
          {row.withheld.map((w) => (
            <Typography component="li" variant="body2" color="text.secondary"
                        key={w.reason}>
              <b>{w.count}</b> — {w.detail}
            </Typography>
          ))}
        </Box>
      )}
    </Paper>
  );
}

export function RetrospectiveScreen({ session }: { session: PlatformSession }) {
  const view = useInsight<Retrospective>(
    "retrospective",
    () => papi.retrospective(session.token),
    [session.token]);

  const data = view.data;

  if (view.error) {
    return <ErrorState title="The look-back could not be read"
                       error={view.error} onRetry={view.reload} />;
  }
  if (view.loading || !data) {
    return <LoadingState rows={4} height={96} label="Reading your history…" />;
  }

  const verdict = VERDICT[data.verdict] ?? VERDICT.PARTIAL;
  const suppressFindings = data.verdict === "UNEXAMINED";
  const history = data.history;

  return (
    <Stack spacing={2.5}>
      <SectionHeader
        level="page"
        title="What your books already hold"
        sub="The same checks that run every day, applied to the history that came across when you connected — and, first, how much of it they could judge." />

      {/* Above the counts, deliberately. The reader who takes a finding count
          before the coverage has already formed the wrong impression. */}
      <Alert severity={verdict.severity}>
        <AlertTitle>{verdict.title}</AlertTitle>
        {data.verdict_detail}
      </Alert>

      {history.first_document === null ? (
        <EmptyState
          title="No history has been read yet"
          reason={history.detail
            ?? "No invoice or bill lines are synced for this organization."} />
      ) : (
        <>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
              <MetricCard
                label="History read"
                value={history.months === null ? "—" : `${history.months} months`}
                sub={`${formatDate(history.first_document)} – ${formatDate(history.last_document)}`} />
            </Box>
            <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
              <MetricCard label="Invoice lines"
                          value={history.sales_lines.toLocaleString()} />
            </Box>
            <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
              {/* Named beside the invoice count on purpose: zero here is the
                  single most common reason the margin checks below cannot
                  speak, and seeing the two side by side is what makes that
                  legible without reading the reasons. */}
              <MetricCard label="Purchase cost lines"
                          value={history.cost_lines.toLocaleString()} />
            </Box>
            <Box sx={{ flex: "1 1 200px", minWidth: 200 }}>
              <MetricCard label="Subjects judged"
                          value={share(data.judged_share)}
                          sub={`${data.judged} of ${data.considered} customers and products`} />
            </Box>
          </Stack>

          <Stack spacing={1.5}>
            {data.detectors.map((row) => (
              <Check key={row.detector} row={row}
                     suppressFindings={suppressFindings} />
            ))}
          </Stack>

          <Typography variant="caption" color="text.secondary">
            Judged against policy {data.thresholds_version}
            {data.as_of ? ` · as of ${formatDate(data.as_of)}` : ""}. Findings
            here are the same ones the daily list raises; this page is the
            backlog of them that your own history already contained.
          </Typography>
        </>
      )}
    </Stack>
  );
}
