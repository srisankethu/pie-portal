/** The pricing calculator: what PIE should charge, and why.
 *
 *  Not a tenant screen. Everything on it is PIE's own commercial position —
 *  cost to serve, intended value capture, the fee each segment should pay — and
 *  the nav item only exists for an identity the server has already told us is
 *  an operator (`papi.monetizationAccess`). The endpoints refuse everyone else
 *  regardless; the door is hidden so nobody is offered one that always 403s,
 *  which is the rule the rest of this nav already follows.
 *
 *  Two decisions worth stating because they are the ones a reader would
 *  otherwise question:
 *
 *  **Money arrives as decimal strings and is parsed once, at the edge.** The
 *  server computes in `Decimal`; turning a fee into a float in six components
 *  is how two panels come to disagree in the last two digits. `n()` is that
 *  edge, and every panel below reads what it returns.
 *
 *  **A refusal is rendered, never hidden.** Every evaluated fee carries the
 *  reason it should not be offered — below the cost floor, under the ROI bar,
 *  past the payback limit — and a row that fails its own threshold while
 *  looking like every other row is exactly how a bad price gets quoted. The
 *  comparison grid carries a verdict column for that reason, and the
 *  recommendation panel leads with the band being empty when it is.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { money } from "../money";
import { papi } from "./api";
import { DataGrid, type ColDef } from "./DataGrid";
import {
  ErrorState, LoadingState, MetricCard, SectionHeader, StatusChip, type Tone,
} from "./kit";
import type {
  MonetizationCalculation, MonetizationEvaluation, MonetizationHybridRow,
  MonetizationScorecard, MonetizationScorecardRow, MonetizationSegments,
  PlatformSession,
} from "./types";

/** The one place a decimal string becomes a number. See the header. */
function n(value: string | null | undefined): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function inr(value: string | null | undefined): string {
  const parsed = n(value);
  return parsed == null ? "—" : money(parsed);
}

/** Crores, for figures where the rupee digits are noise. */
function cr(value: string | null | undefined): string {
  const parsed = n(value);
  return parsed == null ? "—" : `₹${(parsed / 1e7).toFixed(2)} Cr`;
}

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function roiText(value: number | null | undefined): string {
  return value == null ? "UNKNOWN" : `${value.toFixed(1)}×`;
}

/** The form's own state: strings, because these are text fields and parsing
 *  each keystroke into a number makes a half-typed "0." unrepresentable. */
interface FormState {
  annual_rfqs: string;
  pie_rfq_share: string;
  quote_conversion: string;
  order_conversion: string;
  average_order_value: string;
  gross_margin: string;
  sales_engineers: string;
  cost_per_employee_year: string;
  rfq_processing_minutes: string;
  quotation_minutes: string;
  sku_count: string;
  erp_rows_millions: string;
  // PIE impact
  quote_conversion_uplift_pp: string;
  order_conversion_uplift_pp: string;
  aov_uplift: string;
  gross_margin_uplift_pp: string;
  procurement_saving_rate: string;
  minutes_saved_per_rfq: string;
  minutes_saved_per_quote: string;
}

const BLANK: FormState = {
  annual_rfqs: "63400", pie_rfq_share: "0.6", quote_conversion: "0.5",
  order_conversion: "0.3", average_order_value: "160000", gross_margin: "0.23",
  sales_engineers: "18", cost_per_employee_year: "1100000",
  rfq_processing_minutes: "13", quotation_minutes: "20",
  sku_count: "45000", erp_rows_millions: "2.5",
  quote_conversion_uplift_pp: "0.05", order_conversion_uplift_pp: "0.02",
  aov_uplift: "0.03", gross_margin_uplift_pp: "0.006",
  procurement_saving_rate: "0.004",
  minutes_saved_per_rfq: "6", minutes_saved_per_quote: "12",
};

const NUM = (v: string, fallback = 0): number => {
  const parsed = Number(v);
  return Number.isFinite(parsed) ? parsed : fallback;
};

