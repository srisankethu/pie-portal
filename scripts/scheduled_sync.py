#!/usr/bin/env python3
"""Run a sync on a schedule, from cron or a systemd timer.

The platform has no scheduler inside it and this does not add one. It calls the
same endpoint the Data & connection screen calls, so a scheduled pull and a
pressed button are the same operation — there is no second code path to keep
correct, and nothing here can pull differently from the way a person does.

**Why a script and not an in-process scheduler.** A scheduler inside the app
needs to survive more than one worker without two of them firing at once, which
is leader election for a job the operating system already knows how to run. It
would also make "is the sync working" a question about application internals
rather than about a timer you can inspect, disable, and read the logs of.

**Why it signs in each run rather than carrying a token.** Tokens here have no
expiry — ``verify_token`` reads the signature and never looks at ``iat`` — so a
token in a crontab is an unexpiring credential with an owner's authority, and
withdrawing it means rotating ``AUTH_SECRET`` and signing every user out. A
password can be changed for one account without touching anybody else. Both are
secrets in the environment; only one of them is revocable on its own.

Environment:

    PIE_BASE_URL        where the API is           (default http://localhost:8000)
    PIE_SYNC_EMAIL      an owner or manager account
    PIE_SYNC_PASSWORD   that account's password
    PIE_SYNC_TIMEOUT    seconds to wait for the pull to finish (default 3600;
                        0 means start it and do not wait)

Exit codes, which are the whole interface to cron:

    0   the sync finished, or one was already running
    1   a configuration or network problem — nothing was started
    2   the sync ran and failed

A non-zero exit is what makes cron mail you. Printing an error and exiting 0 is
how a broken nightly job stays broken for a fortnight.

    cd backend && python3 ../scripts/scheduled_sync.py

See docs/operations.md for the crontab and systemd forms.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_BASE = "http://localhost:8000"
#: How long between polls while a pull is in flight. A sync reads documents
#: one at a time and takes minutes, so a tighter loop is only load.
POLL_SECONDS = 15
#: Terminal states, from ingestion/jobs.py. QUEUED and RUNNING are the other two.
DONE_OK = ("OK", "PARTIAL")
DONE_BAD = ("FAILED",)


def _log(msg: str) -> None:
    """One line per event, with a timestamp, on stdout.

    cron mails whatever a job prints, so this is the report. It never prints a
    token or a password — a credential in a mail spool is a credential.
    """
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _call(url: str, *, token: Optional[str] = None,
          body: Optional[dict] = None, timeout: int = 60) -> dict[str, Any]:
    data = json.dumps(body or {}).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _find_run(state: dict[str, Any], run_id: Optional[str]) -> Optional[dict]:
    """This script's own run, wherever the state currently holds it.

    It moves as it progresses: in ``active_runs`` while it is going, and in
    ``last`` once it is done. Matching on the id rather than reading whichever
    field looks right is what keeps a second company's concurrent pull from
    being mistaken for this one.
    """
    if not run_id:
        return None
    for row in state.get("active_runs") or []:
        if row and row.get("sync_run_id") == run_id:
            return row
    last = state.get("last") or {}
    return last if last.get("sync_run_id") == run_id else None


def _fail(msg: str, code: int = 1) -> int:
    _log(f"ERROR {msg}")
    return code


def main() -> int:
    base = os.environ.get("PIE_BASE_URL", DEFAULT_BASE).rstrip("/")
    email = os.environ.get("PIE_SYNC_EMAIL")
    password = os.environ.get("PIE_SYNC_PASSWORD")
    try:
        wait_for = int(os.environ.get("PIE_SYNC_TIMEOUT", "3600"))
    except ValueError:
        return _fail("PIE_SYNC_TIMEOUT must be a whole number of seconds")

    if not email or not password:
        return _fail("PIE_SYNC_EMAIL and PIE_SYNC_PASSWORD must both be set")

    try:
        token = _call(f"{base}/api/v1/auth/login",
                      body={"email": email, "password": password}).get("token")
    except urllib.error.HTTPError as e:
        # 401 here is a wrong password or a deactivated account, and it is the
        # failure most likely to go unnoticed: the timer keeps firing and the
        # data keeps ageing. Named rather than folded into "request failed".
        return _fail(f"sign-in as {email} failed with HTTP {e.code}. "
                     f"The account may have been deactivated or its password changed.")
    except (urllib.error.URLError, TimeoutError) as e:
        return _fail(f"could not reach {base} — {e}")
    if not token:
        return _fail("sign-in returned no token")

    try:
        started = _call(f"{base}/api/v1/data/sync", token=token, body={})
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return _fail(f"{email} is not a manager or owner, so it cannot start a sync")
        detail = e.read().decode()[:300]
        return _fail(f"starting the sync failed with HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        return _fail(f"could not reach {base} — {e}")

    run = started.get("run") or {}
    if not started.get("started"):
        # Not an error, and deliberately not exit 1. A timer firing while the
        # previous pull is still going is ordinary on a first sync that reads
        # years of documents, and mailing about it every night teaches people
        # to filter the mail that also carries the real failures.
        _log(f"a sync was already running ({run.get('status')}, "
             f"phase {run.get('phase')}) — did not start a second")
        return 0

    _log(f"started sync {run.get('sync_run_id')}")
    if wait_for <= 0:
        _log("not waiting for it to finish (PIE_SYNC_TIMEOUT=0)")
        return 0

    run_id = run.get("sync_run_id")
    deadline = time.monotonic() + wait_for
    # Poll immediately, then on the interval. A pull with nothing to fetch
    # finishes in under a second, and sleeping first meant the fast case looked
    # exactly like the hung one — which is how the first version of this
    # reported a timeout on a run that had already succeeded.
    while True:
        try:
            state = _call(f"{base}/api/v1/data/sync", token=token)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            # A restart mid-pull is not a failed sync. The loop keeps watching;
            # the deadline is what ends it.
            _log(f"could not read sync state ({e}) — still waiting")
            state = {}

        # Watch *this* run, by id. `last` is whichever run finished most
        # recently, which on a machine with two connected companies pulling
        # concurrently is not necessarily the one this script started.
        mine = _find_run(state, run_id)
        status_now = str((mine or {}).get("status") or "")

        if status_now in DONE_BAD:
            return _fail(f"sync {run_id} failed: "
                         f"{(mine or {}).get('error') or 'no reason recorded'}", code=2)
        if status_now in DONE_OK:
            counts = ", ".join(
                f"{k} {mine[k]}" for k in
                ("customers", "products", "sales_txns", "payments", "purchase_orders")
                if mine.get(k))
            _log(f"sync {run_id} finished {status_now}"
                 + (f" — {counts}" if counts else ""))
            # PARTIAL is a real outcome, not a failure: some connections pulled
            # and some did not, and the run records which. Exit 0 so the timer
            # is not treated as broken, and print it so somebody reads it.
            return 0

        if time.monotonic() >= deadline:
            return _fail(f"sync did not finish within {wait_for}s. It may still "
                         f"be running — check the Data & connection screen "
                         f"before starting another.", code=2)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
