// Groups: the sets somebody draws over customers, vendors and items.
//
// The platform could answer a question about one account and a question about
// the whole book, with nothing in between — and every question a distributor
// actually asks lives in that gap. This is where those sets get drawn; the
// directories are where they get used.
//
// **Two things on this screen are load-bearing and easy to mistake for
// decoration.**
//
// The *size* is beside every group, everywhere. A group is a set, and "which
// set" is half of any answer computed over it: three accounts of four hundred
// and three hundred of four hundred are different claims, and a name alone
// carries neither.
//
// The *version* is on the detail panel. It is the definition's content hash,
// and it moves when the roster moves and stays put on a rename — so somebody
// looking at two figures that disagree can see here whether the group changed
// between them. That is the whole reason it is rendered rather than kept for
// the API: the person who needs it is the one holding two numbers.
//
// Writing is manager and above, which is the server's rule; `may_edit` comes
// back with the list so this screen asks rather than reconstructing it.

import { useCallback, useEffect, useMemo, useState } from "react";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { papi } from "./api";
import { DataGrid, type ColDef } from "./DataGrid";
import {
  EmptyState, ErrorState, FilterPanel, FormDialog, LoadingState, Meta,
  SectionHeader, Section, StatusChip, TOUCH,
} from "./kit";
import type {
  EntityGroup, GroupDetail, GroupKind, PlatformSession,
} from "./types";

/** The three kinds, in the order the nav reads them. Fetched with the list as
 *  well — the server publishes them so no client hardcodes a copy — but needed
 *  before the first response arrives to render the picker at all. */
const KINDS: { value: GroupKind; label: string }[] = [
  { value: "CUSTOMER", label: "Customers" },
  { value: "VENDOR", label: "Vendors" },
  { value: "PRODUCT", label: "Items" },
];

/** One candidate for a roster: an id and the name a person would recognise.
 *
 *  Three directories, one shape. The alternative — a member picker per kind —
 *  is three components that would slowly stop behaving alike, which is the
 *  duplication `CLAUDE.md` §2 is about. */
interface Candidate { id: string; name: string }

/** Where each kind's candidates come from.
 *
 *  All three of these endpoints are ones the platform already serves; none is a
 *  new "list every vendor" surface added for this screen. Vendors and items
 *  come from manager-and-above endpoints, which is not a constraint here —
 *  drawing a group is manager and above anyway.
 */
async function loadCandidates(token: string, kind: GroupKind): Promise<Candidate[]> {
  if (kind === "CUSTOMER") {
    const rows = await papi.listAccounts(token, "", "all");
    return rows.map((r) => ({ id: r.customer_id, name: r.name }));
  }
  if (kind === "VENDOR") {
    const data = await papi.supply(token);
    const rows = (data?.suppliers ?? []) as Record<string, unknown>[];
    return rows
      .filter((r) => typeof r.vendor_id === "string")
      .map((r) => ({ id: r.vendor_id as string,
                     name: String(r.label ?? r.vendor_id) }));
  }
  const data = await papi.catalogue(token, false);
  const rows = (data?.items ?? []) as Record<string, unknown>[];
  return rows.map((r) => ({ id: r.product_id as string, name: String(r.name) }));
}

