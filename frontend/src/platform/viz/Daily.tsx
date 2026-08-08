// The morning read: what needs you, what is at risk, what moved.
//
// Every other screen here answers a question about a *period*. This one answers
// "what do I do now", which is a worklist rather than a narrative — so it is
// tiles with counts and ways in, not charts.
//
// **Freshness leads, and it is allowed to interrupt.** PIE is a derived mirror
// and there is no scheduler in this codebase, so every figure below is as old
// as the last sync. A page that implied otherwise would send somebody looking
// for this morning's payment and let them conclude the product is broken. When
// the sync is stale the band becomes a warning strip above everything, because
// a stale sync does not make one number wrong — it makes all of them another
// day's.

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { useState } from "react";

import { money } from "../../money";
import { InlineLink, LoadingState, MetricCard } from "../kit";
import { papi } from "../api";
import type { PlatformSession } from "../types";
import { useInsight } from "./useInsight";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];

/** A tile's headline. Count first where there is one — it is the thing
 *  somebody acts on; an amount is the exposure behind it. */
function value(t: Row): string {
  if (t.count != null) return Number(t.count).toLocaleString("en-IN");
  if (t.amount != null || t.amount_out != null) {
    const inAmt = Number(t.amount ?? 0);
    const outAmt = Number(t.amount_out ?? 0);
    if (outAmt && inAmt) return money(inAmt);
    return money(inAmt || outAmt);
  }
  return "—";
}

function Tile({ tile, onNavigate }: {
  tile: Row; onNavigate: (r: string) => void;
}) {
  const both = tile.amount != null && tile.amount_out != null
    && (Number(tile.amount) || Number(tile.amount_out));
  const breakdown = rows(tile.breakdown);
  return (
    <MetricCard
      label={String(tile.label)}
      value={value(tile)}
      tip={String(tile.why)}
      sub={
        <Stack spacing={0.5}>
          {both ? (
            <Typography variant="body2" color="text.secondary">
              {money(Number(tile.amount))} in ·{" "}
              {money(Number(tile.amount_out))} out
            </Typography>
          ) : null}
          {/* The per-company split, where the source could answer it exactly.
              A tile with no breakdown is not hiding one — see daily._breakdown:
              a split taken from a capped list would not add up to its own
              headline, and parts that do not sum are worse than no parts. */}
          {breakdown.length > 0 && (
            <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap" }}>
              {breakdown.map((b, i) => (
                <Chip key={i} size="small" variant="outlined"
                      label={`${String(b.company)} ${Number(b.value).toLocaleString("en-IN")}`} />
              ))}
            </Stack>
          )}
          {tile.settled ? (
            <Typography variant="body2" color="text.secondary">
              Nothing outstanding.
            </Typography>
          ) : tile.route ? (
            <InlineLink onClick={() => onNavigate(String(tile.route))}>
              Work through these →
            </InlineLink>
          ) : null}
        </Stack>
      }
    />
  );
}

/** The date control for the "What moved" band — and only that band.
 *
 *  It lives *inside* the band rather than at the top of the page, and that
 *  placement is the whole argument. Three of the four bands are not periods: an
 *  approval is waiting now, an invoice is overdue now, a commitment lands in
 *  the seven days from now. A picker in the page header would look like it
 *  governed all of them, and would then either be ignored by three or — worse —
 *  appear to have re-scoped numbers it never touched. Put where it works, it
 *  cannot make that claim.
 *
 *  Presets first because they are what somebody actually wants ("yesterday",
 *  "this week"), with the two date inputs underneath for the case they do not
 *  cover. "Since last sync" is the default and is offered as a way back — a
 *  filter with no reset is one people leave set and then misread.
 */
