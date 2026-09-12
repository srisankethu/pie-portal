/** The Quote Builder — a screen of the platform, not a second application.
 *
 * It used to be the other half of a two-app shell: a `mode` flag swapped the
 * whole interface for one with its own brand bar, its own sign-in form and its
 * own session in `localStorage`. Opening it from Quotes therefore asked you to
 * sign in again and then displayed somebody else's name and role, because the
 * account you signed into there was one of two fixed demo accounts that
 * accepted any password. The URL did not change either, so Back went to
 * whatever preceded the platform rather than out of the builder.
 *
 * Now it is a route inside the shell, on the platform's own session: same nav,
 * same user, same sign-out, and `/quotes/<id>` is a link somebody can send.
 *
 * **The draft is a row on the server, not a copy in this browser.** It used
 * to be written to `localStorage` on every change and read back on mount,
 * which is what made a mode flag look necessary in the first place — and
 * which also meant one draft per browser, invisible to everyone else, with
 * the lines behind it gone from the server on the next restart. Every change
 * is written through before it is answered now, so there is no Save button:
 * the chip in the header says when the row was last written, and the same
 * quote is open on whichever desk opens it.
 *
 * **A quote opens with no customer.** The enquiry is what arrived; who it is
 * from is a question the desk answers when it has the answer, and it is
 * answered here — in place, re-resolving the lines already pasted — rather
 * than by starting over. The builder used to open every quote against one
 * literal name, and then against whichever name the last draft carried.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { CompanyRequired, api } from "./api";
import type { QuoteCompany } from "./api";
import { CompanyPicker } from "./components/CompanyPicker";
import { CustomerPicker } from "./components/CustomerPicker";
import { QuoteDetails } from "./components/QuoteDetails";
import type { Line, Quote, QuoteFieldDefinition, QuoteOwner } from "./types";
import { IntakeModal } from "./components/IntakeModal";
import { SupplyDrawer } from "./components/SupplyDrawer";
import { LineGrid } from "./components/LineGrid";
import { blockersFor, coverage, type Fix } from "./components/lineProblems";
import { NARROW_BREAKPOINT } from "./platform/DataGrid";
import { QuoteOutcomeBar } from "./components/QuoteOutcomeBar";
import { SummaryBar } from "./components/SummaryBar";
import { EmptyState, ErrorState, FieldLabel, FilterChip, FilterPanel, FormDialog,
         LoadingState, SectionHeader, TOUCH } from "./platform/kit";
import { abilityFor } from "./platform/ability";
import { PATH, pathFor } from "./platform/route";
import type { PlatformSession } from "./platform/types";
import { formatTime } from "./when";
import { useQuoteIntelligence } from "./useQuoteIntelligence";

/** The filter row, named against whichever system this quote's books are in.
 *
 *  A function rather than a constant because one of the eight names the ERP:
 *  the chip counting lines the ledger does not hold said "Missing Zoho item"
 *  to every customer, including the ones running Business Central or Prophet
 *  21. The name is the server's — see `Quote.systemShort` — and "books" where
 *  nothing is connected, which claims no system rather than the wrong one.
 *
 *  The *short* name, matching the chip on the line itself. Eight chips share
 *  one row, and "Not in Dynamics 365 Business Central" wraps it onto two; the
 *  send button below carries the system's full name, so nothing is lost. */
function filtersFor(systemShort: string): [string, string][] {
  return [
    ["ALL", "All"],
    ["NEEDS", "Needs attention"],
    ["PROC", "Potential procurement"],
    ["BOOKS", `Not in ${systemShort}`],
    ["MANUAL", "Manual review"],
    ["UNRES", "Unresolved"],
    ["SUBST", "Substituted"],
    ["EXC", "Commercial exceptions"],
  ];
}


/** What this screen answers — the sentence the Quotes door used to carry on a
 *  page of its own, in front of the thing it was describing.
 *
 *  It used to end "It never pre-fills the field", and the field was pre-filled:
 *  a resolved line opens at the catalogue rate, so a four-line RFQ arrived
 *  priced with a Quotation total ready to send. The claim appeared three times —
 *  here, in the empty state, and in the rate column's own tooltip — while the
 *  behaviour was the opposite in all three. The default is worth keeping; a
 *  forty-line tender is not forty numbers to type. Saying so is the fix, and the
 *  rate cell now marks which numbers are still the catalogue's. */
const SUB =
  "Paste an RFQ and the engine resolves each line into a quote-ready product. "
  + "Quote context shows this customer's own price history and — for managers — the "
  + "cost and margin. A resolved line opens at the catalogue rate, marked “list” "
  + "until you price it; the number that goes out is yours.";

//: Whether the economics columns were left showing. Stored the way round it is
//: because the default is *on* for the role that has them: the empty
//: preference must mean "show me the numbers I am pricing against".
const ECON_KEY = "pie.quote.hide-economics";

function loadEcon(): boolean {
  try {
    return window.localStorage.getItem(ECON_KEY) !== "1";
  } catch {
    // A private-mode browser throws on access rather than returning null. A
    // builder that cannot remember a column preference is still a builder.
    return true;
  }
}

function saveEcon(on: boolean): void {
  try {
    if (on) window.localStorage.removeItem(ECON_KEY);
    else window.localStorage.setItem(ECON_KEY, "1");
  } catch {
    /* see above */
  }
}

