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
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useEffect, useState } from "react";

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

  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
      <Typography variant="overline" color="text.secondary"
                  sx={{ display: "block", lineHeight: 1.3, mb: 1 }}>
        Quote details
        {missing.size > 0 && (
          <Box component="span" sx={{ color: "warning.main", ml: 1 }}>
            · {missing.size} required
          </Box>
        )}
      </Typography>
      <Box sx={{
        display: "grid", gap: 1.5,
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
    </Paper>
  );
}
