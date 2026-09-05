import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import { useCallback, useEffect, useState } from "react";
import { since } from "../when";
import { papi } from "./api";
import {
  EmptyState, ErrorState, FilterPanel, LoadingState, SectionHeader, StatusChip,
} from "./kit";
import type { ConnectorRecord, EntityKind, Identity, IdentityCoverage, IdentityPolicy, IdentitySuggestion } from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Identity management — which connector records describe the same entity.
 *
 * The platform reads several ERPs at once. The same customer sits in Zoho and
 * in ERPNext under different ids; the same item under different codes. Nothing
 * is merged: each connector stays the source of truth for its own records, and
 * an identity only says which records belong together.
 *
 * That distinction is the whole screen. Every record is shown with the
 * connector that supplied it, its own id, and its own values — so a figure can
 * always be traced back to the system it came from. Reviewing a suggestion
 * means comparing two rows side by side and deciding, which is why the evidence
 * ("GSTIN 29ABCDE1234F1Z5") is shown rather than a confidence score: the value
 * is arguable, a number is not.
 */

const KINDS: { key: EntityKind; label: string; hint: string }[] = [
  { key: "customers", label: "Customers",
    hint: "Matched on GSTIN — a registration issued by the government, not by any ERP." },
  { key: "items", label: "Items",
    hint: "Matched on SKU, after punctuation is normalised away." },
];

// "never", not "—": a connector that has never been checked is a different
// state from a missing field, and only one of them is a problem.
function when(iso: string | null | undefined): string {
  return iso ? since(iso) : "never";
}

/** One connector's record, shown as that connector holds it. */
function RecordRow({
  r,
  kind,
  canManage,
  onUnlink,
  lonely,
}: {
  r: ConnectorRecord;
  kind: EntityKind;
  canManage: boolean;
  onUnlink?: (recordId: string) => void;
  /** The only record on its identity — unlinking it would achieve nothing. */
  lonely: boolean;
}) {
  return (
    <tr>
      {/* The company, not just the connector. This column said "zoho" on every
          row, which is the one thing every row has in common when a business
          reads three Zoho books — so the screen whose entire purpose is telling
          connector records apart could not tell them apart. The connector still
          shows, second, because it is what differs once a Tally book lands. */}
      <td>
        <span className="id-connector">
          {r.origin?.company || r.connector}
        </span>
        {r.origin?.company && (
          <span className="id-connector-sub">{r.origin.connector_short}</span>
        )}
      </td>
      <td className="mono">{r.external_id}</td>
      <td>{kind === "customers" ? r.name : r.description}</td>
      <td className="mono">
        {kind === "customers" ? r.gstin || "—" : r.sku || "—"}
      </td>
      <td className="text-muted">{when(r.last_synced_at)}</td>
      {canManage && (
        <td className="id-rowactions">
          {onUnlink && !lonely && (
            <Button variant="text" size="small" onClick={() => onUnlink(r.record_id)}>
              Unlink
            </Button>
          )}
        </td>
      )}
    </tr>
  );
}

function IdentityCard({
  identity,
  kind,
  canManage,
  onUnlink,
  onRelabel,
}: {
  identity: Identity;
  kind: EntityKind;
  canManage: boolean;
  onUnlink: (recordId: string) => void;
  onRelabel: (identityId: string, label: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(identity.label ?? "");
  const linked = identity.connector_count > 1;

  // One rename reached two ways, written once. `ConnectionsPanel`'s connection
  // card carries the identical inline editor and gained the identical pair in
  // the same change — two screens with one rename field must not answer Enter
  // differently.
  function saveLabel() {
    onRelabel(identity.identity_id, label);
    setEditing(false);
  }

  // Restores the stored name rather than leaving the abandoned draft in the
  // box. Without it, cancelling and re-opening shows the text that was
  // explicitly not saved, which reads as though it had been.
  function cancelLabel() {
    setLabel(identity.label ?? "");
    setEditing(false);
  }

  return (
    <Bp className={`id-card ${linked ? "linked" : ""}`}>
      <div className="id-head">
        <div className="id-name">
          {editing ? (
            <>
              {/* A `TextField`, per ui-standards §8 — this was a bare
                  `<input className="input">` carrying an `aria-label` because
                  it had no visible one. The connection card opposite already
                  uses the MUI control for the same field. */}
              <TextField
                size="small"
                label="Identity name"
                autoFocus
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                // Enter saves, Escape cancels — the two presses somebody makes
                // in a one-field inline editor without reaching for the mouse.
                // Additive: both buttons stay, because a shortcut nobody can
                // see is not an affordance on its own.
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); saveLabel(); }
                  else if (e.key === "Escape") { e.preventDefault(); cancelLabel(); }
                }}
                sx={{ maxWidth: 260 }}
              />
              <Button variant="contained" size="small" onClick={saveLabel}>
                Save
              </Button>
              <Button variant="text" size="small" onClick={cancelLabel}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <h4>{identity.display_name || "(unnamed)"}</h4>
              {!identity.label && (
                <Tip text="Taken from the linked records rather than stored, so no single connector becomes the authority on what this entity is called. Rename it to fix a name of your own." />
              )}
              {canManage && (
                <Button variant="text" size="small" onClick={() => setEditing(true)}>
                  Rename
                </Button>
              )}
            </>
          )}
        </div>
        <span className={`id-badge ${linked ? "linked" : "single"}`}>
          {linked
            ? `${identity.connector_count} connectors`
            : `${identity.record_count} record${identity.record_count === 1 ? "" : "s"}`}
        </span>
      </div>

      <Box sx={{ overflowX: "auto" }}>
        <table className="id-table">
          <thead>
            <tr>
              <th>
                <Labelled tip="Which connected company this row came from, and which system it came through. It is never rewritten from another record's values — that is what makes a number on any screen traceable.">
                  Source
                </Labelled>
              </th>
              <th>External ID</th>
              <th>{kind === "customers" ? "Customer name" : "Description"}</th>
              <th>{kind === "customers" ? "GSTIN" : "SKU"}</th>
              <th>Last synced</th>
              {canManage && <th></th>}
            </tr>
          </thead>
          <tbody>
            {identity.records.map((r) => (
              <RecordRow key={r.record_id} r={r} kind={kind} canManage={canManage}
                         onUnlink={onUnlink} lonely={identity.records.length === 1} />
            ))}
          </tbody>
        </table>
      </Box>
    </Bp>
  );
}

