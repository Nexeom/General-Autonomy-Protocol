"""Persistent record of what the Execution Fabric has already executed.

A ``GovernanceDecision`` is a SINGLE-USE authorization. Every other guard the
fabric applies — kill-switch, proposal-id binding, content-digest binding,
signature verification, verdict check — is stateless, so each one passes
identically on every replay of the same decision. This ledger is the stateful
half: it is the replay authority at EVERY authorization level, not only the L2+
gates :mod:`gap_kernel.verification.oob_ledger` covers.

The key is the ``nonce`` the Governance Kernel stamps into the signed decision
payload, so the identity of an execution is authenticated rather than asserted.

State machine
-------------

::

    (absent) --begin--> IN_PROGRESS --finish(success=True)--> COMPLETE
                          ^      |
                          |      +-- finish(success=False) --> FAILED
                          +----------------- begin ------------+

* ``begin`` on an unknown nonce claims a fresh row (``resumed=False``).
* ``begin`` on a FAILED row RESUMES it: the attempt counter advances,
  ``last_attempt_at`` is stamped, and the actions that already completed are
  returned so the caller can skip their side effects.
* ``begin`` on a COMPLETE row raises :class:`ExecutionReplayError` — the
  authorization is spent.
* ``begin`` on an IN_PROGRESS row whose lease is still live also raises: an
  attempt is running right now, and this one is a concurrent replay. Without
  that distinction N threads presenting one authorization simultaneously would
  all resume the same row and all dispatch, so single-use would hold only for
  sequential callers.
* ``finish(success=False)`` settles the row as FAILED and resumable: a transient
  dispatch failure must not burn a valid authorization.
* An IN_PROGRESS row whose lease has expired is resumable — the process holding
  it is presumed dead, so a crash mid-dispatch does not strand the
  authorization. Set ``lease_seconds`` above the longest plausible dispatch.

Pruning keys on ``COALESCE(finished_at, first_seen_at)`` so an abandoned
in-progress row ages out on the same clock as a finished one; keying on
``finished_at`` alone would retain every never-finished row forever.

Prototype: SQLite (``:memory:`` by default). Production: a durable store shared
by every Execution Fabric instance. The retention horizon must exceed the
kernel's decision TTL, or a decision could outlive its own ledger row.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import FrozenSet, Optional

from gap_kernel._time import ensure_utc, utcnow

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"
# A settled failure. Distinct from IN_PROGRESS because "an attempt failed and
# may be retried" and "an attempt is running right now" must not look alike: if
# they do, concurrent presentations of one authorization all resume the same row
# and all dispatch, and single-use holds only for sequential callers.
STATUS_FAILED = "failed"

# How long an IN_PROGRESS claim is presumed live. Within it, a second claim is a
# concurrent replay and is refused. Beyond it, the executing process is presumed
# dead and the row becomes resumable, so a crash mid-dispatch does not strand the
# authorization forever.
DEFAULT_LEASE_SECONDS = 300

# Default retention for pruning. Comfortably longer than any decision TTL, so a
# still-valid decision always has a row to be refused against.
DEFAULT_RETENTION_SECONDS = 30 * 24 * 3600


class ExecutionReplayError(Exception):
    """Raised when a decision's execution nonce cannot be claimed."""


@dataclass(frozen=True)
class ExecutionRow:
    """The ledger's view of one execution attempt."""

    nonce: str
    decision_id: str
    proposal_id: str
    status: str
    attempts: int
    resumed: bool
    completed_actions: FrozenSet[str]


