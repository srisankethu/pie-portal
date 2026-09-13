// Narrow a screen to a group somebody drew.
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

import { useEffect, useMemo, useState } from "react";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { papi } from "./api";
import { TOUCH } from "./kit";
import type { EntityGroup, GroupKind } from "./types";

/** No group: the whole book. An empty string rather than null so it is a valid
 *  select value, the same choice `CompanyFilter.ALL` makes and for the same
 *  reason MUI forces it. */
export const ALL_GROUPS = "";

export interface GroupFilterState {
  /** The slug to send as `?group=`, or `""` for the whole book. */
  group: string;
  setGroup: (slug: string) => void;
  options: EntityGroup[];
  /** False while the list is loading and when the workspace has drawn none, so
   *  a screen renders no control rather than an empty one. */
  show: boolean;
}

/** Load this workspace's groups of one kind, and hold which is selected.
 *
 *  Archived groups are excluded — the server's default — so a group somebody
 *  retired stops being offered while every past answer computed under it stays
 *  explainable. A selection that names an archived group still resolves if it
 *  arrives from a saved link; that is the server's decision and this control
 *  does not second-guess it.
 */
export function useGroupFilter(token: string, kind: GroupKind): GroupFilterState {
  const [group, setGroup] = useState<string>(ALL_GROUPS);
  const [options, setOptions] = useState<EntityGroup[]>([]);

  useEffect(() => {
    let live = true;
    // A failure here is silent on purpose and it is the one judgement call in
    // this file: the screen behind this control works without it, and an error
    // banner over a working directory because an optional filter could not load
    // teaches people to ignore banners. The control simply does not render.
    papi.listGroups(token, kind)
      .then((r) => { if (live) setOptions(r.groups); })
      .catch(() => { if (live) setOptions([]); });
    return () => { live = false; };
  }, [token, kind]);

  // A group the control is not rendering must not still be filtering — the same
  // rule `CompanyFilter` applies, and it bites harder here: the selection is a
  // server parameter, so a stale one would keep narrowing an answer with no
  // control on screen to reset it.
  const active = options.length > 0 ? group : ALL_GROUPS;

  return useMemo(
    () => ({ group: active, setGroup, options, show: options.length > 0 }),
    [active, options]);
}

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
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      sx={{ minWidth, "& .MuiInputBase-root": TOUCH }}
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
    </TextField>
  );
}
