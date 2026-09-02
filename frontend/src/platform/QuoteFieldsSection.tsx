/** Settings: the quote-level fields this organization asks for, and which
 *  are mandatory.
 *
 * A quote used to be a customer and a list of lines. This is where a
 * business says what else every quote must carry — the customer's reference,
 * a validity date, an incoterm — and what it may carry. The built-in handful
 * every organization starts with can be made mandatory or hidden but keep
 * their key and kind (the platform reads a date as a date); anything else is
 * the organization's own, with a kind, options for a choice, and a required
 * switch. Managers see the list; an owner edits it, and the whole list is
 * saved in one `PUT` so the order on screen is the order in the builder.
 *
 * Removing a custom field hides it rather than deleting it: a draft that
 * already holds a value keeps the value and the label it was given.
 */
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import ArrowDownwardOutlined from "@mui/icons-material/ArrowDownwardOutlined";
import ArrowUpwardOutlined from "@mui/icons-material/ArrowUpwardOutlined";
import DeleteOutlineOutlined from "@mui/icons-material/DeleteOutlineOutlined";
import { useCallback, useEffect, useState } from "react";

import { papi } from "./api";
import { ErrorState, LoadingState, TOUCH } from "./kit";
import { Bp, Labelled } from "./ui";
import type { QuoteFieldSpec } from "./types";

const KIND_LABEL: Record<QuoteFieldSpec["kind"], string> = {
  TEXT: "Text", MULTILINE: "Paragraph", NUMBER: "Number", DATE: "Date", CHOICE: "Choice",
};

export function QuoteFieldsSection({ token, canManage }: { token: string; canManage: boolean }) {
  const [fields, setFields] = useState<QuoteFieldSpec[] | null>(null);
  const [saved, setSaved] = useState<QuoteFieldSpec[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await papi.getQuoteFields(token);
      setFields(r.fields);
      setSaved(r.fields);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const dirty = fields !== null && JSON.stringify(fields) !== JSON.stringify(saved);

  const update = (i: number, patch: Partial<QuoteFieldSpec>) =>
    setFields((f) => f && f.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const move = (i: number, by: number) =>
    setFields((f) => {
      if (!f) return f;
      const j = i + by;
      if (j < 0 || j >= f.length) return f;
      const next = [...f];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const remove = (i: number) => setFields((f) => f && f.filter((_, j) => j !== i));
  const add = () =>
    setFields((f) => [...(f ?? []), { label: "", kind: "TEXT", required: false, choices: [] }]);

  const save = async () => {
    if (!fields) return;
    setBusy(true);
    setNote(null);
    try {
      const r = await papi.updateQuoteFields(token, fields);
      setFields(r.fields);
      setSaved(r.fields);
      setError(null);
      setNote(`Saved — ${r.fields.filter((f) => f.required).length} of ${r.fields.length} field(s) required on every quote.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Bp className="st-section">
      <h3>
        <Labelled tip="What every quote carries besides its lines. A required field stops the send until it is answered; the list shows which quotes are waiting on one.">
          Quote fields
        </Labelled>
      </h3>
      <p className="st-help">
        The details the Quote Builder asks for on every quote, in this order.
        Switch on <b>required</b> to stop a quote being sent without it. Owner only.
      </p>

      {error && <ErrorState error={error} onRetry={() => void load()} />}
      {fields === null && !error && <LoadingState rows={3} />}

      {fields && (
        <Stack spacing={1}>
          {fields.map((f, i) => (
            <Stack key={f.key ?? `new-${i}`} direction="row" spacing={1} useFlexGap
                   sx={{ flexWrap: "wrap", alignItems: "center" }}>
              <TextField
                size="small" label="Label" value={f.label} disabled={!canManage}
                onChange={(e) => update(i, { label: e.target.value })}
                sx={{ minWidth: 200, flex: 1 }}
              />
              <TextField
                select size="small" label="Kind" value={f.kind}
                // A built-in keeps its kind: the platform reads validity as a
                // date, and a business cannot turn it into a paragraph.
                disabled={!canManage || Boolean(f.builtin)}
                onChange={(e) => update(i, { kind: e.target.value as QuoteFieldSpec["kind"] })}
                sx={{ width: 140 }}
              >
                {(Object.keys(KIND_LABEL) as QuoteFieldSpec["kind"][]).map((k) => (
                  <MenuItem key={k} value={k}>{KIND_LABEL[k]}</MenuItem>
                ))}
              </TextField>
              {f.kind === "CHOICE" && (
                <TextField
                  size="small" label="Options, comma-separated"
                  value={f.choices.join(", ")} disabled={!canManage}
                  onChange={(e) => update(i, {
                    choices: e.target.value.split(",").map((c) => c.trim()).filter(Boolean),
                  })}
                  sx={{ minWidth: 220, flex: 1 }}
                />
              )}
              <Box component="label" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <Switch size="small" checked={f.required} disabled={!canManage}
                        onChange={(e) => update(i, { required: e.target.checked })} />
                <Typography variant="body2">Required</Typography>
              </Box>
              {canManage && (
                <Stack direction="row">
                  <IconButton size="small" sx={TOUCH} aria-label={`Move ${f.label || "field"} up`}
                              disabled={i === 0} onClick={() => move(i, -1)}>
                    <ArrowUpwardOutlined fontSize="small" />
                  </IconButton>
                  <IconButton size="small" sx={TOUCH} aria-label={`Move ${f.label || "field"} down`}
                              disabled={i === fields.length - 1} onClick={() => move(i, 1)}>
                    <ArrowDownwardOutlined fontSize="small" />
                  </IconButton>
                  <IconButton
                    size="small" sx={TOUCH}
                    aria-label={`Remove ${f.label || "field"}`}
                    title={f.builtin
                      ? "A built-in field can be hidden from the builder; drafts keep any value under it."
                      : "Hide this field; drafts keep any value under it."}
                    onClick={() => remove(i)}
                  >
                    <DeleteOutlineOutlined fontSize="small" />
                  </IconButton>
                </Stack>
              )}
            </Stack>
          ))}
          {canManage && (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", pt: 1 }}>
              <Button variant="outlined" size="small" sx={TOUCH} onClick={add}>
                Add a field
              </Button>
              <Button variant="contained" size="small" sx={TOUCH} onClick={() => void save()}
                      disabled={busy || !dirty}>
                {busy ? "Saving…" : "Save fields"}
              </Button>
              {note && <Typography variant="body2" color="text.secondary">{note}</Typography>}
            </Stack>
          )}
          {canManage && dirty && (
            <Alert severity="info" sx={{ mt: 1 }}>
              Not saved yet. A field left out of the list is hidden from the builder, not deleted.
            </Alert>
          )}
        </Stack>
      )}
    </Bp>
  );
}
