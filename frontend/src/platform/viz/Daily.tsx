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
import Typography from "@mui/material/Typography";

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

export function DailyScreen({
  session, onNavigate,
}: { session: PlatformSession; onNavigate: (route: string) => void }) {
  const { data, loading, error } = useInsight(
    "daily", () => papi.daily(session.token), [session.token]);

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
