// Narrow a page to a group somebody drew.
//
// **The deliberate opposite of `CompanyFilter`, and the difference is the whole
// reason this is a second component rather than a parameter on that one.**
// `CompanyFilter` narrows rows already in the browser and its own header says
// why it must never touch a total: the platform pools every connected company
// on purpose, so a control that quietly re-scoped an aggregate would be making a
// different claim than the one the server computed.
//
// A group filter makes exactly that different claim. "What does the aerospace
// book do" is a question about a set, and the share, the concentration and the
// coverage percentage all have to be recomputed inside it — a denominator that
// stayed at the whole book while the numerator moved is a wrong number with an
// explanation attached. So this control holds a *slug* and hands it to the
// server, and the screen refetches. Nothing here filters anything.
//
// Two consequences worth stating, because they are what a reader would check:
//
// - **The options come from the server, not from the rows.** `CompanyFilter`
//   builds its list out of what is on screen, which is right for a control that
//   hides rows and wrong for one that changes the question: a group with nobody
//   in it yet is still a group somebody may ask about, and it would never appear
//   in a list derived from rows.
// - **It renders with one option.** The other rule `CompanyFilter` follows —
//   nothing below two companies — is about a control that would do nothing. One
//   group is a real choice against "everything", so it is offered.
//
// Restricted groups never arrive here at all: the server withholds them from a
// salesperson's list, so there is no branch in this file that could forget to.
//
// **Who owns the selection.** Not this file, and not the panel that draws it:
// `groupScope.tsx` holds one selection per page, in the URL, and renders this
// control once for each kind the page takes. This is the select and nothing
// else. It used to export a `useGroupFilter` hook alongside, and ten panels
// each held their own — which put two customer-group selects on the Payments
// page, disagreeing.

import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { TOUCH } from "./kit";
import type { EntityGroup } from "./types";

/** No group: the whole book. An empty string rather than null so it is a valid
 *  select value, the same choice `CompanyFilter.ALL` makes and for the same
 *  reason MUI forces it. */
export const ALL_GROUPS = "";

/** The select itself. Renders nothing when there is nothing to choose from.
 *
 *  `displayEmpty` and a shrunk label for the reason `CompanyFilter`'s own
 *  select documents: without them MUI treats the empty value as "nothing
 *  selected", draws the field blank while showing every row, and leaves the
 *  label sitting on top of the text. */
export function GroupFilter({
  label = "Group", value, onChange, options, show, minWidth = 200,
}: {
  label?: string;
  value: string;
  onChange: (slug: string) => void;
  options: EntityGroup[];
  show: boolean;
  minWidth?: number;
}) {
  if (!show) return null;
  // A selection the options do not contain still has to appear in the control.
  // The selection comes from the URL now, so it can name a group that was
  // archived since the link was sent, or one this reader may not see, or a
  // typo — and in every one of those cases the page behind it is showing the
  // server's refusal. A select whose `value` matches no item renders blank,
  // which would say "the whole book" over an error about a group. Shown as the
  // slug, because that is all this side knows about it, and shown so that it
  // can be cleared.
  const unknown = value !== ALL_GROUPS && !options.some((g) => g.slug === value);
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      // Full width on a phone, natural width at a desk. A 190px control in a
      // wrapping row leaves a ragged half-line on a 390px screen, and this is a
      // viewport the desk quotes from rather than one the layout survives —
      // `ui-standards.md` §12. `sm` is the breakpoint the other two phone
      // branches in this codebase read, deliberately.
      sx={{ minWidth, width: { xs: "100%", sm: "auto" },
            "& .MuiInputBase-root": TOUCH }}
      slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
    >
      <MenuItem value={ALL_GROUPS}>All</MenuItem>
      {options.map((g) => (
        <MenuItem key={g.slug} value={g.slug}>
          {g.name}
          {/* The size, because a group is a set and "which set" is half the
              question — an answer computed over three of four hundred accounts
              should not look like an answer about the book. */}
          {` · ${g.members}`}
        </MenuItem>
      ))}
      {unknown && <MenuItem value={value}>{value}</MenuItem>}
    </TextField>
  );
}
