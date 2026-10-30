"""Append-only durable traces and lock-protected budget reservations; no SQL/values/tokens."""

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from atlas_contracts.analytics import AnalyticsError, AnalyticsModel
from pydantic import Field


class Budget(AnalyticsModel):
    max_calls: int = Field(default=50, ge=1, le=10000)
    max_elapsed_ms: int = Field(default=120000, ge=10)
    max_rows: int = Field(default=10000, ge=1)
    concurrency: int = Field(default=4, ge=1, le=64)


class AuditLog:
    def __init__(self, path: Path, budget: Budget) -> None:
        self.path, self.budget = path, budget
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Audit paths cannot be symlinks")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.parent.stat().st_mode & 0o077:
            raise PermissionError("Audit directory must have mode 0700")

    @contextmanager
    def _file(self) -> Iterator[Any]:
        descriptor = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+") as handle:
            if os.fstat(handle.fileno()).st_mode & 0o077:
                raise PermissionError("Audit file must have mode 0600")
            lock_deadline = time.monotonic() + 0.05
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= lock_deadline:
                        raise AnalyticsError(
                            "audit_unavailable", "Audit ledger is busy", True
                        ) from None
                    time.sleep(0.002)
            if os.fstat(handle.fileno()).st_size > 20_000_000:
                raise AnalyticsError("audit_unavailable", "Audit ledger requires operator rotation")
            yield handle

    @staticmethod
    def _append(handle: Any, record: dict[str, Any]) -> None:
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps({**record, "recorded_at": time.time()}, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    def reserve(self, record: dict[str, Any], timeout_ms: int, row_reservation: int) -> None:
        with self._file() as handle:
            handle.seek(0)
            try:
                records = [json.loads(line) for line in handle]
            except (ValueError, TypeError) as exc:
                raise AnalyticsError("audit_unavailable", "Audit ledger is invalid") from exc
            matching = [
                r
                for r in records
                if r.get("investigation_id") == record["investigation_id"]
                and r.get("principal") == record["principal"]
            ]
            if any(r.get("request_id") == record["request_id"] for r in matching):
                raise AnalyticsError("duplicate_request", "Request ID has already been used")
            starts = [r for r in matching if r["event"] == "started"]
            finishes = {r["trace_id"]: r for r in records if r["event"] == "finished"}
            elapsed = sum(
                finishes[r["trace_id"]]["elapsed_ms"]
                if r["trace_id"] in finishes
                else r["reserved_ms"]
                for r in starts
            )
            rows = sum(
                finishes[r["trace_id"]]["rows"] if r["trace_id"] in finishes else r["reserved_rows"]
                for r in starts
            )
            # A timed-out call can still be cancelling a driver. Never reclaim its slot
            # while the owner process lives; dead owners remain charged worst-case budget.
            active = sum(
                r["event"] == "started"
                and r["trace_id"] not in finishes
                and self._owner_alive(r.get("owner_pid"))
                for r in records
            )
            if (
                len(starts) >= self.budget.max_calls
                or elapsed + timeout_ms > self.budget.max_elapsed_ms
            ):
                raise AnalyticsError(
                    "budget_exceeded", "Investigation request/time budget exhausted"
                )
            if rows + row_reservation > self.budget.max_rows:
                raise AnalyticsError("budget_exceeded", "Investigation result budget exhausted")
            if active >= self.budget.concurrency:
                raise AnalyticsError(
                    "concurrency_limited", "Gateway is at its concurrency limit", True
                )
            self._append(
                handle,
                {
                    **record,
                    "event": "started",
                    "reserved_ms": timeout_ms,
                    "reserved_rows": row_reservation,
                    "deadline": time.time() + timeout_ms / 1000,
                    "owner_pid": os.getpid(),
                },
            )

    @staticmethod
    def _owner_alive(pid: int | None) -> bool:
        if pid is None:
            # Legacy unfinished leases have no owner identity. Recovery requires operator
            # confirmation, rather than releasing potentially live work by wall clock.
            return True
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def append(self, record: dict[str, Any]) -> None:
        with self._file() as handle:
            self._append(handle, record)

    def records(self) -> list[dict[str, Any]]:
        with self._file() as handle:
            handle.seek(0)
            return [json.loads(line) for line in handle]
