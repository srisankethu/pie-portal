"""Command-line entry point. Engine, config and reports — no UI, no Zoho."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from .config import ConfigError, load_config


def _cmd_show_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, as_of=args.as_of)
    print(json.dumps({"version": cfg.version,
                      "effective_from": str(cfg.effective_from),
                      "effective_to": str(cfg.effective_to),
                      "parameters": cfg.raw}, indent=2, default=str))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Fail loudly on a config that cannot safely produce a payout."""
    problems: list[str] = []
    cfg = load_config(args.config, as_of=args.as_of)

    cash = cfg.dec("payout", "cash_share")
    bank = cfg.dec("payout", "relationship_bank_share")
    if cash + bank != Decimal("1"):
        problems.append(f"payout split {cash} + {bank} does not sum to 1")

    rates = cfg.get("payout", "r_by_entity")
    unset = [e for e, v in rates.items() if Decimal(str(v)) <= 0]
    if unset:
        problems.append(
            f"r is zero for {unset} — these entities pay nothing until the "
            "shadow run calibrates them (this is expected before Phase 2)")

    weights = cfg.get("health", "weights")
    if sum(int(v) for v in weights.values()) != 100:
        problems.append(f"health weights sum to {sum(weights.values())}, not 100")

    rsi_weights = cfg.get("rsi", "weights")
    if sum(int(v) for v in rsi_weights.values()) != 100:
        problems.append(f"RSI weights sum to {sum(rsi_weights.values())}, not 100")

    prop = cfg.dec("recovery", "proposer_share")
    close = cfg.dec("recovery", "closer_share")
    if prop + close != Decimal("1"):
        problems.append(f"recovery split {prop} + {close} does not sum to 1")

    for line in problems:
        print(f"  ! {line}")
    if not problems:
        print(f"config {cfg.version}: consistent")
    # Unset r is a warning, not a failure — it is the correct pre-calibration
    # state and the engine refuses to pay rather than guessing.
    hard = [p for p in problems if "shadow run" not in p]
    return 1 if hard else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="incentive-engine",
        description="Deterministic salesperson incentive computation.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="the date whose parameter block to load; never "
                             "defaults to today, because a payout that changes "
                             "with the afternoon is not auditable")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show-config").set_defaults(func=_cmd_show_config)
    sub.add_parser("verify").set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
