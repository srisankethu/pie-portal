/** Today: the morning's work, one item at a time.
 *
 * The screen this replaces was a briefing — eight tiles in two bands, each
 * saying "Work through these →" into a list, into a card, into a detail page,
 * and then an action. Four clicks before anything happened, and the place you
 * had reached in the list was lost every time you came back. The queue could be
 * read and could not be *worked*.
 *
 * So: the queue is the screen, the first item is already open, and acting is
 * one key. The rail on the left is where you are in the morning; the panel is
 * the item, with its arithmetic, the model's reading of that arithmetic, what
 * it will not answer, and the actions named as the verbs they are. `J` and `K`
 * move, `Enter` takes the first action, and the queue advances on its own.
 *
 * **What the prototype promised and this does not.** The design offers undo on
 * every action. Undo exists here exactly where the server can take something
 * back: a decision can be reopened, so it is offered one. An approval decision
 * and a recorded quote outcome are written to an append-only trail on purpose —
 * somebody signed them — and there is no un-decide. Rather than show a button
 * that would fail, those say what they are: recorded, with a link to where the
 * record can be changed by making another one. A promise of reversibility that
 * the server will not honour is worse than the honest sentence.
 *
 * **Three sources, four kinds.** The design names approvals, decisions, chases
 * and outcome questions. A chase is not a fourth fetch: money past its due date
 * arrives as a decision of type `CASH_RECEIVABLE_OVERDUE`, ranked by the same
 * server that ranks the rest. `queue.ts` holds the merge and the order.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";

import { intelligence } from "../../intelligence";
import { formatDate, todayISO } from "../../when";
import {
  EmptyState, ErrorState, FactTable, InlineLink, LoadingState, Meta, MetricCard,
  PanelMark, SectionHeader, StatusChip, TOUCH,
} from "../kit";
import { DEFAULT_LOSS_CHOICES } from "../RecordOutcomeDialog";
import { papi } from "../api";
import { PATH } from "../route";
import type { DecisionDetail, PlatformSession } from "../types";
import type { QuoteLossReason } from "../../types";
import { useInsight } from "../viz/useInsight";
import { buildQueue, OUTCOMES_PER_MORNING, type QueueAction, type QueueItem } from "./queue";

/** What happened to an item this morning, and whether it can be taken back.
 *
 *  The item is held here rather than looked up again, because answering one
 *  takes it out of the next fetch: an approved request is no longer open, and a
 *  quote with an outcome is no longer unanswered. Keeping only the id would
 *  mean the done list emptied itself as the answers landed. */
interface Settled {
  item: QueueItem;
  outcome: string;
  undo?: () => void;
}

