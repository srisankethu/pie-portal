// Which quotes were won, which were lost, and whether the price is the reason.
//
// The platform has recorded quote outcomes for a long time and has never shown
// one. This is the screen that reads them, and it is deliberately two screens
// stacked: what happened, then — for a manager — why.
//
// **The dialog is the point of the top half.** A win rate is only as good as
// the outcomes somebody bothered to record, so recording one has to live where
// the rate is read rather than three clicks away in the builder. Marking a
// quote lost asks why, from the server's own vocabulary, because a list of five
// codes is countable and a paragraph is not. The note stays beside it, which is
// what stops the list lying when reality does not fit one of the five.
//
// **Nothing here computes a number.** Every figure is served by
// `/api/v1/insight/quote-outcomes` and `/quote-pricing`, and the pricing half
// is a *separate request* rather than fields on the first — a salesperson's
// browser is never sent margin and then asked not to draw it. When they lack
// the role, the panel is absent rather than rendered and 403'd, the same rule
// the cash projection follows.

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useMemo, useState } from "react";

import { intelligence } from "../../intelligence";
import { money } from "../../money";
import { formatDate } from "../../when";
import type { QuoteLossReason, QuoteOutcomeStatus } from "../../types";
import { abilityFor } from "../ability";
import { papi } from "../api";
import { DataGrid, numeric, text } from "../DataGrid";
import type { ColDef } from "../DataGrid";
import { EntityName } from "../EntityName";
import {
  CurrencyValue, EmptyState, ErrorState, LoadingState, MetricCard,
  PercentageValue, SectionHeader, StatusChip, VarianceIndicator,
} from "../kit";
import type { EntityOrigin, PlatformSession, Sourced } from "../types";
import { Seg } from "./Seg";
import { useInsight } from "./useInsight";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
/** The same list, read as the shape a grid's columns declare. One helper
 *  rather than a cast per call site, so the `unknown` hop is written once. */
const typed = <T,>(v: unknown): T[] => (v as T[] | undefined) ?? [];
const num = (v: unknown): number => Number(v ?? 0);
const maybe = (v: unknown): number | null => (v == null ? null : Number(v));

/** Whose problem a loss is. The owner's question — *a pricing problem or a
 *  stock problem* — is this mapping, and the server sends the key so the two
 *  ends cannot drift on which reason means which. */
const OWNER_TONE: Record<string, "bad" | "warn" | "info" | "neutral"> = {
  PRICING: "bad",
  SUPPLY: "warn",
  POSITION: "info",
  NOT_OURS: "neutral",
  UNKNOWN: "neutral",
};

type SliceRow = Sourced & {
  key: string; label: string;
  decided: number; won: number; lost: number;
  win_rate: number | null; estimable: boolean;
  won_value: number; lost_value: number; quoted_value: number;
};

type QuoteRow = {
  quote_id: string; customer_label: string; status: string;
  loss_reason: string | null; loss_reason_label: string | null;
  decided_on: string; lines: number; value: number;
};

type AwaitingRow = {
  quote_id: string; customer_id: string | null; customer_label: string;
  status: QuoteOutcomeStatus; sent_at: string | null;
  lines: number; value: number; allowed_next: QuoteOutcomeStatus[];
};

const SLICES: [string, string][] = [
  ["customers", "Customer"],
  ["principals", "Principal"],
  ["product_lines", "Product line"],
  ["months", "Month"],
];

export function QuoteOutcomesScreen({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "quote-outcomes", () => papi.quoteOutcomes(session.token), [session.token]);

  // Manager territory, and omitted rather than rendered and then refused —
  // a panel that always fails teaches people the product is broken.
  const mayReadEconomics = abilityFor(session).can("read", "economics");

  return (
    <Stack spacing={3}>
      <SectionHeader
        title="Quotes won and lost"
        sub={"How often quotes are accepted, by customer, principal and product "
             + "line — and what the losses have in common. Recorded outcomes "
             + "only: a quote nobody has answered is not a loss."}
      />
      <OutcomePanel data={data} loading={loading} error={error} reload={reload}
                    session={session} />
      {mayReadEconomics && <PricingPanel session={session} />}
    </Stack>
  );
}

// ── what happened ───────────────────────────────────────────────────────────