function toRequest(form: FormState, name: string) {
  return {
    profile: {
      name,
      annual_rfqs: Math.max(0, Math.round(NUM(form.annual_rfqs))),
      pie_rfq_share: NUM(form.pie_rfq_share),
      quote_conversion: NUM(form.quote_conversion),
      order_conversion: NUM(form.order_conversion),
      // Money as a string: it goes to a `Decimal` on the server, and a JSON
      // number would round-trip through a float on the way.
      average_order_value: form.average_order_value || "0",
      gross_margin: NUM(form.gross_margin),
      sales_engineers: Math.max(0, Math.round(NUM(form.sales_engineers))),
      cost_per_employee_year: form.cost_per_employee_year || "0",
      rfq_processing_minutes: NUM(form.rfq_processing_minutes),
      quotation_minutes: NUM(form.quotation_minutes),
      sku_count: Math.max(0, Math.round(NUM(form.sku_count))),
      erp_rows_millions: NUM(form.erp_rows_millions),
    },
    impact: {
      quote_conversion_uplift_pp: NUM(form.quote_conversion_uplift_pp),
      order_conversion_uplift_pp: NUM(form.order_conversion_uplift_pp),
      aov_uplift: NUM(form.aov_uplift),
      gross_margin_uplift_pp: NUM(form.gross_margin_uplift_pp),
      procurement_saving_rate: NUM(form.procurement_saving_rate),
      minutes_saved_per_rfq: NUM(form.minutes_saved_per_rfq),
      minutes_saved_per_quote: NUM(form.minutes_saved_per_quote),
      response_time_improvement: 0.6,
      substitution_opportunity_rate: 0.18,
      substitution_success_rate: 0.35,
    },
  };
}

function verdictTone(row: MonetizationEvaluation): Tone {
  if (row.refusal) return "bad";
  if (!row.clears_payback) return "warn";
  return "good";
}

function verdictLabel(row: MonetizationEvaluation): string {
  if (row.refusal) return "Refused";
  return "Clears";
}