export default function TodayScreen({
  session, decisions, loading, error, onReload, onDecisionAction, onUndoDecision, flash,
}: {
  session: PlatformSession;
  /** The open decisions, with detail, in the server's ranked order. Loaded by
   *  the shell because three screens read the same list; passed rather than
   *  re-fetched so this screen cannot disagree with the badge above it. */
  decisions: DecisionDetail[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  /** Record a decision action. The shell owns the vocabulary and the modal
   *  where an action needs a reason written down. */
  onDecisionAction: (id: string, actionKey: string) => void;
  /** Put a decision back in the queue — the one action here the server can
   *  genuinely reverse. */
  onUndoDecision: (id: string) => void;
  flash: (message: string, undo?: () => void) => void;
}) {
  const approvals = useInsight(
    "today-approvals", () => papi.listApprovals(session.token), [session.token]);
  // A page rather than the pile: three are asked this morning, and a handful
  // more are held so answering one does not leave the section empty.
  const unanswered = useInsight(
    "today-unanswered", () => papi.unrecordedQuotes(session.token, 12), [session.token]);

  const [settled, setSettled] = useState<Record<string, Settled>>({});
  const [cursor, setCursor] = useState(0);
  const [asking, setAsking] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const items = useMemo(() => buildQueue({
    approvals: approvals.data?.requests ?? [],
    decisions,
    unanswered: unanswered.data?.quotes ?? [],
  }), [approvals.data, decisions, unanswered.data]);

  const openItems = items.filter((i) => !settled[i.id]);
  const done = Object.values(settled);
  const current = openItems[Math.min(cursor, openItems.length - 1)] ?? null;
  const total = openItems.length + done.length;

  const move = useCallback((by: number) => {
    setAsking(null);
    setCursor((c) => Math.max(0, Math.min(openItems.length - 1, c + by)));
  }, [openItems.length]);

  /** Mark an item answered and land on the next one.
   *
   *  The cursor is left where it is rather than advanced: the item it pointed
   *  at has just left the open list, so the same index already *is* the next
   *  item. It is clamped on render for the one case that is not true of — the
   *  last item in the queue. */
  const settle = useCallback((item: QueueItem, outcome: string, undo?: () => void) => {
    setSettled((s) => ({ ...s, [item.id]: { item, outcome, undo } }));
    setAsking(null);
    flash(`${item.short} — ${outcome}`, undo);
  }, [flash]);

  const recordOutcome = useCallback(async (
    item: QueueItem, status: "WON" | "LOST", reason?: QuoteLossReason,
  ) => {
    const [, ref] = item.id.split(/:(.+)/);
    setBusy(true);
    try {
      await intelligence.documentOutcome(session.token, ref, status, "", undefined, reason);
      unanswered.reload();
      settle(item, status === "WON" ? "won" : `lost · ${reason ?? ""}`.trim());
    } catch (e) {
      // The server's own sentence. A LOST with no reason comes back naming
      // every reason a person may choose, and paraphrasing that throws away the
      // only part that says what to do next.
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [session.token, unanswered, settle, flash]);

  const act = useCallback(async (item: QueueItem, action: QueueAction) => {
    if (busy) return;
    const [, id] = item.id.split(/:(.+)/);

    if (item.kind === "decision") {
      // The shell records it — including the modal where the action has to say
      // why — so the note rule lives in one place. Undo is the server's
      // `reopen`, offered because it exists.
      onDecisionAction(id, action.key);
      settle(item, action.label.toLowerCase(), () => onUndoDecision(id));
      return;
    }

    if (item.kind === "approval") {
      setBusy(true);
      try {
        await papi.decideApproval(session.token, id, action.key);
        approvals.reload();
        settle(item, action.label.toLowerCase());
      } catch (e) {
        // The server's own sentence: a below-cost approval refused for want of
        // a rationale says so, and it names the field.
        flash((e as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }

    // An outcome. `Still open` is not a status the server takes — it is this
    // reader saying "not yet", which is a skip with a sentence on it.
    if (action.key === "STILL_OPEN") {
      settle(item, "still open");
      return;
    }
    if (action.key === "LOST") {
      setAsking(item.id);
      return;
    }
    await recordOutcome(item, "WON");
  }, [busy, session.token, approvals, onDecisionAction, onUndoDecision, settle,
      recordOutcome, flash]);

  // J/K/Enter/U, and only while this screen is the one being read. A key that
  // acts is not bound while focus is in a field, and `Enter` takes the first
  // action because that is the one the item is named after — *Approve*,
  // *Accept and act*, *Won*.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "j") { e.preventDefault(); move(1); }
      else if (k === "k") { e.preventDefault(); move(-1); }
      else if (k === "escape") setAsking(null);
      else if (e.key === "Enter" && current && current.actions.length) {
        e.preventDefault();
        void act(current, current.actions[0]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, act, current]);

  const day = formatDate(todayISO());

  if (error) {
    return <ErrorState
      title="This morning's queue did not load"
      error={<>{error} Nothing you did is lost — this is the read, not your books.</>}
      onRetry={onReload} busy={loading} />;
  }
  if (loading || approvals.loading || unanswered.loading) {
    return <LoadingState rows={4} label="Reading this morning's books…" />;
  }

  const stillUnanswered = (unanswered.data?.count ?? 0) - doneOutcomes(settled);

  return (
    <>
      <SectionHeader
        title={day}
        sub={current
          ? `${openItems.length} to work through · ${done.length} done`
          : `All ${total} dealt with. The queue rebuilds after tonight's sync.`}
        actions={
          <Stack spacing={0.5} sx={{ minWidth: 200 }}>
            <LinearProgress
              variant="determinate"
              value={total ? (done.length / total) * 100 : 0}
              aria-label="Progress through this morning"
            />
            <Meta>
              <b>J</b> / <b>K</b> move · <b>↵</b> act
            </Meta>
          </Stack>
        }
      />

      {items.length === 0 ? (
        <EmptyState
          title="Nothing needs you this morning"
          reason={unanswered.data?.empty_reason
            ?? "No approval is waiting, no decision is open, and every quote the "
             + "ERP raised has an outcome recorded against it."}
          action={<Button component={RouterLink} to={PATH.evidence}>Look at the evidence instead</Button>}
        />
      ) : (
        <Box sx={{
          display: "grid", gap: 2, alignItems: "start",
          gridTemplateColumns: { xs: "1fr", md: "320px minmax(0, 1fr)" },
        }}>
          {/* On a phone the queue is below the item it is a queue of. The rail
              is orientation — where you are in the morning — and five rows of
              it above the fold would push the thing you are meant to act on off
              the screen, which is the fault this console exists to fix. */}
          <Box sx={{ position: { md: "sticky" }, top: { md: 72 },
                     order: { xs: 2, md: 0 } }}>
            <PanelMark>This morning, hardest first</PanelMark>
            <Stack component="ol" spacing={0.5} aria-label="Queue"
                   sx={{ listStyle: "none", p: 0, m: 0, mt: 1 }}>
              {openItems.map((item, at) => {
                const isCurrent = current?.id === item.id;
                return (
                  <li key={item.id}>
                    <Box
                      component="button"
                      type="button"
                      onClick={() => { setCursor(at); setAsking(null); }}
                      sx={{
                        all: "unset", boxSizing: "border-box", display: "block", width: "100%",
                        cursor: "pointer", p: 1.25, borderRadius: 1,
                        minHeight: TOUCH.minHeight,
                        border: "1px solid",
                        borderColor: isCurrent ? "primary.main" : "var(--color-divider)",
                        bgcolor: isCurrent ? "var(--color-accent-100)" : "background.paper",
                        "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
                      }}
                    >
                      <Meta>{item.kindLabel}</Meta>
                      <Typography variant="body2"
                                  sx={{ fontWeight: isCurrent ? 600 : 500 }}>
                        {item.short}
                      </Typography>
                    </Box>
                  </li>
                );
              })}
              {/* Answered, and still on screen. A queue that simply shortens
                  gives no sense of having got anywhere — and the outcome is
                  worth reading back, because it is what the toast said as it
                  went past. */}
              {done.map((s) => (
                <li key={s.item.id}>
                  <Box sx={{ p: 1.25, borderRadius: 1, opacity: 0.62,
                             border: "1px solid var(--color-divider)" }}>
                    <Meta>{s.outcome}</Meta>
                    <Typography variant="body2" sx={{ textDecoration: "line-through" }}>
                      {s.item.short}
                    </Typography>
                  </Box>
                </li>
              ))}
            </Stack>
            <Meta sx={{ mt: 1 }}>
              Approvals first — somebody is waiting and a quote cannot be sent.
              Then the server's own ranking. Then three questions about quotes
              nobody answered.
            </Meta>
          </Box>

          <Box sx={{ order: { xs: 1, md: 0 } }}>
            {current ? (
              <ItemPanel
                item={current}
                busy={busy}
                asking={asking === current.id}
                onAct={(a) => void act(current, a)}
                onReason={(code) => void recordOutcome(current, "LOST", code)}
                onSkip={() => move(1)}
              />
            ) : (
              <DonePanel done={done} stillUnanswered={stillUnanswered} />
            )}
          </Box>
        </Box>
      )}
    </>
  );
}

/** How many of this morning's three questions have been answered — the number
 *  the done panel subtracts from the pile so it does not overstate what is
 *  left. */
function doneOutcomes(settled: Record<string, Settled>): number {
  return Object.keys(settled).filter((id) => id.startsWith("outcome:")).length;
}

function ItemPanel({
  item, busy, asking, onAct, onReason, onSkip,
}: {
  item: QueueItem;
  busy: boolean;
  asking: boolean;
  onAct: (a: QueueAction) => void;
  onReason: (code: QuoteLossReason) => void;
  onSkip: () => void;
}) {
  const withheld = item.rows.some((r) => r.restricted);

  return (
    <Paper variant="outlined">
      <Box sx={{ height: 3, bgcolor: item.tone === "bad" ? "error.main"
                                    : item.tone === "warn" ? "warning.main"
                                    : "var(--color-accent-400)" }} />
      <Box sx={{ p: 2.5 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", mb: 1 }}>
          <StatusChip label={item.kindLabel} tone={item.tone} />
          <Meta inline>{item.meta}</Meta>
        </Stack>
        <Typography variant="h2" sx={{ mb: 1, maxWidth: "34ch" }}>{item.title}</Typography>
        {item.body && (
          <Typography variant="body1" sx={{ mb: 2, maxWidth: "74ch" }}>{item.body}</Typography>
        )}

        <Box sx={{
          display: "grid", gap: 3, alignItems: "start",
          gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1fr) 260px" },
        }}>
          <Box>
            <PanelMark mark="ink">The arithmetic</PanelMark>
            {item.rows.length ? (
              <FactTable
                label={`${item.title} — the figures`}
                rows={item.rows.map((r) => [r.label, r.value])}
              />
            ) : (
              <Meta>No figures came back with this one.</Meta>
            )}
            {withheld && (
              <Meta sx={{ mt: 1 }}>
                Cost and margin are not part of your role. They are absent from
                this response, not hidden in your browser.
              </Meta>
            )}

            {item.interpretation ? (
              <Box sx={{ mt: 2 }}>
                <PanelMark mark="accent">Interpretation</PanelMark>
                <Typography variant="body2" sx={{
                  borderLeft: "2px solid", borderColor: "primary.main",
                  pl: 2, maxWidth: "74ch",
                }}>
                  {item.interpretation}
                </Typography>
                <Meta sx={{ mt: 0.5 }}>
                  Written by a model from the figures above. The arithmetic is
                  the authority.
                </Meta>
              </Box>
            ) : item.kind === "decision" && (
              <Box sx={{ mt: 2 }}>
                <PanelMark mark="accent">Interpretation</PanelMark>
                <Meta>
                  None came back, so this is empty rather than guessed. The
                  arithmetic is unaffected and is what the decision rests on.
                </Meta>
              </Box>
            )}
          </Box>

          <Stack spacing={2}>
            <MetricCard
              label={item.costLabel}
              value={item.cost}
              unknown={item.cost ? undefined : "Not shown for your role"}
              sub={item.costNote}
            />
            <Box>
              <PanelMark>In context</PanelMark>
              <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                {item.links.map((l) => (
                  <InlineLink key={l.to + l.label} to={l.to}>{l.label}</InlineLink>
                ))}
              </Stack>
            </Box>
            {item.limits.length > 0 && (
              <Box>
                <PanelMark>This will not answer</PanelMark>
                <Stack component="ul" spacing={0.5} sx={{ pl: 2, m: 0, mt: 0.5 }}>
                  {item.limits.map((l) => (
                    <Typography component="li" variant="caption" color="text.secondary" key={l}>
                      {l}
                    </Typography>
                  ))}
                </Stack>
              </Box>
            )}
          </Stack>
        </Box>
      </Box>

      <Box sx={{
        borderTop: "1px solid var(--color-divider)", bgcolor: "var(--color-neutral-200)",
        p: 2, display: "flex", gap: 1.5, flexWrap: "wrap", alignItems: "center",
      }}>
        {item.actions.length ? (
          <>
            {item.actions.map((a, i) => (
              <Button
                key={a.key}
                onClick={() => onAct(a)}
                disabled={busy}
                variant={a.tone === "primary" || a.tone === "good" ? "contained" : "outlined"}
                color={a.tone === "danger" ? "error" : a.tone === "good" ? "success" : "primary"}
                sx={{ minHeight: TOUCH.minHeight }}
              >
                {a.label}{i === 0 ? " ↵" : ""}
              </Button>
            ))}
            <Box sx={{ flex: 1 }} />
            <Button onClick={onSkip} color="inherit" sx={{ minHeight: TOUCH.minHeight }}>
              Not now
            </Button>
          </>
        ) : (
          <Typography variant="body2" color="text.secondary">{item.waiting}</Typography>
        )}
      </Box>

      {asking && (
        <Box sx={{ borderTop: "1px solid var(--color-divider)", p: 2,
                   bgcolor: "var(--color-accent-100)" }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>{item.reasonPrompt}</Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1 }}>
            {DEFAULT_LOSS_CHOICES.map((c) => (
              <Chip
                key={c.code}
                label={c.label}
                onClick={() => onReason(c.code)}
                disabled={busy}
                sx={{ ...TOUCH }}
              />
            ))}
          </Stack>
          <Meta sx={{ mt: 1 }}>
            A forced reason is a fabricated reason, and it comes back later as a
            chart. Say what you know.
          </Meta>
        </Box>
      )}
    </Paper>
  );
}

/** The end of the morning: what was answered, what can still be taken back, and
 *  what the platform knows and is deliberately not calling urgent. */
function DonePanel({ done, stillUnanswered }: {
  done: Settled[];
  stillUnanswered: number;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 3 }}>
      <Typography variant="h2" sx={{ mb: 1 }}>Done for today</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, maxWidth: "64ch" }}>
        {done.length
          ? `You answered ${done.length} ${done.length === 1 ? "thing" : "things"} this morning.`
          : "Nothing was waiting."}
      </Typography>

      <Stack spacing={0.5} sx={{ mb: 3 }}>
        {done.map((s) => (
          <Stack key={s.item.id} direction="row" spacing={2}
                 sx={{ alignItems: "center", justifyContent: "space-between",
                       border: "1px solid var(--color-divider)", borderRadius: 1, p: 1.25 }}>
            <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }}>{s.item.short}</Typography>
            <Meta inline>{s.outcome}</Meta>
            {s.undo
              ? <Button size="small" onClick={s.undo}>Undo</Button>
              // Said rather than shown as a disabled button: an approval and a
              // recorded outcome are signed, append-only, and the way to change
              // one is to record another.
              : <Meta inline>recorded</Meta>}
          </Stack>
        ))}
      </Stack>

      <Box sx={{ borderTop: "1px solid var(--color-divider)", pt: 2 }}>
        <PanelMark>Known, and deliberately not urgent</PanelMark>
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          {stillUnanswered > 0 && (
            <Typography variant="body2">
              {stillUnanswered} more quotes have no recorded outcome —{" "}
              {OUTCOMES_PER_MORNING} a morning, not the pile at once.{" "}
              <InlineLink to={PATH.unrecordedQuotes}>The whole pile</InlineLink>
            </Typography>
          )}
          <Typography variant="body2">
            Everything the platform found and did not rank is still at{" "}
            <InlineLink to={PATH.list}>all decisions</InlineLink>, and the
            patterns behind them are in{" "}
            <InlineLink to={PATH.evidence}>the evidence library</InlineLink>.
          </Typography>
        </Stack>
      </Box>
    </Paper>
  );
}
