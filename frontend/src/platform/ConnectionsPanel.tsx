import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import AddOutlined from "@mui/icons-material/AddOutlined";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatDate, since, todayISO } from "../when";
import { papi } from "./api";
import {
  EmptyState, ErrorState, LoadingState, SectionHeader, StatusChip, type Tone,
} from "./kit";
import type {
  ConnectionCheck,
  ConnectionCredential,
  ConnectionsView,
  ConnectorCatalogEntry,
  ConnectorField,
  ErpDiscoveredCompany,
  NewConnectionInput,
  PlatformSession,
  ZohoConnection,
  ZohoSecret,
  ZohoVisibleOrg,
} from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Zoho connections — as many companies as the business has books to read.
 *
 * The screen this replaces could hold exactly one Zoho company per platform
 * organization, which meant a business with three legal entities needed three
 * separate logins to look at three sets of books. Removing that restriction
 * makes three things worth saying on screen rather than leaving them to be
 * discovered:
 *
 * **Which company is broken.** "The organization is connected" stops meaning
 * anything once there are three and one has a revoked token. Health is per
 * connection, with when it was last checked — a connection that has never been
 * checked and one that failed an hour ago look identical otherwise, and only
 * one of them is a problem.
 *
 * **What the sign-in must be granted.** A half-granted sign-in is the most
 * common reason a connection authenticates and then returns nothing: the
 * credential works, one endpoint refuses, and the sync reports zero rows with
 * no visible cause. Every system's grants are listed with what each one buys,
 * so the failure is diagnosable before it happens — inside the add panel and
 * under the connector the tabs selected, because Zoho's scope strings mean
 * nothing to somebody connecting NetSuite.
 *
 * **What pooling costs.** Rows from every enabled connection on an
 * organization are analysed together — revenue and margin roll up across all
 * of them. That is the right default for one business with several books and
 * the wrong one for entities that must stay apart, so it is stated up front.
 */

// The manual-credentials form stores `accounts_base` (and its paired `api_base`)
// on the connection — the data centre the refresh token was issued in. A token
// from one data centre is rejected by every other, so this is the one field a
// manual connection cannot get wrong silently.
// `code` is the short data-centre token the authorize endpoint takes (`?dc=`);
// the two bases are what the manual path stores on the credential. One list,
// because they are one fact — which Zoho estate this grant belongs to — and two
// lists would disagree the first time a data centre was added to one of them.
const DC_PRESETS: {
  code: string; label: string; accounts_base: string; api_base: string;
}[] = [
  { code: "in", label: "India (.in)", accounts_base: "https://accounts.zoho.in",
    api_base: "https://www.zohoapis.in/books/v3" },
  { code: "com", label: "United States (.com)", accounts_base: "https://accounts.zoho.com",
    api_base: "https://www.zohoapis.com/books/v3" },
  { code: "eu", label: "Europe (.eu)", accounts_base: "https://accounts.zoho.eu",
    api_base: "https://www.zohoapis.eu/books/v3" },
  { code: "com.au", label: "Australia (.com.au)", accounts_base: "https://accounts.zoho.com.au",
    api_base: "https://www.zohoapis.com.au/books/v3" },
  { code: "jp", label: "Japan (.jp)", accounts_base: "https://accounts.zoho.jp",
    api_base: "https://www.zohoapis.jp/books/v3" },
];

const EMPTY_FORM = {
  zoho_organization_id: "",
  label: "",
  client_id: "",
  client_secret: "",
  // One field, not two: an owner holds a grant at exactly one of its two
  // stages, and `secretKind` beside it says which. `ZohoSecretField` has the
  // reasoning.
  secret: "",
  accounts_base: DC_PRESETS[0].accounts_base,
  api_base: DC_PRESETS[0].api_base,
};

type ZohoSecretKind = "grant_code" | "refresh_token";

function zohoSecret(kind: ZohoSecretKind, value: string): ZohoSecret {
  return kind === "grant_code" ? { grant_code: value } : { refresh_token: value };
}

/**
 * The one box an owner pastes a Zoho grant into — in both places they can.
 *
 * A grant code and a refresh token are the same credential one step apart: the
 * API console's **Generate Code** produces the first, and exchanging it once
 * produces the second. The server runs that exchange now, so either is
 * accepted here.
 *
 * **It asks which, rather than sniffing it.** The two are indistinguishable by
 * sight — both `1000.xxxxxxxx.xxxxxxxx` — so a box labelled only "Refresh
 * token" silently accepts the code, and the connection then fails its check
 * with `invalid_code`: a message about a revoked token, on a credential that
 * was minted ninety seconds ago. That is the failure this control exists to
 * remove, and guessing from the value would only move the guess server-side.
 *
 * **Grant code is the default** because it is what the console hands you. A
 * refresh token exists at all only if somebody has already run the exchange by
 * hand, which was step 3 of the setup doc and is now this server's job.
 *
 * One component for two call sites, per the UI digest: the add form and the
 * rotate box differ in their ids and their surrounding copy, not in this.
 */
function ZohoSecretField({
  idPrefix, kind, onKindChange, value, onChange, maxWidth = 520,
}: {
  idPrefix: string;
  kind: ZohoSecretKind;
  onKindChange: (kind: ZohoSecretKind) => void;
  value: string;
  onChange: (value: string) => void;
  maxWidth?: number;
}) {
  const isCode = kind === "grant_code";
  return (
    // A column, explicitly. Both children are inline-level — MUI's
    // `ToggleButtonGroup` and `TextField` are each `inline-flex` — so in a
    // plain block they flow onto one line wherever the width allows it, and
    // the chooser ended up *beside* the field it labels on a desktop and above
    // it on a phone. The cap moves to the wrapper so the field can just fill
    // it.
    <Box sx={{ mt: 1.5, maxWidth, display: "flex", flexDirection: "column",
               alignItems: "flex-start" }}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={kind}
        onChange={(_e, v) => {
          if (v) onKindChange(v as ZohoSecretKind);
        }}
        aria-label="Which Zoho credential you have"
        sx={{ mb: 1 }}
      >
        <ToggleButton value="grant_code">Grant code</ToggleButton>
        <ToggleButton value="refresh_token">Refresh token</ToggleButton>
      </ToggleButtonGroup>
      <TextField
        id={`${idPrefix}-secret`}
        label={isCode ? "Grant code" : "Refresh token"}
        /* Masked only when it is the long-lived one. A refresh token is a
           bearer secret that keeps working until it is revoked; a grant code
           dies in minutes, is pasted once under time pressure, and hiding it
           buys nothing while costing the glance that catches a short paste. */
        type={isCode ? "text" : "password"}
        size="small"
        fullWidth
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        placeholder="1000.xxxxxxxx.xxxxxxxx"
        helperText={
          isCode
            ? "Zoho API console → your Self Client → Generate Code, with the scopes listed below. It is single-use and expires in minutes, so paste it straight away — this server exchanges it and stores only the refresh token that comes back."
            : "The value an exchange already produced, not the code from Generate Code. Encrypted before it is stored and never shown again."
        }
        slotProps={{ htmlInput: { spellCheck: false } }}
        sx={{ alignSelf: "stretch" }}
      />
    </Box>
  );
}

// "never", not "—": a connector that has never been checked is a different
// state from a missing field, and only one of them is a problem.
function when(iso: string | null | undefined): string {
  return iso ? since(iso) : "never";
}

// "Check failed" rather than "Not reachable" for the stored false, because
// there are now two ways to earn it and they send you to different places: a
// dead credential, or a login that reaches the company perfectly and was never
// granted a scope the pull needs. Calling the second one unreachable points at
// the one thing that is demonstrably fine. A live check knows which it was and
// says so; the stored row only knows that something failed, and the detail line
// under the chip carries the specifics either way.
//
// Two fields where there was one, because they answer to different readers.
// `tone` is `StatusChip`'s, which is what a person reads — a word and a shape,
// never a hue on its own (ui-standards §6; the `.cx-badge` span this replaces
// was hue and nothing else, and is named in §10's table as StatusChip's
// predecessor). `band` is the `.cx-card` modifier that tints the card's left
// edge, which is a second cue for scanning three cards at once and carries no
// meaning the chip does not already state in words. `tip` is new and is the
// point of using a chip at all: a badge reading "Check failed" that cannot say
// what tends to cause it is decoration.
function health(c: ZohoConnection, check?: ConnectionCheck | null):
  { tone: Tone; band: string; label: string; tip: string } {
  if (!c.enabled) {
    return {
      tone: "neutral", band: "off", label: "Paused",
      tip: "Not pulled, and not feeding the analysis. Nothing already synced "
         + "was removed, so resuming brings its rows back with it.",
    };
  }
  if (check?.missing_required_scopes?.length) {
    return {
      tone: "bad", band: "bad", label: "Missing permissions",
      tip: "The sign-in itself works. A grant the pull needs was refused, so "
         + "no sync can run until it is granted.",
    };
  }
  if (c.last_check_ok === true) {
    return {
      tone: "good", band: "ok", label: "Reachable",
      tip: "The last check reached this company, and every grant it was able "
         + "to probe answered.",
    };
  }
  if (c.last_check_ok === false) {
    return {
      tone: "bad", band: "bad", label: "Check failed",
      tip: "Either the stored sign-in no longer works, or a grant the pull "
         + "needs was never made. The line underneath says which.",
    };
  }
  // No band: never-checked is not a problem, and dimming it like a paused
  // company would say it was one.
  return {
    tone: "neutral", band: "", label: "Not checked",
    tip: "This connection has never been asked whether it still works, which "
       + "is not the same as it being broken.",
  };
}