function OutcomePanel({
  data, loading, error, reload, session,
}: {
  data: Record<string, unknown> | null;
  loading: boolean; error: string | null; reload: () => void;
  session: PlatformSession;
}) {
  const [slice, setSlice] = useState("customers");
  const [recording, setRecording] = useState<AwaitingRow | null>(null);

  const reasons = rows(data?.reasons);
  const catalogue = (data?.reason_catalogue as Record<string, Record<string, string>>) ?? {};
  const owners = (data?.owners as Record<string, string>) ?? {};
  const decided = num(data?.decided);
  const winRate = maybe(data?.win_rate);
  const floor = num(data?.min_decided_quotes);
  const sourcesDiffer = Boolean(data?.sources_differ);
  const unpriced = num(data?.unpriced_quotes);

  const sliceRows = useMemo(
    () => (data ? typed<SliceRow>(data[slice]) : null), [data, slice]);

  const sliceColumns = useMemo<ColDef<SliceRow>[]>(() => [
    {
      ...text<SliceRow>("label", slice === "months" ? "Month" : "Name"),
      minWidth: 200,
      cellRenderer: (p: { data?: SliceRow }) => (p.data ? (
        <EntityName name={p.data.label} origin={p.data.origin as EntityOrigin | undefined}
                    show={sourcesDiffer && slice === "customers"} strong={false} />
      ) : null),
    },
    numeric<SliceRow>("decided", "Decided", (v) => v.toLocaleString("en-IN"),
                      { width: 110 }),
    numeric<SliceRow>("won", "Won", (v) => v.toLocaleString("en-IN"), { width: 90 }),
    numeric<SliceRow>("lost", "Lost", (v) => v.toLocaleString("en-IN"), { width: 90 }),
    {
      ...numeric<SliceRow>("win_rate", "Win rate", (v) => `${(v * 100).toFixed(0)}%`,
                           { width: 150 }),
      // Below the floor there is no rate, and the cell says which — a blank
      // would read as nought, which is a finding this evidence cannot support.
      cellRenderer: (p: { data?: SliceRow }) => (p.data ? (
        p.data.estimable
          ? <PercentageValue value={p.data.win_rate} digits={0} />
          : <StatusChip label={`${p.data.decided} of ${floor}`} dense
                        tip={`A win rate is not computed until ${floor} quotes have been decided.`} />
      ) : null),
    },
    numeric<SliceRow>("lost_value", "Value lost", money, { width: 150 }),
    numeric<SliceRow>("quoted_value", "Value quoted", money, { width: 155 }),
  ], [slice, sourcesDiffer, floor]);

  const quoteColumns = useMemo<ColDef<QuoteRow>[]>(() => [
    text<QuoteRow>("customer_label", "Customer", { minWidth: 200 }),
    text<QuoteRow>("quote_id", "Quote", { flex: 0.6, minWidth: 130 }),
    {
      field: "status" as never, headerName: "Outcome", width: 120,
      cellRenderer: (p: { data?: QuoteRow }) => (p.data ? (
        <StatusChip label={p.data.status === "WON" ? "Won" : "Lost"}
                    tone={p.data.status === "WON" ? "good" : "bad"} />
      ) : null),
    },
    {
      field: "loss_reason" as never, headerName: "Why", flex: 1, minWidth: 190,
      cellRenderer: (p: { data?: QuoteRow }) => {
        if (!p.data || p.data.status === "WON") return null;
        const code = p.data.loss_reason ?? "NOT_RECORDED";
        return (
          <StatusChip
            label={p.data.loss_reason_label ?? code}
            tone={OWNER_TONE[catalogue[code]?.owner ?? "UNKNOWN"] ?? "neutral"}
            tip={catalogue[code]?.meaning}
          />
        );
      },
    },
    {
      ...text<QuoteRow>("decided_on", "Decided", { flex: 0.6, minWidth: 130 }),
      valueFormatter: (p: { value?: unknown }) => formatDate(String(p.value ?? "")),
    },
    numeric<QuoteRow>("lines", "Lines", (v) => v.toLocaleString("en-IN"), { width: 100 }),
    numeric<QuoteRow>("value", "Quoted", money, { width: 140 }),
  ], [catalogue]);

  if (loading) return <LoadingState rows={4} height={80} label="Reading quote outcomes…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  const awaiting = typed<AwaitingRow>(data?.awaiting);

  return (
    <Stack spacing={3}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <Box sx={{ flex: 1 }}>
          <MetricCard
            label="Win rate"
            value={winRate == null
              ? "—"
              : <PercentageValue value={winRate} digits={0} />}
            sub={winRate == null
              ? `${decided} decided — a rate is not computed until ${floor}`
              : `${num(data?.won)} won of ${decided} decided`}
            tip={"Counted per quote, not per line: a customer accepts or declines "
                 + "a whole quote."}
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard label="Awaiting an answer" value={num(data?.open)}
                      sub="Not counted as losses" />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard label="Value won" value={<CurrencyValue value={num(data?.won_value)} />}
                      sub="At the price that went out" />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard label="Value lost" value={<CurrencyValue value={num(data?.lost_value)} />}
                      sub={unpriced ? `${unpriced} decided quote${unpriced === 1 ? "" : "s"} had no priced lines` : undefined} />
        </Box>
      </Stack>

      {data?.empty_reason ? (
        <EmptyState title="No quote has been answered yet"
                    reason={String(data.empty_reason)} />
      ) : null}

      {reasons.length > 0 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <SectionHeader
            level="widget" title="Why the losses were lost"
            sub={"A pricing problem and a stock problem look identical in a win "
                 + "rate and need completely different work."}
          />
          <Stack spacing={1.5} sx={{ mt: 1.5 }}>
            {reasons.map((r) => {
              const owner = String(r.owner);
              return (
                <Box key={String(r.reason)}>
                  <Stack direction="row" spacing={1}
                         sx={{ alignItems: "center", mb: 0.5 }}>
                    <StatusChip label={String(r.label)} tone={OWNER_TONE[owner] ?? "neutral"}
                                tip={catalogue[String(r.reason)]?.meaning} />
                    <Typography variant="body2">
                      {String(r.count)} lost · <CurrencyValue value={num(r.value)} />
                    </Typography>
                    <Typography variant="caption" color="text.secondary"
                                sx={{ ml: "auto" }}>
                      {owners[owner] ?? owner}
                    </Typography>
                  </Stack>
                  <LinearProgress
                    variant="determinate"
                    value={Math.round(num(r.share) * 100)}
                    aria-label={`${r.label}: ${Math.round(num(r.share) * 100)}% of losses`}
                    color={OWNER_TONE[owner] === "bad" ? "error"
                      : OWNER_TONE[owner] === "warn" ? "warning" : "primary"}
                  />
                </Box>
              );
            })}
          </Stack>
        </Paper>
      )}

      {awaiting.length > 0 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <SectionHeader
            level="widget" title="Waiting on the customer"
            sub={"Record what happened here. A loss asks why, because a win rate "
                 + "is only as good as the reasons behind it."}
          />
          <Box sx={{ mt: 1.5 }}>
            <DataGrid<AwaitingRow>
              ariaLabel="Quotes awaiting an outcome"
              rows={awaiting}
              getRowId={(r) => r.quote_id}
              filters={false}
              height={Math.min(420, 100 + awaiting.length * 44)}
              columns={[
                text<AwaitingRow>("customer_label", "Customer", { minWidth: 200 }),
                text<AwaitingRow>("quote_id", "Quote", { flex: 0.6, minWidth: 130 }),
                {
                  field: "status" as never, headerName: "State", width: 110,
                  cellRenderer: (p: { data?: AwaitingRow }) => (p.data ? (
                    <StatusChip label={p.data.status === "SENT" ? "Sent" : "Draft"}
                                tone={p.data.status === "SENT" ? "info" : "neutral"} />
                  ) : null),
                },
                numeric<AwaitingRow>("value", "Quoted", money, { width: 140 }),
                {
                  headerName: "", width: 150, sortable: false, filter: false,
                  // The column carries its own control, so clicking it must not
                  // also fire a row handler — `DataGridProps` documents this.
                  cellRenderer: (p: { data?: AwaitingRow }) => (p.data ? (
                    <Button size="small" onClick={() => setRecording(p.data!)}>
                      Record outcome
                    </Button>
                  ) : null),
                },
              ]}
            />
          </Box>
        </Paper>
      )}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <SectionHeader
          level="widget" title="Where we win and where we do not"
          sub={"A quote spanning several principals counts once in each of their "
               + "rates, so these sum to more than the total."}
          actions={<Seg label="By" value={slice} onChange={setSlice} options={SLICES} />}
        />
        <Box sx={{ mt: 1.5 }}>
          <DataGrid<SliceRow>
            ariaLabel="Win rate by slice"
            rows={sliceRows}
            columns={sliceColumns}
            getRowId={(r) => r.key}
            empty={<EmptyState title="Nothing decided in this cut yet"
                               reason={String(data?.empty_reason ?? "")} />}
          />
        </Box>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <SectionHeader level="widget" title="Every decided quote" />
        <Box sx={{ mt: 1.5 }}>
          <DataGrid<QuoteRow>
            ariaLabel="Decided quotes"
            rows={data ? typed<QuoteRow>(data.quotes) : null}
            columns={quoteColumns}
            getRowId={(r) => r.quote_id}
            empty={<EmptyState title="No quote has been marked won or lost"
                               reason={String(data?.empty_reason ?? "")} />}
          />
        </Box>
      </Paper>

      <RecordOutcomeDialog
        quote={recording}
        catalogue={catalogue}
        session={session}
        onClose={() => setRecording(null)}
        onRecorded={() => { setRecording(null); reload(); }}
      />
    </Stack>
  );
}