export function GroupsScreen({ session }: { session: PlatformSession }) {
  const [kind, setKind] = useState<GroupKind>("CUSTOMER");
  const [groups, setGroups] = useState<EntityGroup[] | null>(null);
  const [mayEdit, setMayEdit] = useState(false);
  const [emptyReason, setEmptyReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await papi.listGroups(session.token, kind, showArchived);
      setGroups(r.groups);
      setMayEdit(r.may_edit);
      setEmptyReason(r.empty_reason);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token, kind, showArchived]);

  useEffect(() => { load(); }, [load]);

  const columns = useMemo<ColDef<EntityGroup>[]>(() => [
    { field: "name", headerName: "Group", flex: 2, minWidth: 180 },
    {
      field: "members", headerName: "Members", width: 120, type: "numericColumn",
      // The size is a column rather than a detail, because scanning "which of
      // these sets is big enough to ask a question about" is what somebody
      // opens this screen to do.
    },
    {
      field: "visibility", headerName: "Seen by", width: 150,
      valueFormatter: (p) =>
        p.value === "RESTRICTED" ? "Management only" : "Everyone",
    },
    { field: "created_by", headerName: "Drawn by", flex: 1, minWidth: 140 },
    {
      field: "group_version", headerName: "Version", width: 150,
      cellClass: "mono",
    },
  ], []);

  if (error) {
    return <ErrorState title="Groups could not be loaded" error={error}
                       onRetry={load} />;
  }

  return (
    <div>
      <SectionHeader
        title="Groups"
        sub="Sets of customers, vendors or items you want to ask questions about together — the PSU accounts, one principal's items, the vendors on ninety-day terms. Pick one on a directory or an analysis and every figure is recomputed inside it."
        actions={mayEdit ? (
          <Button variant="contained" onClick={() => setCreating(true)}>
            Draw a group
          </Button>
        ) : undefined}
      />

      <FilterPanel>
        <TextField
          select size="small" label="Of" value={kind}
          sx={{ minWidth: 160, "& .MuiInputBase-root": TOUCH }}
          onChange={(e) => { setOpen(null); setKind(e.target.value as GroupKind); }}
        >
          {KINDS.map((k) => (
            <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>
          ))}
        </TextField>
        {/* A standalone on/off holding a state, so a Switch — never a
            ToggleButton and never a Button whose variant carries the state. */}
        <FormControlLabel
          sx={TOUCH}
          control={<Switch checked={showArchived} size="small"
                           onChange={(e) => setShowArchived(e.target.checked)} />}
          label="Include archived"
        />
      </FilterPanel>

      {groups === null ? (
        <LoadingState rows={3} height={64} />
      ) : groups.length === 0 ? (
        <EmptyState
          title="No groups yet"
          reason={emptyReason
            ?? "Nothing has been grouped here yet."}
        />
      ) : (
        <DataGrid
          ariaLabel="Groups"
          rows={groups}
          columns={columns}
          getRowId={(g) => g.slug}
          onRowClick={(g) => setOpen(g.slug)}
          renderNarrow={(g) => (
            <Stack spacing={0.5}>
              <Typography variant="subtitle2">{g.name}</Typography>
              <Meta>{g.members} member{g.members === 1 ? "" : "s"}</Meta>
              {g.visibility === "RESTRICTED" && (
                <StatusChip label="Management only" tone="warn" />
              )}
            </Stack>
          )}
        />
      )}

      {open && (
        <GroupDetailPanel
          session={session} kind={kind} slug={open}
          onClose={() => setOpen(null)}
          onChanged={load}
        />
      )}

      {creating && (
        <CreateGroupDialog
          kind={kind}
          onClose={() => setCreating(false)}
          onCreate={async (body) => {
            await papi.createGroup(session.token, { ...body, entity_kind: kind });
            setCreating(false);
            await load();
          }}
        />
      )}
    </div>
  );
}

/** One group: who is in it, and the controls to change that.
 *
 *  Rendered below the list rather than in a dialog. Adding twenty accounts to a
 *  group is a session rather than a form somebody fills in and dismisses, and a
 *  modal over the list would hide the thing being worked from.
 */