/** One set of books offered by a sign-in: its name, its id in that system, and
 *  whatever is already true of it.
 *
 *  Three copies of this markup existed — the ERP discovery list, the Zoho
 *  company picker, and the check result on a connection card — and they had
 *  drifted apart: two were `<button class="cred-org">` and the third a `<span>`
 *  carrying an inline `display: block`. All three wrote the state word
 *  ("already added", "this one") as an `<em>` whose only distinguishing mark
 *  was the accent colour `.cred-org em` gave it, which is exactly the coloured
 *  text ui-standards §6 rules out. One component, per §10, and the state word
 *  is a `StatusChip`.
 *
 *  `onPick` is absent where the row is being *reported* rather than offered —
 *  the card's check result lists what a sign-in reaches and there is nothing to
 *  choose. A row nobody can act on must not look like a button.
 *
 *  `Button`, and deliberately not `ListItemButton`, which is the obvious
 *  choice for a row you pick and is wrong here. `ListItemButton` renders a
 *  `<div role="button">`, so `disabled` reaches the DOM as `aria-disabled`
 *  and the press still fires — and the one row that is disabled is the
 *  company already connected, which is precisely the one where filling the
 *  form in would produce an add the server can only refuse. A native
 *  `<button disabled>` cannot be pressed at all. */
function CompanyChoice({
  name, id, note, disabled = false, onPick,
}: {
  name: string;
  id: string;
  /** A state word about this row — "already added", "this one". */
  note?: string;
  disabled?: boolean;
  onPick?: () => void;
}) {
  const body = (
    <Stack direction="row" spacing={1}
           sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 0.5 }}>
      <Typography variant="body2" component="span">{name}</Typography>
      <Typography variant="caption" component="span" className="mono"
                  color="text.secondary">
        {id}
      </Typography>
      {note && <StatusChip label={note} tone="info" dense />}
    </Stack>
  );
  if (!onPick) return <Box sx={{ px: 1, py: 0.5 }}>{body}</Box>;
  return (
    <Button
      type="button"
      variant="outlined"
      color="inherit"
      size="small"
      disabled={disabled}
      onClick={onPick}
      // `typography: "body2"` rather than a font size: these are company
      // names, not button labels, and the ramp already has the rung.
      sx={{
        width: "100%",
        justifyContent: "flex-start",
        borderColor: "divider",
        typography: "body2",
        px: 1.25,
        py: 0.75,
      }}
    >
      {body}
    </Button>
  );
}

/** The `<ul>` those rows sit in. Unstyled, because the row carries its own
 *  frame — and a list, rather than a stack of divs, so a screen reader
 *  announces how many companies a sign-in reaches before reading them out. */
function ChoiceList({ children }: { children: React.ReactNode }) {
  return (
    <Stack component="ul" spacing={0.5}
           sx={{ listStyle: "none", m: 0, mt: 1, p: 0 }}>
      {children}
    </Stack>
  );
}

/* ── one connection ───────────────────────────────────────────────────────── */

