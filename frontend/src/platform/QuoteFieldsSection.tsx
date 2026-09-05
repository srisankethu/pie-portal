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
 *
 * **A list of rows rather than a grid, deliberately.** `docs/ui-standards.md`
 * §3 sends a table to `DataGrid` the moment its row count is set by the size
 * of the business; this one is set by the shape of one quote form — five
 * built-ins and a handful the organization adds — and every row is a set of
 * live controls in an order somebody rearranges. That is a form, and it stays
 * one.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArrowDownwardOutlined from "@mui/icons-material/ArrowDownwardOutlined";
import ArrowUpwardOutlined from "@mui/icons-material/ArrowUpwardOutlined";
import DeleteOutlineOutlined from "@mui/icons-material/DeleteOutlineOutlined";
import { useCallback, useEffect, useState } from "react";

import { papi } from "./api";
import { EmptyState, ErrorState, LoadingState, SectionHeader, StatusChip, TOUCH } from "./kit";
import { Bp } from "./ui";
import type { QuoteFieldSpec } from "./types";

const KIND_LABEL: Record<QuoteFieldSpec["kind"], string> = {
  TEXT: "Text", MULTILINE: "Paragraph", NUMBER: "Number", DATE: "Date", CHOICE: "Choice",
};

/** What the server will refuse, said on the row that will cause it.
 *
 *  `quote_fields.replace_definitions` raises on a choice field with no
 *  options, naming the field. Repeating the rule here is a hint, not a gate:
 *  Save stays enabled and the server stays the authority, so a rule that
 *  changes there leaves a stale sentence rather than a form nobody can
 *  submit. */