function GroupDetailPanel({ session, kind, slug, onClose, onChanged }: {
  session: PlatformSession;
  kind: GroupKind;
  slug: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<GroupDetail | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [picked, setPicked] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDetail(await papi.getGroup(session.token, kind, slug));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token, kind, slug]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let live = true;
    loadCandidates(session.token, kind)
      .then((c) => { if (live) setCandidates(c); })
      .catch(() => { if (live) setCandidates([]); });
    return () => { live = false; };
  }, [session.token, kind]);

  const inGroup = useMemo(
    () => new Set((detail?.roster ?? []).map((m) => m.entity_id)), [detail]);
  const offerable = useMemo(
    () => candidates.filter((c) => !inGroup.has(c.id)), [candidates, inGroup]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) return <LoadingState rows={1} height={120} />;

  return (
    <Section
      sx={{ mt: 2 }}
      title={detail.name}
      sub={detail.description ?? undefined}
      badge={detail.visibility === "RESTRICTED"
        ? <StatusChip label="Management only" tone="warn" />
        : undefined}
      actions={
        <Stack direction="row" spacing={1}>
          {detail.may_edit && (
            <Button onClick={() => setEditing(true)} disabled={busy}>Edit</Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </Stack>
      }
    >
      {/* The version, where the person who needs it is standing: somebody
          holding two figures that disagree. It moves when the roster moves and
          not when the name does, so "same version" is a real answer to "were
          these computed over the same set". */}
      <Meta>
        {detail.members} member{detail.members === 1 ? "" : "s"}
        {" · version "}
        <span className="mono">{detail.group_version}</span>
        {detail.archived && " · archived"}
      </Meta>

      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}

      {editing && (
        <EditGroupDialog
          detail={detail}
          onClose={() => setEditing(false)}
          onSave={async (body) => {
            await papi.updateGroup(session.token, kind, slug, body);
            setEditing(false);
            await load();
            onChanged();
          }}
        />
      )}

      {detail.may_edit && (
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 2 }}>
          <Autocomplete
            multiple
            size="small"
            sx={{ flex: 1, minWidth: 240 }}
            options={offerable}
            value={picked}
            disabled={busy}
            getOptionLabel={(o) => o.name}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            onChange={(_, v) => setPicked(v)}
            renderInput={(params) => (
              <TextField {...params} label={`Add ${detail.entity_label.toLowerCase()}`} />
            )}
          />
          <Button
            variant="contained"
            disabled={busy || picked.length === 0}
            sx={TOUCH}
            onClick={() => act(async () => {
              await papi.addGroupMembers(session.token, kind, slug,
                                         picked.map((p) => p.id));
              setPicked([]);
            })}
          >
            Add {picked.length > 0 && picked.length}
          </Button>
        </Stack>
      )}

      {detail.roster.length === 0 ? (
        <EmptyState
          title="Nobody in this group yet"
          reason="A group with no members computes an empty screen rather than the whole book, so nothing will be shown behind it until somebody is added."
        />
      ) : (
        <Stack direction="row" spacing={0} sx={{ mt: 2, flexWrap: "wrap", gap: 1 }}>
          {detail.roster.map((m) => (
            <Chip
              key={m.entity_id}
              label={m.name ?? m.entity_id}
              // A member whose entity is no longer in the books is named
              // honestly rather than dropped — a roster that quietly shrinks is
              // a definition that changed without anybody deciding to.
              variant={m.name ? "filled" : "outlined"}
              onDelete={detail.may_edit && !busy
                ? () => act(() => papi.removeGroupMember(
                    session.token, kind, slug, m.entity_id))
                : undefined}
            />
          ))}
        </Stack>
      )}
    </Section>
  );
}

/** Rename, re-describe, restrict or archive an existing group.
 *
 *  **Everything on this form is deliberately outside the group's definition**,
 *  which is why none of it moves the version. A rename changes what the pickers
 *  call the group and no number computed under it; archiving stops it being
 *  offered and does not change who was in it. Membership is edited on the panel
 *  behind this dialog, and it is the only thing that restamps — so a rename and
 *  a roster edit can never be one request that half-succeeds.
 *
 *  **An emptied description is sent as `null`, not omitted.** The endpoint reads
 *  which fields the caller actually sent rather than which came back non-null,
 *  precisely so a description can be cleared; sending `undefined` here would
 *  reach that code as "not mentioned" and the field could be set and never
 *  unset.
 */
