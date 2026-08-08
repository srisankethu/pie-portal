// Narrow a list to one connected company.
//
// Per-row attribution answers "which ABC Industries is this?". It does not
// answer "show me only the Chennai book", which is the other half of working
// across three legal entities — and scanning a two-hundred-row directory for
// the rows whose second line says one thing is not an answer either.
//
// **Client-side, over rows already loaded, and only ever over rows.** It never
// touches a total computed upstream. That is the honest boundary: the platform
// pools every enabled connection on purpose — revenue and margin roll up across
// all of them — so a control that quietly re-scoped an aggregate would be
// making a different claim than the one the server computed. Filtering a list
// of entities hides rows; it does not restate a number.
//
// **The options come from the rows themselves.** No endpoint, and no company
// offered that has nothing in the list — an empty filter result the user chose
// from a dropdown is a dead end they were invited into.
//
// **Nothing renders below two companies**, the same rule the source badges
// follow. One company means one option, and a select with one option is a
// control that does nothing.

import { useMemo, useState } from "react";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { connectorMark } from "./EntityName";
import type { Sourced } from "./types";

/** All companies. Empty string rather than null so it is a valid select value. */
export const ALL = "";

export interface CompanyOption {
  connectionId: string;
  label: string;
  icon: string;
  count: number;
}

/** True when these rows actually come from more than one place.
 *
 *  Distinct from the server's `sources_differ`, which is about the
 *  *organization*. A list can be single-company inside a three-company
 *  organization — every item one account bought, say — and repeating one
 *  company's name down that list is noise, not information. Both tests have to
 *  pass before a source is worth the space.
 */
export function distinguishes(rows: Sourced[]): boolean {
  const seen = new Set(rows.map((r) => r.origin?.connection_id ?? "?"));
  return seen.size > 1;
}

export function useCompanyFilter<T extends Sourced>(rows: T[]) {
  const [company, setCompany] = useState<string>(ALL);

  const options = useMemo<CompanyOption[]>(() => {
    const byId = new Map<string, CompanyOption>();
    for (const row of rows) {
      const id = row.origin?.connection_id;
      if (!id) continue;
      const found = byId.get(id);
      if (found) found.count += 1;
      else byId.set(id, {
        connectionId: id,
        label: row.origin?.company || "Unnamed company",
        icon: connectorMark(row.origin),
        count: 1,
      });
    }
    return [...byId.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [rows]);

  const show = options.length > 1;
  // A filter the control is not rendering must not still be filtering. Two
  // companies narrow to one, a sync retires the second, and the survivor would
  // otherwise stay hidden behind a select nobody can see to reset.
  const active = show ? company : ALL;

  /** Narrow any list of the same shape.
   *
   *  Separate from `filtered` because a screen usually searches and sorts
   *  before it filters, and that derived list is built inside a branch while
   *  this hook must be called at the top of the component. Passing the derived
   *  rows to `apply` keeps both true — and keeps the options drawn from the
   *  whole list, so choosing a company after a search does not offer only the
   *  companies the search happened to leave.
   */
  const apply = <R extends Sourced>(subset: R[]): R[] =>
    (active === ALL
      ? subset
      : subset.filter((r) => r.origin?.connection_id === active));

  const filtered = useMemo(() => apply(rows), [rows, active]);   // eslint-disable-line

  return { company: active, setCompany, options, filtered, apply, show };
}

export function CompanyFilter({
  options, value, onChange, show, label = "Company",
}: {
  options: CompanyOption[];
  value: string;
  onChange: (v: string) => void;
  show: boolean;
  label?: string;
}) {
  if (!show) return null;
  const total = options.reduce((n, o) => n + o.count, 0);
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      // `displayEmpty` because without it the control renders *blank* while
      // showing every row: MUI treats an empty value as "nothing selected" and
      // hides the option that represents it. A filter whose resting state looks
      // unset is one people set twice and then wonder why nothing changed.
      //
      // `shrink` because the two settings disagree otherwise. The label floats
      // when MUI thinks the field is filled, and "filled" means a non-empty
      // value — but "All companies" *is* the empty value, so the label stayed
      // in its resting position and sat on top of the text the select was
      // already showing. Opening the menu focused the field and floated it,
      // which is why it looked correct only while open. `displayEmpty` means
      // there is always content to clear, so the label should always be clear
      // of it. This also notches the outlined fieldset, since the notch follows
      // the label.
      slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
      sx={{ minWidth: 210 }}
    >
      {/* The counts travel with the names. "4U Precision" alone does not say
          whether choosing it leaves eighty rows or two. */}
      <MenuItem value={ALL}>All companies · {total}</MenuItem>
      {options.map((o) => (
        <MenuItem key={o.connectionId} value={o.connectionId}>
          {o.icon} {o.label} · {o.count}
        </MenuItem>
      ))}
    </TextField>
  );
}

// ── the other kind: narrowing the numbers, not the rows ─────────────────────
//
// `CompanyFilter` above hides rows and never restates a total, and that is
// right for a directory: the platform pools companies on purpose, so a control
// that quietly re-scoped an aggregate would claim something the server never
// computed.
//
// It is wrong wherever the aggregate *is* the screen. "112 customers do not
// take cutting tools", "Kennametal is 38% of spend" — those are the output, and
// a narrowed list underneath an org-wide headline puts one company's rows
// beneath three companies' arithmetic. Those screens ask the server for one
// company and re-render everything.
//
// The two live next to each other so the choice is a choice. If you are adding
// a filter and cannot say which of these you want, the question to ask is
// whether any number on the screen is a share, a count of the whole, or a
// total. If one is, it needs this.

export interface CompanyScopeOption {
  connection_id: string;
  label: string;
  customers?: number;
}

/** A server-side company scope: the caller refetches when it changes.
 *
 *  Options come from the *connection list*, not from row provenance. A picker
 *  built from the rows disappears exactly when it is most needed — a book
 *  synced before connections were stamped leaves every origin null and the
 *  control silently never renders, which is how the mix grid shipped without
 *  one on a live three-company book.
 *
 *  A company with nothing in it is still offered, and says so. Hiding it leaves
 *  somebody wondering which of their books went missing; "0 customers" answers
 *  that in place.
 */
export function CompanyScope({
  options, value, onChange, unit = "customer",
}: {
  options: CompanyScopeOption[];
  value: string;
  onChange: (connectionId: string) => void;
  unit?: string;
}) {
  if (options.length < 2) return null;
  return (
    <TextField
      select size="small" label="Company" value={value}
      onChange={(e) => onChange(e.target.value)}
      slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
      sx={{ minWidth: 240, mb: 2 }}
    >
      <MenuItem value={ALL}>All companies</MenuItem>
      {options.map((o) => (
        <MenuItem key={o.connection_id} value={o.connection_id}>
          {o.label}
          {o.customers != null && (
            <> · {o.customers} {unit}{o.customers === 1 ? "" : "s"}</>
          )}
        </MenuItem>
      ))}
    </TextField>
  );
}
