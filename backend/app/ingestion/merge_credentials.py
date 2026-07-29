"""Collapse duplicate Zoho credentials onto one row.

The one-off cleanup for a deployment that connected each legal entity by
re-entering the same OAuth app. After this, three entities under one Zoho login
are three connections over one credential — and rotating is one operation
instead of three, which is the entire point.

Run after ``alembic upgrade head``::

    python -m app.ingestion.merge_credentials --dry-run
    python -m app.ingestion.merge_credentials

It lives here rather than in the migration because deciding two rows hold the
same secret requires decrypting them, and a migration that cannot run without
the correct ``CREDENTIAL_ENCRYPTION_KEY`` is a migration that fails at the worst
moment. This is separate, re-runnable, and safe to skip.

Nothing is deleted that is still in use: connections are repointed first, and
only then is the now-orphaned duplicate removed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..domain import models


def _secret_key(cred: models.ZohoCredential) -> Optional[tuple[str, str, str]]:
    """What this credential actually authenticates as, in plaintext.

    None when it cannot be decrypted — an unreadable row is never merged into
    or out of, because "these two might be the same" is not good enough to
    repoint a live connection.
    """
    try:
        return (cred.client_id,
                crypto.decrypt(cred.client_secret_encrypted),
                crypto.decrypt(cred.refresh_token_encrypted))
    except Exception:  # noqa: BLE001
        return None


def plan(session: Session) -> list[tuple[models.ZohoCredential, list[models.ZohoCredential]]]:
    """Groups of credentials holding one secret: (keeper, duplicates)."""
    groups: dict[tuple[str, str, str], list[models.ZohoCredential]] = defaultdict(list)
    for cred in session.scalars(select(models.ZohoCredential).order_by(
            models.ZohoCredential.created_at)):
        key = _secret_key(cred)
        if key is not None:
            groups[key].append(cred)
    # The oldest row wins, so the credential everything already points at by
    # default is the one that survives.
    return [(rows[0], rows[1:]) for rows in groups.values() if len(rows) > 1]


def merge(session: Session, *, dry_run: bool = False) -> dict:
    report = {"groups": 0, "credentials_removed": 0, "connections_repointed": 0,
              "details": []}

    for keeper, duplicates in plan(session):
        report["groups"] += 1
        moved: list[str] = []
        for dup in duplicates:
            connections = list(session.scalars(select(models.ZohoConnection).where(
                models.ZohoConnection.credential_id == dup.credential_id)))
            for conn in connections:
                moved.append(conn.organization_id)
                if not dry_run:
                    conn.credential_id = keeper.credential_id
                    # A stale inline copy is exactly what rotation would miss.
                    conn.client_id = None
                    conn.client_secret_encrypted = None
                    conn.refresh_token_encrypted = None
            # Every organization that was using the duplicate keeps its access
            # through the keeper — merging must not quietly revoke anyone.
            if not dry_run:
                shared = set(keeper.shared_with_organization_ids or [])
                shared.update(dup.shared_with_organization_ids or [])
                shared.add(dup.owner_organization_id)
                shared.update(c.organization_id for c in connections)
                shared.discard(keeper.owner_organization_id)
                keeper.shared_with_organization_ids = sorted(shared)
                session.delete(dup)
            report["credentials_removed"] += 1

        report["connections_repointed"] += len(moved)
        report["details"].append({
            "kept": keeper.credential_id,
            "kept_owner": keeper.owner_organization_id,
            "removed": [d.credential_id for d in duplicates],
            "organizations_repointed": moved,
        })

    if not dry_run:
        session.flush()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change, without changing it")
    args = parser.parse_args()

    from ..db import SessionLocal

    session = SessionLocal()
    try:
        report = merge(session, dry_run=args.dry_run)
        if not args.dry_run:
            session.commit()

        if report["groups"] == 0:
            print("No duplicate Zoho credentials — nothing to merge.")
            return
        verb = "would be" if args.dry_run else "were"
        print(f"{report['groups']} duplicated grant(s) found.")
        for d in report["details"]:
            print(f"  keep {d['kept']} (owned by {d['kept_owner']})")
            for removed in d["removed"]:
                print(f"    remove {removed}")
            for org in d["organizations_repointed"]:
                print(f"    repoint organization {org}")
        print(f"{report['credentials_removed']} credential(s) and "
              f"{report['connections_repointed']} connection(s) {verb} changed.")
        if args.dry_run:
            print("Dry run — nothing was written. Re-run without --dry-run to apply.")
        else:
            print("Rotating now updates one credential and every connection follows.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