function SuggestionCard({
  s,
  kind,
  canManage,
  onDecide,
}: {
  s: IdentitySuggestion;
  kind: EntityKind;
  canManage: boolean;
  onDecide: (id: string, accept: boolean) => void;
}) {
  const incoming = s.incoming;
  const label = (r: ConnectorRecord) =>
    kind === "customers" ? r.name : r.description;
  const key = (r: ConnectorRecord) =>
    kind === "customers" ? r.gstin : r.sku;

  return (
    <Bp className="id-sugg">
      <div className="id-sugg-h">
        Possible existing identity found
        <span className="id-evidence">{s.evidence}</span>
      </div>
      {/* A name match and a GSTIN match are not the same claim, and a reviewer
          reading one sentence for both would weigh them the same. The name rule
          only runs where there is no identifier to compare at all — so it is the
          difference between two rows nobody could ever link and two rows a
          government registration says are one company. */}
      {s.strategy === "NAME" ? (
        <>
          <StatusChip label="Weaker evidence — a name, not an identifier" tone="warn" />
          <p className="st-help">
            Matched on the name alone, because neither record carries a{" "}
            {kind === "customers" ? "GSTIN" : "SKU"} to compare. Two unrelated businesses can
            share a name and one group can trade under several, so this is never
            linked automatically however the setting above is set — it waits for
            you every time. Nothing has been linked yet.
          </p>
        </>
      ) : (
        <p className="st-help">
          Matched on {s.strategy}. Nothing has been linked — this is a proposal, and
          the two records below stay exactly as their connectors hold them either way.
        </p>
      )}

      <div className="id-compare">
        <div>
          <div className="id-side">Just imported</div>
          <div className="id-side-name">{label(incoming) || "(no name)"}</div>
          <dl>
            <div><dt>Connector</dt><dd>{incoming.connector}</dd></div>
            <div><dt>External ID</dt><dd className="mono">{incoming.external_id}</dd></div>
            <div>
              <dt>{kind === "customers" ? "GSTIN" : "SKU"}</dt>
              <dd className="mono">{key(incoming) || "—"}</dd>
            </div>
          </dl>
        </div>
        <div className="id-compare-arrow" aria-hidden="true">→</div>
        <div>
          <div className="id-side">Existing identity</div>
          <div className="id-side-name">{s.target.display_name || "(unnamed)"}</div>
          <dl>
            {s.target.records.map((r) => (
              <div key={r.record_id}>
                <dt>{r.connector}</dt>
                <dd className="mono">{r.external_id}</dd>
              </div>
            ))}
            <div>
              <dt>{kind === "customers" ? "GSTIN" : "SKU"}</dt>
              <dd className="mono">{key(s.target.records[0]) || "—"}</dd>
            </div>
          </dl>
        </div>
      </div>

      {canManage ? (
        <div className="id-sugg-actions">
          <Button variant="contained" size="small"
                  onClick={() => onDecide(s.suggestion_id, true)}>
            Link to this identity
          </Button>
          <Button variant="outlined" size="small"
                  onClick={() => onDecide(s.suggestion_id, false)}>
            Keep them separate
          </Button>
          <Tip text="Keeping them separate is recorded too, and the same pair will not be proposed again. Linking can be undone later — the records themselves are never altered either way." />
        </div>
      ) : (
        <p className="st-help">An owner decides these.</p>
      )}
    </Bp>
  );
}