const optionsMissing = (f: QuoteFieldSpec) =>
  f.kind === "CHOICE" && f.choices.length === 0;

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
      <SectionHeader
        level="widget"
        title="Quote fields"
        tip="What every quote carries besides its lines. A required field stops the send until it is answered; the list shows which quotes are waiting on one."
        sub={<>
          The details the Quote Builder asks for on every quote, in this order.
          Switch on <b>required</b> to stop a quote being sent without it. Owner only.
        </>}
        // Said once, at the top, rather than left for somebody to infer from
        // every control being greyed out. A manager reads this list; only an
        // owner changes it.
        actions={canManage ? undefined : (
          <StatusChip
            label="Read-only"
            tone="neutral"
            tip="Managers see the list an owner set. Changing it is an owner's call — a manager who could add a mandatory field could also remove one."
          />
        )}
      />

      {/* Two failures, one `error` state, because there is one request of each
          kind: the load runs on mount, so an error with no list is the load's
          and an error beside a list is the save's. They need different offers
          — "Try again" on a failed *save* used to re-read the server's copy,
          which silently discards the edits that had just failed to save. The
          save's retry is the save, and it is rendered below with the rest of
          the feedback. */}
      {fields === null && error && <ErrorState error={error} onRetry={() => void load()} />}
      {fields === null && !error && <LoadingState rows={3} />}

      {fields && (
        <Stack spacing={1.5}>
          {fields.length === 0 ? (
            <EmptyState
              title="Nothing asked for beyond the lines"
              reason={`A quote here is a customer and its lines, and this organization asks for nothing else — not even a validity date. ${
                canManage ? "Add a field to change that." : "An owner can add one."}`}
            />
          ) : (
            // A divider between rows rather than spacing alone: a row wraps on
            // a laptop once a choice field is on it, and without a rule
            // between them the wrapped half reads as belonging to the row
            // below — which is the row whose order buttons sit beside it.
            <Stack divider={<Divider />}>
              {fields.map((f, i) => (
                <Stack key={f.key ?? `new-${i}`} direction="row" spacing={1} useFlexGap
                       sx={{ flexWrap: "wrap", alignItems: "center", py: 1 }}>
                  <TextField
                    size="small" label="Label" value={f.label} disabled={!canManage}
                    placeholder="Machine model"
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
                  {/* Why the kind above is locked and why removing this one
                      only hides it. Both were true before and neither was on
                      screen — a disabled control with no reason beside it
                      reads as a fault. */}
                  {f.builtin && (
                    <StatusChip
                      label="Built-in" tone="neutral" dense
                      tip="A field the platform understands by name, so its kind is fixed. Its label, its place in the order and whether it is required are yours."
                    />
                  )}
                  {f.kind === "CHOICE" && (
                    <TextField
                      size="small" label="Options, comma-separated"
                      value={f.choices.join(", ")} disabled={!canManage}
                      error={optionsMissing(f)}
                      helperText={optionsMissing(f)
                        ? "At least one — a choice with no options cannot be answered."
                        : undefined}
                      onChange={(e) => update(i, {
                        choices: e.target.value.split(",").map((c) => c.trim()).filter(Boolean),
                      })}
                      sx={{ minWidth: 220, flex: 1 }}
                    />
                  )}
                  <FormControlLabel
                    sx={{ ...TOUCH, mr: 0 }}
                    control={
                      <Switch
                        size="small" checked={f.required} disabled={!canManage}
                        onChange={(e) => update(i, { required: e.target.checked })}
                        // Ten rows of "Required" tell a screen reader which
                        // control this is and never which field it belongs
                        // to. The visible word leads the name, so what is
                        // read and what is seen still match.
                        slotProps={{ input: {
                          "aria-label": `Required — ${f.label || "new field"}`,
                        } }}
                      />
                    }
                    label={<Typography variant="body2">Required</Typography>}
                  />
                  {canManage && (
                    // Pushed to the end of the row so the actions line up in a
                    // column of their own, whatever width the fields take.
                    <Stack direction="row" sx={{ ml: "auto" }}>
                      <IconButton size="small" sx={TOUCH} aria-label={`Move ${f.label || "field"} up`}
                                  disabled={i === 0} onClick={() => move(i, -1)}>
                        <ArrowUpwardOutlined fontSize="small" />
                      </IconButton>
                      <IconButton size="small" sx={TOUCH} aria-label={`Move ${f.label || "field"} down`}
                                  disabled={i === fields.length - 1} onClick={() => move(i, 1)}>
                        <ArrowDownwardOutlined fontSize="small" />
                      </IconButton>
                      {/* MUI's tooltip rather than the `title` attribute it
                          replaces: the browser's own appears after about a
                          second, never on focus and not at all on a tablet,
                          which is where this list is read. */}
                      <Tooltip title={f.builtin
                        ? "A built-in field can be hidden from the builder; drafts keep any value under it."
                        : "Hide this field; drafts keep any value under it."}>
                        <IconButton
                          size="small" sx={TOUCH}
                          aria-label={`Remove ${f.label || "field"}`}
                          onClick={() => remove(i)}
                        >
                          <DeleteOutlineOutlined fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  )}
                </Stack>
              ))}
            </Stack>
          )}

          {canManage && (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Button variant="outlined" size="small" sx={TOUCH} onClick={add}>
                Add a field
              </Button>
              {/* The label stays put while it saves. A word that swaps to
                  "Saving…" is a button whose accessible name moves while it is
                  being pressed, and §7 asks for MUI's own loading components
                  rather than a second way of saying the same thing. */}
              <Button variant="contained" size="small" sx={TOUCH} onClick={() => void save()}
                      loading={busy} loadingPosition="start" disabled={!dirty}>
                Save fields
              </Button>
            </Stack>
          )}

          {/* One place for what just happened. "Saved — 2 of 6 required" used
              to sit beside the buttons while "Not saved yet" sat under them,
              so an edit after a save showed both at once saying opposite
              things. */}
          {error && (
            <ErrorState title="Not saved" error={error} busy={busy}
                        onRetry={() => void save()} />
          )}
          {canManage && dirty && (
            <Alert severity="info">
              Not saved yet. A field left out of the list is hidden from the builder, not deleted.
            </Alert>
          )}
          {canManage && !dirty && note && <Alert severity="success">{note}</Alert>}
        </Stack>
      )}
    </Bp>
  );
}
