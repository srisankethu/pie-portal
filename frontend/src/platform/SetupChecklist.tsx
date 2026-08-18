/** What a new organization still has to do, on the first screen it sees.
 *
 * A tenant that has just signed up has no connection, no history and therefore
 * nothing on any screen. Without this the product's first impression is a
 * dozen correct empty states, and "correct" is not the same as "useful" — the
 * reader cannot tell an empty book from a broken one.
 *
 * Three things it deliberately does *not* do:
 *
 * **It does not persist.** Every step is derived server-side from real rows on
 * every request (`app/onboarding.py`), so deleting the only connection brings
 * the panel back. A "dismiss" button would be a stored flag saying setup was
 * finished when it was not, which is the defect the server side was written to
 * avoid.
 *
 * **It does not nag.** Once every *required* step is done the panel is gone,
 * including while the two optional steps are still open. A permanent banner
 * asking for a margin policy is one people learn to look past, and then the
 * one that matters is looked past too.
 *
 * **It does not offer what the reader cannot do.** Connecting Zoho and setting
 * floors are owner actions. A salesperson whose company has connected nothing
 * still sees the panel — it is the honest reason every screen is empty — but
 * reads it rather than being sent to a screen that will refuse them.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { papi } from "./api";
import type { OnboardingStep, PlatformSession } from "./types";
import { StatusChip } from "./kit";

function StepRow({ step, canAct }: { step: OnboardingStep; canAct: boolean }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-start" }}>
      {/* An icon *and* a chip, never colour alone — ui-standards §6. */}
      {step.done ? (
        <CheckCircleIcon fontSize="small" color="success" sx={{ mt: 0.3 }} />
      ) : (
        <RadioButtonUncheckedIcon fontSize="small" color="disabled" sx={{ mt: 0.3 }} />
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" spacing={1} useFlexGap
               sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="subtitle2">{step.title}</Typography>
          {!step.required && (
            <StatusChip tone="neutral" label="Recommended" />
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary">
          {step.detail}
        </Typography>
      </Box>
      {!step.done && canAct && (
        <Button component={RouterLink} to={step.route} size="small" sx={{ flexShrink: 0 }}>
          Open
        </Button>
      )}
    </Stack>
  );
}

export function SetupChecklist({ session }: { session: PlatformSession }) {
  const { data, isError } = useQuery({
    queryKey: ["onboarding", session.organization_id],
    queryFn: () => papi.onboarding(session.token),
  });

  // Silent on failure, and this is the one place in the app where that is the
  // right answer: this panel is scaffolding for a tenant that has not finished
  // setting up, so "the setup checklist did not load" at the top of the first
  // screen is noise for the several hundred days after it stops applying.
  if (isError || !data || data.complete) return null;

  const done = data.steps.filter((s) => s.done).length;
  const canAct = session.role === "OWNER";

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <Typography variant="h3" sx={{ mb: 0.5 }}>Finish setting up</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        The screens stay empty until your books are in. This takes about ten
        minutes and only has to happen once.
      </Typography>

      <LinearProgress
        variant="determinate"
        value={(done / data.steps.length) * 100}
        sx={{ mb: 2.5, height: 6, borderRadius: 3 }}
        aria-label={`${done} of ${data.steps.length} setup steps done`}
      />

      <Stack spacing={2}>
        {data.steps.map((s) => (
          <StepRow key={s.key} step={s} canAct={canAct} />
        ))}
      </Stack>

      {!canAct && (
        <Alert severity="info" sx={{ mt: 2.5 }}>
          These are owner actions. Until they are done, the screens here have
          nothing to read — that is why they look empty.
        </Alert>
      )}
    </Paper>
  );
}
