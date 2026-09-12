/** The analysis screens, indexed by the question each one answers.
 *
 * Twelve of these were nav items, and a nav item is an invitation to start
 * work there. Nobody starts a Tuesday on Bonds: these are what you open when a
 * decision has already made you want the pattern behind it, and presenting them
 * as twenty places to begin is what made the queue — the one screen that says
 * what to do — item six of thirty-four.
 *
 * So they are one door, and the index is **the question, not the screen's
 * name**. "Landscape" says nothing to somebody who has not already read it;
 * "Which accounts are big and thin at the same time?" is why they would open
 * it. The names are kept beside the questions because a reader who knows the
 * product navigates by them.
 *
 * **No figures on the cards.** The prototype showed a live reading under each
 * question — "₹11.2L is four accounts that stopped entirely" — and it is the
 * right instinct and the wrong place: it is twelve requests to render an index
 * nobody decides from, and the moment one of them fails or goes stale the index
 * is lying about a screen it is only supposed to point at. The reading belongs
 * on the screen that computed it.
 */
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";

import type { AppAbility } from "./ability";
import { visibleEvidence } from "./destinations";
import { InlineLink, PanelMark, SectionHeader } from "./kit";
import { PATH, pathFor } from "./route";

export default function EvidenceLibrary({ ability }: { ability: AppAbility }) {
  const entries = visibleEvidence(ability);

  return (
    <>
      <InlineLink to={PATH.home}>← Today</InlineLink>
      <Box sx={{ mt: 1 }}>
        <SectionHeader
          title="Evidence"
          sub={`${entries.length} views, indexed by the question each one answers. None of them `
             + "is a place to start work — they are here for when a decision makes you want to "
             + "see the pattern behind it."}
        />
      </Box>
      <Box sx={{
        display: "grid",
        gap: 1.5,
        gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(3, 1fr)" },
      }}>
        {entries.map((e) => (
          <Paper key={e.screen} variant="outlined" sx={{ p: 2 }}>
            <PanelMark>{e.name}</PanelMark>
            <Typography
              component={RouterLink}
              to={pathFor(e.screen)}
              variant="h3"
              sx={{ display: "block", mt: 0.5, textDecoration: "none", color: "text.primary",
                    "&:hover": { color: "primary.main" } }}
            >
              {e.question}
            </Typography>
          </Paper>
        ))}
      </Box>
    </>
  );
}
