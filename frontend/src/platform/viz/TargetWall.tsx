// Am I going to hit my numbers, and what do they pay?
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
// **And what it is worth.** A target on its own does not say why anybody should
// chase it. The scheme does — two to three points of purchases on this book —
// so every card carries what is already secured and what the next rung is worth,
// and the headline carries the total still winnable this quarter. That total is
// a sum of *uplifts*, never of rebates: money already earned is not at stake.
//
// **A projection, or a stated refusal.** Where the period is old enough and the
// evidence thick enough, the card says where the book closes at the rate it is
// buying. Where it is not, it says so in words and shows nothing — the server
// makes that call, and a screen that filled the gap with a confident number
// would be inventing the one thing the platform refuses to.
//
// **Behind is amber, not red.** Red is reserved for a loss on this palette, and
// a supplier target that is behind in week six is a thing to work, not a thing
// that has gone wrong. Every card also says it in words — colour is never the
// only cue.
//
// **Sorted by what needs attention.** Furthest behind pace first — on the
// server, which is where the arithmetic that decides it already lives.

import Button from "@mui/material/Button";
import { useState } from "react";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { isAre } from "../format";
import { EntityName } from "../EntityName";
import { StatusChip } from "../kit";
import type { EntityOrigin, PlatformSession } from "../types";
import { Panel, stateOf } from "./Panel";
import { pct, useInsight } from "./useInsight";
import { TargetEditor } from "./Targets";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);
const obj = (v: unknown): Row | null => (v as Row | null | undefined) ?? null;

export function TargetWallScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "schemes", () => papi.schemes(session.token), [session.token]);
  const [editing, setEditing] = useState(false);

  const sourcesDiffer = Boolean(data?.sources_differ);
  // Every row here has a live target — the server only returns periods today
  // falls inside — so there is no blank card to filter out.
  const live = rows(data?.rows);
  const behind = live.filter((r) => obj(r.progress)?.on_pace === false);
  const atStake = num(data?.at_stake_total);

  return (
    <Panel
      title="Supplier targets"
      question="Where each principal's number stands, what it pays, and whether the pace clears it"
      state={stateOf(loading, error, data?.empty_reason as string)}
      error={error} emptyReason={data?.empty_reason as string} onRetry={reload} wide
      actions={
        <Button type="button" size="small" variant="outlined"
                onClick={() => setEditing(true)}>
          Add or edit
        </Button>
      }
    >
      {live.length === 0 ? (
        <div className="viz-state">
          <p className="viz-state-title">No targets on record</p>
          <p className="viz-muted">
            Nothing in Zoho holds a principal's target or the rebate behind it,
            so they are typed in once and kept. Add one and this wall fills in —
            actual against target, how much of the period has gone, and what
            hitting it is worth.
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
            <strong>{behind.length}</strong> of {live.length}{" "}
            {live.length === 1 ? "principal" : "principals"}{" "}
            {isAre(behind.length)} behind the pace of their
            period.
            {atStake > 0 && (
              <>
                {" "}
                <strong>{money(atStake)}</strong> of rebate is still to play
                for — what reaching the next rung pays, over what is already
                earned.
              </>
            )}
          </p>

          <ul className="wall">
            {live.map((r) => (
              <Bullet key={String(r.vendor_id)} row={r}
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

function Bullet({ row, sourcesDiffer }: { row: Row; sourcesDiffer: boolean }) {
  const t = obj(row.progress);
  const rebate = obj(row.rebate);
  if (!t) return null;

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

      {rebate && <Rebate rebate={rebate} />}
    </li>
  );
}

/** What the scheme pays, and where the period lands. */
function Rebate({ rebate }: { rebate: Row }) {
  const secured = obj(rebate.secured);
  const next = obj(rebate.next_slab);
  const projection = obj(rebate.projection);
  const absent = obj(rebate.absent);
  const marginal = obj(rebate.marginal);
  const unpriced = obj(rebate.marginal_absent);

  // Absent, not zero. Nobody having said what the rebate is differs from a
  // principal who pays none, and the wall must not read the first as the second.
  if (!rebate.scheme) {
    return (
      <p className="viz-muted wall-note">
        No scheme recorded, so nothing here says what hitting this number pays.
        {projection != null && (
          <> At this rate it closes at{" "}
            <strong>{money(num(projection.projected_close))}</strong>.</>
        )}
      </p>
    );
  }

  return (
    <>
      <div className="wall-head" style={{ marginTop: 6 }}>
        <StatusChip
          dense
          tone={secured ? "good" : "neutral"}
          label={secured
            ? `${pct(num(secured.rate), 1)} secured · ${money(num(secured.rebate))}`
            : "no rung reached yet"}
          tip={secured
            ? "Earned on everything bought so far, if the period closed today."
            : "Nothing is earned below the first rung of this scheme."} />
        {/* The one state on this wall that expires when the quarter does, so
            it gets a chip rather than a sentence three lines down. */}
        {marginal?.free === true && (
          <StatusChip
            dense
            tone="good"
            label="next rung is free"
            tip={"There is less left to buy than the rung pays, so the "
              + "increment costs nothing net. It lapses when the period closes."} />
        )}
      </div>

      {next && (
        <p className="viz-muted wall-note">
          <strong>{money(num(next.gap))}</strong> more reaches{" "}
          {pct(num(next.rate), 1)} — worth{" "}
          <strong>{money(num(next.uplift))}</strong> on top.
        </p>
      )}

      {projection ? (
        <p className="viz-muted wall-note">
          At this rate it closes at{" "}
          <strong>{money(num(projection.projected_close))}</strong>
          {projection.clears_target === true
            ? <> — clears the number</>
            : <>, {money(num(projection.shortfall))} short</>}
          {projection.rebate != null && (
            <> · rebate <strong>{money(num(projection.rebate))}</strong></>
          )}
        </p>
      ) : (
        absent && (
          // The refusal, in the words the server chose. A screen that guessed
          // here would be putting a confident number on a week of evidence.
          <p className="viz-muted wall-note">
            <em>{String(absent.label)}.</em> {String(absent.why)}
          </p>
        )
      )}

      {/* What the rung costs, as against what it pays. Measured above the
          projected close, so it reads after it. Every figure here is formatted
          rather than derived — `effective_cost` and `earned_per_rupee` are two
          sides of one number the server computed, and working the second out
          from the first on this screen would put margin arithmetic in the UI. */}
      {marginal ? (
        <p className="viz-muted wall-note">
          <strong>{money(num(marginal.gap))}</strong> more than that collects{" "}
          <strong>{money(num(marginal.gain))}</strong>
          {marginal.free === true
            ? <> — <strong>that buying costs nothing net</strong>.</>
            : <>, so the extra costs {pct(num(marginal.effective_cost), 0)} of
                list — <strong>{pct(num(marginal.earned_per_rupee), 0)} back</strong>.</>}
        </p>
      ) : (
        // Only the "already clearing it" state is worth saying here. The other
        // two reasons are a missing projection, which the refusal above has
        // just explained, and a missing scheme, which this card returned on.
        unpriced?.reason === "LANDS_ANYWAY" && (
          <p className="viz-muted wall-note">
            <em>{String(unpriced.label)}.</em> {String(unpriced.why)}
          </p>
        )
      )}
    </>
  );
}
