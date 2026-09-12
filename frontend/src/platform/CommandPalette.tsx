/** ⌘K: say what you want to do.
 *
 * The nav answers "where is the screen called X", which is only useful to
 * somebody who already knows this product's vocabulary. This answers the
 * question a new reader actually has — *I want to quote a customer* — and the
 * question an experienced one has at speed: *Money, now*.
 *
 * Two sections, and the order is the point. **Do** comes first and is written
 * in verbs; **Go to** is every destination, tab and piece of evidence, built
 * from `destinations.ts` so a screen that moves cannot fall out of the palette
 * and a screen a role cannot open never appears in it.
 *
 * Every row is a link. Enter follows it, a click follows it, and ⌘-click opens
 * it in a tab — the palette is a faster way to the same addresses, not a second
 * navigation system with its own rules.
 */
import { useMemo, useState } from "react";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import InputAdornment from "@mui/material/InputAdornment";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import ListSubheader from "@mui/material/ListSubheader";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import SearchOutlined from "@mui/icons-material/SearchOutlined";
import { Link as RouterLink } from "react-router-dom";

import type { AppAbility } from "./ability";
import {
  DESTINATIONS, MONEY_TABS, SETUP_TABS, visibleEvidence, visibleTabs,
} from "./destinations";
import { PATH, pathFor, type Screen } from "./route";

interface Command {
  label: string;
  to: string;
  /** Words somebody might type that are not in the label. The product's own
   *  vocabulary is here so "storyboard" still finds the morning read. */
  also?: string;
}

/** The verbs. Written as what a person wants, not as the screen that serves it
 *  — "See who owes us money" rather than "Receivables". */
function doCommands(): Command[] {
  return [
    { label: "Work through this morning's queue", to: PATH.home, also: "today triage" },
    { label: "Quote a customer", to: PATH.quotes, also: "new quote estimate price" },
    { label: "See who owes us money", to: PATH.payments, also: "receivables overdue chase" },
    { label: "Say what happened to a quote", to: PATH.unrecordedQuotes,
      also: "won lost outcome record" },
    { label: "Answer a price somebody is waiting on", to: PATH.approvals,
      also: "approve approval sign off" },
    { label: "Check what the last sync read", to: PATH.data,
      also: "connection zoho tally sync" },
  ];
}

export default function CommandPalette({
  open, onClose, ability, isOperator,
}: {
  open: boolean;
  onClose: () => void;
  ability: AppAbility;
  isOperator?: boolean;
}) {
  const [q, setQ] = useState("");

  const places = useMemo<Command[]>(() => {
    const seen = new Set<Screen>();
    const rows: Command[] = [];
    const add = (label: string, screen: Screen, also?: string) => {
      if (seen.has(screen)) return;
      seen.add(screen);
      rows.push({ label, to: pathFor(screen), also });
    };
    for (const d of DESTINATIONS) add(d.label, d.screen);
    for (const t of visibleTabs(MONEY_TABS, ability, isOperator)) add(`Money · ${t.label}`, t.screen);
    for (const t of visibleTabs(SETUP_TABS, ability, isOperator)) add(`Setup · ${t.label}`, t.screen);
    add("Evidence — every analysis view", "evidence", "analysis charts patterns");
    for (const e of visibleEvidence(ability)) add(`Evidence · ${e.name}`, e.screen, e.question);
    add("All decisions", "list", "queue backlog");
    return rows;
  }, [ability, isOperator]);

  const match = (c: Command) => {
    const needle = q.trim().toLowerCase();
    if (!needle) return true;
    return `${c.label} ${c.also ?? ""}`.toLowerCase().includes(needle);
  };

  const dos = doCommands().filter(match);
  const gos = places.filter(match);

  const close = () => { setQ(""); onClose(); };

  return (
    <Dialog
      open={open}
      onClose={close}
      fullWidth
      maxWidth="sm"
      // Near the top, where a palette belongs: it is a control over the page
      // behind it, and a centred modal reads as an interruption of it.
      slotProps={{ paper: { sx: { position: "absolute", top: 72, m: 0 } } }}
      aria-label="What do you want to do?"
    >
      <TextField
        autoFocus
        fullWidth
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Type what you want to do…"
        variant="standard"
        slotProps={{
          input: {
            disableUnderline: false,
            startAdornment: (
              <InputAdornment position="start">
                <SearchOutlined />
              </InputAdornment>
            ),
          },
        }}
        sx={{ p: 2 }}
      />
      <DialogContent sx={{ pt: 0, maxHeight: 420 }}>
        {dos.length === 0 && gos.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            Nothing here matches that. The palette searches what you can do and
            where you can go — not your customers or items.
          </Typography>
        )}
        {dos.length > 0 && (
          <List dense subheader={<ListSubheader disableSticky>Do</ListSubheader>}>
            {dos.map((c) => (
              <ListItemButton key={c.label} component={RouterLink} to={c.to} onClick={close}>
                <ListItemText primary={c.label} />
              </ListItemButton>
            ))}
          </List>
        )}
        {gos.length > 0 && (
          <List dense subheader={<ListSubheader disableSticky>Go to</ListSubheader>}>
            {gos.map((c) => (
              <ListItemButton key={c.label} component={RouterLink} to={c.to} onClick={close}>
                <ListItemText primary={c.label} secondary={c.also} />
              </ListItemButton>
            ))}
          </List>
        )}
      </DialogContent>
    </Dialog>
  );
}