class ExecutionLedger:
    """Append-and-settle record of decision executions, keyed on nonce."""

    def __init__(
        self,
        db_path: str = ":memory:",
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ):
        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self.lease_seconds = lease_seconds
        # begin() is a read-modify-write across two statements, and a fabric may
        # be driven from several threads. The lock plus BEGIN IMMEDIATE makes the
        # claim atomic — otherwise two concurrent replays could both see "not yet
        # complete" and both dispatch.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                nonce           TEXT PRIMARY KEY,
                decision_id     TEXT NOT NULL,
                proposal_id     TEXT NOT NULL,
                status          TEXT NOT NULL,
                attempts        INTEGER NOT NULL,
                first_seen_at   TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                finished_at     TEXT
            )
            """
        )
        # Per-action completion keys make a resumed execution idempotent: an
        # action that already succeeded is not dispatched again under the same
        # authorization, so a partial-failure retry cannot repeat side effects.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_actions (
                nonce        TEXT NOT NULL,
                action_key   TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (nonce, action_key)
            )
            """
        )

    # --- state machine ------------------------------------------------------

    def begin(
        self, nonce: str, *, decision_id: str, proposal_id: str
    ) -> ExecutionRow:
        """Claim (or resume) the execution of ``nonce``.

        Raises :class:`ExecutionReplayError` if the nonce is already COMPLETE, or
        if it is bound to a different decision or proposal than the caller
        presents (a nonce authorizes one decision for one proposal).
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = utcnow().isoformat()
                row = self._conn.execute(
                    "SELECT * FROM executions WHERE nonce = ?", (nonce,)
                ).fetchone()

                if row is None:
                    # A fresh claim starts with a clean action set. Any action
                    # rows still bearing this nonce are orphans of a pruned
                    # execution; inheriting them would silently skip actions that
                    # were never dispatched under THIS claim.
                    self._conn.execute(
                        "DELETE FROM execution_actions WHERE nonce = ?", (nonce,)
                    )
                    self._conn.execute(
                        "INSERT INTO executions (nonce, decision_id, proposal_id, "
                        "status, attempts, first_seen_at, last_attempt_at, finished_at) "
                        "VALUES (?, ?, ?, ?, 1, ?, ?, NULL)",
                        (nonce, decision_id, proposal_id, STATUS_IN_PROGRESS, now, now),
                    )
                    result = ExecutionRow(
                        nonce=nonce,
                        decision_id=decision_id,
                        proposal_id=proposal_id,
                        status=STATUS_IN_PROGRESS,
                        attempts=1,
                        resumed=False,
                        completed_actions=frozenset(),
                    )
                elif row["status"] == STATUS_COMPLETE:
                    raise ExecutionReplayError(
                        f"Execution nonce '{nonce}' (decision {row['decision_id']}) has "
                        f"already been executed to completion; the authorization is spent."
                    )
                elif row["decision_id"] != decision_id or row["proposal_id"] != proposal_id:
                    raise ExecutionReplayError(
                        f"Execution nonce '{nonce}' is bound to decision "
                        f"{row['decision_id']} / proposal {row['proposal_id']}, not to "
                        f"decision {decision_id} / proposal {proposal_id}."
                    )
                elif (
                    row["status"] == STATUS_IN_PROGRESS
                    and not self._lease_expired(row["last_attempt_at"])
                ):
                    raise ExecutionReplayError(
                        f"Execution nonce '{nonce}' (decision {row['decision_id']}) is "
                        f"already in flight; the authorization is single-use and cannot "
                        f"be presented concurrently."
                    )
                else:
                    self._conn.execute(
                        "UPDATE executions SET attempts = attempts + 1, "
                        "last_attempt_at = ? WHERE nonce = ?",
                        (now, nonce),
                    )
                    result = ExecutionRow(
                        nonce=nonce,
                        decision_id=row["decision_id"],
                        proposal_id=row["proposal_id"],
                        status=STATUS_IN_PROGRESS,
                        attempts=row["attempts"] + 1,
                        resumed=True,
                        completed_actions=self._completed_actions(nonce),
                    )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return result

    def _lease_expired(self, last_attempt_at: Optional[str]) -> bool:
        """True if an IN_PROGRESS claim is old enough to presume its owner died.

        A missing or unparseable stamp is treated as still-live: refusing a
        concurrent claim is the fail-closed answer, since the alternative is
        letting one authorization dispatch twice.
        """
        if not last_attempt_at:
            return False
        try:
            started = ensure_utc(datetime.fromisoformat(last_attempt_at))
        except (ValueError, TypeError):
            return False
        return (utcnow() - started).total_seconds() > self.lease_seconds

    def finish(self, nonce: str, success: bool) -> None:
        """Settle an execution.

        On success the row becomes COMPLETE and no further ``begin`` succeeds.
        On failure it becomes FAILED — settled and resumable — with
        ``last_attempt_at`` stamped, so a transient failure does not burn the
        authorization while an attempt still in flight stays distinguishable
        from one that has stopped.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = utcnow().isoformat()
                if success:
                    updated = self._conn.execute(
                        "UPDATE executions SET status = ?, finished_at = ?, "
                        "last_attempt_at = ? WHERE nonce = ?",
                        (STATUS_COMPLETE, now, now, nonce),
                    ).rowcount
                else:
                    updated = self._conn.execute(
                        "UPDATE executions SET status = ?, last_attempt_at = ? "
                        "WHERE nonce = ?",
                        (STATUS_FAILED, now, nonce),
                    ).rowcount
                if not updated:
                    raise ExecutionReplayError(
                        f"Cannot settle nonce '{nonce}': no execution was begun for it."
                    )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # --- per-action idempotency --------------------------------------------

    def record_action(self, nonce: str, action_key: str) -> None:
        """Mark one action of this execution as completed (idempotent)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO execution_actions "
                "(nonce, action_key, completed_at) VALUES (?, ?, ?)",
                (nonce, action_key, utcnow().isoformat()),
            )

    def completed_actions(self, nonce: str) -> FrozenSet[str]:
        """The action keys that have already succeeded under this nonce."""
        with self._lock:
            return self._completed_actions(nonce)

    def _completed_actions(self, nonce: str) -> FrozenSet[str]:
        rows = self._conn.execute(
            "SELECT action_key FROM execution_actions WHERE nonce = ?", (nonce,)
        ).fetchall()
        return frozenset(r["action_key"] for r in rows)

    # --- inspection / retention --------------------------------------------

    def status(self, nonce: str) -> Optional[str]:
        """The nonce's current status, or ``None`` if the ledger has no row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM executions WHERE nonce = ?", (nonce,)
            ).fetchone()
            return row["status"] if row else None

    def prune(self, max_age_seconds: Optional[int] = None) -> int:
        """Drop rows older than the retention horizon; returns how many were removed.

        Ages a row by ``COALESCE(finished_at, first_seen_at)``, so an execution
        that was begun and abandoned expires on the same clock as one that
        finished — keying on ``finished_at`` alone would retain it forever.
        """
        horizon = self.retention_seconds if max_age_seconds is None else max_age_seconds
        cutoff = (utcnow() - timedelta(seconds=horizon)).isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "DELETE FROM execution_actions WHERE nonce IN ("
                    "  SELECT nonce FROM executions "
                    "  WHERE COALESCE(finished_at, first_seen_at) < ?"
                    ")",
                    (cutoff,),
                )
                removed = self._conn.execute(
                    "DELETE FROM executions "
                    "WHERE COALESCE(finished_at, first_seen_at) < ?",
                    (cutoff,),
                ).rowcount
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return removed


__all__ = [
    "STATUS_COMPLETE",
    "STATUS_IN_PROGRESS",
    "ExecutionLedger",
    "ExecutionReplayError",
    "ExecutionRow",
]
