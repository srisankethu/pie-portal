// Am I going to hit my numbers?
//
// An authorised distributor's year is run against numbers the principals set,
// and that question deserves a wall you scan in five seconds — not a pace track
// eight pixels tall buried inside a dependency row, which is where it lived
// first.
//
// **A bullet chart per principal.** One bar for what has been done, one marker
// for where the period is. The comparison between those two is the whole
// judgement: 60% of a number sounds fine and is not, if 80% of the quarter has
// gone. An achievement percentage on its own hides that until the last week,
// which is exactly when it stops being fixable.
//
// **Behind is amber, not red.** Red is reserved for a loss on this palette, and
// a supplier target that is behind in week six is a thing to work, not a thing
// that has gone wrong. Every card also says it in words — colour is never the
// only cue.
//
// **Sorted by what needs attention.** Furthest behind pace first, so the wall
// answers "where do I put this month" by being read top-left to bottom-right.

import Button from "@mui/material/Button";
import { useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { EntityName } from "../EntityName";
import { StatusChip } from "../kit";
import type { EntityOrigin, PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { TargetEditor } from "./Targets";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);

export function TargetWallScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "dependency", () => papi.dependency(session.token), [session.token]);
  const [editing, setEditing] = useState(false);

  const vendors = (data?.vendors as Row | null | undefined) ?? null;
  const sourcesDiffer = Boolean(data?.sources_differ);

  // Only principals with a live number. A card per supplier would be a wall of
  // blanks, and a blank is not a missed target — it is a target nobody has
  // entered.
  const withTargets = rows(vendors?.rows)
    .filter((r) => r.target != null)
    .sort((a, b) => slack(a) - slack(b));

  const behind = withTargets.filter((r) => (r.target as Row).on_pace === false);

  return (
    <Panel
      title="Supplier targets"
      question="Where each principal's number stands, and whether the pace clears it"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <Button type="button" size="small" variant="outlined"
                onClick={() => setEditing(true)}>
          Add or edit
        </Button>
      }
    >
      {withTargets.length === 0 ? (
        <div className="viz-state">
          <p className="viz-state-title">No targets on record</p>
          <p className="viz-muted">
            Nothing in Zoho holds a principal's target, so they are typed in
            once and kept. Add one and this wall fills in — actual against
            target, with how much of the period has gone.
          </p>
          <Button type="button" variant="contained" size="small"
                  onClick={() => setEditing(true)}>
            Add a target
          </Button>
        </div>
      ) : (
        <>
          <p className="viz-headline">
            {/* The noun agrees with the total and the verb with the count —
                "1 of 2 principal is" reads as a bug in the page. */}
            <strong>{behind.length}</strong> of {withTargets.length}{" "}
            {withTargets.length === 1 ? "principal" : "principals"}{" "}
            {behind.length === 1 ? "is" : "are"} behind the pace of their
            period.
          </p>

          <ul className="wall">
            {withTargets.map((r) => (
              <Bullet key={String(r.entity_id)} row={r}
                      sourcesDiffer={sourcesDiffer} />
            ))}
          </ul>
        </>
      )}

      {editing && (
        <TargetEditor session={session} onClose={() => setEditing(false)}
                      onSaved={reload} />
      )}
    </Panel>
  );
}

/** How far behind pace, as a signed number. Negative sorts first. */
function slack(row: Row): number {
  const t = row.target as Row;
  return num(t.achieved) - num(t.period_elapsed);
}

function Bullet({ row, sourcesDiffer }: { row: Row; sourcesDiffer: boolean }) {
  const t = row.target as Row;
  const achieved = num(t.achieved);
  const elapsed = num(t.period_elapsed);
  const onPace = t.on_pace === true;
  // The scale runs to whichever is larger, so an over-achieved target does not
  // draw a bar past the end of its own track.
  const ceiling = Math.max(1, achieved);

  return (
    <li className={`wall-card${onPace ? "" : " behind"}`}>
      <div className="wall-head">
        <EntityName name={String(row.label)}
                    origin={row.origin as EntityOrigin | undefined}
                    show={sourcesDiffer} strong />
        <StatusChip label={onPace ? "on pace" : "behind"}
                    tone={onPace ? "good" : "warn"} dense />
      </div>

      <div className="wall-figures">
        <strong>{money(num(t.actual))}</strong>
        <span className="viz-muted">of {money(num(t.amount))}</span>
      </div>

      <div className="wall-track" role="img"
           aria-label={`${pct(achieved, 0)} of the target, ${pct(elapsed, 0)} of the period gone`}>
        <span className={`wall-fill${onPace ? " ok" : " behind"}`}
              style={{ width: `${(achieved / ceiling) * 100}%` }} />
        {/* Where the bar would have to reach to be on time. The one mark that
            turns an achievement number into a judgement. */}
        <span className="wall-pace" style={{ left: `${(elapsed / ceiling) * 100}%` }} />
      </div>

      <p className="viz-muted wall-note">
        {pct(achieved, 0)} done · {pct(elapsed, 0)} of the period gone
        {" · "}{String(t.basis_label)}
      </p>
      <p className="viz-muted wall-note">
        {formatDate(String(t.period_start))} – {formatDate(String(t.period_end))}
        {num(t.days_left) > 0 && <> · {num(t.days_left)} days left</>}
        {t.required_run_rate != null && (
          <> · <strong>{money(num(t.required_run_rate))}/day</strong> to close it</>
        )}
      </p>
    </li>
  );
}
