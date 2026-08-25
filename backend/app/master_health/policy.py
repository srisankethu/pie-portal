"""The remediation assumptions, loaded and versioned.

Nothing in this package hardcodes an effort figure. They live in
``policy.yaml``, and the content hash of that file is stamped on every report
as ``mh_…`` — the same discipline as ``CommercialThresholds.version`` and for
the same reason: a ranked worklist is a *judgement*, two runs may rank
differently after somebody edits an estimate, and a report that cannot name
the policy that ranked it cannot explain the difference.

The stamp prefix is ``mh_`` rather than ``ci_`` or ``th_`` deliberately. Those
are the commercial and signal stamps and they mean different things; a reader
who sees one where the other belongs must not be able to read it as the same
policy (``CLAUDE.md`` §1 — "``th_`` and ``ci_`` are not the same stamp").
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, List, Optional

import yaml

DEFAULT_PATH = Path(__file__).parent / "policy.yaml"


class PolicyError(ValueError):
    """A remediation policy that cannot be trusted to rank a worklist."""


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, float):
        raise PolicyError(
            f"{where}: {value!r} is a float. Quote every number in policy.yaml "
            f"so it arrives as a string and becomes an exact Decimal.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise PolicyError(f"{where}: {value!r} is not a number") from exc


@dataclass(frozen=True)
class RemediationAction:
    """One class of fix: what it costs, and what it buys."""

    action_id: str
    label: str
    trigger: str
    setup_hours: Decimal
    rows_per_hour: Decimal
    recovers: str

    def hours_for(self, rows: int) -> Decimal:
        """Setup plus throughput. Zero rows costs nothing — nobody starts."""
        if rows <= 0:
            return Decimal("0")
        return self.setup_hours + (Decimal(rows) / self.rows_per_hour)

    def rows_per_hour_for(self, rows: int) -> Optional[Decimal]:
        """The ranking key: rows this fixes divided by hours it takes.

        ``None`` for an action with nothing to fix, which is not a rate of zero
        — it is an action that does not belong on the worklist at all.
        """
        hours = self.hours_for(rows)
        if rows <= 0 or hours <= 0:
            return None
        return Decimal(rows) / hours


@dataclass(frozen=True)
class RemediationPolicy:
    actions: tuple[RemediationAction, ...]
    version: str
    source: str


def load_policy(path: Optional[Path] = None) -> RemediationPolicy:
    path = Path(path or DEFAULT_PATH)
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"could not read remediation policy {path}: {exc}") from exc
    try:
        doc = yaml.safe_load(body.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("actions"), list):
        raise PolicyError(f"{path}: expected a mapping with an 'actions:' list")

    actions: List[RemediationAction] = []
    seen: set[str] = set()
    for entry in doc["actions"]:
        if not isinstance(entry, dict):
            raise PolicyError(f"{path}: every action must be a mapping")
        action_id = str(entry.get("id") or "").strip()
        trigger = str(entry.get("trigger") or "").strip()
        if not action_id or not trigger:
            raise PolicyError(f"{path}: an action needs both an 'id' and a 'trigger'")
        if action_id in seen:
            raise PolicyError(f"{path}: duplicate action id '{action_id}'")
        seen.add(action_id)
        rate = _dec(entry.get("rows_per_hour"), f"{path}:{action_id}.rows_per_hour")
        if rate <= 0:
            raise PolicyError(f"{path}:{action_id}: rows_per_hour must be above zero")
        actions.append(RemediationAction(
            action_id=action_id,
            label=str(entry.get("label") or action_id),
            trigger=trigger,
            setup_hours=_dec(entry.get("setup_hours", "0"), f"{path}:{action_id}.setup_hours"),
            rows_per_hour=rate,
            recovers=" ".join(str(entry.get("recovers") or "").split()),
        ))
    if not actions:
        raise PolicyError(f"{path}: no actions, so no worklist could be ranked")
    # Hashed over the file's bytes rather than over the parsed values: a comment
    # edit moving the stamp is a false positive nobody minds, but a value edit
    # that did NOT move it would be a report that cannot explain itself.
    version = "mh_" + hashlib.sha256(body).hexdigest()[:10]
    return RemediationPolicy(actions=tuple(actions), version=version, source=str(path))
