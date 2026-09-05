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
 *
 * A `Paper`, not a `Card`, per `docs/ui-standards.md` §2: setup is a state of
 * the workspace, not a business entity with an identity somebody could open.
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
import { Meta, SectionHeader, StatusChip } from "./kit";

function StepRow({ step, canAct }: { step: OnboardingStep; canAct: boolean }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-start" }}>
      {/* A shape as well as a colour — ui-standards §6 — and `titleAccess` so
          that shape reaches somebody who cannot see it. A filled tick and an
          empty ring are the same silence to a screen reader without it, and
          "which of these have I done" is the only question this panel asks. */}
      {step.done ? (
        <CheckCircleIcon
          fontSize="small" color="success" titleAccess="Done" sx={{ mt: 0.25 }}
        />
      ) : (
        <RadioButtonUncheckedIcon
          fontSize="small" color="disabled" titleAccess="Still to do" sx={{ mt: 0.25 }}
        />
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" spacing={1} useFlexGap
               sx={{ alignItems: "center", flexWrap: "wrap" }}>
          {/* The widget-title rung of §4's ramp, not the label rung. This was
              `subtitle2`, which in this theme is 12px uppercase — smaller than
              the sentence explaining it, so every row read title-last. A
              finished step then recedes to secondary ink, which is emphasis
              only: the icon beside it is what carries the state. */}
          <Typography
            variant="h5"
            component="div"
            color={step.done ? "text.secondary" : "text.primary"}
          >
            {step.title}
          </Typography>
          {!step.required && (
            <StatusChip tone="neutral" label="Recommended" />
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary">
          {step.detail}
        </Typography>
      </Box>
      {!step.done && canAct && (
        <Button
          component={RouterLink}
          to={step.route}
          size="small"
          // Four rows, four buttons, and a screen reader listing the controls
          // on this page hears "Open" four times with nothing to tell them
          // apart. The visible word stays short because the row it sits in is
          // what supplies the context on screen.
          aria-label={`Open: ${step.title}`}
          sx={{ flexShrink: 0 }}
        >
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
  // screen is noise for the several hundred days after it stops applying. The
  // same goes for the moment before it arrives — no skeleton, because the
  // panel's own absence is the normal state and reserving height for it would
  // make every established workspace's home page jump.
  if (isError || !data || data.complete) return null;

  const total = data.steps.length;
  const done = data.steps.filter((s) => s.done).length;
  const canAct = session.role === "OWNER";

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <SectionHeader
        level="widget"
        title="Finish setting up"
        sub={"The screens stay empty until your books are in. This takes about "
             + "ten minutes and only has to happen once."}
        // The tally the progress bar was carrying in its label and nowhere a
        // sighted reader could see it. Every step in it is listed below, so it
        // counts the recommended ones too — a figure measuring a different set
        // from the list under it would be worse than no figure.
        actions={<Meta>{done} of {total} done</Meta>}
      />

      <LinearProgress
        variant="determinate"
        value={(done / total) * 100}
        sx={{ mb: 2.5 }}
        aria-label="Setup progress"
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
