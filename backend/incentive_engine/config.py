"""The single versioned parameter file, loaded and effective-dated.

Nothing in this engine hardcodes a rate, a band edge or a threshold. Every one
of them lives in ``config/parameters.yaml`` with an effective date, so a
historical period recomputes identically: the run records the config version
that produced it, and asking for a period loads the block in force on that
period's dates rather than today's.

Money is ``Decimal`` from the moment it leaves YAML. The file stores every
numeric parameter as a *quoted string* on purpose — an unquoted 0.15 is parsed
by YAML as a float, and a float that has already lost precision cannot be
recovered by wrapping it in ``Decimal`` afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_PATH = Path(__file__).parent / "config" / "parameters.yaml"


class ConfigError(ValueError):
    """A parameter file that cannot be trusted to produce a payout."""


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, float):
        # Refused rather than coerced: 0.1 has already lost precision by the
        # time it reaches here, and silently accepting it would make payouts
        # differ by paise between machines.
        raise ConfigError(
            f"{where}: {value!r} is a float. Quote every numeric parameter in "
            "the YAML so it arrives as a string and becomes an exact Decimal.")
    return Decimal(str(value))


@dataclass(frozen=True)
class Config:
    """One effective-dated parameter block."""

    version: str
    effective_from: date
    effective_to: Optional[date]
    raw: dict

    def get(self, *path: str) -> Any:
        node: Any = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"missing parameter: {'.'.join(path)}")
            node = node[key]
        return node

    def dec(self, *path: str) -> Decimal:
        return _dec(self.get(*path), ".".join(path))

    def int_(self, *path: str) -> int:
        return int(self.get(*path))

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


def load_config(path: Path | str | None = None,
                as_of: Optional[date] = None) -> Config:
    """Load the parameter block in force on ``as_of``.

    ``as_of`` is an explicit argument and never defaults to the clock: a
    computation that reads the time of day is not reproducible, and a payout
    that changes because it was re-run on a different afternoon is not
    auditable.
    """
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text())
    if not isinstance(raw, dict) or "version" not in raw:
        raise ConfigError("parameter file has no version")

    start = _as_date(raw.get("effective_from"), "effective_from")
    end = _as_date(raw.get("effective_to"), "effective_to")
    cfg = Config(version=str(raw["version"]), effective_from=start,
                 effective_to=end, raw=raw)
    if as_of is not None and not cfg.covers(as_of):
        raise ConfigError(
            f"config {cfg.version} covers {start}..{end or 'open'} and does not "
            f"cover {as_of}. Load the block that was in force; do not "
            "recompute a historical period under today's rates.")
    return cfg


def _as_date(value: Any, where: str) -> Any:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:      # pragma: no cover - malformed config
        raise ConfigError(f"{where}: {value!r} is not a date") from e
