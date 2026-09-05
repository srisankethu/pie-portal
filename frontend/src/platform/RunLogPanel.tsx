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
//
// **How much of the log is on screen is a fact, and it was being overstated.**
// The endpoint pages at 2,000 lines and a finished run is polled exactly once,
// so an hour-long pull left this panel holding the head of its log under a
// heading reading "5,431 lines" — the count of what the server holds, over a
// box holding the first two thousand of them. Which of the two it is showing is
// now a `StatusChip`, for the reason `SkippedRowsPanel` gives at length: a
// truncation a reader cannot see is read as the whole thing by the third time
// of looking at it, and "ALL" and "SOME" must not look the same.
//
// **An empty log is a claim, so it says what is missing rather than nothing.**
// The server sends its own reason for a log with no lines in it — a run from
// before the log was stored with it keeps none — and that sentence is what the
// empty state shows. The one thing the server cannot know is this panel's
// filter: with "Problems only" on, the total it counts is the filtered one, so
// its "this run kept no log" would be said about a run that simply had nothing
// to warn about. Under the filter the panel says what it actually knows, which
// is that a clean run and an empty one are indistinguishable from here.

import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import { count } from "../money";
import { tokens } from "../theme";
import { papi } from "./api";
import { saveBlob } from "./download";
import { EmptyState, ErrorState, SectionHeader, StatusChip, TOUCH } from "./kit";
import { Bp } from "./ui";
import type { SyncLogLine } from "./types";

/** How often a live run is polled. Slow enough to be cheap, fast enough that a
 *  phase change shows up while somebody is still looking at the screen. */
const POLL_MS = 4000;

/** Colour by severity, from the theme rather than from literals — a log where
 *  the errors do not stand out is a log people scroll past.
 *
 *  Never the only cue: every line above INFO also writes its level out beside
 *  the message, so the distinction survives greyscale, forced colours and a
 *  reader who cannot separate red from grey. That is what §6 asks of anything
 *  carrying meaning, and it is why an ordinary line takes the body ink rather
 *  than a muted one — with INFO muted too, the levels differed only by hue. */
function toneOf(level: string): string {
  if (level === "ERROR" || level === "CRITICAL") return "error.main";
  if (level === "WARNING") return "warning.main";
  return "text.primary";
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

  const held = lines.length;
  /** Whether the box below holds the run's whole log or the head of it. */
  const complete = held >= total;
  /** Two different failures, and they must not be drawn as one. Nothing on
   *  screen means nobody managed to look; lines already held means the
   *  follow-along stopped while the run carried on. Drawing the first as an
   *  empty state would say "there is nothing to see" about something unread,
   *  which is the distinction `kit.ErrorState` exists to keep. */
  const failedOutright = Boolean(error) && held === 0;
  const empty = !loading && !failedOutright && held === 0;

  return (
    <>
      <SectionHeader
        level="widget"
        title="What this sync did"
        tip="Everything this pull recorded as it ran — each phase it entered, every warning, and the full traceback if it failed. Kept with the run, so a sync that died an hour in can still be read afterwards."
        actions={
          <>
            {running && (
              <StatusChip
                label="Still running"
                tone="info"
                tip="The run is alive and this panel is following it — new lines arrive every few seconds without the page being reloaded."
              />
            )}
            {/* Only once something has come back. Before that `total` is 0 and
                a chip is a count stated before it is known. */}
            {total > 0 && (
              <StatusChip
                label={complete ? `ALL ${count(total)}` : `${count(held)} OF ${count(total)}`}
                tone={complete ? "good" : "warn"}
                // The count is the filtered one when the switch is on, and a
                // chip that does not say so is the same overstatement in a
                // smaller font — the heading it replaced did say "with a
                // problem".
                tip={complete
                  ? `Every line this run recorded${problemsOnly ? " at warning level or above" : ""} is in the panel below.`
                  : `The panel below holds the start of the log${problemsOnly ? ", as filtered" : ""}; the server holds more than one page of it. The download is the whole log, unfiltered.`}
              />
            )}
          </>
        }
      />

      <Stack direction="row" spacing={1.5}
             sx={{ mb: 1.5, alignItems: "center", flexWrap: "wrap" }}>
        <FormControlLabel
          // `TOUCH` because a switch is 20px of hit target and this screen is
          // read on a tablet next to a machine — see kit.TOUCH.
          sx={{ ...TOUCH, mr: 0 }}
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

      {/* A poll that failed with lines already on screen. Said out loud rather
          than left to the fact that nothing new arrives: a log that has quietly
          stopped updating looks exactly like a quiet run. */}
      {error && !failedOutright && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          <AlertTitle>The log has stopped updating</AlertTitle>
          {error}
          <Typography variant="body2" sx={{ mt: 1 }}>
            What is below is what had already arrived.
          </Typography>
        </Alert>
      )}

      {failedOutright ? (
        <ErrorState title="The log did not load" error={error} />
      ) : empty ? (
        <EmptyState
          // "has no log" is a verdict, and it is the wrong one about a run that
          // started twenty seconds ago — the server's own reason below says
          // "not yet", and a title contradicting it is the benign default in
          // the other direction.
          title={problemsOnly
            ? "No warnings or errors recorded"
            : running ? "No log lines yet" : "This run has no log"}
          reason={problemsOnly
            ? "Nothing at warning level or above came back for this run. That reads as a clean pull, and this filter cannot tell a clean pull from one that kept no log at all — turn it off before concluding either."
            : note ?? "The server returned no lines and no reason for it, which is itself worth reporting."}
          action={problemsOnly ? (
            <Button variant="outlined" size="small"
                    onClick={() => setProblemsOnly(false)}>
              Show every line
            </Button>
          ) : undefined}
        />
      ) : (
        <Bp sx={{ p: 0 }}>
          <Box
            component="pre"
            // Focusable and named, because a scrollable region a mouse can
            // reach and a keyboard cannot is unreachable for half its readers.
            role="region"
            aria-label="Sync log"
            aria-busy={loading}
            tabIndex={0}
            sx={{
              m: 0, p: 1.5,
              maxHeight: { xs: 320, md: 460 },
              overflow: "auto",
              // The theme's own mono stack rather than one written out here —
              // a literal is a value that will not follow (§11).
              fontFamily: tokens.fontMono,
              typography: "body2",
              // Long lines — a traceback frame, a Zoho URL — wrap rather than
              // pushing the page sideways.
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {loading && held === 0 && (
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <CircularProgress size={16} />
                <Typography variant="body2" color="text.secondary">
                  Reading the log…
                </Typography>
              </Stack>
            )}
            {lines.map((line) => (
              <Box key={line.seq} sx={{ color: toneOf(line.level) }}>
                {/* Secondary, not disabled: the clock is read against the
                    server's own log and at 38% ink it sat under the contrast
                    floor small text needs. It still recedes, because the
                    message beside it now takes the body ink. */}
                <Box component="span" sx={{ color: "text.secondary" }}>
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
      )}
    </>
  );
}
