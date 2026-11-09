"""
Connection audit: process-wide socket/
resolver attempt logging, so a deployment that must make no outbound network calls can be
checked from its own log rather than assumed to comply.

Self-contained on purpose: it imports nothing from the rest of the service and
is installed at startup, before any networking code runs.

`install` monkeypatches `socket.socket.connect`/`connect_ex` and
`socket.getaddrinfo` -- the chokepoints every Python networking library
(requests, httpx, raw sockets) ultimately calls through -- to append one
JSON line per attempt to a per-run log file before the call proceeds. This
is an AUDIT, not a firewall: it never blocks a call, so it is safe to
install unconditionally; what actually prevents egress is the deployment's
network topology. Installing is a no-op unless `CONN_AUDIT_LOG_DIR` is set.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()
_log_path: Optional[Path] = None
_installed = False

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo


def _record(kind: str, target: object) -> None:
    if _log_path is None:
        return
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kind": kind,
        "target": repr(target),
        "pid": os.getpid(),
    }
    with _lock:
        with _log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _audited_connect(self: socket.socket, address):
    _record("connect", address)
    return _orig_connect(self, address)


def _audited_connect_ex(self: socket.socket, address):
    _record("connect_ex", address)
    return _orig_connect_ex(self, address)


def _audited_getaddrinfo(host, port, *args, **kwargs):
    _record("getaddrinfo", (host, port))
    return _orig_getaddrinfo(host, port, *args, **kwargs)


def install(service_name: str) -> Optional[Path]:
    """Idempotent (repeated calls after the first are a no-op, returning the
    same path). Reads `CONN_AUDIT_LOG_DIR` from the environment; when unset,
    returns `None` and installs nothing (audit is opt-in per profile). The
    per-run artifact is created eagerly, empty, so a log file exists for every run, even one that makes zero outbound
    attempts -- an empty file is the proof of zero, not an absent one."""
    global _log_path, _installed
    log_dir = os.environ.get("CONN_AUDIT_LOG_DIR")
    if not log_dir:
        return None
    if _installed:
        return _log_path

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    start_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _log_path = Path(log_dir) / f"connections-{service_name}-{start_ts}.jsonl"
    _log_path.touch()

    socket.socket.connect = _audited_connect
    socket.socket.connect_ex = _audited_connect_ex
    socket.getaddrinfo = _audited_getaddrinfo
    _installed = True
    return _log_path


def current_log_path() -> Optional[Path]:
    return _log_path


def attempt_count() -> int:
    """Test/verify helper: number of recorded attempts in the current run's
    artifact, 0 if not installed or nothing was ever recorded."""
    if _log_path is None or not _log_path.exists():
        return 0
    with _log_path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _reset_for_tests() -> None:
    """Test-only: undo `install` so successive tests get a clean slate.
    Never called by application code."""
    global _log_path, _installed
    socket.socket.connect = _orig_connect
    socket.socket.connect_ex = _orig_connect_ex
    socket.getaddrinfo = _orig_getaddrinfo
    _log_path = None
    _installed = False