function MovedRange({
  frm, to, onChange,
}: { frm: string; to: string; onChange: (f: string, t: string) => void }) {
  const day = (back: number) => {
    const d = new Date();
    d.setDate(d.getDate() - back);
    return d.toISOString().slice(0, 10);
  };
  const presets: [string, () => void][] = [
    ["Since last sync", () => onChange("", "")],
    ["Today", () => onChange(day(0), day(0))],
    ["Yesterday", () => onChange(day(1), day(1))],
    ["Last 7 days", () => onChange(day(6), "")],
    ["Last 30 days", () => onChange(day(29), "")],
  ];
  return (
    <Stack direction="row" spacing={1}
           sx={{ flexWrap: "wrap", rowGap: 1, mb: 1, alignItems: "center" }}>
      {presets.map(([label, set]) => (
        <Chip key={label} label={label} size="small" onClick={set}
              variant={label === "Since last sync" && !frm ? "filled" : "outlined"} />
      ))}
      <TextField
        type="date" size="small" label="From" value={frm}
        onChange={(e) => onChange(e.target.value, to)}
        slotProps={{ inputLabel: { shrink: true } }} sx={{ width: 165 }} />
      <TextField
        type="date" size="small" label="To" value={to}
        onChange={(e) => onChange(frm, e.target.value)}
        // Meaningless without a start, and a lone end date would silently be
        // ignored by the server rather than doing anything.
        disabled={!frm}
        slotProps={{ inputLabel: { shrink: true } }} sx={{ width: 165 }} />
    </Stack>
  );
}

export function DailyScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (route: string) => void }) {
  // The window the "What moved" band reports over. Empty means the default:
  // since the previous sync. Deliberately *not* page-wide — see MovedRange.
  const [frm, setFrm] = useState("");
  const [to, setTo] = useState("");
  const { data, loading, error } = useInsight(
    "daily", () => papi.daily(session.token, frm || undefined, to || undefined),
    [session.token, frm, to]);

  const fresh = (data?.freshness as Row | undefined) ?? {};
  const bands = rows(data?.bands);
  const wants = Number(data?.wants_attention ?? 0);

  if (loading) return <LoadingState rows={2} label="Reading this morning…" />;
  // Not a blocking error screen: the rest of the landing page is still useful
  // when this one endpoint fails, so it says so in place and gets out of the
  // way. "We could not look" is never "there is nothing to see".
  if (error) {
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        <AlertTitle>The morning read did not load</AlertTitle>
        {error}
      </Alert>
    );
  }
  if (!bands.length) return null;

  return (
    <Box sx={{ mb: 3 }}>
      <Alert
        severity={fresh.stale ? "warning" : "info"}
        sx={{ mb: 2 }}
        action={
          <InlineLink onClick={() => onNavigate("data")}>Sync</InlineLink>
        }
      >
        <AlertTitle sx={{ mb: 0 }}>
          {fresh.stale ? "This is not today's picture" : "Up to date"}
        </AlertTitle>
        {String(fresh.headline ?? "")}
      </Alert>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        {wants === 0
          ? "Nothing is asking for you this morning."
          : `${wants} thing${wants === 1 ? "" : "s"} want${wants === 1 ? "s" : ""} attention.`}
        {" "}
        {/* No total in money. Dead stock and overdue cash are different claims,
            and a sum of them is a figure that means nothing but invites a
            decision against it — the same reason the queue below has no total. */}
      </Typography>

      <Stack spacing={2.5}>
        {bands.map((band, i) => (
          <Box key={i}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: "block", lineHeight: 1.6 }}>
              {String(band.label)} — {String(band.question)}
            </Typography>
            {band.key === "MOVED" && (
              <MovedRange frm={frm} to={to}
                          onChange={(f, t) => { setFrm(f); setTo(t); }} />
            )}
            <Box
              sx={{
                display: "grid", gap: 1.5, mt: 0.5,
                gridTemplateColumns: {
                  xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(4, 1fr)",
                },
              }}
            >
              {rows(band.tiles).map((t, j) => (
                <Tile key={j} tile={t} onNavigate={onNavigate} />
              ))}
            </Box>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