function ConnectionCard({
  conn,
  catalogEntry,
  canManage,
  canSync,
  onCheck,
  onRename,
  onToggle,
  onDelete,
  onRotate,
  onErpRotate,
  onSync,
  syncing,
  syncBusy,
}: {
  conn: ZohoConnection;
  /** The connector's catalog entry — the field list a non-Zoho rotation
   *  renders from. Undefined for Zoho, whose rotation is the token flow. */
  catalogEntry?: ConnectorCatalogEntry;
  canManage: boolean;
  canSync: boolean;
  onCheck: (id: string) => Promise<ConnectionCheck | null>;
  onRename: (id: string, label: string) => Promise<void>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRotate: (id: string, secret: ZohoSecret,
             client?: { client_id: string; client_secret: string }) => Promise<string>;
  onErpRotate: (id: string, values: Record<string, string>) => Promise<string>;
  onSync: (id: string, since: string, full: boolean) => Promise<void>;
  /** This card's own company is the one being pulled.
   *
   *  Only this company's own job blocks this button. The flag it replaces meant
   *  "some sync is running", which disabled all three companies the moment any
   *  one of them started — and made the per-connection concurrency the server
   *  grew unreachable from the only screen that would have used it. Two
   *  connected Zoho companies are two independent pulls against two different
   *  APIs. */
  syncing: boolean;
  /** A start request has been posted and not yet answered. Briefly true for
   *  every card, because until the server replies the screen does not know
   *  which company the click was for. */
  syncBusy: boolean;
}) {
  // Older cached payloads may predate the discriminator; a row with none is a
  // Zoho row, because Zoho rows are the only ones that can predate it.
  const isZoho = (conn.connector ?? "zoho") === "zoho";
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(conn.label);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<ConnectionCheck | null>(null);
  // Seeded from what this company was last read from, so a repeat pull offers
  // the window that was already chosen for it rather than a global default.
  const [since, setSince] = useState(conn.suggested_since);
  const [full, setFull] = useState(false);
  // Rotation is a property of *this connection's* Zoho sign-in, so it lives on
  // this card rather than on a credentials panel elsewhere on the page. It is
  // closed by default: a token box permanently open on a working connection
  // invites somebody to paste into it.
  const [rotating, setRotating] = useState(false);
  const [newToken, setNewToken] = useState("");
  const [tokenKind, setTokenKind] = useState<ZohoSecretKind>("grant_code");
  const [rotateNote, setRotateNote] = useState<string | null>(null);
  // The client pair, and whether it is being replaced too. Closed by default
  // for the reason the token box is: a rotation normally happens under the same
  // app, and two secret boxes standing open invite somebody to re-type a secret
  // that was already correct. But it has to be *reachable*, because the one
  // failure a token-only rotation causes is a token generated under a different
  // Self Client — Zoho answers that with `invalid_client_secret`, and until
  // this existed the screen's only remedy was the one that could not fix it.
  const [newClient, setNewClient] = useState({ client_id: "", client_secret: "" });
  const [replacingClient, setReplacingClient] = useState(false);

  useEffect(() => setLabel(conn.label), [conn.label]);
  useEffect(() => setSince(conn.suggested_since), [conn.suggested_since]);

  const h = health(conn, check);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  // One rename reached two ways. Written as functions rather than twice inline
  // because a key handler that repeats the button's body is how the two drift:
  // the button carries `disabled={busy}`, and an Enter that skipped that guard
  // would post the rename again while the first one was still in flight.
  function saveRename() {
    if (busy) return;
    void run(async () => {
      await onRename(conn.connection_id, label.trim());
      setRenaming(false);
    });
  }

  // Restores the stored name, so re-opening the editor does not show the draft
  // that was explicitly abandoned.
  function cancelRename() {
    setLabel(conn.label);
    setRenaming(false);
  }

  return (
    <Bp className={`cx-card ${h.band}`.trim()}>
      <div className="cx-head">
        <div className="cx-name">
          {renaming ? (
            <>
              <TextField
                size="small"
                label="Connection name"
                autoFocus
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                // Enter saves, Escape cancels — the two presses somebody makes
                // in a one-field inline editor without reaching for the mouse.
                // Additive: both buttons stay, because a shortcut nobody can
                // see is not an affordance on its own, and this editor is
                // opened by people who have never used it before.
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); saveRename(); }
                  else if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
                }}
                sx={{ maxWidth: 260 }}
              />
              <Button
                variant="contained" size="small"
                disabled={busy}
                onClick={saveRename}
              >
                Save
              </Button>
              <Button variant="text" size="small" onClick={cancelRename}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <h4>{conn.label}</h4>
              {canManage && (
                <Button variant="text" size="small" onClick={() => setRenaming(true)}>
                  Rename
                </Button>
              )}
            </>
          )}
        </div>
        <div className="cx-headright">
          {/* Which system this book lives in. With every company on one
              connector this repeats itself, but this is the screen where the
              distinction is managed, so here it is information. */}
          <Chip size="small" variant="outlined"
                label={conn.connector_label ?? "Zoho Books"} />
          <StatusChip label={h.label} tone={h.tone} tip={h.tip} />
        </div>
      </div>

      {/* The check's answer, immediately under the chip that summarises it.
          Both of these used to sit below the four fact columns *and* below the
          rotate panel, so on a broken connection the words "Check failed" and
          the sentence saying what failed were separated by everything else on
          the card — and the scope gaps, which are the actionable half, were
          further down still. A diagnosis belongs next to the claim it
          explains. */}
      {(check?.detail ?? conn.last_check_detail) && (
        h.tone === "bad" ? (
          <Alert severity="error" sx={{ mt: 1 }}>
            {check?.detail ?? conn.last_check_detail}
          </Alert>
        ) : (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {check?.detail ?? conn.last_check_detail}
          </Typography>
        )
      )}

      {/* Only the gaps. A list of ten green ticks is noise on a healthy
          connection, and it buries the one line that needs acting on.

          The verdict was `li.bad` — red text and nothing else — for the
          refused scopes, which left a reader in greyscale unable to tell a
          refusal from a probe that could not reach an answer. Those are
          different facts and one of them is not a problem, so each row now
          leads with the word for its own state. */}
      {check?.scopes && check.scopes.some((s) => s.granted !== true) && (
        <Stack component="ul" spacing={0.5}
               sx={{ listStyle: "none", m: 0, mt: 1, p: 0 }}>
          {check.scopes.filter((s) => s.granted !== true).map((s) => {
            const blocking = check.missing_required_scopes?.includes(s.scope);
            return (
              <Stack component="li" key={s.scope} direction="row" spacing={1}
                     sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 0.5 }}>
                <StatusChip
                  dense
                  label={s.granted === false ? "refused" : "untested"}
                  tone={s.granted === false ? (blocking ? "bad" : "warn") : "neutral"}
                />
                <Typography variant="caption" component="code">{s.scope}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {s.granted === false
                    ? (blocking
                        ? "no sync can run until this is granted"
                        : "what it reads stays empty")
                    : "could not be tested, so this says nothing either way"}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      )}

      <dl className="cx-facts">
        <div>
          <dt>
            <Labelled tip={isZoho
              ? "The company id inside Zoho Books, from Settings → Organization Profile or the id in its URL. It is a request parameter on every call, not part of the login."
              : `The ${catalogEntry?.company_term ?? "company"} this connection reads, by the id ${conn.connector_label ?? "the source system"} knows it under.`}>
              {isZoho ? "Zoho company"
                      : `${conn.connector_label ?? "Source"} ${catalogEntry?.company_term ?? "company"}`}
            </Labelled>
          </dt>
          <dd className="mono">{conn.zoho_organization_id}</dd>
        </div>
        <div>
          <dt>
            <Labelled tip={isZoho
              ? "The OAuth grant used to reach it. A refresh token belongs to a Zoho user, not a company, so one grant usually serves every company that user can see — and rotating it once covers all of them."
              : "The stored sign-in used to reach it. One sign-in can serve several companies, and replacing it once covers all of them."}>
              Sign-in used
            </Labelled>
          </dt>
          <dd>
            {conn.credential_label}
            {conn.credential_rotated_at && (
              <Typography variant="caption" component="div" color="text.secondary">
                rotated {formatDate(conn.credential_rotated_at)}
              </Typography>
            )}
            {canManage && !rotating && (
              <Button variant="text" size="small" sx={{ mt: 0.5, pl: 0 }}
                      onClick={() => { setRotating(true); setRotateNote(null); }}>
                {isZoho ? "Replace the token" : "Replace the sign-in"}
              </Button>
            )}
          </dd>
        </div>
        {isZoho ? (
          <div>
            <dt>
              <Labelled tip="A refresh token issued in one Zoho data centre is rejected by every other. A mismatch here is the single most common setup failure.">
                Data centre
              </Labelled>
            </dt>
            <dd>
              {DC_PRESETS.find((p) => p.accounts_base === conn.accounts_base)?.label ??
                conn.accounts_base.replace("https://accounts.", "")}
            </dd>
          </div>
        ) : (
          conn.config && Object.keys(conn.config).length > 0 && (
            <div>
              <dt>
                <Labelled tip="The non-secret settings this connection was entered with. Secrets are encrypted at rest and never shown again.">
                  Settings
                </Labelled>
              </dt>
              <dd className="mono">
                {Object.entries(conn.config)
                  .map(([k, v]) => `${k}: ${v}`).join(" · ")}
              </dd>
            </div>
          )
        )}
        <div>
          <dt>
            <Labelled tip="When this connection was last asked whether it still works. A connection that has never been checked and one that failed an hour ago look identical without this.">
              Last checked
            </Labelled>
          </dt>
          <dd>{when(conn.last_checked_at)}</dd>
        </div>
      </dl>

      {rotating && !isZoho && catalogEntry && (
        <ErpRotateForm
          entry={catalogEntry}
          credentialLabel={conn.credential_label}
          busy={busy}
          onCancel={() => setRotating(false)}
          onSubmit={(values) => run(async () => {
            const note = await onErpRotate(conn.connection_id, values);
            setRotating(false);
            setRotateNote(note);
          })}
        />
      )}

      {rotating && isZoho && (
        <div className="cx-rotate">
          {/* The explanation is on the field rather than behind a tooltip. A
              rotation is done once, under pressure, by somebody who has just
              been told a connection is broken — the sentences that decide what
              they paste and whether they also replace the client pair should
              not be behind a "?" at that moment.

              Rotating from a grant code is the common case, not the exotic
              one: the console's answer to a revoked or expired token is a
              fresh code, and until this existed the screen asked for the one
              thing the console does not give you. */}
          <ZohoSecretField
            idPrefix={`cx-token-${conn.connection_id}`}
            kind={tokenKind}
            onKindChange={setTokenKind}
            value={newToken}
            onChange={setNewToken}
            maxWidth={420}
          />
          <Typography variant="caption" color="text.secondary"
                      component="p" sx={{ mt: 0.5, maxWidth: 420 }}>
            The client id and secret are left alone by default, because
            re-typing a secret that is already correct is how a working
            connection gets broken — but if this came from a different app,
            replace them too or Zoho refuses the pair.
          </Typography>
          {/* Reachable, not open. A token generated under a *different* Zoho
              app is the one failure a token-only rotation produces, and Zoho
              reports it as `invalid_client_secret` — which reads as "your
              secret is wrong" and sends people to change the data centre, the
              one setting that was right. */}
          {replacingClient ? (
            <>
              <TextField
                id={`cx-rcid-${conn.connection_id}`}
                label="Client ID"
                size="small"
                fullWidth
                value={newClient.client_id}
                onChange={(e) => setNewClient({ ...newClient, client_id: e.target.value })}
                autoComplete="off"
                helperText="Only when the token came from a different app in the Zoho API console. A client keeps one id across data centres but has a separate secret in each, so copy both from the console for this connection's data centre."
                slotProps={{ htmlInput: { spellCheck: false } }}
                sx={{ maxWidth: 420 }}
              />
              <TextField
                id={`cx-rcs-${conn.connection_id}`}
                label="Client secret"
                type="password"
                size="small"
                fullWidth
                value={newClient.client_secret}
                onChange={(e) => setNewClient({ ...newClient, client_secret: e.target.value })}
                autoComplete="off"
                sx={{ maxWidth: 420 }}
              />
            </>
          ) : (
            <Button variant="text" size="small"
                    onClick={() => setReplacingClient(true)}>
              The token came from a different app — replace the client id and secret too
            </Button>
          )}
          {/* Said before it happens, not after. One Zoho grant usually reaches
              every company its user can see, so rotating from here rotates
              those too — which is the point, and a surprise if unstated.

              An `Alert`, not the 11.5px grey `.st-help` it was: this is the
              most consequential sentence in the panel and it was set as the
              least prominent thing in it. */}
          <Alert severity="info">
            This replaces the sign-in for every company using{" "}
            <strong>{conn.credential_label}</strong>, not only this one. The
            connection is re-checked immediately afterwards.
          </Alert>
          <div className="cx-rotate-actions">
            <Button variant="contained" size="small"
                    disabled={busy || !newToken.trim() || (replacingClient
                      && !(newClient.client_id.trim() && newClient.client_secret.trim()))}
                    onClick={() => run(async () => {
                      // Sent only when both are filled in: a half-supplied pair
                      // would replace one side of a matched credential and
                      // break a connection that was merely being re-tokened.
                      const note = await onRotate(
                        conn.connection_id,
                        zohoSecret(tokenKind, newToken.trim()),
                        replacingClient
                          ? { client_id: newClient.client_id.trim(),
                              client_secret: newClient.client_secret.trim() }
                          : undefined);
                      setNewToken("");
                      setTokenKind("grant_code");
                      setNewClient({ client_id: "", client_secret: "" });
                      setReplacingClient(false);
                      setRotating(false);
                      setRotateNote(note);
                    })}>
              {busy ? "Rotating…" : "Rotate"}
            </Button>
            <Button variant="text" size="small"
                    onClick={() => {
                      setRotating(false);
                      setNewToken("");
                      setTokenKind("grant_code");
                      setNewClient({ client_id: "", client_secret: "" });
                      setReplacingClient(false);
                    }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {/* The server's own sentence about what else changed underneath. A
          success surface rather than grey prose: rotating one connection can
          move two others, and that is the part worth not missing. */}
      {rotateNote && (
        <Alert severity="success" sx={{ mt: 1 }}>{rotateNote}</Alert>
      )}

      {check?.visible_organizations && check.visible_organizations.length > 0 && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            This sign-in also reaches{" "}
            {check.visible_organizations.length === 1 ? "this company only" : "these companies"}:
          </Typography>
          <ChoiceList>
            {check.visible_organizations.map((o) => (
              <Box component="li" key={o.organization_id}>
                <CompanyChoice
                  name={o.name}
                  id={o.organization_id}
                  note={o.organization_id === conn.zoho_organization_id
                    ? "this one" : undefined}
                />
              </Box>
            ))}
          </ChoiceList>
        </>
      )}

      {/* The date belongs next to the button that uses it. It used to live in
          a panel further down the page, so pressing "Pull from this company"
          read as a pull with no date at all — and the date it silently used
          was whatever had been typed for a different company. */}
      {canSync && (
        <div className="cx-pull">
          {/* The date and the button go when a company is paused, because a
              paused company is not pulled. What it last brought in does not —
              that is history, and it is the thing you check before deciding
              whether to resume it. */}
          {conn.enabled && (
            <>
              {/* The one field on this screen whose explanation stays behind a
                  `Tip` rather than becoming `helperText`. The setup and
                  rotation fields are filled in once, under pressure, so their
                  prose is worth having permanently open; this one is on every
                  card and is read every time somebody pulls, and three copies
                  of a four-line paragraph standing open on three cards would
                  cost more than they explain. */}
              <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                <TextField
                  id={`cx-since-${conn.connection_id}`}
                  type="date"
                  size="small"
                  label="Read this company's books from"
                  value={since}
                  onChange={(e) => setSince(e.target.value)}
                  slotProps={{
                    inputLabel: { shrink: true },
                    htmlInput: { max: todayISO() },
                  }}
                  sx={{ width: 260 }}
                />
                <Tip
                  text={
                    <>
                      Every invoice and bill dated after this is fetched individually, so an
                      earlier date means a longer pull. The detectors compare the last 90 days
                      against the 90 before that and need six months of history before they
                      will call a decline.
                      {conn.covered_from && (
                        <> This company has been read from {conn.covered_from}{" "}
                          onwards. An earlier date reads the months in between for
                          the first time.</>
                      )}
                    </>
                  }
                />
              </Stack>
              {/* What this company actually holds, and what the chosen date will
                  cost. "Last pulled from 2025-01-01" answers neither: a nightly
                  pull can run for a year and still cover only the window the first
                  run asked for.

                  The second line is the one that matters. Widening the window used
                  to be a silent no-op — the run went green and fetched nothing —
                  so the screen now says, before the button is pressed, which of the
                  two pulls is about to happen. It is `body2` rather than the
                  caption the rest of this block uses for that reason. */}
              <Typography variant="body2" color="text.secondary">
                {conn.covered_from ? (
                  <>Read from <strong>{formatDate(conn.covered_from)}</strong> onwards
                    so far.{" "}
                    {since < conn.covered_from
                      ? <>This date reaches further back, so those extra months are
                          listed in full — slower than a repeat pull.</>
                      : <>This date is inside that, so the pull only picks up what
                          has changed.</>}
                  </>
                ) : (
                  <>Nothing has been read from this company yet, so this first pull
                    lists everything from the date above.</>
                )}
              </Typography>
              <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                <FormControlLabel
                  control={
                    <Checkbox
                      size="small"
                      checked={full}
                      onChange={(e) => setFull(e.target.checked)}
                    />
                  }
                  label="Re-read documents already held"
                  slotProps={{ typography: { variant: "body2" } }}
                />
                <Tip text="A repeat pull normally skips documents it already holds, which is what makes it fast. Tick this after granting a scope that was missing — the documents are there, but the fields that scope unlocks are not." />
              </Stack>
            </>
          )}
          {conn.last_sync ? (
            <Box>
              <Typography variant="caption" component="div" color="text.secondary">
                Last pulled {when(conn.last_sync.started_at)} from {conn.last_sync.since ?? "a rolling window"} ·{" "}
                {conn.last_sync.sales_txns} sales lines, {conn.last_sync.cost_records} cost records
                {/* The outcome word was rendered lower-cased in the same grey
                    as the counts beside it, which made "failed" read as another
                    statistic. It is a state, so it is a chip. */}
                {conn.last_sync.status !== "OK" && (
                  <>
                    {" · "}
                    <StatusChip
                      dense
                      label={conn.last_sync.status}
                      tone={conn.last_sync.status === "FAILED" ? "bad" : "warn"}
                    />
                  </>
                )}
              </Typography>
              {conn.last_sync.error && (
                <Alert severity="error" sx={{ mt: 1 }}>{conn.last_sync.error}</Alert>
              )}
            </Box>
          ) : (
            <Typography variant="caption" color="text.secondary">
              This company has never been pulled on its own.
            </Typography>
          )}
        </div>
      )}

      <div className="cx-actions">
        {canManage && (
          <Button
            variant="outlined" size="small"
            disabled={busy}
            onClick={() => run(async () => setCheck(await onCheck(conn.connection_id)))}
          >
            {busy ? "Checking…" : "Check"}
          </Button>
        )}
        {canSync && conn.enabled && (
          <Button
            variant="contained" size="small"
            disabled={syncing || syncBusy}
            onClick={() => onSync(conn.connection_id, since, full)}
          >
            {syncing ? "Pulling this one…" : syncBusy ? "Starting…" : "Pull from this company"}
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        {canManage && (
          <>
            <Button
              variant="text" size="small"
              disabled={busy}
              onClick={() => run(() => onToggle(conn.connection_id, !conn.enabled))}
            >
              {conn.enabled ? "Pause" : "Resume"}
            </Button>
            <Tip
              text={
                conn.enabled
                  ? "Pausing stops this company feeding the analysis and stops it being pulled, without touching the credentials or anything already synced. Reversible."
                  : "Resuming puts this company back into the pooled analysis. Its previously synced rows were never removed, so they return with it."
              }
            />
            <Button
              variant="text" size="small"
              disabled={busy}
              onClick={() => run(() => onDelete(conn.connection_id))}
            >
              Remove
            </Button>
            <Tip text="Drops the credentials for this company and stops pulling it. Rows already synced from it stay — they are facts about what was traded, and this is a decision about access, not about history." />
          </>
        )}
      </div>
    </Bp>
  );
}

/* ── registered ERP connectors (NetSuite, Business Central, Acumatica, P21,
      Sage) ─────────────────────────────────────────────────────────────────
   These forms render from the catalog's field specs, so this file never
   hardcodes what one system needs — connector number seven appears in the
   picker the day its backend module registers. Zoho keeps its richer bespoke
   flow (OAuth, data centres, scope probing) below. */

function FieldInput({
  field,
  idPrefix,
  value,
  onChange,
}: {
  field: ConnectorField;
  idPrefix: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const id = `${idPrefix}-${field.name}`;
  // The connector declares its own label, help and optionality, so all three
  // land where MUI already has a slot for them rather than in a hand-built
  // label / `.st-help` / `<input>` triple. `help` becomes permanent helper
  // text rather than a tooltip: it is the only documentation these fields
  // have, and a NetSuite consumer key entered wrongly fails at the next
  // screen rather than at this one.
  return (
    <TextField
      id={id}
      label={field.required ? field.label : `${field.label} (optional)`}
      size="small"
      fullWidth
      type={field.secret ? "password" : "text"}
      autoComplete="off"
      required={field.required}
      placeholder={field.placeholder || undefined}
      helperText={field.help || undefined}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      slotProps={{ htmlInput: { spellCheck: false } }}
      sx={{ mt: 1.5, maxWidth: 520 }}
    />
  );
}

function ErpRotateForm({
  entry,
  credentialLabel,
  busy,
  onCancel,
  onSubmit,
}: {
  entry: ConnectorCatalogEntry;
  credentialLabel: string;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (values: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const ready = entry.credential_fields.every(
    (f) => !f.required || (values[f.name] ?? "").trim() !== "");
  return (
    <div className="cx-rotate">
      <Typography variant="body2">
        Enter the fresh sign-in for {entry.label}. All of it — a half-replaced
        credential is how a working connection gets broken.
      </Typography>
      {entry.credential_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix="cx-erp-rotate"
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}
      {/* The same disclosure, on the same surface, as the Zoho rotation above:
          one sign-in reaches several companies in every connector, so both
          halves of this file say so the same way. */}
      <Alert severity="info" sx={{ mt: 1.5 }}>
        This replaces the sign-in for every company using{" "}
        <strong>{credentialLabel}</strong>, not only this one. The connection
        is re-checked immediately afterwards.
      </Alert>
      <div className="cx-rotate-actions">
        <Button variant="contained" size="small" disabled={busy || !ready}
                onClick={() => onSubmit(values)}>
          {busy ? "Rotating…" : "Rotate"}
        </Button>
        <Button variant="text" size="small" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function ErpConnectForm({
  entry,
  token,
  onAdded,
}: {
  entry: ConnectorCatalogEntry;
  token: string;
  onAdded: () => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConnectionCheck | null>(null);
  const [companies, setCompanies] = useState<ErpDiscoveredCompany[] | null>(null);

  // Discovery signs in with the credential half alone, so it is offered as
  // soon as that half is complete — the company field is what it exists to
  // fill in.
  const credentialReady = entry.credential_fields.every(
    (f) => !f.required || (values[f.name] ?? "").trim() !== "");

  async function discover() {
    setError(null);
    setCompanies(null);
    setBusy(true);
    try {
      const creds = Object.fromEntries(
        entry.credential_fields
          .map((f) => [f.name, (values[f.name] ?? "").trim()])
          .filter(([, v]) => v !== ""));
      const r = await papi.discoverErpCompanies(token, entry.key, creds);
      setCompanies(r.companies);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const entered = Object.fromEntries(
        Object.entries(values)
          .map(([k, v]) => [k, v.trim()])
          .filter(([, v]) => v !== ""));
      const r = await papi.addErpConnection(token, {
        connector: entry.key,
        values: entered,
        label: label.trim(),
      });
      setResult(r);
      setValues({});
      setLabel("");
      setCompanies(null);
      await onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {entry.credential_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix={`cx-erp-${entry.key}`}
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}

      {entry.can_discover && (
        <Box sx={{ mt: 1 }}>
          <Button type="button" variant="text" size="small"
                  disabled={busy || !credentialReady}
                  onClick={discover}>
            List the {entry.company_term} choices this sign-in can see
          </Button>
          {companies && (
            <ChoiceList>
              {companies.length === 0 && (
                <Typography component="li" variant="caption" color="text.secondary">
                  {entry.label} returned nothing for this sign-in.
                </Typography>
              )}
              {companies.map((c) => (
                <Box component="li" key={c.id}>
                  <CompanyChoice
                    name={c.name}
                    id={c.id}
                    onPick={() => {
                      setValues((s) => ({
                        ...s, [entry.external_id_field]: c.id }));
                      setLabel((l) => l || c.name);
                    }}
                  />
                </Box>
              ))}
            </ChoiceList>
          )}
        </Box>
      )}

      {entry.connection_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix={`cx-erp-${entry.key}`}
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}

      <TextField
        id={`cx-erp-${entry.key}-label`}
        label="Name it"
        size="small"
        fullWidth
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        helperText="What you call this entity — a name, not an id. A list of three ids is unreadable at the moment you need it."
        sx={{ mt: 1.5, maxWidth: 520 }}
      />

      {error && <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>}
      {result && (
        // `warning`, not `error`, when the check fails: the company *was*
        // added and the sentence says so. Rendering that in red sends somebody
        // back to add it a second time.
        <Alert severity={result.ok ? "success" : "warning"} sx={{ mt: 1.5 }}>
          {result.ok
            ? `Connected. ${result.detail ?? ""}`
            : `Added, but the check failed: ${result.detail ?? "no detail"}. ` +
              "Fix the values and use Replace the sign-in on its card."}
        </Alert>
      )}
      <Box sx={{ mt: 1.5 }}>
        <Button type="submit" variant="contained" size="small" disabled={busy}>
          {busy ? "Adding…" : `Add ${entry.company_term}`}
        </Button>
      </Box>
    </form>
  );
}

/* ── adding one ───────────────────────────────────────────────────────────── */

/**
 * Everything it takes to add a company, in a dialog behind one button.
 *
 * It was an always-open panel under the list, and it is the tallest thing on
 * this screen by a wide margin: seven systems in a tab strip, three ways to
 * sign in, a form per way, the housekeeping list of sign-ins that reach
 * nothing, and the full access list with its two copyable scope strings. On a
 * phone that is several screens of setup standing permanently between the
 * companies and everything below them — read once, when a company is added,
 * and scrolled past every other time.
 *
 * **Mounted whether or not it is open, and that is deliberate.** The
 * authorized path leaves for Zoho and comes back to a fresh page load carrying
 * `?oauth=…&handoff=…` in the hash; the effect that spends that handoff is
 * here, so a component that only existed while the dialog was open would
 * return from Zoho to nobody listening. It opens itself instead — which is
 * also the better screen, because what that effect leaves behind is a picker
 * asking which company the new sign-in should connect.
 */
function AddConnection({
  view,
  catalog,
  token,
  onAdded,
  open,
  onOpenChange,
}: {
  view: ConnectionsView;
  catalog: ConnectorCatalogEntry[];
  token: string;
  onAdded: () => Promise<void>;
  open: boolean;
  /** Two-way, because this component opens itself on return from Zoho. */
  onOpenChange: (open: boolean) => void;
}) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  // Which system the company lives in. Zoho first — it is the platform's
  // richest flow and the incumbent — then every registered connector.
  const [connector, setConnector] = useState("zoho");
  const entry = catalog.find((c) => c.key === connector);
  // Only the sign-ins for the system being added. A credential is a grant into
  // one specific system, so offering a Zoho sign-in while Acumatica is selected
  // offers a choice the server can only refuse — `add_connection` takes the
  // connector *from* the credential, and the mismatch is not expressible there.
  const signIns = useMemo(
    () => view.credentials.filter((c) => c.connector === connector),
    [view.credentials, connector]);
  const hasCredentials = signIns.length > 0;
  // Owned only: the server refuses to delete a grant another organization owns
  // and merely shared with this one, so offering the button would be offering
  // a 403.
  const unusedSignIns = useMemo(
    () => signIns.filter((c) => c.used_by === 0 && c.is_owner),
    [signIns]);
  const [mode, setMode] = useState<"existing" | "new" | "oauth">(
    hasCredentials ? "existing" : "new");
  // The data centre a Zoho grant belongs to. Not portable between estates: a
  // code issued by accounts.zoho.com is not redeemable at accounts.zoho.in, so
  // it is chosen before the redirect rather than guessed after it.
  const [dc, setDc] = useState("in");
  const [form, setForm] = useState(EMPTY_FORM);
  // Which stage of a Zoho grant the box below holds. Separate from
  // `form` because it is a question about the value, not one of the
  // values the server is sent.
  const [secretKind, setSecretKind] = useState<ZohoSecretKind>("grant_code");
  const [credentialId, setCredentialId] = useState(signIns[0]?.credential_id ?? "");
  const [orgs, setOrgs] = useState<ZohoVisibleOrg[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Adding the *first* company creates the first sign-in, and the second
  // company should then reuse it — that is the whole point of separating the
  // two. Left alone, this form stayed on the fresh-secrets tab with a stale
  // empty credential id, so adding a second company either did nothing or was
  // rejected for secrets it was never asked for.
  const hadCredentials = useRef(hasCredentials);
  useEffect(() => {
    if (!hasCredentials) {
      setMode("new");
      setCredentialId("");
      hadCredentials.current = false;
      return;
    }
    if (!hadCredentials.current) {
      // First sign-in just arrived: switch to reuse and forget the secrets.
      setMode("existing");
      setForm(EMPTY_FORM);
      hadCredentials.current = true;
    }
    if (!signIns.some((c) => c.credential_id === credentialId)) {
      setCredentialId(signIns[0].credential_id);
    }
  }, [hasCredentials, signIns, credentialId]);


  // Coming back from Zoho. The callback redirects to `/#/data?oauth=…`, and
  // under a hash router the query lives inside the hash — `window.location
  // .search` is empty here, which is the kind of thing that silently returns
  // "no handoff" for ever.
  useEffect(() => {
    const hash = window.location.hash;
    const q = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
    const params = new URLSearchParams(q);
    const outcome = params.get("oauth");
    if (!outcome) return;

    // Clear it before doing anything else: the handoff is single-use, and a
    // URL that still carries it is one a reload tries to spend again.
    const handoff = params.get("handoff");
    const reason = params.get("reason");
    window.history.replaceState(null, "", hash.split("?")[0] || "#/data");

    if (outcome !== "ok" || !handoff) {
      setError(reason || "The authorization did not complete.");
      onOpenChange(true);   // the only surface this error has
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await papi.claimZohoAuthorization(token, handoff);
        if (cancelled) return;
        // An authorization's only product is a sign-in, so from here this is
        // the ordinary reuse path: pick the company, connect it.
        setCredentialId(r.credential_id);
        setMode("existing");
        setError(null);
        await onAdded();
        // The authorization produced a sign-in and nothing else. Somebody has
        // to say which company it should connect, so the dialog opens itself
        // on that question rather than leaving a finished round trip looking
        // like nothing happened.
        onOpenChange(true);
      } catch (e) {
        if (!cancelled) { setError((e as Error).message); onOpenChange(true); }
      }
    })();
    return () => { cancelled = true; };
    // Once, on mount: the URL has been cleared by then, so re-running would
    // find nothing and a changing `token` must not re-spend a used handoff.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startAuthorization() {
    setBusy(true);
    setError(null);
    try {
      const r = await papi.authorizeZoho(token, dc);
      // A full-page navigation, not `window.open`. The previous implementation
      // opened a popup and then listened for nothing, so the callback's answer
      // landed in a window the opener could not read — and popups are blocked
      // by default in enough browsers, and on enough phones, that the flow
      // would have been unreachable for many people even had it worked.
      window.location.assign(r.authorization_url);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function listCompanies() {
    setError(null);
    setOrgs(null);
    try {
      const r = await papi.credentialOrganizations(token, credentialId);
      setOrgs(r.visible_organizations);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  /** Remove a sign-in that no longer reaches a company.
   *
   *  The counterpart to the retention that leaves it here: removing a company
   *  deliberately keeps its sign-in, and without this the ones left over are
   *  permanent — offered in the picker above for ever, indistinguishable from
   *  a live grant except by a count nobody reads as an instruction.
   */
  async function removeSignIn(c: ConnectionCredential) {
    if (
      !window.confirm(
        `Remove the sign-in ${c.client_id.slice(0, 18)}…?\n\n` +
          "It reaches no company here, so nothing stops being pulled and nothing " +
          "already synced is touched. The secret is deleted — connecting through " +
          "this sign-in again means entering it again.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await papi.removeCredential(token, c.credential_id);
      setOrgs(null);
      await onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: NewConnectionInput =
        mode === "existing"
          ? {
              credential_id: credentialId,
              zoho_organization_id: form.zoho_organization_id.trim(),
              label: form.label.trim(),
            }
          : {
              zoho_organization_id: form.zoho_organization_id.trim(),
              label: form.label.trim(),
              client_id: form.client_id.trim(),
              client_secret: form.client_secret.trim(),
              // `grant_code` or `refresh_token`, never both — the server
              // refuses a body carrying the pair, because a request that
              // supplies two credentials has not said which one it means.
              ...zohoSecret(secretKind, form.secret.trim()),
              accounts_base: form.accounts_base,
              api_base: form.api_base,
            };
      await papi.addConnection(token, body);
      setForm(EMPTY_FORM);
      setSecretKind("grant_code");
      setOrgs(null);
      await onAdded();
      // Closed only once the list behind it has been reloaded, so what the
      // dialog uncovers is the company that was just added rather than the
      // empty state it replaced.
      onOpenChange(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      // Not dismissable mid-request: a click on the backdrop while the add is
      // in flight would hide the only place its error can be read.
      onClose={() => { if (!busy) onOpenChange(false); }}
      fullWidth
      maxWidth="md"
      // Full screen on a phone. This is the longest form in the product — seven
      // systems, three ways to sign in, and the whole access list — and a
      // centred dialog at 390px wide is a letterbox with its own scrollbar
      // inside the page's.
      fullScreen={narrow}
      aria-labelledby="cx-add-title"
    >
      <DialogTitle id="cx-add-title">
        Add a company{" "}
        <Tip text="Each company you add is one set of books in its own system — a Zoho Books organization, a NetSuite account, a Business Central company. Adding a second does not create a second tenant here: the rows land together in this organization's analysis." />
      </DialogTitle>
      <DialogContent dividers>
      {/* At the top, and once. It used to sit immediately above each mode's
          own button, which is the bottom of a long scroll — so a refusal
          arrived off-screen, under content the reader had already passed. The
          button is in `DialogActions` now and cannot carry it. */}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {/* One strip, every system, Zoho included — it is a row in the catalog
          now rather than a button written out here, so the tab and the access
          list below it cannot describe different systems.

          A `ToggleButtonGroup`, per ui-standards §5: these were `<button>`s
          with a hand-rolled `.cx-tab` pill, an `aria-pressed` written out by
          hand and a selected state that existed only as an attribute selector
          in the stylesheet. The MUI control is the same semantics with the
          focus ring, the hit target and the selected ink coming from the
          theme. */}
      {catalog.length > 1 && (
        <ToggleButtonGroup
          exclusive
          size="small"
          value={connector}
          onChange={(_e, v) => { if (v) setConnector(v as string); }}
          aria-label="Which system"
          sx={{ mb: 1.5, flexWrap: "wrap" }}
        >
          {catalog.map((c) => (
            <ToggleButton key={c.key} value={c.key}>{c.label}</ToggleButton>
          ))}
        </ToggleButtonGroup>
      )}

      {entry?.setup_note && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {entry.setup_note}
        </Typography>
      )}

      {connector !== "zoho" && entry && (
        <ErpConnectForm entry={entry} token={token} onAdded={onAdded} />
      )}

      {connector === "zoho" && (
      <>
      {/* The second strip, and it never carried the `aria-label` the first one
          did — so a screen reader announced three unrelated pressed buttons
          with nothing saying what they were three ways of doing. */}
      <ToggleButtonGroup
        exclusive
        size="small"
        value={mode}
        onChange={(_e, v) => {
          if (v) setMode(v as "existing" | "new" | "oauth");
        }}
        aria-label="How to sign in to Zoho"
        sx={{ mb: 1.5, flexWrap: "wrap" }}
      >
        {hasCredentials && (
          <ToggleButton value="existing">Use a sign-in already on file</ToggleButton>
        )}
        <ToggleButton value="new">Enter credentials manually</ToggleButton>
        {entry?.can_authorize && (
          <ToggleButton value="oauth">Sign in with Zoho</ToggleButton>
        )}
      </ToggleButtonGroup>

      {mode === "oauth" ? (
        <div>
          <Typography variant="body2" color="text.secondary">
            Sign in at Zoho and grant access — nothing to generate, and no secret
            to paste. It produces a sign-in on this screen, exactly like a
            manually-entered one; you then choose which company to connect.
          </Typography>
          <TextField
            id="cx-oauth-dc"
            select
            fullWidth
            size="small"
            label="Where the books are kept"
            sx={{ mt: 1.5 }}
            value={dc}
            onChange={(e) => setDc(e.target.value)}
            helperText={
              "A Zoho account lives in one data centre and a grant is not " +
              "portable between them. Getting this wrong is the single most " +
              "common setup failure."
            }
          >
            {DC_PRESETS.map((d) => (
              <MenuItem key={d.code} value={d.code}>{d.label}</MenuItem>
            ))}
          </TextField>
        </div>
      ) : (
      // Named, because the button that submits it is outside it — in
      // `DialogActions`, where a dialog's primary action belongs. `form=` on
      // the button is what still makes it a submit, so native validation and
      // the Enter key behave exactly as they did inline.
      <form id="cx-add-form" onSubmit={submit}>
        {mode === "existing" ? (
          <>
            <Typography variant="body2" color="text.secondary">
              The normal path for a second or third company. One Zoho sign-in already
              reaches every company that user can see, so re-entering the same secret
              would only create a copy for a future rotation to miss.
            </Typography>
            <TextField
              id="cx-cred"
              select
              fullWidth
              size="small"
              label="Zoho sign-in"
              sx={{ mt: 1.5 }}
              value={credentialId}
              onChange={(e) => {
                setCredentialId(e.target.value);
                setOrgs(null);
              }}
            >
              {signIns.map((c) => (
                <MenuItem key={c.credential_id} value={c.credential_id}>
                  {c.label} · {c.client_id.slice(0, 18)}… · used by {c.used_by}{" "}
                  {c.used_by === 1 ? "company" : "companies"}
                </MenuItem>
              ))}
            </TextField>

            <Button
              type="button"
              variant="text" size="small"
              sx={{ mt: 1 }}
              disabled={!credentialId}
              onClick={listCompanies}
            >
              Show the companies this reaches
            </Button>
            {orgs && (
              <ChoiceList>
                {orgs.length === 0 && (
                  <Typography component="li" variant="caption" color="text.secondary">
                    Zoho returned no companies for this sign-in.
                  </Typography>
                )}
                {orgs.map((o) => (
                  <Box component="li" key={o.organization_id}>
                    <CompanyChoice
                      name={o.name}
                      id={o.organization_id}
                      disabled={o.already_connected}
                      note={o.already_connected ? "already added" : undefined}
                      onPick={() =>
                        setForm((f) => ({
                          ...f,
                          zoho_organization_id: o.organization_id,
                          label: f.label || o.name,
                        }))
                      }
                    />
                  </Box>
                ))}
              </ChoiceList>
            )}
          </>
        ) : (
          <>
            {/* The same fact as the OAuth tab's data-centre field, said the
                same way. It was a `<label>` + `Labelled` tooltip here and a
                `helperText` three hundred lines up — two answers to one
                question, in one file, about the single most common setup
                failure this screen has. */}
            <TextField
              id="cx-dc"
              select
              fullWidth
              size="small"
              label="Data centre"
              sx={{ mt: 1.5, maxWidth: 520 }}
              value={form.accounts_base}
              onChange={(e) => {
                const p = DC_PRESETS.find((d) => d.accounts_base === e.target.value);
                if (p) setForm({ ...form, accounts_base: p.accounts_base, api_base: p.api_base });
              }}
              helperText={
                "Must match the account the token was issued from. A token from "
                + "accounts.zoho.in is rejected by accounts.zoho.com with an error "
                + "that reads like a bad secret."
              }
            >
              {DC_PRESETS.map((p) => (
                <MenuItem key={p.accounts_base} value={p.accounts_base}>
                  {p.label}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              id="cx-client-id"
              label="Client ID"
              size="small"
              fullWidth
              required
              value={form.client_id}
              onChange={(e) => setForm({ ...form, client_id: e.target.value })}
              sx={{ mt: 1.5, maxWidth: 520 }}
            />

            <TextField
              id="cx-client-secret"
              label="Client secret"
              type="password"
              size="small"
              fullWidth
              required
              value={form.client_secret}
              onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
              sx={{ mt: 1.5, maxWidth: 520 }}
            />

            <ZohoSecretField
              idPrefix="cx-refresh"
              kind={secretKind}
              onKindChange={setSecretKind}
              value={form.secret}
              onChange={(v) => setForm({ ...form, secret: v })}
            />
            {/* Kept on the screen rather than only in the field's helper: a
                grant missing a scope authenticates perfectly well and then
                returns nothing, which is the failure that looks like an empty
                company rather than a broken sign-in. */}
            <Typography variant="caption" color="text.secondary"
                        component="p" sx={{ mt: 0.5, maxWidth: 520 }}>
              Generate it with the scopes listed below — one missing a scope
              signs in and then returns nothing.
            </Typography>
          </>
        )}

        <TextField
          id="cx-zoho-org"
          label="Zoho Books organization id"
          size="small"
          fullWidth
          required
          value={form.zoho_organization_id}
          onChange={(e) => setForm({ ...form, zoho_organization_id: e.target.value })}
          helperText="Settings → Organization Profile in Zoho Books, or the id in its URL. Not the same as this platform's organization."
          sx={{ mt: 1.5, maxWidth: 520 }}
        />

        <TextField
          id="cx-label"
          label="Name it"
          size="small"
          fullWidth
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          helperText={'What you call this entity — "4U Precision", not "60036630626". A list of three numbers is unreadable at the moment you need it.'}
          sx={{ mt: 1.5, maxWidth: 520 }}
        />

      </form>
      )}
      </>
      )}

      {/* The sign-ins left behind by companies that have been removed. Kept
          on purpose — see `clear_zoho_connection` — but kept without a way out
          they accumulate, and the picker above offers every one of them as
          though it still reached something.

          Below the form rather than under the picker it refers to: between the
          picker and the organization-id field it split the add-a-company flow
          in half, and the rule above it read as the end of a section that had
          not ended. */}
      {unusedSignIns.length > 0 && (
        <Box className="cx-unused">
          <Typography variant="body2" color="text.secondary">
            {unusedSignIns.length === 1
              ? "One sign-in on file reaches no company."
              : `${unusedSignIns.length} sign-ins on file reach no company.`}{" "}
            Removing a company leaves its sign-in behind so that reconnecting
            does not mean re-entering a secret. One you are finished with can go.
          </Typography>
          <Stack component="ul" spacing={0.5}
                 sx={{ listStyle: "none", m: 0, mt: 1, p: 0 }}>
            {unusedSignIns.map((c) => (
              <Stack component="li" key={c.credential_id} direction="row" spacing={1}
                     sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 0.5 }}>
                <Typography variant="caption" component="span" className="mono"
                            color="text.secondary">
                  {c.client_id.slice(0, 18)}…
                </Typography>
                <Button
                  type="button"
                  variant="text" size="small"
                  disabled={busy}
                  onClick={() => removeSignIn(c)}
                >
                  Remove
                </Button>
                <Tip text="Deletes the stored secret. Nothing is connected through this sign-in, so no company stops being pulled and nothing already synced is affected." />
              </Stack>
            ))}
          </Stack>
        </Box>
      )}

      {entry && <Access entry={entry} />}
      </DialogContent>
      <DialogActions>
        <Button type="button" onClick={() => onOpenChange(false)} disabled={busy}>
          Cancel
        </Button>
        {/* One primary action, and which one it is depends on the mode — the
            authorized path leaves for Zoho rather than submitting anything, so
            it cannot be the same button wearing a different word. */}
        {mode === "oauth" ? (
          <Button type="button" variant="contained" disabled={busy}
                  onClick={startAuthorization}>
            {busy ? "Redirecting…" : "Continue to Zoho"}
          </Button>
        ) : (
          <Button type="submit" form="cx-add-form" variant="contained"
                  disabled={busy}>
            {busy ? "Adding…" : "Add company"}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

/* ── what the selected system must let it read ─────────────────────────────── */

/**
 * The access requirements of *the connector the tabs above have selected*.
 *
 * This lived in its own panel below the form and rendered Zoho's ten scope
 * strings whichever system was picked — so choosing NetSuite left a set of
 * `ZohoBooks.*.READ` strings on screen under the heading "Scopes this platform
 * needs", naming grants that do not exist in NetSuite and omitting every one
 * that does. Two panels about one decision, and only one of them was listening
 * to the tabs.
 *
 * It is inside the add-a-company panel now, under the connector's own name, for
 * that reason: the thing that changes the form has to change this too, and the
 * cheapest way to guarantee it is to leave them nowhere to disagree.
 */
/** One pasteable scope string, with its own Copy button and its own label.
 *
 * A component rather than the markup twice: the connector offers two strings —
 * everything, and the minimum that still runs a sync — and the "Copied" flag
 * belongs to whichever button was actually pressed. One shared flag would light
 * up under both.
 */
function ScopeString({
  value,
  label,
  connectorKey,
}: {
  value: string;
  label: string;
  connectorKey: string;
}) {
  const [copied, setCopied] = useState(false);
  // Reset when the tabs move: "Copied" left standing under a different
  // system's list claims something that was never put on the clipboard.
  useEffect(() => setCopied(false), [connectorKey]);
  if (!value) return null;
  return (
    <div className="cx-scopestring">
      <Typography variant="caption" component="span" color="text.secondary">
        {label}
      </Typography>
      <code>{value}</code>
      <Button
        variant="text" size="small"
        onClick={() => {
          navigator.clipboard?.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function Access({ entry }: { entry: ConnectorCatalogEntry }) {
  if (entry.permissions.length === 0) return null;
  return (
    <div className="cx-access">
      {/* `subtitle2` on a real `<h3>`, which is what `.section-h` was drawing by
          hand — the theme already has that rung (12px, uppercase, heading face)
          and now it comes from there rather than from a stylesheet rule that
          has to be kept in step with it. Deliberately *not* `kit.SectionHeader`:
          that renders `variant="h3"` at 21px, which would be louder than the
          "Add a company" heading this section sits underneath. Same reasoning
          as the note on `Pane` in CatalogSources. */}
      <Stack direction="row" spacing={1}
             sx={{ mt: 1.75, mb: 1, alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <Typography variant="subtitle2" component="h3" color="text.secondary">
          What {entry.label} must let it {entry.can_write_quotes ? "read and write" : "read"}
        </Typography>
        <Tip text="Access is granted per grant, and a sign-in missing one still authenticates — the endpoint it needed refuses, and the sync reports zero rows of that kind with nothing obviously wrong. Granting fewer does not fail loudly; it fails quietly, later." />
        <Box sx={{ flex: 1 }} />
        {/* Stated on the screen where access is granted, because this is the
            moment an owner decides how much to hand over — and "we can create
            records in your ledger" is the part of that decision they should
            not have to infer from a scope name. A Chip rather than coloured
            text, per the UI standard. */}
        <Chip
          size="small"
          variant="outlined"
          color={entry.can_write_quotes ? "primary" : "default"}
          label={entry.can_write_quotes
            ? "Can create quotes here"
            : "Read-only — quotes cannot be sent here"}
        />
      </Stack>
      {entry.permission_note && (
        <Typography variant="body2" color="text.secondary">
          {entry.permission_note}
        </Typography>
      )}
      <Box sx={{ overflowX: "auto" }}>
        {/* A fact list, not a business table: its length is set by this
            connector, not by the size of the business. */}
        <table className="cx-scopes">
          <tbody>
            {entry.permissions.map((perm) => (
              <tr key={perm.name}>
                <td className="mono">{perm.name}</td>
                <td>{perm.why}</td>
                <td className="req">{perm.required ? "required" : "optional"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
      {/* Only where the system takes one. Every ERP in the registry is clicked
          rather than typed, and an empty box to copy would be worse than none.
          The full set leads, and the minimum sits under it — that order is the
          recommendation, and it is the one the note above argues for. */}
      <ScopeString
        value={entry.permission_string}
        label="Everything this platform reads — recommended"
        connectorKey={entry.key}
      />
      <ScopeString
        value={entry.permission_string_minimum}
        label="The least that still runs a sync"
        connectorKey={entry.key}
      />
    </div>
  );
}

/* ── the panel ────────────────────────────────────────────────────────────── */

export function ConnectionsPanel({
  session,
  canSync,
  onSync,
  busyConnections,
  everyCompanyBusy = false,
  starting,
}: {
  session: PlatformSession;
  canSync: boolean;
  /** Runs a pull for one company, from the date that company's card chose. */
  onSync: (connectionId: string, since: string, full: boolean) => Promise<void>;
  /** Companies with a pull in flight, from the server. Each card gates on its
   *  own membership here rather than on a single organization-wide flag. */
  busyConnections: string[];
  /** An all-companies run is in flight, which covers every card here. It
   *  reports itself with a NULL connection, so it is never in
   *  `busyConnections` — this is how a card knows it is being pulled anyway. */
  everyCompanyBusy?: boolean;
  /** A start request has been posted and not yet answered. */
  starting: boolean;
}) {
  const [view, setView] = useState<ConnectionsView | null>(null);
  const [catalog, setCatalog] = useState<ConnectorCatalogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Whether the add-a-company dialog is showing. Here rather than inside it,
  // because the button that opens it is in this screen's header and the
  // dialog closes itself once the list behind it has reloaded.
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await papi.listConnections(session.token));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  // Once, not per load: the catalog is the deployment's connector list and
  // changes with releases, not with clicks. Its failure degrades to a
  // Zoho-only add form rather than taking the whole panel down.
  useEffect(() => {
    papi.connectorCatalog(session.token)
      .then((r) => setCatalog(r.connectors))
      .catch(() => setCatalog([]));
  }, [session.token]);

  async function check(id: string): Promise<ConnectionCheck | null> {
    try {
      const r = await papi.checkConnection(session.token, id);
      await load();
      return r;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }

  async function rename(id: string, label: string) {
    try {
      await papi.editConnection(session.token, id, { label });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggle(id: string, enabled: boolean) {
    try {
      await papi.editConnection(session.token, id, { enabled });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  /** Replace the Zoho grant this connection signs in with.
   *
   *  One call, on the connection, because that is the thing somebody is
   *  looking at when they decide to rotate. The server names every other
   *  company that changed underneath, and that sentence is what comes back. */
  async function rotate(
    id: string, secret: ZohoSecret,
    client?: { client_id: string; client_secret: string },
  ): Promise<string> {
    setError(null);
    const r = await papi.rotateConnectionToken(session.token, id, secret, client);
    await load();
    return String(r.note ?? "Rotated.");
  }

  /** The registered-connector sibling: the whole credential, from the field
   *  list its catalog entry declares. Same disclosure about shared grants. */
  async function erpRotate(id: string, values: Record<string, string>): Promise<string> {
    setError(null);
    const r = await papi.rotateErpConnection(session.token, id, values);
    await load();
    return String(r.note ?? "Rotated.");
  }

  async function remove(id: string) {
    const c = view?.connections.find((x) => x.connection_id === id);
    if (
      !window.confirm(
        `Remove "${c?.label ?? id}"?\n\n` +
          "The credentials for this company are dropped and it stops being pulled. " +
          "Invoices, bills and margins already synced from it stay — they are facts " +
          "about what was traded, and disconnecting is about access, not history.\n\n" +
          "They will keep feeding this organization's totals until you delete the " +
          "organization itself.",
      )
    ) {
      return;
    }
    try {
      const r = await papi.removeConnection(session.token, id);
      setNote(r.note);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!view) {
    return error ? (
      <ErrorState title="Could not read the connections" error={error} onRetry={load} />
    ) : (
      <LoadingState rows={1} height={90} />
    );
  }

  const enabled = view.connections.filter((c) => c.enabled).length;

  return (
    <>
      {/* `kit.SectionHeader`, per §10 — this was a `<div className=
          "dp-screen-head">` wrapping a hand-written `<h2>`/`<p>` pair, and
          `.dp-screen-head` has no rule in the stylesheet at all, so the layout
          it appeared to carry was doing nothing. `level="section"` because the
          page title above this one belongs to `DataScreen`. */}
      <SectionHeader
        level="section"
        title="Companies"
        tip="One company here is one Zoho Books organization. Add as many as the business keeps books for — the rows land together in this organization's analysis."
        sub={[
          view.connections.length === 0
            ? "None connected yet"
            : `${view.connections.length} connected · ${enabled} feeding the analysis`,
          view.source_mode !== "api" ? "running against the offline sample source" : null,
        ]
          .filter(Boolean)
          .join(" · ")}
        actions={view.can_manage ? (
          // The whole of adding a company is behind this now. It carries its
          // words as well as the `+`: a bare icon button beside a heading is a
          // guess about what it adds, and this screen also removes, rotates
          // and syncs.
          <Button variant="contained" size="small" startIcon={<AddOutlined />}
                  onClick={() => setAdding(true)}>
            Add company
          </Button>
        ) : undefined}
      />

      {/* `warning`, which is the severity `.cx-pool` was already drawing with
          `--caution-bg`/`--warn` by hand. Pooling is not an error and not a
          neutral note: it is the consequence somebody has to accept before
          connecting a second book. */}
      {view.connections.length > 1 && (
        <Alert severity="warning" sx={{ mb: 2 }}>{view.pooling_note}</Alert>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {note && (
        // The removal receipt. It was the caution panel above with three
        // colours overridden inline to make it green — which is a severity,
        // and severity is what `Alert` is for. "Dismiss" is kept as its own
        // button rather than becoming MUI's ✕, because the word is what says
        // the note can be let go without doing anything else.
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={() => setNote(null)}>
              Dismiss
            </Button>
          }
        >
          {note}
        </Alert>
      )}

      <div className="cx-list">
        {view.connections.map((c) => (
          <ConnectionCard
            key={c.connection_id}
            conn={c}
            catalogEntry={catalog.find((e) => e.key === c.connector)}
            canManage={view.can_manage}
            canSync={canSync}
            onCheck={check}
            onRotate={rotate}
            onErpRotate={erpRotate}
            onRename={rename}
            onToggle={toggle}
            onDelete={remove}
            onSync={async (id, since, full) => {
              await onSync(id, since, full);
              await load();   // last pulled / suggested date move with the run
            }}
            syncing={busyConnections.includes(c.connection_id) || everyCompanyBusy}
            syncBusy={starting}
          />
        ))}
        {/* `kit.EmptyState`, which separates the fact from the consequence —
            the two used to run together in one sentence on a bare panel, and
            the consequence is the half that decides whether somebody acts. */}
        {view.connections.length === 0 && (
          <EmptyState
            title="No company is connected"
            reason={view.can_manage
              ? "Every screen is showing sample data, or nothing at all, until one is connected. Add company, above, adds the first."
              : "Ask an owner to add one."}
          />
        )}
      </div>

      {/* Rendered whether or not it is open — see `AddConnection`: the handoff
          Zoho redirects back with is spent by an effect inside it, and a
          dialog mounted only while open would come back from an authorization
          to nobody listening. */}
      {view.can_manage && (
        <AddConnection view={view} catalog={catalog} token={session.token}
                       onAdded={load}
                       open={adding} onOpenChange={setAdding} />
      )}
    </>
  );
}