function passesFilter(l: Line, f: string, flagged: Set<string>): boolean {
  switch (f) {
    case "EXC":
      return flagged.has(l.id);
    case "NEEDS":
      return l.flags.attention;
    case "PROC":
      return l.flags.procurement;
    case "BOOKS":
      return l.flags.missingBooks;
    case "MANUAL":
      return l.flags.manualReview;
    case "UNRES":
      return l.flags.unresolved;
    case "SUBST":
      return l.flags.substituted;
    case "MFLOOR":
      return !!l.economics?.below_floor;
    default:
      return true;
  }
}

export default function QuoteBuilder({ session }: { session: PlatformSession }) {
  const t = session.token;
  // Mirrors the server, which omits cost and margin for a salesperson rather
  // than sending them for the browser to hide. `ability.ts` is the one table
  // that answers this, so the answer here cannot drift from the nav's.
  const mgmt = abilityFor(session).can("read", "economics");
  const navigate = useNavigate();
  // Which draft this is. The URL carries it, so the same quote opens on
  // whichever desk follows the link — there is no other source.
  const { id } = useParams<{ id: string }>();

  const [quote, setQuote] = useState<Quote | null>(null);
  // Why the draft could not be opened: it was removed, or it is not this
  // organization's, or the request failed. Held so the screen can say so
  // and offer the way back, rather than sitting on skeletons.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  // Ids, not a `Record<id, boolean>`: the grid speaks ids, the discount call
  // takes ids, and a map that kept `false` entries made "how many are selected"
  // a filter over the keys rather than a length.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [drawerLineId, setDrawerLineId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The customer question, on demand from the header — and offered, not
  // forced, when the quote has none yet.
  const [pickerOpen, setPickerOpen] = useState(false);
  /** The company question, open only when the server refused to choose one
   *  for a *new* quote — so an organization reading a single company's books
   *  never meets it. */
  const [companyChoice, setCompanyChoice] = useState<QuoteCompany[] | null>(null);
  // The organization's quote-level fields. Read once per mount: Settings is
  // where they change, and a builder open across that edit re-reads on its
  // next visit.
  const [definitions, setDefinitions] = useState<QuoteFieldDefinition[]>([]);
  /** Handing the quote over: who it can go to, once asked for. */
  const [handover, setHandover] = useState<{ members: QuoteOwner[]; to: string } | null>(null);
  // Why the last attempt to send was refused. Held on the screen rather than
  // flashed, and cleared by the next change to the quote — which is exactly
  // when the sentence might stop being true.
  const [sendBlock, setSendBlock] = useState<string | null>(null);
  /** Whether the cost and margin columns are showing.
   *
   *  A toggle rather than always on, and remembered rather than reset on every
   *  visit: it is the difference between the screen somebody prices a quote on
   *  and the screen they read one back on, and asking again each morning is the
   *  kind of small tax that makes a control not worth having. Only ever
   *  consulted where the role has economics at all — a salesperson's response
   *  carries no cost, so there is nothing for them to toggle. */
  const [econ, setEcon] = useState(loadEcon);

  // One assessment for the whole quote — see useQuoteIntelligence.
  const ci = useQuoteIntelligence(quote, t);

  // The same `SnackbarProvider` the rest of the platform uses, rather than the
  // fixed-position div and 2.4s timer this used to hand-roll: two of them in
  // quick succession replaced each other, and 2.4s is too short to read a
  // sentence. `flash` keeps its name so every call site is unchanged.
  const { enqueueSnackbar } = useSnackbar();
  const flash = useCallback(
    (msg: string, variant: "default" | "success" = "default") => {
      enqueueSnackbar(msg, { variant, autoHideDuration: variant === "success" ? 8000 : 3000 });
    },
    [enqueueSnackbar],
  );

  // Open the draft the URL names. Without an id there is nothing to open, and
  // the workspace is where one is chosen or started.
  useEffect(() => {
    if (!id) {
      navigate(PATH.quotes, { replace: true });
      return;
    }
    let cancelled = false;
    setQuote(null);
    setLoadError(null);
    api.getQuote(t, id)
      .then((q) => { if (!cancelled) setQuote(q); })
      .catch((e) => { if (!cancelled) setLoadError((e as Error).message); });
    return () => { cancelled = true; };
  }, [id, t, navigate]);

  useEffect(() => {
    let cancelled = false;
    api.fieldDefinitions(t)
      .then((d) => { if (!cancelled) setDefinitions(d); })
      // No definitions is a panel that does not render, not an error worth a
      // banner: the lines are the work, the details are alongside them.
      .catch(() => { if (!cancelled) setDefinitions([]); });
    return () => { cancelled = true; };
  }, [t]);

  useEffect(() => {
    // A refusal describes the quote as it was. Any change to the quote may have
    // answered it, and a stale blocker is worse than none.
    setSendBlock(null);
  }, [quote]);

  const flaggedLines = useMemo(
    () =>
      new Set(
        Object.values(ci.byLineId)
          .filter((i) => i.exceptions.some((e) => e.severity !== "INFO"))
          .map((i) => i.line_id),
      ),
    [ci.byLineId],
  );

  const visible = useMemo(() => {
    if (!quote) return [];
    const q = search.trim().toLowerCase();
    return quote.lines.filter(
      (l) =>
        passesFilter(l, filter, flaggedLines) &&
        (!q ||
          [l.reqCode, l.reqDesc, l.supplyCode, l.raw].filter(Boolean).join(" ").toLowerCase().includes(q)),
    );
  }, [quote, filter, search, flaggedLines]);

  /** Start another draft and open it. This one stays in the workspace as it
   *  is — nothing is abandoned, which is what "New quote" used to mean here.
   *
   *  The company is decided at creation, once, and the server refuses to
   *  choose where the organization reads several books: ask, then retry. */
  const startNewQuote = (connectionId?: string) =>
    guard(async () => {
      let q: Quote;
      try {
        q = await api.createQuote(t, "", undefined, connectionId);
      } catch (e) {
        if (e instanceof CompanyRequired) {
          setCompanyChoice(e.companies);
          return;
        }
        throw e;
      }
      setCompanyChoice(null);
      setSelectedIds([]);
      setFilter("ALL");
      setSearch("");
      navigate(pathFor("quotes", q.id));
      flash(`Started ${q.number}`);
    });

  const clearSelection = () => {
    setSelectedIds([]);
    flash("Selection cleared");
  };

  const selectVisible = () => {
    const ids = visible.map((l) => l.id);
    setSelectedIds((prev) => [...new Set([...prev, ...ids])]);
    flash(`${ids.length} visible line(s) selected`);
  };

  /** A line deleted, or filtered out of the grid, must not keep counting
   *  towards "3 selected" — or towards a discount applied to it. */
  const visibleIds = useMemo(() => new Set(visible.map((l) => l.id)), [visible]);
  const selection = useMemo(
    () => selectedIds.filter((id) => visibleIds.has(id)), [selectedIds, visibleIds]);

  /** Whether a line already has an approval waiting, so a strip offers to ask
   *  once rather than every time it is drawn.
   *
   *  Up here with the other hooks, not down beside the actions it reads like:
   *  everything below the `if (!quote)` return runs only once the draft has
   *  landed, so a hook there is called on the second render and not the first.
   *  React counts hooks per render, and one that appears late crashes the whole
   *  screen — which is exactly what this did, at every viewport, from the press
   *  of "New quote" onwards. */
  const approvalPendingFor = useCallback(
    (lineId: string) =>
      (ci.gate?.requests ?? []).some(
        (r) => r.subject_line_id === lineId && r.status === "PENDING"),
    [ci.gate]);

  // Keyboard: `/` to search and Escape to close, both of which belong to the
  // page. Everything *inside* the grid — ↑↓, Enter to open a line, Space to
  // select — is ag-grid's now. It used to be re-implemented here over a
  // `focusId` of our own, which meant two listeners for one keystroke and a
  // focus ring that could disagree with the row the grid thought was current.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (document.activeElement?.tagName || "").toUpperCase();
      const typing = tag === "INPUT" || tag === "TEXTAREA";
      if (e.key === "Escape") {
        // Not while the intake is in flight. The dialog stops its *own*
        // Escape then (`onClose` is withheld), and this listener is on the
        // window, so without the same guard the keystroke would still close
        // the one thing holding the pasted RFQ and the refusal about to be
        // shown in it. `busy` is only ever raised by an action on this screen,
        // and none of them is reachable behind a modal, so while the dialog is
        // open it means the intake and nothing else.
        if (intakeOpen) { if (!busy) setIntakeOpen(false); }
        else if (drawerLineId) setDrawerLineId(null);
        else if (typing) (document.activeElement as HTMLElement).blur();
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        document.getElementById("qb-search")?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [intakeOpen, drawerLineId, busy]);

  // No "could not be started" screen any more. It existed for the auto-create
  // that opened a quote against a literal customer on mount; starting a quote
  // is now something a person does, and a failure to do it belongs next to the
  // control they pressed. `guard` reports it through `flash`, like every other
  // action on this screen.

  /** Say who this quote is for — or change it — in place.
   *
   *  The server resolves the lines already on the quote again under the new
   *  customer's identity scope and says what it kept, so a quote pasted
   *  before the customer was known is honest about whose it is. Changing the
   *  customer used to start a new quote and discard this one, because each
   *  line remembers the scope it was resolved under; re-resolving is the
   *  answer to that, not abandonment.
   *
   *  The dialog closes when the change *lands*, not on the press. */
  const chooseCustomer = (c: { id: string; name: string }) =>
    guard(async () => {
      const q = await api.setCustomer(t, quote!.id, c.name, c.id);
      setQuote(q);
      setPickerOpen(false);
      flash(q.note ?? `${q.number} is for ${c.name}`, "success");
    });

  /** Save the quote-level details. Not through `guard`: the panel shows the
   *  refusal beside the field it is about, which a snackbar cannot. */
  const saveFields = async (fields: Record<string, string | number>) => {
    setQuote(await api.setFields(t, quote!.id, fields));
  };

  const openHandover = () =>
    guard(async () => {
      const members = await api.assignees(t);
      setHandover({ members: members.filter((m) => m.id !== quote!.ownerId),
                    to: "" });
    });

  const handOver = () =>
    guard(async () => {
      if (!handover?.to) return;
      const q = await api.setOwner(t, quote!.id, handover.to);
      setQuote(q);
      setHandover(null);
      flash(q.note ?? "Handed over", "success");
    });

  if (!quote) {
    return (
      <Box>
        <SectionHeader title="Quote Builder" sub={SUB} />
        {loadError ? (
          <ErrorState
            title="This quote could not be opened"
            error={loadError}
            onRetry={() => navigate(PATH.quotes)}
          />
        ) : (
          <LoadingState rows={3} label="Opening the quote…" />
        )}
      </Box>
    );
  }

  const drawerLine = quote.lines.find((l) => l.id === drawerLineId) || null;

  async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Resolve a pasted RFQ into lines, with the document it arrived as.
   *
   *  Not through `guard`, for the reason `saveFields` is not: `guard` reports
   *  through `flash` and then *swallows*, and a snackbar cannot say which of
   *  the two calls was refused beside the text it is about. The dialog holds
   *  the only copy of what was typed, so it awaits this promise, stays open on
   *  a rejection and shows the server's own sentence — which means this must
   *  reject rather than resolve quietly. `busy` is still raised and lowered by
   *  hand, so the screen behind the dialog greys out exactly as it did. */
  const doIntake = async (text: string, channel: string, file: File | null) => {
    setBusy(true);
    try {
      // The document first, and its id named by the intake — two calls rather
      // than one multipart request, because the upload has its own refusals
      // (413 for a size or archive ceiling, 415 for a type) and its own
      // statuses, and a document lost to an unrelated intake failure would
      // have to be attached again.
      //
      // Whichever one throws reaches the dialog, so a refused document stops
      // here with the server's own sentence and the RFQ text is still in the
      // dialog to try again with. Uploading second would be worse in exactly
      // the way that matters: the lines would already be on the quote, and the
      // desk would be told the attachment failed with nothing left to retry.
      const doc = file ? await api.uploadRfqDocument(t, file) : null;
      const q = await api.intake(t, quote!.id, text, channel || undefined,
                                 doc?.rfq_document_id);
      setQuote(q);
      // Closed here rather than by the dialog: this is the line that knows the
      // enquiry landed, and the dialog stays open for every path that did not
      // reach it.
      setIntakeOpen(false);
      const read = q.lines.filter((l) => l.proposed).length;
      // Whether the wording itself was kept. The server captures it only when
      // the channel was stated, so this is how the desk sees that leaving the
      // dropdown unset costs the corpus a line — reported, never silent.
      const kept = q.intake?.captured ? " — wording kept" : "";
      // Said only when the server actually kept the enquiry row, because that
      // row is where the document link lives. A document attached with no
      // channel stated is stored and reachable, but nothing joins it to this
      // RFQ — claiming otherwise would be the message telling somebody a link
      // exists that they will later fail to find.
      const attached = doc && q.intake?.captured ? ` — ${doc.filename} attached` : "";
      // Says which produced the lines. A reading presented as though somebody
      // had typed it is the one outcome worth avoiding here.
      flash(read
        ? `${q.summary.total} line(s) read from your message — check each one${kept}${attached}`
        : `${q.summary.total} line(s) in quote${kept}${attached}`);
    } finally {
      // No `catch`. The throw is the contract with the dialog, and swallowing
      // it here is exactly the bug: a refusal that closed the dialog would
      // take the pasted RFQ with it.
      setBusy(false);
    }
  };

  const doConfirmReading = (id: string) =>
    guard(async () => {
      const q = await api.confirmReading(t, quote!.id, id);
      setQuote(q);
    });

  const doSelect = (code: string, manual: boolean) =>
    guard(async () => {
      const q = await api.selectSupply(t, quote!.id, drawerLineId!, code, manual);
      setQuote(q);
      // A confirmed mapping is a durable fact the person just taught the
      // system — it outranks the routine "supply set" acknowledgement, and is
      // held longer because it is a sentence rather than a status.
      if (q.note) flash(q.note, "success");
      else flash(code === drawerLine?.reqCode
        ? "Reverted to requested product" : `Supply set to ${code}`);
    });

  const doRevert = () =>
    guard(async () => {
      const q = await api.selectSupply(t, quote!.id, drawerLineId!, drawerLine!.reqCode, false);
      setQuote(q);
    });

  const doSetPrice = (id: string, price: number | null) =>
    guard(async () => setQuote(await api.setPrice(t, quote!.id, id, price)));

  const doSetCustomCost = (id: string, cost: number | null, note: string) =>
    guard(async () => {
      setQuote(await api.setCustomCost(t, quote!.id, id, cost, note));
      flash(cost === null
        ? "Cost price cleared — back to the cost on record"
        : "Cost price recorded for this line");
    });

  const doDeleteLine = (id: string) =>
    guard(async () => {
      const q = await api.deleteLine(t, quote!.id, id);
      setQuote(q);
      flash("Line removed from quote");
    });

  const doCreateItem = (id: string) =>
    guard(async () => {
      const q = await api.createItem(t, quote!.id, id);
      setQuote(q);
      // Against the mock this write could not fail, so the message was
      // unconditional. Against a real ledger it can, and announcing a creation
      // that did not happen is the one thing this screen must not do — the line
      // is left reading CREATE FAILED, and the reason is said out loud.
      flash(q.createItemError || `Item created in ${q.systemLabel}`);
    });

  /** A fix pressed on a line's strip.
   *
   *  Every one of these is an action this screen already had — they were behind
   *  the supply drawer, a column button, or nowhere at all. What is new is
   *  *where they are*: on the row whose problem they answer, at the moment it
   *  is discovered, rather than at the end of the journey.
   *
   *  The approval ask carries the exception's own code and title as its reason,
   *  because the person approving needs to know which boundary was crossed and
   *  the screen already knows. */
  const doFix = (line: Line, fix: Fix) => {
    switch (fix.kind) {
      case "accept-reading":
        return void doConfirmReading(line.id);
      case "choose-candidate":
        return void guard(async () => {
          const q = await api.selectSupply(t, quote!.id, line.id, fix.code, false);
          setQuote(q);
          flash(q.note ?? `Supply set to ${fix.code}`, q.note ? "success" : "default");
        });
      case "open-supply":
        return setDrawerLineId(line.id);
      case "set-price":
        return void doSetPrice(line.id, fix.price);
      case "ask-approval":
        return void guard(async () => {
          await ci.requestApproval(line.id, fix.reasonCode, fix.reason);
          flash("Asked. The quote stays here until it is answered.", "success");
        });
      case "create-item":
        return void doCreateItem(line.id);
    }
  };

  const doDiscount = (pct: number) =>
    guard(async () => {
      const q = await api.discount(t, quote!.id, selection, pct);
      setQuote(q);
      flash(`${q.applied} line(s) discounted ${pct}%`);
    });

  /** Create the document in the customer's books, or say — durably — why not.
   *
   *  The refusal used to be a three-second grey snackbar and a filter change.
   *  Press the button, watch the grid re-filter, and by the time you have
   *  looked down at it the only statement of *what was wrong* has gone; if you
   *  were already on that filter, nothing on screen changed at all. The reason
   *  now stays until the quote changes, which is also when it stops being true.
   *  Success is held the same way — see the sent chip in the summary bar. */
  const doEstimate = () =>
    guard(async () => {
      setSendBlock(null);
      // The server re-checks the approval gate; this only avoids a round trip
      // that is certain to be refused, and says why in the same words.
      if (ci.gate && !ci.gate.can_submit) {
        setSendBlock(ci.gate.blocked_reason
          ?? "This quote needs approval before it can be sent.");
        setFilter("EXC");
        return;
      }
      const r = await api.createEstimate(t, quote!.id);
      if (!r.ok) {
        setSendBlock(r.message);
        if (r.blockers.length) setFilter("NEEDS");
        return;
      }
      // Re-read the quote so the summary bar learns the estimate number it now
      // carries; the send endpoint answers with the estimate, not the quote.
      setQuote(await api.getQuote(t, quote!.id));
      flash(r.message, "success");
    });

  const selectedCount = selection.length;
  const hasLines = quote.lines.length > 0;
  const gateBlockedReason = ci.gate && !ci.gate.can_submit ? ci.gate.blocked_reason : null;
  // Everything still standing between this quote and the customer — the same
  // problems the grid draws on the rows, counted once per kind. Computed here
  // rather than inside the bar so the bar cannot disagree with the grid about
  // what is wrong: one model, two renderings.
  const blockers = blockersFor(quote, ci.byLineId, gateBlockedReason);
  // Empty is the default, and the header treats it as a question still open.
  const hasCustomer = quote.customer.trim().length > 0;
  // The server's answer to "may this person change it" — the same rule every
  // mutation enforces with a 403, applied here so the refusal is never the
  // first thing somebody sees.
  const readOnly = !quote.canEdit;
  // What to call the ledger, in its own words — see `Quote.systemLabel`.
  const filters = filtersFor(quote.systemShort);
  const ownerName = quote.owner?.name || "its owner";

  return (
    <Box>
      <SectionHeader
        title="Quote Builder"
        sub={SUB}
        actions={
          <>
            {/* Back to the list, then the two things this screen is opened
                to do — all with a full tap target like the controls below. */}
            <Button variant="text" size="small" sx={TOUCH}
                    onClick={() => navigate(PATH.quotes)}>
              All quotes
            </Button>
            <Button variant="outlined" size="small" sx={TOUCH}
                    onClick={() => startNewQuote()} disabled={busy}>
              New quote
            </Button>
            {/* Cost and margin, for the reader who has them. Offered only to
                that reader: a salesperson's response carries no cost at all, so
                a toggle here would be a control over two empty columns. */}
            {mgmt && (
              <Button
                variant={econ ? "contained" : "outlined"}
                color={econ ? "primary" : "inherit"}
                size="small"
                sx={TOUCH}
                aria-pressed={econ}
                onClick={() => setEcon((on) => { saveEcon(!on); return !on; })}
              >
                Economics
              </Button>
            )}
            <Button variant="contained" size="small" sx={TOUCH}
                    onClick={() => setIntakeOpen(true)} disabled={readOnly}>
              Paste RFQ
            </Button>
          </>
        }
      />

      {/* Which quote this is, whose it is, and when the row was last written.
          A `Paper` strip rather than the brand bar this used to occupy: the
          shell above already says who is signed in and what the product is
          called, and repeating it here was half of why the screen felt like a
          different application. */}
      <Paper
        variant="outlined"
        sx={{
          p: 1.5, mb: 2,
          display: "flex", flexWrap: "wrap", alignItems: "center", gap: 2, rowGap: 1,
        }}
      >
        <Box>
          <FieldLabel>Quote</FieldLabel>
          <Typography sx={{ fontFamily: "var(--font-heading)", fontWeight: 600 }}>
            {quote.number}
          </Typography>
        </Box>
        <Box sx={{ minWidth: 0 }}>
          <FieldLabel>Customer</FieldLabel>
          {/* A control, not a caption — and, until somebody answers, the
              question itself. A quote opens with no customer; this is where
              one is chosen, and it reads as a thing still to do rather than
              as a blank. */}
          {hasCustomer ? (
            <Button
              type="button"
              variant="text"
              size="small"
              onClick={() => setPickerOpen(true)}
              disabled={readOnly}
              sx={{ ...TOUCH, p: 0, justifyContent: "flex-start",
                    textTransform: "none", lineHeight: 1.4,
                    fontFamily: "var(--font-heading)", fontWeight: 600,
                    "&.Mui-disabled": { color: "text.primary" } }}
            >
              {quote.customer}
            </Button>
          ) : (
            <Button
              type="button"
              variant="outlined"
              size="small"
              color="warning"
              onClick={() => setPickerOpen(true)}
              disabled={readOnly}
              sx={{ ...TOUCH, textTransform: "none" }}
            >
              {readOnly ? "No customer yet" : "Choose customer"}
            </Button>
          )}
        </Box>
        <Box sx={{ minWidth: 0 }}>
          <FieldLabel>Owner</FieldLabel>
          {/* Whose quote this is. Every quote has one — whoever started it —
              and only they change it, plus managers where the policy allows.
              The owner (or a manager) can hand it over from here. */}
          <Button
            type="button"
            variant="text"
            size="small"
            onClick={openHandover}
            disabled={readOnly || busy}
            title={readOnly ? undefined : "Hand this quote to somebody else"}
            sx={{ ...TOUCH, p: 0, justifyContent: "flex-start",
                  textTransform: "none", lineHeight: 1.4,
                  fontFamily: "var(--font-heading)", fontWeight: 600,
                  "&.Mui-disabled": { color: "text.primary" } }}
          >
            {quote.owner?.name || "—"}
          </Button>
        </Box>
        <Box sx={{ flex: 1 }} />
        {/* When the server last wrote this quote. Every change is written
            through before it is answered, so there is no Save button to
            press and nothing that lives only in this browser. */}
        {quote.savedAt && (
          <Chip size="small" variant="outlined"
                label={`Saved ${formatTime(quote.savedAt)}`} />
        )}
      </Paper>

      {/* Chips, matching the decision queue's filter row. These select what the
          grid shows; they are not actions, and rendering them as buttons said
          otherwise on both screens.

          `FilterPanel` rather than a `Paper` spelling out the same six sx
          properties: kit.tsx §10 already owns "the controls above a list", and
          a duplicate scan named this strip and the header strip above it as one
          clone. The header strip is not this — it identifies the quote — so
          only this one moves. */}
      {/* Nothing to narrow yet. On an empty quote this rendered eight chips all
          reading 0, a search box over nothing, and two disabled buttons —
          above an empty state whose whole message is "paste an RFQ". Controls
          for a list that does not exist are the loudest thing on the screen at
          the one moment there is exactly one thing to do. */}
      {hasLines && (
      <FilterPanel>
        {filters.map(([key, label]) => (
          <FilterChip
            key={key}
            label={label}
            count={quote.filterCounts[key] ?? 0}
            selected={filter === key}
            // Unresolved lines and lines needing a decision are the two states
            // that stop a quote being sent, so their count is coloured even when
            // the chip is not the active one.
            alert={(key === "NEEDS" || key === "UNRES")
                   && (quote.filterCounts[key] ?? 0) > 0}
            onClick={() => setFilter(key)}
          />
        ))}
        {mgmt && (quote.filterCounts.MFLOOR ?? 0) > 0 && (
          <FilterChip
            label="Below margin floor"
            count={quote.filterCounts.MFLOOR}
            selected={filter === "MFLOOR"}
            tone="error"
            onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
          />
        )}
        <Box sx={{ flex: 1 }} />
        {/* Full width on a phone: a 220px search box beside a wrapped row of
            chips left ~90px of usable field, which is not a search box. */}
        <TextField
          id="qb-search"
          size="small"
          placeholder="Search  ( / )"
          aria-label="Search quote lines"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ width: { xs: "100%", sm: 220 }, "& .MuiInputBase-root": TOUCH }}
        />
        {/* Both of these named something other than what they do. "Select
            visible" reads as column visibility, which is what that phrase means
            in every other grid; it selects the lines passing the current
            filter, and saying how many makes the filter's effect visible before
            the press rather than after. "Clear" sat immediately right of the
            search box and cleared the *selection* — the one thing a reader
            beside a search field will not assume it means. */}
        <Button variant="outlined" size="small" sx={TOUCH}
                onClick={selectVisible} disabled={!visible.length}>
          Select all {visible.length} shown
        </Button>
        <Button variant="outlined" size="small" sx={TOUCH}
                onClick={clearSelection} disabled={!selectedCount}>
          Clear selection
        </Button>
      </FilterPanel>
      )}

      {/* An `Alert`, not a hand-coloured banner: the severity carries an icon
          and a role as well as a hue, which is the standard everywhere else in
          this product. */}
      {mgmt && quote.marginFloor && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => setFilter(filter === "MFLOOR" ? "ALL" : "MFLOOR")}
            >
              {filter === "MFLOOR" ? "Show all" : "Review these"}
            </Button>
          }
        >
          <AlertTitle>
            {quote.marginFloor.count} line(s) priced below the{" "}
            {Math.round(quote.marginFloor.floor * 100)}% margin floor
          </AlertTitle>
          Lowest margin {(quote.marginFloor.worst * 100).toFixed(1)}% — review before creating the
          estimate.
        </Alert>
      )}

      {/* Whose quote this is, when it is not the reader's to change. An
          `Alert` rather than a disabled screen with no explanation: every
          control below is greyed for a reason, and this is the reason. */}
      {readOnly && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <AlertTitle>This quote belongs to {ownerName}</AlertTitle>
          You can read it. Only {ownerName}
          {session.role === "SALESPERSON" ? " or a manager" : ""} can change or send
          it — ask them, or have it handed to you.
        </Alert>
      )}

      <QuoteDetails
        quote={quote}
        definitions={definitions}
        readOnly={readOnly}
        onSave={saveFields}
      />

      {/* The organization's mandatory details this quote has not answered.
          Named, next to the grid, because the send refuses on exactly these
          and "details missing" is not a sentence anybody can act on. */}
      {hasLines && quote.missingFields.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <AlertTitle>
            {quote.missingFields.length} required detail(s) before this quote can be sent
          </AlertTitle>
          {quote.missingFields.join(", ")} — fill them in under Quote details above.
        </Alert>
      )}

      {/* The stand-in, said out loud. `ZOHO_QUOTE_SERVICE` defaults to
          `mock`, which is right — a fresh clone must never write to a real
          ledger — and silent, which is not: the stand-in derives in-books,
          stock and list price from a hash of the code, so a line reading
          "NOT IN BOOKS" under this organization's real system name is
          telling somebody something about nothing. §1 asks for the absence
          to be stated rather than to read as a pass. */}
      {hasLines && !quote.booksLive && quote.system && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <AlertTitle>These lines were not checked against {quote.systemLabel}</AlertTitle>
          The platform is running against its offline stand-in, so stock, list
          price and “in the books” on every line below are stand-in figures
          rather than the ones {quote.systemLabel} holds. Set
          <code> ZOHO_QUOTE_SERVICE=live </code> to read the real ledger.
        </Alert>
      )}

      {/* The lines are in and nobody has said whose they are. Said here,
          beside the grid, because this is the point at which it starts to
          matter: pricing reads the customer's history, the books to send into
          are the customer's, and both are unanswerable until this is. */}
      {hasLines && !hasCustomer && (
        <Alert
          severity="info"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={() => setPickerOpen(true)}>
              Choose customer
            </Button>
          }
        >
          <AlertTitle>This quote has no customer yet</AlertTitle>
          Price history and the books it is sent into are the customer's, so the
          quote cannot be sent until one is chosen. Lines already here are
          resolved again for them.
        </Alert>
      )}

      {/* Why the last send was refused, in the words the server used. An
          `Alert` for the same reason the one above is: the severity carries an
          icon and a role as well as a hue. It names the lines, so "3 line(s)
          must be resolved" is followed by which three. */}
      {sendBlock && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          onClose={() => setSendBlock(null)}
        >
          <AlertTitle>This quote was not sent</AlertTitle>
          {sendBlock}
        </Alert>
      )}

      {/* No selection strip here. "3 selected · Apply 10% discount" was on this
          screen twice — once above the grid and once in the summary bar, which
          is sticky and therefore always on screen anyway — and "Clear selection"
          was a third spelling of the toolbar's own Clear. A jscpd pass named it;
          it is the kind of duplicate that reads as thoroughness until somebody
          asks which of the two buttons is the real one. */}

      {!hasLines ? (
        <EmptyState
          title="Paste an RFQ to start building the quote"
          reason={
            "Each line becomes a reviewed item with supplier options, availability and the right "
            + "next action. Resolved lines open at the catalogue rate as a starting point — the "
            + "price that goes out is the one you set."
          }
          action={
            <Stack direction="row" spacing={1} useFlexGap sx={{ justifyContent: "center", flexWrap: "wrap" }}>
              <Button variant="contained" onClick={() => setIntakeOpen(true)}>
                Paste RFQ
              </Button>
            </Stack>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          title="Nothing matches the current filter or search"
          reason="Clear the filter, change the search term, or add a fresh RFQ."
          action={
            <Button variant="outlined" onClick={() => { setFilter("ALL"); setSearch(""); }}>
              Show all {quote.lines.length} lines
            </Button>
          }
        />
      ) : (
        <>
          {/* The grid scrolls inside its own box; the page never scrolls
              sideways (ui-standards §3). */}
          <LineGrid
            lines={visible}
            mgmt={mgmt}
            econ={econ}
            readOnly={readOnly}
            systemShort={quote.systemShort}
            intel={ci.byLineId}
            approvalPendingFor={approvalPendingFor}
            selectedIds={selection}
            onSelectionChange={setSelectedIds}
            onOpen={setDrawerLineId}
            onSetPrice={doSetPrice}
            onDeleteLine={doDeleteLine}
            onFix={doFix}
          />
          {/* Hidden on the phone rendering. Every hint here is about the grid
              — ↑↓ moves the focused row, F2 opens the rate editor, Enter opens
              the supply drawer — and none of it is true of the cards, which
              have no keyboard and no editor to open. A legend for controls that
              are not there is worse than no legend. */}
          <Box
            className="kbd-hints"
            sx={{
              mt: "var(--space-4)",
              display: "none",
              // The wrapper's own breakpoint, read rather than restated: the
              // legend must appear exactly when the grid it describes does.
              [`@media (min-width:${NARROW_BREAKPOINT}px)`]: { display: "flex" },
            }}
          >
            <span>
              <span className="kbd">↑↓</span> navigate
            </span>
            <span>
              <span className="kbd">Enter</span> supply options
            </span>
            <span>
              <span className="kbd">Space</span> select
            </span>
            <span>
              <span className="kbd">F2</span> edit the rate
            </span>
            <span>
              <span className="kbd">/</span> search
            </span>
            <span>
              <span className="kbd">Esc</span> close
            </span>
          </Box>
        </>
      )}

      <SummaryBar
        quote={quote}
        readOnly={readOnly}
        selectedCount={selectedCount}
        onDiscount={doDiscount}
        onCreateEstimate={doEstimate}
        gateBlockedReason={gateBlockedReason}
        blockers={blockers}
        covers={coverage(quote)}
        busy={busy}
      />

      {/* Below the total, not above it: the question "did this win?" only
          arises once the quote has gone out, and the estimate button is what
          sends it. Nothing rendered until the quote has been recorded at all —
          QuoteOutcomeBar returns null without an outcome. */}
      <QuoteOutcomeBar
        outcome={ci.data?.outcome ?? null}
        onRecord={ci.recordOutcome}
        busy={busy || ci.loading}
      />

      {intakeOpen && !readOnly && (
        <IntakeModal onClose={() => setIntakeOpen(false)} onSubmit={doIntake} />
      )}
      {drawerLine && (
        <SupplyDrawer
          line={drawerLine}
          customer={quote.customer}
          token={t}
          mgmt={mgmt}
          intel={ci.byLineId[drawerLine.id] ?? null}
          intelLoading={ci.loading}
          intelError={ci.error}
          onRecordOverride={ci.recordOverride}
          onRequestApproval={ci.requestApproval}
          approvalStatus={
            ci.gate?.requests.find((r) => r.subject_line_id === drawerLine.id) ?? null
          }
          // A real navigation now: the drawer's "open the analysis" link lands
          // on a platform URL in the same shell, rather than swapping the app.
          onOpenPlatform={(path) => navigate(path)}
          onClose={() => setDrawerLineId(null)}
          onSetCustomCost={doSetCustomCost}
          onSelect={doSelect}
          onRevert={doRevert}
          readOnly={readOnly}
        />
      )}

      {/* Handing the quote over. The list is the organization's active
          members, minus the current owner. */}
      <FormDialog open={handover !== null} onClose={() => setHandover(null)} fullWidth maxWidth="xs">
        <DialogTitle>Hand {quote.number} to somebody else</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            They become the owner: the one person who can change or send it,
            apart from a manager where your policy allows. You keep read access.
          </DialogContentText>
          <TextField
            select fullWidth size="small" label="New owner"
            value={handover?.to ?? ""}
            onChange={(e) => setHandover((h) => h && { ...h, to: e.target.value })}
          >
            {(handover?.members ?? []).map((m) => (
              <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>
            ))}
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setHandover(null)}>Cancel</Button>
          <Button variant="contained" disabled={busy || !handover?.to} onClick={handOver}>
            Hand over
          </Button>
        </DialogActions>
      </FormDialog>

      {/* Choosing — or changing — the customer re-resolves the lines already
          on the quote under that customer's identity scope, and the server
          says what it kept. Cancellable, and this is what it returns to: a
          quote with no customer is a quote with a question open, not a locked
          screen. */}
      <CustomerPicker
        open={pickerOpen}
        session={session}
        busy={busy}
        title={hasCustomer ? "Change customer" : "Who is this quote for?"}
        note={quote.lines.length
          ? `This quote has ${quote.lines.length} line(s)`
            + (hasCustomer ? ` resolved for ${quote.customer}` : "")
            + `. They are resolved again for the customer you choose; prices `
            + `you typed are kept where the same product comes back.`
          : "Pricing reads this customer's own history. Start typing a name."}
        onPick={chooseCustomer}
        onCancel={() => setPickerOpen(false)}
      />
      {companyChoice && (
        <CompanyPicker
          open
          busy={busy}
          companies={companyChoice}
          customer=""
          onPick={(cid) => startNewQuote(cid)}
          onCancel={() => setCompanyChoice(null)}
        />
      )}
    </Box>
  );
}
