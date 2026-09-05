/** The quote-level details — the organization's own fields on this quote.
 *
 * A quote used to be a customer and a list of lines. Every distributor's
 * quote also carries a handful of details the document needs, and some
 * businesses will not let one go out without them. Which fields exist and
 * which are mandatory is the organization's decision, made in Settings and
 * read here (`api.fieldDefinitions`); this panel renders them, marks the
 * required ones, and says which are still empty.
 *
 * **Saved on commit, one field at a time.** Each field writes through on
 * blur (or on change, for a choice and a date) rather than behind a Save
 * button, which is how every other change on this screen already lands; the
 * server answers with the whole quote, so `missingFields` is always the
 * server's list and never this component's guess. A value the definition
 * refuses — a date that is not one, a choice off the list — is reported in
 * the server's words and the field keeps what was typed so it can be fixed.
 */
import Box from "@mui/material/Box";
import Collapse from "@mui/material/Collapse";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useEffect, useState } from "react";

import { TOUCH } from "../platform/kit";

import type { Quote, QuoteFieldDefinition } from "../types";

export function QuoteDetails({ quote, definitions, readOnly, onSave }: {
  quote: Quote;
  definitions: QuoteFieldDefinition[];
  /** The reader may not change this quote — inputs render, disabled. */
  readOnly: boolean;
  /** Persist one field. Resolves with the server's quote, or rejects with
   *  the server's sentence. */
  onSave: (fields: Record<string, string | number>) => Promise<void>;
}) {
  // What is typed, per key, until it commits. Reset from the quote whenever
  // the server answers, so a colleague's save shows up on the next re-read.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<Record<string, string>>({});
  useEffect(() => {
    const next: Record<string, string> = {};
    for (const d of definitions) {
      const v = quote.fields[d.key];
      next[d.key] = v === undefined || v === null ? "" : String(v);
    }
    setDraft(next);
    setProblem({});
  }, [quote.id, quote.fields, definitions]);

  if (definitions.length === 0) return null;

  const missing = new Set(quote.missingFields);

  const commit = async (d: QuoteFieldDefinition) => {
    const held = quote.fields[d.key];
    const was = held === undefined || held === null ? "" : String(held);
    if ((draft[d.key] ?? "") === was) return;
    try {
      await onSave({ ...quote.fields, [d.key]: draft[d.key] ?? "" });
      setProblem((p) => ({ ...p, [d.key]: "" }));
    } catch (e) {
      setProblem((p) => ({ ...p, [d.key]: (e as Error).message }));
    }
  };

  /* Filled last, and it used to sit first.
   *
   * These are the details the *document* needs — reference, validity, terms —
   * not the work. Fully expanded above the grid they took 203px of a 900px
   * screen, and the first quote line began at 765px: 85% of the viewport was
   * chrome about the quote and 15% was the quote. So it opens only when it is
   * the thing to do — something required is still empty — and is a summary row
   * otherwise, which is the state a finished quote is in.
   *
   * Not hidden: the header states what is filled and what is missing without
   * being opened, and the count is the same one the send gate refuses on. */
  const [open, setOpen] = useState(missing.size > 0);
  const filled = definitions.filter((d) => {
    const v = quote.fields[d.key];
    return v !== undefined && v !== null && String(v) !== "";
  }).length;

  return (
    <Paper variant="outlined" sx={{ px: 1.5, py: open ? 1.5 : 0.75, mb: 2 }}>
      <Box
        component="button"
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        sx={{
          ...TOUCH,
          width: "100%", display: "flex", alignItems: "center", gap: 1,
          background: "none", border: 0, p: 0, cursor: "pointer",
          textAlign: "left", color: "inherit", font: "inherit",
        }}
      >
        <ExpandMoreOutlined
          fontSize="small"
          sx={{
            color: "text.secondary",
            transform: open ? "none" : "rotate(-90deg)",
            transition: "transform .15s",
            "@media (prefers-reduced-motion: reduce)": { transition: "none" },
          }}
        />
        <Typography variant="overline" color="text.secondary" sx={{ lineHeight: 1.3 }}>
          Quote details
        </Typography>
        {missing.size > 0 ? (
          <Typography variant="caption" sx={{ color: "warning.main" }}>
            {missing.size} required before sending
          </Typography>
        ) : (
          <Typography variant="caption" color="text.secondary">
            {filled} of {definitions.length} filled
          </Typography>
        )}
      </Box>
      <Collapse in={open} unmountOnExit>
      <Box sx={{
        display: "grid", gap: 1.5, mt: 1.5,
        gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(3, 1fr)" },
      }}>
        {definitions.map((d) => {
          const value = draft[d.key] ?? "";
          const isMissing = missing.has(d.label);
          const common = {
            size: "small" as const,
            label: d.required ? `${d.label} *` : d.label,
            value,
            disabled: readOnly,
            error: Boolean(problem[d.key]) || isMissing,
            helperText: problem[d.key] || (isMissing ? "Required before sending" : undefined),
            slotProps: { inputLabel: { shrink: true } },
            sx: d.kind === "MULTILINE" ? { gridColumn: { md: "1 / -1" } } : undefined,
          };
          if (d.kind === "CHOICE") {
            return (
              <TextField key={d.key} {...common} select
                onChange={(e) => {
                  setDraft((x) => ({ ...x, [d.key]: e.target.value }));
                  void onSave({ ...quote.fields, [d.key]: e.target.value })
                    .catch((err) => setProblem((p) => ({ ...p, [d.key]: (err as Error).message })));
                }}>
                <MenuItem value="">—</MenuItem>
                {d.choices.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
              </TextField>
            );
          }
          return (
            <TextField key={d.key} {...common}
              type={d.kind === "DATE" ? "date" : d.kind === "NUMBER" ? "number" : "text"}
              multiline={d.kind === "MULTILINE"}
              minRows={d.kind === "MULTILINE" ? 2 : undefined}
              onChange={(e) => setDraft((x) => ({ ...x, [d.key]: e.target.value }))}
              onBlur={() => void commit(d)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && d.kind !== "MULTILINE") {
                  (e.target as HTMLInputElement).blur();
                }
              }}
            />
          );
        })}
      </Box>
      </Collapse>
    </Paper>
  );
}