// ── recording an outcome ────────────────────────────────────────────────────
//
// Won is one click. Lost asks why, from the server's own list, and the free
// text sits underneath rather than instead of it: the code is what makes a loss
// countable, the note is what stops the code being a lie when the real answer
// was "their buyer left".

function RecordOutcomeDialog({
  quote, catalogue, session, onClose, onRecorded,
}: {
  quote: AwaitingRow | null;
  catalogue: Record<string, Record<string, string>>;
  session: PlatformSession;
  onClose: () => void;
  onRecorded: () => void;
}) {
  const [status, setStatus] = useState<QuoteOutcomeStatus>("WON");
  const [reason, setReason] = useState<QuoteLossReason | "">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const { enqueueSnackbar } = useSnackbar();

  // The catalogue carries a bucket for losses recorded before the vocabulary
  // existed. It is a reading state, never something to write, so it is not
  // offered here.
  const choices = Object.entries(catalogue).filter(([code]) => code !== "NOT_RECORDED");

  async function save() {
    if (!quote) return;
    setBusy(true);
    try {
      await intelligence.outcome(
        session.token, quote.quote_id, status, quote.customer_label,
        note.trim() || undefined,
        status === "LOST" ? (reason as QuoteLossReason) : undefined);
      enqueueSnackbar(`${quote.quote_id} recorded as ${status.toLowerCase()}`,
                      { variant: "success" });
      setStatus("WON"); setReason(""); setNote("");
      onRecorded();
    } catch (e) {
      enqueueSnackbar(e instanceof Error ? e.message : "Could not record that outcome",
                      { variant: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={quote !== null} onClose={busy ? undefined : onClose} fullWidth
            maxWidth="sm">
      <DialogTitle>What happened to {quote?.quote_id}?</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {quote?.customer_label} · {money(quote?.value ?? 0)} quoted across{" "}
          {quote?.lines ?? 0} line{quote?.lines === 1 ? "" : "s"}. A decided quote
          cannot be reopened, because a margin analysis has already counted it.
        </DialogContentText>
        <Stack spacing={2}>
          <TextField
            select fullWidth label="Outcome" value={status}
            onChange={(e) => setStatus(e.target.value as QuoteOutcomeStatus)}
          >
            {(quote?.allowed_next ?? ["WON", "LOST"])
              .filter((s) => s === "WON" || s === "LOST")
              .map((s) => (
                <MenuItem key={s} value={s}>{s === "WON" ? "Won" : "Lost"}</MenuItem>
              ))}
          </TextField>
          {status === "LOST" && (
            <TextField
              select fullWidth required label="Why we lost it" value={reason}
              onChange={(e) => setReason(e.target.value as QuoteLossReason)}
              helperText={reason ? catalogue[reason]?.meaning
                : "A loss without a reason can be counted and never learned from."}
            >
              {choices.map(([code, entry]) => (
                <MenuItem key={code} value={code}>{entry.label}</MenuItem>
              ))}
            </TextField>
          )}
          <TextField
            fullWidth multiline minRows={2} label="Note (optional)"
            value={note} onChange={(e) => setNote(e.target.value)}
            helperText="What the list cannot say — the PO number, who took it, what they asked for."
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained" onClick={save}
          disabled={busy || (status === "LOST" && !reason)}
        >
          {busy ? "Recording…" : "Record"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── why, for a manager ──────────────────────────────────────────────────────

type ComparisonRow = {
  product_id: string; product_label: string; quantity_band: string;
  won_observations: number; lost_observations: number;
  won_median_price: number; lost_median_price: number; gap_pct: number | null;
};

function PricingPanel({ session }: { session: PlatformSession }) {
  const { data, loading, error, reload } = useInsight(
    "quote-pricing", () => papi.quotePricing(session.token), [session.token]);

  const columns = useMemo<ColDef<ComparisonRow>[]>(() => [
    text<ComparisonRow>("product_label", "Item", { minWidth: 220 }),
    text<ComparisonRow>("quantity_band", "Quantity band", { flex: 0.5, minWidth: 140 }),
    numeric<ComparisonRow>("won_median_price", "Wins at", money, { width: 130 }),
    numeric<ComparisonRow>("lost_median_price", "Lost at", money, { width: 130 }),
    {
      ...numeric<ComparisonRow>("gap_pct", "Gap", (v) => `${(v * 100).toFixed(1)}%`,
                                { width: 140 }),
      cellRenderer: (p: { data?: ComparisonRow }) => (p.data ? (
        // Up is bad here: a losing price above the winning one is the finding.
        <VarianceIndicator value={p.data.gap_pct} format="percent" invert />
      ) : null),
    },
    numeric<ComparisonRow>("won_observations", "Wins", (v) => String(v), { width: 100 }),
    numeric<ComparisonRow>("lost_observations", "Losses", (v) => String(v), { width: 110 }),
  ], []);

  if (loading) return <LoadingState rows={3} height={70} label="Reading priced snapshots…" />;
  if (error) return <ErrorState title="The pricing analysis did not load"
                                error={error} onRetry={reload} />;

  const gap = maybe(data?.median_gap_pct);
  const history = data?.versus_own_history as { observations: number;
                                                median_gap_pct: number } | null;

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <SectionHeader
        level="section" title="Are we losing on price?"
        sub={"What the losses were quoted at, against what the same item at the "
             + "same quantity actually wins at. Cost and margin — management "
             + "information, and not part of the win rate above."}
      />

      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mt: 2 }}>
        <Box sx={{ flex: 1 }}>
          <MetricCard
            label="Losses sit above the winning price by"
            value={gap == null ? "—" : <PercentageValue value={gap} />}
            sub={gap == null
              ? `No item has enough quotes on both sides yet (${num(data?.min_price_observations)} each)`
              : `Across ${num(data?.compared_products)} item${num(data?.compared_products) === 1 ? "" : "s"}`}
            tip={String(data?.note ?? "")}
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard
            label="Margin on what we win"
            value={<PercentageValue value={maybe(data?.won_margin)} />}
            sub={`Σ gross profit ÷ Σ revenue over ${num(data?.costed_quotes)} fully-costed quotes`}
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard
            label="Margin on what we lost"
            value={<PercentageValue value={maybe(data?.lost_margin)} />}
            variance={<VarianceIndicator value={maybe(data?.margin_gap_pp)}
                                          format="percent" invert
                                          label="against what wins" />}
            sub="Percentage points, never a percentage of a percentage"
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <MetricCard
            label="Against what they already pay"
            value={history == null ? "—"
              : <PercentageValue value={history.median_gap_pct} />}
            sub={history == null
              ? "Too few lost lines have a price history behind them"
              : `Median across ${history.observations} lost lines`}
            tip={"The winning price says where the market is; this says whether "
                 + "we moved against this particular relationship."}
          />
        </Box>
      </Stack>

      <Box sx={{ mt: 2 }}>
        <DataGrid<ComparisonRow>
          ariaLabel="Losing prices against winning prices"
          rows={data ? typed<ComparisonRow>(data.comparisons) : null}
          columns={columns}
          getRowId={(r) => `${r.product_id}:${r.quantity_band}`}
          empty={<EmptyState
            title="No item has been quoted often enough on both sides"
            reason={String(data?.empty_reason ?? "")} />}
        />
      </Box>
    </Paper>
  );
}