/** An empty review queue, saying which kind of empty it is.
 *
 *  This replaced one sentence that read every empty queue the same way: "None
 *  means no exact matches were found — not that matching is switched off." True
 *  and reassuring, and it was on screen while two `Pitti Engineering Ltd` rows
 *  billed separately, because neither carried a GSTIN and the GSTIN rule could
 *  not look at either of them. Nothing found and nothing *lookable-at* are
 *  different answers and only one of them means the book is fine.
 *
 *  The counts come from the records rather than from the queue, for the reason
 *  `identity.review_coverage` gives: a number derived from an empty queue can
 *  only ever describe the queue.
 */
function NothingToReview({
  kind, coverage,
}: { kind: EntityKind; coverage: IdentityCoverage | null }) {
  const noun = kind === "customers" ? "customer" : "item";
  if (!coverage || coverage.records === 0) {
    return (
      <EmptyState
        title="Nothing to review"
        reason={`No ${noun} records have been imported yet, so there is nothing to match. Run a sync from Data & connection.`} />
    );
  }
  const { records, with_key: keyed, without_key: keyless, key_name } = coverage;
  if (keyless === 0) {
    return (
      <EmptyState
        title="Nothing to review"
        reason={`All ${records} ${noun} records carry a ${key_name}, and no two of them share one. This is the matcher having looked at everything and found nothing to join — the answer, not a gap.`} />
    );
  }
  return (
    <EmptyState
      title="Nothing to review"
      reason={
        <>
          {keyless} of {records} {noun} records carry no {key_name}, so the{" "}
          {key_name} rule cannot compare them however often a sync runs
          {keyed > 0 && <> — the other {keyed} it can</>}. Those {keyless} are
          matched on their name instead, which only proposes an exact match after
          case, punctuation and company-form words are set aside. Nothing here
          means no two of them are named the same either.
        </>
      } />
  );
}

/* ── the screen ─────────────────────────────────────────────────────────── */