export function MonetizationScreen({ session }: { session: PlatformSession }) {
  const [form, setForm] = useState<FormState>(BLANK);
  const [segments, setSegments] = useState<MonetizationSegments | null>(null);
  const [result, setResult] = useState<MonetizationCalculation | null>(null);
  const [scorecard, setScorecard] = useState<MonetizationScorecard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("Mid-market distributor");

  const set = (key: keyof FormState) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const run = useCallback(async (state: FormState, label: string) => {
    setBusy(true);
    setError(null);
    try {
      setResult(await papi.monetizationCalculate(
        session.token, toRequest(state, label)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [session.token]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [seg, card] = await Promise.all([
          papi.monetizationSegments(session.token),
          papi.monetizationScorecard(session.token),
        ]);
        if (!live) return;
        setSegments(seg);
        setScorecard(card);
      } catch (e) {
        if (live) setError((e as Error).message);
      }
    })();
    return () => { live = false; };
  }, [session.token]);

  useEffect(() => { void run(BLANK, "Mid-market distributor"); }, [run]);

  const loadArchetype = (key: string) => {
    const a = segments?.archetypes?.[key];
    if (!a) return;
    const next: FormState = {
      ...form,
      annual_rfqs: String(a.annual_rfqs ?? ""),
      pie_rfq_share: String(a.pie_rfq_share ?? ""),
      quote_conversion: String(a.quote_conversion ?? ""),
      order_conversion: String(a.order_conversion ?? ""),
      average_order_value: String(a.average_order_value ?? ""),
      gross_margin: String(a.gross_margin ?? ""),
      sales_engineers: String(a.sales_engineers ?? ""),
      cost_per_employee_year: String(a.cost_per_employee_year ?? ""),
      rfq_processing_minutes: String(a.rfq_processing_minutes ?? ""),
      quotation_minutes: String(a.quotation_minutes ?? ""),
      sku_count: String(a.sku_count ?? ""),
      erp_rows_millions: String(a.erp_rows_millions ?? ""),
    };
    setForm(next);
    setName(String(a.name ?? key));
    void run(next, String(a.name ?? key));
  };

  const loadImpact = (key: string) => {
    const i = segments?.impacts?.[key];
    if (!i) return;
    const next: FormState = {
      ...form,
      quote_conversion_uplift_pp: String(i.quote_conversion_uplift_pp ?? 0),
      order_conversion_uplift_pp: String(i.order_conversion_uplift_pp ?? 0),
      aov_uplift: String(i.aov_uplift ?? 0),
      gross_margin_uplift_pp: String(i.gross_margin_uplift_pp ?? 0),
      procurement_saving_rate: String(i.procurement_saving_rate ?? 0),
      minutes_saved_per_rfq: String(i.minutes_saved_per_rfq ?? 0),
      minutes_saved_per_quote: String(i.minutes_saved_per_quote ?? 0),
    };
    setForm(next);
    void run(next, name);
  };

  const strategyColumns = useMemo<ColDef<MonetizationEvaluation>[]>(() => [
    { headerName: "Pricing model", field: "fee.label" as never, flex: 2, minWidth: 200,
      valueGetter: (p) => p.data?.fee.label },
    { headerName: "Metric", flex: 1.4, minWidth: 150,
      valueGetter: (p) => p.data?.fee.metric },
    { headerName: "Annual fee", flex: 1.2, minWidth: 130, type: "numericColumn",
      valueGetter: (p) => n(p.data?.fee.annual_fee ?? null),
      valueFormatter: (p) => (p.value == null ? "—" : money(p.value as number)) },
    { headerName: "Customer ROI", flex: 1, minWidth: 120, type: "numericColumn",
      valueGetter: (p) => p.data?.customer_roi ?? null,
      valueFormatter: (p) => roiText(p.value as number | null) },
    { headerName: "PIE capture", flex: 1, minWidth: 110, type: "numericColumn",
      valueGetter: (p) => p.data?.value_capture_pct ?? null,
      valueFormatter: (p) => pct(p.value as number | null) },
    { headerName: "Payback (mo)", flex: 1, minWidth: 120, type: "numericColumn",
      valueGetter: (p) => p.data?.payback_months ?? null,
      valueFormatter: (p) => (p.value == null ? "—" : (p.value as number).toFixed(1)) },
    { headerName: "% of gross profit", flex: 1.1, minWidth: 140,
      type: "numericColumn",
      valueGetter: (p) => p.data?.fee_pct_of_gross_profit ?? null,
      valueFormatter: (p) => pct(p.value as number | null, 2) },
    // A verdict column, not a colour. See the header: a row that fails its own
    // threshold and looks like the rest is how a bad price gets quoted.
    { headerName: "Verdict", flex: 1.6, minWidth: 180,
      valueGetter: (p) => (p.data ? verdictLabel(p.data) : ""),
      tooltipValueGetter: (p) => p.data?.refusal ?? "",
      cellRenderer: (p: { data?: MonetizationEvaluation }) => (
        p.data
          ? <StatusChip tone={verdictTone(p.data)} label={verdictLabel(p.data)} />
          : null),
    },
  ], []);

  const scorecardColumns = useMemo<ColDef<MonetizationScorecardRow>[]>(() => [
    { headerName: "#", field: "rank", width: 70, type: "numericColumn" },
    { headerName: "Pricing metric", field: "label", flex: 2, minWidth: 220 },
    { headerName: "Weighted", field: "weighted_score", flex: 1, minWidth: 110,
      type: "numericColumn",
      valueFormatter: (p) => (p.value as number).toFixed(2) },
    { headerName: "Value fit", flex: 1, minWidth: 100, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.correlation_with_value },
    { headerName: "Measurable", flex: 1, minWidth: 110, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.ease_of_measurement },
    { headerName: "Predictable", flex: 1, minWidth: 110, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.predictability },
    // Derived from the incentive register rather than judged — see the server.
    { headerName: "Gaming resist.", flex: 1, minWidth: 120, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.gaming_resistance },
    { headerName: "Worst exposure", flex: 1, minWidth: 125, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.worst_case_exposure },
    { headerName: "Renewal defence", flex: 1, minWidth: 130, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.renewal_defensibility },
    { headerName: "Low friction", flex: 1, minWidth: 110, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.low_sales_friction },
    { headerName: "Alignment", flex: 1, minWidth: 110, type: "numericColumn",
      valueGetter: (p) => p.data?.scores.incentive_alignment },
  ], []);

  const wf = result?.waterfall;
  const rec = result?.recommendation;
  const econ = result?.pie_unit_economics;

  return (
    <Stack spacing={3}>
      <SectionHeader
        title="Pricing model"
        sub={
          "What PIE should charge for the value it creates. Every figure is "
          + "computed from the inputs below and PIE's own assumption set "
          + (result ? `(${result.parameters_version}).` : ".")
        }
      />

      {error && <ErrorState error={error} />}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} useFlexGap
                 sx={{ flexWrap: "wrap", alignItems: "center" }}>
            <Typography variant="overline" color="text.secondary">
              Start from
            </Typography>
            {Object.entries(segments?.archetypes ?? {}).map(([key, a]) => (
              <Chip key={key} label={String(a.name ?? key)} size="small"
                    variant="outlined" onClick={() => loadArchetype(key)} />
            ))}
            <Divider orientation="vertical" flexItem />
            <Typography variant="overline" color="text.secondary">
              PIE impact
            </Typography>
            {Object.keys(segments?.impacts ?? {}).map((key) => (
              <Chip key={key} label={key} size="small" variant="outlined"
                    onClick={() => loadImpact(key)} />
            ))}
          </Stack>

          <Grid container spacing={2}>
            <Grid size={{ xs: 12, md: 3 }}>
              <TextField fullWidth size="small" label="Customer name"
                         value={name} onChange={(e) => setName(e.target.value)} />
            </Grid>
            {([
              ["annual_rfqs", "Annual RFQs"],
              ["pie_rfq_share", "Share through PIE (0-1)"],
              ["quote_conversion", "RFQ → quote (0-1)"],
              ["order_conversion", "Quote → order (0-1)"],
              ["average_order_value", "Average order value (₹)"],
              ["gross_margin", "Gross margin (0-1)"],
              ["sales_engineers", "Sales engineers"],
              ["cost_per_employee_year", "Cost per engineer / yr (₹)"],
              ["rfq_processing_minutes", "Minutes per RFQ"],
              ["quotation_minutes", "Minutes per quote"],
              ["sku_count", "SKUs in catalogue"],
              ["erp_rows_millions", "ERP rows (millions)"],
            ] as [keyof FormState, string][]).map(([key, label]) => (
              <Grid size={{ xs: 6, sm: 4, md: 3 }} key={key}>
                <TextField fullWidth size="small" label={label}
                           value={form[key]} onChange={set(key)} />
              </Grid>
            ))}
          </Grid>

          <Divider />
          <Typography variant="overline" color="text.secondary">
            What PIE changes
          </Typography>
          <Grid container spacing={2}>
            {([
              ["quote_conversion_uplift_pp", "Quote conversion uplift (pp)"],
              ["order_conversion_uplift_pp", "Order conversion uplift (pp)"],
              ["aov_uplift", "Order value uplift (ratio)"],
              ["gross_margin_uplift_pp", "Gross margin uplift (pp)"],
              ["procurement_saving_rate", "Procurement saving (of COGS)"],
              ["minutes_saved_per_rfq", "Minutes saved per RFQ"],
              ["minutes_saved_per_quote", "Minutes saved per quote"],
            ] as [keyof FormState, string][]).map(([key, label]) => (
              <Grid size={{ xs: 6, sm: 4, md: 3 }} key={key}>
                <TextField fullWidth size="small" label={label}
                           value={form[key]} onChange={set(key)} />
              </Grid>
            ))}
          </Grid>

          <Box>
            <Button variant="contained" disabled={busy}
                    onClick={() => void run(form, name)}>
              {busy ? "Calculating…" : "Recalculate"}
            </Button>
          </Box>
        </Stack>
      </Paper>

      {!result && !error && <LoadingState label="Pricing this customer" />}

      {wf && rec && econ && (
        <>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <MetricCard label="Economic value created"
                          value={cr(wf.total_economic_value)}
                          sub={`Incremental gross profit ${cr(wf.incremental_gross_profit)}`} />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <MetricCard label="Recommended annual fee"
                          value={inr(rec.recommended_annual_fee)}
                          sub={`Flat, banded · ${rec.structure.turnover_band}`} />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <MetricCard label="Customer ROI"
                          value={roiText(rec.evaluation.customer_roi)}
                          sub={`PIE captures ${pct(rec.evaluation.value_capture_pct)} `
                               + "of the value created"} />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <MetricCard label="PIE contribution margin"
                          value={pct(econ.contribution_margin)}
                          sub={`LTV/CAC ${econ.ltv_cac == null ? "—" : econ.ltv_cac.toFixed(1)}`
                               + ` · evidence ${econ.evidence_grade}`} />
            </Grid>
          </Grid>

          {rec.band.is_empty && (
            <Alert severity="error">{rec.band.note}</Alert>
          )}
          {econ.warnings.map((w) => (
            <Alert key={w} severity="warning">{w}</Alert>
          ))}

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="Is 0.1% of margin viable?"
              sub={result.margin_hypothesis.verdict.statement}
            />
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Reading of &ldquo;margin&rdquo;</TableCell>
                    <TableCell align="right">Fee at 0.1%</TableCell>
                    <TableCell align="right">Cost to serve</TableCell>
                    <TableCell align="right">Short by</TableCell>
                    <TableCell>Covers cost?</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(result.margin_hypothesis.verdict.readings)
                    .map(([base, reading]) => (
                      <TableRow key={base}>
                        <TableCell>{base.replaceAll("_", " ").toLowerCase()}</TableCell>
                        <TableCell align="right">{inr(reading.annual_fee)}</TableCell>
                        <TableCell align="right">
                          {inr(result.margin_hypothesis.cost_floor)}
                        </TableCell>
                        <TableCell align="right">
                          {reading.shortfall_multiple == null
                            ? "—" : `${reading.shortfall_multiple.toFixed(1)}×`}
                        </TableCell>
                        <TableCell>
                          <StatusChip
                            tone={reading.covers_cost_to_serve ? "good" : "bad"}
                            label={reading.covers_cost_to_serve ? "Yes" : "No"} />
                        </TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            </Box>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="Baseline → with PIE"
              sub={"Incremental revenue is reported and is never the value "
                        + "base. Value is gross profit, plus buy-side savings, "
                        + "plus labour only where an hourly rate exists."}
            />
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stage</TableCell>
                    <TableCell align="right">Baseline</TableCell>
                    <TableCell align="right">With PIE</TableCell>
                    <TableCell align="right">Difference</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {([
                    ["RFQs", wf.baseline.rfqs, wf.with_pie.rfqs],
                    ["Quotes", wf.baseline.quotes, wf.with_pie.quotes],
                    ["Orders", wf.baseline.orders, wf.with_pie.orders],
                    ["Revenue", wf.baseline.revenue, wf.with_pie.revenue],
                    ["Gross profit", wf.baseline.gross_profit,
                     wf.with_pie.gross_profit],
                  ] as [string, string, string][]).map(([label, before, after]) => {
                    const isMoney = label === "Revenue" || label === "Gross profit";
                    const fmt = (v: string) =>
                      isMoney ? cr(v) : (n(v) ?? 0).toLocaleString("en-IN",
                        { maximumFractionDigits: 0 });
                    const delta = (n(after) ?? 0) - (n(before) ?? 0);
                    return (
                      <TableRow key={label}>
                        <TableCell>{label}</TableCell>
                        <TableCell align="right">{fmt(before)}</TableCell>
                        <TableCell align="right">{fmt(after)}</TableCell>
                        <TableCell align="right">
                          {isMoney ? cr(String(delta))
                            : delta.toLocaleString("en-IN",
                              { maximumFractionDigits: 0 })}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </Box>
            <Divider sx={{ my: 2 }} />
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
              <Chip size="small" variant="outlined"
                    label={`From conversion ${cr(wf.components.gp_from_conversion)}`} />
              <Chip size="small" variant="outlined"
                    label={`From order value ${cr(wf.components.gp_from_order_value)}`} />
              <Chip size="small" variant="outlined"
                    label={`From margin held ${cr(wf.components.gp_from_margin)}`} />
              <Chip size="small" variant="outlined"
                    label={`Procurement ${cr(wf.components.procurement_savings)}`} />
              <Chip size="small" variant="outlined"
                    label={"Labour "
                           + (wf.components.productivity_savings == null
                             ? `UNKNOWN (${n(wf.hours_saved)?.toFixed(0) ?? "—"} h saved)`
                             : cr(wf.components.productivity_savings))} />
            </Stack>
            {wf.productivity_excluded_reason && (
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: "block", mt: 1 }}>
                {wf.productivity_excluded_reason}
              </Typography>
            )}
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="Every pricing model, side by side"
              sub={"Each rate is solved so the model would collect the same "
                        + "target on this customer — so what differs is the shape, "
                        + "not somebody's choice of rate."}
            />
            <DataGrid<MonetizationEvaluation>
              ariaLabel="Every pricing model, side by side"
              rows={result.all_strategies}
              columns={strategyColumns}
              getRowId={(row) => row.fee.strategy}
              pageSize={15}
            />
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader title="Hybrid structures" />
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Structure</TableCell>
                    <TableCell align="right">Annual fee</TableCell>
                    <TableCell align="right">Fixed</TableCell>
                    <TableCell align="right">Variable</TableCell>
                    <TableCell align="right">Customer ROI</TableCell>
                    <TableCell align="right">Metric score</TableCell>
                    <TableCell>Bound</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.hybrids.rows.map((row: MonetizationHybridRow) => (
                    <TableRow key={row.structure}>
                      <TableCell>{row.structure}</TableCell>
                      <TableCell align="right">{inr(row.fee.annual_fee)}</TableCell>
                      <TableCell align="right">
                        {inr(row.fee.fixed_component)}
                      </TableCell>
                      <TableCell align="right">
                        {inr(row.fee.variable_component)}
                      </TableCell>
                      <TableCell align="right">{roiText(row.customer_roi)}</TableCell>
                      <TableCell align="right">
                        {row.weighted_score?.toFixed(2) ?? "—"}
                      </TableCell>
                      <TableCell>{row.fee.bound_applied ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="What to actually offer"
              sub={rec.band.note}
            />
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, md: 6 }}>
                <Stack spacing={1}>
                  <Typography variant="h4">
                    {inr(rec.recommended_annual_fee)} / year
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {rec.structure.metric} · {rec.structure.turnover_band}
                    {" · nothing moves with the customer's book."}
                  </Typography>
                  <Typography variant="body2">{rec.structure.why}</Typography>
                  {/* The metered structure, shown rather than hidden: it
                      collects the same money, and a reader deciding between
                      them needs to see what the second one costs. */}
                  <Typography variant="overline" color="text.secondary">
                    Alternative, same money
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {inr(rec.alternative.platform_fee)}{" + "}
                    {rec.alternative.variable_rate_pct} of connected-book
                    revenue, capped at {inr(rec.alternative.variable_cap)}.
                    {" Scores "}{rec.alternative.weighted_score?.toFixed(2)}
                    {" against "}{rec.structure.weighted_score?.toFixed(2)}.
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {rec.alternative.why_not_chosen}
                  </Typography>
                  <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
                    <Chip size="small" label={`Cost floor ${inr(rec.band.cost_floor)}`} />
                    <Chip size="small"
                          label={`ROI ceiling ${inr(rec.band.roi_ceiling)}`} />
                    <Chip size="small"
                          label={`Target ${inr(rec.band.target_at_capture)}`} />
                  </Stack>
                </Stack>
              </Grid>
              <Grid size={{ xs: 12, md: 6 }}>
                <Stack spacing={1}>
                  <Typography variant="overline" color="text.secondary">
                    First customers (design partner)
                  </Typography>
                  <Typography variant="h5">
                    {inr(rec.design_partner_offer.annual_fee)} / year
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {rec.design_partner_offer.discount_vs_list == null
                      ? "—"
                      : `${pct(rec.design_partner_offer.discount_vs_list, 0)} off list`}
                    {" · in exchange for:"}
                  </Typography>
                  <Box component="ul" sx={{ m: 0, pl: 3 }}>
                    {rec.design_partner_offer.conditions.map((c) => (
                      <Typography component="li" variant="body2" key={c}>{c}</Typography>
                    ))}
                  </Box>
                </Stack>
              </Grid>
            </Grid>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="What this customer's turnover cannot tell us"
              sub={"Two customers of identical turnover can create very "
                   + "different value depending on how much of their enquiry "
                   + "flow they route through PIE. PIE cannot measure that "
                   + "share from synced rows, so the recommendation above "
                   + "prices at the reference adoption below — this is what "
                   + "changes if the real number is different."}
            />
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>PIE adoption (share of enquiries routed)</TableCell>
                    <TableCell align="right">Value created</TableCell>
                    <TableCell align="right">Connected-book revenue</TableCell>
                    <TableCell>Band this falls in</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.adoption_sensitivity.rows.map((row) => (
                    <TableRow key={row.pie_rfq_share}
                              selected={row.is_reference}>
                      <TableCell>
                        {pct(row.pie_rfq_share, 0)}
                        {row.is_reference && (
                          <Box component="span" sx={{ ml: 1 }}>
                            <StatusChip tone="info" label="Assumed" />
                          </Box>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        {cr(row.total_economic_value)}
                      </TableCell>
                      <TableCell align="right">
                        {cr(row.connected_book_revenue)}
                      </TableCell>
                      <TableCell>
                        {/* Same band on every row on purpose: connected-book
                            revenue barely moves with adoption, which is
                            exactly the blind spot this panel exists to show. */}
                        unchanged
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
            <Alert severity="info" sx={{ mt: 2 }}>
              Value moves {result.adoption_sensitivity.value_spread_across_sweep?.toFixed(1)}×
              across this sweep while the billed revenue moves only{" "}
              {result.adoption_sensitivity.connected_book_revenue_spread_across_sweep?.toFixed(2)}×
              — value is about{" "}
              {result.adoption_sensitivity.value_sensitivity_relative_to_revenue?.toFixed(0)}×
              more sensitive to adoption than the number this customer's fee is
              set from. {result.adoption_sensitivity.reading}
            </Alert>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <SectionHeader
              title="PIE's own economics at that price"
              sub={`Evidence grade: ${econ.evidence_grade}. The weakest input, `
                        + "not the average — support, customer success and CAC are "
                        + "the three nobody has measured."}
            />
            <Grid container spacing={2}>
              {([
                ["Annual revenue", inr(econ.annual_revenue)],
                ["Cost to serve", inr(econ.cost.total_cogs)],
                ["Gross margin", pct(econ.gross_margin)],
                ["Contribution", inr(econ.contribution)],
                ["LTV", inr(econ.ltv)],
                ["CAC", inr(econ.cac)],
                ["LTV / CAC", econ.ltv_cac == null ? "—" : `${econ.ltv_cac.toFixed(1)}×`],
                ["CAC payback", econ.cac_payback_months == null
                  ? "UNKNOWN" : `${econ.cac_payback_months.toFixed(0)} mo`],
                ["Revenue per RFQ", inr(econ.revenue_per_rfq)],
                ["Revenue per order", inr(econ.revenue_per_order)],
              ] as [string, string][]).map(([label, value]) => (
                <Grid size={{ xs: 6, sm: 4, md: 2.4 }} key={label}>
                  <Typography variant="overline" color="text.secondary">
                    {label}
                  </Typography>
                  <Typography variant="body1"
                              sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {value}
                  </Typography>
                </Grid>
              ))}
            </Grid>
          </Paper>
        </>
      )}

      {scorecard && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <SectionHeader
            title="Pricing metric scorecard"
            sub={scorecard.evidence_note}
          />
          <DataGrid<MonetizationScorecardRow>
            ariaLabel="Pricing metric scorecard, ranked"
            rows={scorecard.ranking}
            columns={scorecardColumns}
            getRowId={(row) => row.key}
            pageSize={15}
          />
        </Paper>
      )}
    </Stack>
  );
}