function EditGroupDialog({ detail, onClose, onSave }: {
  detail: GroupDetail;
  onClose: () => void;
  onSave: (body: { name?: string; description?: string | null;
                   visibility?: string; archived?: boolean }) => Promise<void>;
}) {
  const [name, setName] = useState(detail.name);
  const [description, setDescription] = useState(detail.description ?? "");
  const [restricted, setRestricted] = useState(detail.visibility === "RESTRICTED");
  const [archived, setArchived] = useState(detail.archived);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = description.trim();
  const unchanged =
    name.trim() === detail.name
    && trimmed === (detail.description ?? "")
    && restricted === (detail.visibility === "RESTRICTED")
    && archived === detail.archived;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        // `null` rather than `undefined` when emptied — see the note above.
        description: trimmed === "" ? null : trimmed,
        visibility: restricted ? "RESTRICTED" : "OPERATIONAL",
        archived,
      });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <FormDialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Edit {detail.name}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          None of this changes who is in the group, so the figures computed
          behind it stay exactly as comparable as they were — its version{" "}
          <span className="mono">{detail.group_version}</span> does not move.
          Members are added and removed on the panel behind this.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            autoFocus fullWidth size="small" label="Name" value={name}
            disabled={busy} required
            onChange={(e) => setName(e.target.value)}
          />
          <TextField
            fullWidth size="small" label="What it is for" value={description}
            disabled={busy} multiline minRows={2}
            helperText="Leave it empty to clear the description."
            onChange={(e) => setDescription(e.target.value)}
          />
          <FormControlLabel
            sx={TOUCH}
            control={<Switch checked={restricted} size="small" disabled={busy}
                             onChange={(e) => setRestricted(e.target.checked)} />}
            label="Management only"
          />
          {/* Archive rather than delete, and said plainly: a number was quoted
              under this group and the version that produced it has to stay
              resolvable. Archiving takes it out of the pickers and leaves every
              past answer explainable. */}
          <FormControlLabel
            sx={TOUCH}
            control={<Switch checked={archived} size="small" disabled={busy}
                             onChange={(e) => setArchived(e.target.checked)} />}
            label="Archived — hidden from the pickers, never deleted"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !name.trim() || unchanged}
                onClick={submit}>
          Save
        </Button>
      </DialogActions>
    </FormDialog>
  );
}

/** Draw a new, empty group. */
function CreateGroupDialog({ kind, onClose, onCreate }: {
  kind: GroupKind;
  onClose: () => void;
  onCreate: (body: { name: string; description?: string;
                     visibility?: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [restricted, setRestricted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = KINDS.find((k) => k.value === kind)?.label.toLowerCase() ?? "records";

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        name: name.trim(),
        description: description.trim() || undefined,
        visibility: restricted ? "RESTRICTED" : "OPERATIONAL",
      });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <FormDialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Draw a group of {label}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          A name now, members after. Every figure computed behind this group is
          recomputed inside it, so editing who is in it changes those figures —
          which is why the group carries a version that moves when its members
          do.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            autoFocus fullWidth size="small" label="Name" value={name}
            disabled={busy} required
            helperText="What the pickers will call it — “PSU accounts”, “Aerospace”."
            onChange={(e) => setName(e.target.value)}
          />
          <TextField
            fullWidth size="small" label="What it is for" value={description}
            disabled={busy} multiline minRows={2}
            onChange={(e) => setDescription(e.target.value)}
          />
          {/* Offered, and worth offering: a group hand-picked along a cost
              boundary and named for it would publish that boundary to everyone
              who can read the roster. This is the control that stops it
              happening by accident. */}
          <FormControlLabel
            sx={TOUCH}
            control={<Switch checked={restricted} size="small" disabled={busy}
                             onChange={(e) => setRestricted(e.target.checked)} />}
            label="Management only"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !name.trim()} onClick={submit}>
          Draw it
        </Button>
      </DialogActions>
    </FormDialog>
  );
}
