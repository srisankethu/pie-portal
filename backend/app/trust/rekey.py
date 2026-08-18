"""Diagnose — and, where there is no alternative, recover — tenant data keys.

Run it to answer one question::

    python -m app.trust.rekey

A tenant's DEK is stored wrapped by ``CREDENTIAL_ENCRYPTION_KEY``. When that
wrapping stops opening, every write of a name into the vault raises
``KeyUnavailable``, and the sentence the error carries used to prescribe a
single remedy — restore the previous master key. That is right in one of two
states and destructive in the other, and the two are indistinguishable from
inside ``trust/keys``:

* **The master key was rotated and the old value still exists.** Restore it.
  Nothing is lost, and this tool is not the answer.
* **The current master key is the right one and this key row is older than it.**
  The usual history: an organization connected while the public dev default was
  still in force, a real key was set, the connection was re-entered under the
  new key and started working again — and the tenant key row, which nothing
  re-enters, stayed wrapped under the value nobody kept.

The evidence separating them is already in the database. Stored connection
credentials are encrypted under the *same* master key (``app/crypto.py``), so
credentials that still decrypt prove the value in force is the one this
deployment has been writing under, and it is the key row that is stale. That is
what this reports, per organization, before anything is changed.

``--reissue`` acts on the second state only. It costs the model-payload log:
those rows are not derived from anything and cannot be rebuilt, so the count is
printed first and a reason is required. The name vault is rebuilt by the next
sync, which is why the tool does not touch it.

Read-only by default, like ``ingestion/merge_credentials``, and for the same
reason: deciding that a key is beyond recovery is a judgement, and a judgement
belongs to whoever is holding the deployment.
"""
from __future__ import annotations

import argparse
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crypto
from ..domain import models
from . import erasure, keys

#: name → model for the tables ``erasure.DESTROYED`` says are encrypted under
#: the DEK. Derived from that tuple rather than restated, so a third ciphertext
#: class added there is counted here without anybody remembering this file.
_MANIFEST = dict(erasure.MANIFESTED)


def _ciphertext_counts(session: Session, organization_id: str) -> dict[str, int]:
    """How many rows become unreadable if this key is replaced."""
    counts: dict[str, int] = {}
    for entry in erasure.DESTROYED:
        model = _MANIFEST.get(entry["table"])
        if model is None:                      # a table with no exportable model
            continue
        counts[entry["table"]] = int(session.scalar(
            select(func.count()).select_from(model)
            .where(model.organization_id == organization_id)) or 0)
    return counts


def master_key_is_current(session: Session) -> Optional[bool]:
    """Does the master key in force still open the credentials on file?

    ``True`` means it does, so a key row that will not unwrap is stale rather
    than the environment being wrong — restoring an older master key would take
    those working credentials down. ``False`` means the credentials do not open
    either, which points at the environment and away from any single tenant.
    ``None`` means no credential is stored, so there is no evidence here and
    the tool says so rather than picking the comfortable answer.
    """
    # Both halves of the table: a Zoho row carries the secret and refresh token
    # in their own columns, a connector row carries one encrypted JSON
    # document. Either decrypting is the same evidence.
    ciphertexts = [c for cred in session.scalars(select(models.ZohoCredential))
                   for c in (cred.client_secret_encrypted, cred.secrets_encrypted)
                   if c]
    if not ciphertexts:
        return None
    for ciphertext in ciphertexts:
        try:
            crypto.decrypt(ciphertext)
            return True
        except Exception:                      # noqa: BLE001 — this row, not all
            continue
    return False


def survey(session: Session) -> list[dict]:
    """Every organization's key state, with what a replacement would cost."""
    out: list[dict] = []
    for org in session.scalars(select(models.Organization)
                               .order_by(models.Organization.organization_id)):
        state = keys.inspect(session, org.organization_id)
        row = {"organization_id": org.organization_id, "name": org.name,
               "state": state}
        if state == keys.KEY_UNREADABLE:
            row["unreadable_if_reissued"] = _ciphertext_counts(
                session, org.organization_id)
        out.append(row)
    return out


def _report(session: Session, rows: list[dict]) -> None:
    current = master_key_is_current(session)
    print("Stored credentials under the current CREDENTIAL_ENCRYPTION_KEY: " + {
        True: "decrypt — the master key in force is the one this deployment "
              "has been writing under.",
        False: "do NOT decrypt — the master key itself looks wrong. Restore "
               "the previous value before touching any tenant key.",
        None: "none stored, so there is no evidence either way here.",
    }[current])
    print()
    for row in rows:
        line = f"  {row['organization_id']:<24} {row['state']:<10} {row['name']}"
        print(line)
        for table, count in (row.get("unreadable_if_reissued") or {}).items():
            print(f"      {table}: {count} row(s) encrypted under the old key")
    broken = [r for r in rows if r["state"] == keys.KEY_UNREADABLE]
    print()
    if not broken:
        print("No unreadable data key. Nothing here to recover.")
        return
    print(f"{len(broken)} organization(s) hold a data key that cannot be "
          f"unwrapped. Syncs still run and still write their documents; the "
          f"name vault is what is not being written.")
    if current is not True:
        print("Not a candidate for --reissue while the master key itself is "
              "in doubt: restore the previous CREDENTIAL_ENCRYPTION_KEY first "
              "and re-run this.")
    else:
        print("The previous master key would recover these, if it exists. If "
              "it does not, --reissue --reason '…' issues a fresh key and "
              "makes the row counts above permanently unreadable.")


def reissue_all(session: Session, *, reason: str,
                actor_user_id: Optional[str]) -> list[str]:
    """Issue a fresh key for every organization whose key cannot be unwrapped."""
    done: list[str] = []
    for row in survey(session):
        if row["state"] != keys.KEY_UNREADABLE:
            continue
        keys.reissue(session, row["organization_id"], reason=reason,
                     actor_user_id=actor_user_id)
        done.append(row["organization_id"])
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reissue", action="store_true",
                        help="Issue a fresh data key for every organization "
                             "whose key cannot be unwrapped. Irreversible.")
    parser.add_argument("--reason", default="",
                        help="Why, in a sentence. Recorded on the key row.")
    parser.add_argument("--actor", default=None,
                        help="Who is doing this, recorded alongside the reason.")
    args = parser.parse_args()

    from ..db import SessionLocal

    session = SessionLocal()
    try:
        rows = survey(session)
        _report(session, rows)
        if not args.reissue:
            return
        if master_key_is_current(session) is False:
            raise SystemExit(
                "Refusing to reissue: the stored credentials do not decrypt "
                "either, so the master key is the thing that is wrong. "
                "Restoring it recovers everything; reissuing here would "
                "destroy data that the right key can still read.")
        if not args.reason.strip():
            raise SystemExit("--reissue needs --reason.")
        done = reissue_all(session, reason=args.reason, actor_user_id=args.actor)
        session.commit()
        print()
        if done:
            print(f"Reissued: {', '.join(done)}. Run a sync to rebuild the "
                  f"name vault from the plaintext display columns.")
        else:
            print("Nothing to reissue.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