export function IdentityScreen({ token }: { token: string }) {
  const [kind, setKind] = useState<EntityKind>("customers");
  const [tab, setTab] = useState<"review" | "all">("review");
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [suggestions, setSuggestions] = useState<IdentitySuggestion[]>([]);
  const [coverage, setCoverage] = useState<IdentityCoverage | null>(null);
  const [policy, setPolicy] = useState<IdentityPolicy | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [q, setQ] = useState("");
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, sugg, pol] = await Promise.all([
        papi.listIdentities(token, kind, q, linkedOnly),
        papi.listSuggestions(token, kind),
        papi.identityPolicy(token),
      ]);
      setIdentities(list.identities);
      setCanManage(list.can_manage);
      setSuggestions(sugg.suggestions);
      setCoverage(sugg.coverage);
      setPolicy(pol);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token, kind, q, linkedOnly]);

  useEffect(() => {
    load();
  }, [load]);

  async function guard(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const autoFlag = kind === "customers" ? "auto_link_customers" : "auto_link_items";

  return (
    <div className="dp-screen">
      {/* `kit.SectionHeader`, per §10 — this was a `<div className=
          "dp-screen-head">` wrapping a hand-written `<h2>`/`<p>` pair, and
          `.dp-screen-head` has no rule anywhere in the stylesheet, so the
          layout it appeared to carry was doing nothing. `ConnectionsPanel`
          moved off the same class first; this is the same conversion, so two
          screens do not describe one heading two ways. The tip carries a
          `<b>`, which `SectionHeader` can hold now that its `tip` is a
          ReactNode. */}
      <SectionHeader
        title="Identities"
        tip={
          <>
            Records from different systems are <b>linked, never merged</b>. Each
            connector stays the source of truth for its own data; an identity
            only records which rows describe the same business entity, so a
            figure can always be traced back to the system it came from.
          </>
        }
        sub={KINDS.find((k) => k.key === kind)?.hint}
        actions={
          /* A `ToggleButtonGroup`, per ui-standards §5 and following the
             conversion `ConnectionsPanel` already made of these same `.cx-tab`
             pills: hand-rolled `<button>`s whose selected state existed only
             as an `aria-pressed` attribute selector in the stylesheet, with
             the focus ring and hit target that implies. Deliberately *not*
             `Tabs` — this picks which records the screen is about, and the tab
             strip below picks which view of them, so making both tab bars
             would leave nothing saying which is the outer scope. */
          <ToggleButtonGroup
            exclusive
            size="small"
            value={kind}
            onChange={(_e, v) => {
              // `exclusive` sends null when the pressed one is pressed again;
              // that is a no-op here rather than a scope with no kind.
              if (v) { setKind(v as EntityKind); setTab("review"); }
            }}
            aria-label="Which records"
          >
            {KINDS.map((k) => (
              <ToggleButton key={k.key} value={k.key}>{k.label}</ToggleButton>
            ))}
          </ToggleButtonGroup>
        }
      />

      {/* `ErrorState`, not a bare Alert: "we could not look" must never be read
          as "there is nothing to see", and the empty states below are now
          specific enough that an error styled like one would be mistaken for
          one. The kit component is the one that also offers the way back. */}
      {error && (
        <Box sx={{ mb: 2 }}>
          <ErrorState error={error} onRetry={load} busy={loading} />
        </Box>
      )}

      {/* MUI `Tabs`, per ui-standards §9 — the same `.cx-tab` pills, but this
          pair genuinely switches between two panels of one screen rather than
          narrowing a scope, so it is a tab list and carries the roles a screen
          reader needs to announce "1 of 2" and to find the panel each one
          controls. The count is a `Chip` rather than the `<b>` it was: §6, and
          a queue length is a state a reader scans for, not emphasis. */}
      <Tabs
        value={tab}
        onChange={(_e, v) => setTab(v as "review" | "all")}
        aria-label="Which identities"
        sx={{ mb: 2 }}
      >
        <Tab
          value="review"
          id="id-tab-review"
          aria-controls="id-panel-review"
          label={
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <span>To review</span>
              {suggestions.length > 0 && (
                <Chip size="small" label={suggestions.length} />
              )}
            </Stack>
          }
        />
        <Tab
          value="all"
          id="id-tab-all"
          aria-controls="id-panel-all"
          label="All identities"
        />
      </Tabs>

      {tab === "review" ? (
        <Box role="tabpanel" id="id-panel-review" aria-labelledby="id-tab-review">
          {policy && (
            <Bp className="st-section">
              <h3>
                <Labelled tip="With this off, a sync that finds an exact match records a suggestion and waits. On, it links without asking — faster, and unrecoverable when the match was a group trading under one registration.">
                  Automatic linking
                </Labelled>
              </h3>
              <label className={`st-switch ${policy.can_manage ? "" : "readonly"}`}>
                <input type="checkbox" checked={Boolean(policy[autoFlag])}
                       disabled={!policy.can_manage}
                       onChange={(e) =>
                         guard(() => papi.updateIdentityPolicy(token,
                           { [autoFlag]: e.target.checked } as Partial<IdentityPolicy>))} />
                <span>
                  <b>Link {kind} automatically on an exact match</b>
                  <span className="st-help">{policy.note}</span>
                </span>
              </label>
            </Bp>
          )}

          {!loading && suggestions.length === 0 && (
            <NothingToReview kind={kind} coverage={coverage} />
          )}

          <div className="id-list">
            {suggestions.map((s) => (
              <SuggestionCard key={s.suggestion_id} s={s} kind={kind}
                              canManage={canManage}
                              onDecide={(id, accept) =>
                                guard(() => papi.decideSuggestion(token, kind, id, accept))} />
            ))}
          </div>
        </Box>
      ) : (
        <Box role="tabpanel" id="id-panel-all" aria-labelledby="id-tab-all">
          {/* `kit.FilterPanel`, per §10 — "the controls above a list", which is
              what `.ci-controls` was a fourth answer to. Its own `label` rule
              set `display: grid` on every label inside it, so a
              `FormControlLabel` could not live there: the checkbox would have
              stacked above its words. That is why the whole row moves rather
              than the checkbox alone. `.sync-check` is retired with it. */}
          <FilterPanel>
            <TextField
              size="small"
              label="Search"
              placeholder="name, id, GSTIN or SKU"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              sx={{ minWidth: 240 }}
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={linkedOnly}
                  onChange={(e) => setLinkedOnly(e.target.checked)}
                />
              }
              label="Only entities seen in more than one system"
            />
          </FilterPanel>

          {loading && <LoadingState rows={3} />}
          {!loading && identities.length === 0 && (
            <EmptyState
              title="No identities yet"
              reason="They are created as records arrive — run a sync from Data & connection."
            />
          )}

          <div className="id-list">
            {identities.map((i) => (
              <IdentityCard key={i.identity_id} identity={i} kind={kind}
                            canManage={canManage}
                            onUnlink={(recordId) =>
                              guard(() => papi.unlinkRecord(token, kind, recordId))}
                            onRelabel={(id, label) =>
                              guard(() => papi.relabelIdentity(token, kind, id, label))} />
            ))}
          </div>
        </Box>
      )}
    </div>
  );
}
