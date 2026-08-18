// What one sync actually did — the log the run kept of itself.
//
// The screen used to say, of a job that died after an hour, "See the server
// log." The people who read that sentence have a browser and no shell, and on a
// hosted deployment there may be no log to see: this product's own migrations
// were switching application logging off at every startup. So the run keeps its
// own log now, and this is where it is read.
//
// A log is a stream of text, not a table of business entities, so it is a
// scrollable block rather than a `DataGrid` — `docs/ui-standards.md` §3 draws
// that line at "row count set by the size of the business", and this one is set
// by how chatty a pull was. The download is fetched from the server for the
// same reason the skipped-rows CSV is: a file assembled from what is on screen
// is a page calling itself the record.

import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import { papi } from "./api";
import { saveBlob } from "./download";
import { Bp, Labelled } from "./ui";
import type { SyncLogLine } from "./types";

/** How often a live run is polled. Slow enough to be cheap, fast enough that a
 *  phase change shows up while somebody is still looking at the screen. */
const POLL_MS = 4000;

/** Colour by severity, from the theme rather than from literals — a log where
 *  the errors do not stand out is a log people scroll past. */
function toneOf(level: string): string {
  if (level === "ERROR" || level === "CRITICAL") return "error.main";
  if (level === "WARNING") return "warning.main";
  return "text.secondary";
}

/** The clock part of an ISO stamp. The date is on the run above; repeating it
 *  on every line of an hour-long pull is noise that pushes the message out. */
function clockOf(at: string | null): string {
  if (!at) return "";
  const t = at.slice(11, 19);
  return t || at;
}

export function RunLogPanel({ token, runId, running }: {
  token: string;
  runId: string;
  /** Whether the run is still going, from the sync state the screen already
   *  polls. Drives the follow-along, so a finished run is fetched once. */
  running: boolean;
}) {
  const [lines, setLines] = useState<SyncLogLine[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Where the last poll got to. A ref rather than state: it changes on every
  // poll and nothing renders from it, so it must not drive a re-render.
  const cursor = useRef(-1);

  // A change of run — or of filter — is a different log, not more of this one.
  useEffect(() => {
    cursor.current = -1;
    setLines([]);
    setLoading(true);
  }, [runId, problemsOnly]);

  useEffect(() => {
    let live = true;

    const poll = async () => {
      try {
        const page = await papi.syncRunLog(token, runId, {
          afterSeq: cursor.current, problemsOnly,
        });
        if (!live) return;
        cursor.current = page.next_seq;
        // Appended, not replaced: the panel holds the whole story of the run
        // while the server only ever sends what is new.
        if (page.lines.length) setLines((held) => [...held, ...page.lines]);
        setTotal(page.total);
        setNote(page.note);
        setError(null);
      } catch (e) {
        if (live) setError((e as Error).message);
      } finally {
        if (live) setLoading(false);
      }
    };

    void poll();
    if (!running) return () => { live = false; };
    const timer = setInterval(poll, POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, [token, runId, running, problemsOnly]);

  const downloadText = useCallback(async () => {
    setSaving(true);
    try {
      const { blob, filename } = await papi.syncRunLogText(token, runId);
      saveBlob(blob, filename);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [token, runId]);

  return (
    <>
      <div className="section-h">
        <Labelled tip="Everything this pull recorded as it ran — each phase it entered, every warning, and the full traceback if it failed. Kept with the run, so a sync that died an hour in can still be read afterwards.">
          What this sync did
        </Labelled>
        <span className="fsrc">
          {" "}
          {total > 0
            ? `${total} line${total === 1 ? "" : "s"}${problemsOnly ? " with a problem" : ""}`
            : ""}
          {running ? " · still running" : ""}
        </span>
      </div>

      <Stack direction="row" spacing={1.5}
             sx={{ mb: 1, alignItems: "center", flexWrap: "wrap" }}>
        <FormControlLabel
          control={<Switch size="small" checked={problemsOnly}
                           onChange={(e) => setProblemsOnly(e.target.checked)} />}
          label={<Typography variant="body2">Problems only</Typography>}
        />
        <Button variant="outlined" size="small" onClick={downloadText}
                disabled={saving}>
          {saving ? "Preparing…" : "Download log"}
        </Button>
        <Typography variant="body2" color="text.secondary">
          The whole log, not the part shown here.
        </Typography>
      </Stack>

      {error && <Box sx={{ mb: 1 }}><Alert severity="error">{error}</Alert></Box>}
      {note && <Box sx={{ mb: 1 }}><Alert severity="info">{note}</Alert></Box>}

      <Bp style={{ padding: 0 }}>
        <Box
          component="pre"
          aria-label="Sync log"
          sx={{
            m: 0, p: 1.5, maxHeight: 420, overflow: "auto",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12.5, lineHeight: 1.55,
            // Long lines — a traceback frame, a Zoho URL — wrap rather than
            // pushing the page sideways.
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}
        >
          {loading && lines.length === 0 && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <CircularProgress size={16} />
              <Typography variant="body2" color="text.secondary">
                Reading the log…
              </Typography>
            </Box>
          )}
          {lines.map((line) => (
            <Box key={line.seq} component="div" sx={{ color: toneOf(line.level) }}>
              <Box component="span" sx={{ color: "text.disabled" }}>
                {clockOf(line.at)}{" "}
              </Box>
              {line.level !== "INFO" && (
                <Box component="span" sx={{ fontWeight: 600 }}>{line.level} </Box>
              )}
              {line.message}
            </Box>
          ))}
        </Box>
      </Bp>
    </>
  );
}
