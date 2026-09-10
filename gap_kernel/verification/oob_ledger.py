"""Persistent, append-only ledger of reserved OOB authorizations.

Replaces the in-memory replay ``set`` that the Execution Fabric previously used.
A given ``(decision_id, signature)`` pair belongs to at most one execution.
Repeating its reservation is safe only for the same approver and non-null
execution nonce. This lets a crashed execution recover after the reservation
committed but before its own completion marker was persisted. Legacy unbound
reservations remain strictly single-use.
"""

from __future__ import annotations

import sqlite3
import threading
from gap_kernel._time import utcnow


class ReplayError(Exception):
    """Raised when an OOB authorization signature is reused."""


class OOBLedger:
    """Append-only record of which OOB authorizations have been consumed.

    Prototype: SQLite (``:memory:`` by default). Production: a durable,
    append-only store (file-backed SQLite, Postgres with row-level security,
    or a WORM ledger) shared by every Execution Fabric instance.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oob_authorizations (
                        decision_id     TEXT NOT NULL,
                        signature       TEXT NOT NULL,
                        approver_key_id TEXT NOT NULL,
                        used_at         TEXT NOT NULL,
                        execution_nonce TEXT,
                        PRIMARY KEY (decision_id, signature)
                    )
                    """
                )
                columns = {
                    row[1] for row in self._conn.execute(
                        "PRAGMA table_info(oob_authorizations)"
                    ).fetchall()
                }
                if "execution_nonce" not in columns:
                    # NULL deliberately preserves the old non-reusable contract.
                    self._conn.execute(
                        "ALTER TABLE oob_authorizations ADD COLUMN execution_nonce TEXT"
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def has_been_used(self, decision_id: str, signature: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM oob_authorizations WHERE decision_id = ? AND signature = ?",
                (decision_id, signature),
            ).fetchone()
            return row is not None

    def record_use(
        self, decision_id: str, signature: str, approver_key_id: str,
        *, execution_nonce: str | None = None,
    ) -> None:
        """Reserve an approval, or recover the exact same bound reservation.

        This does not claim the execution nonce or allow concurrent dispatch:
        the execution ledger and its caller still provide those protections.
        Unbound callers retain the original strictly single-use behavior.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT approver_key_id, execution_nonce FROM oob_authorizations "
                    "WHERE decision_id = ? AND signature = ?",
                    (decision_id, signature),
                ).fetchone()
                if row is not None:
                    if (execution_nonce is None or row[1] != execution_nonce
                            or row[0] != approver_key_id):
                        raise ReplayError(
                            f"OOB authorization for decision {decision_id} has already been used."
                        )
                else:
                    self._conn.execute(
                        "INSERT INTO oob_authorizations "
                        "(decision_id, signature, approver_key_id, used_at, execution_nonce) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (decision_id, signature, approver_key_id, utcnow().isoformat(),
                         execution_nonce),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        """Release the ledger connection after its callers have stopped."""
        with self._lock:
            self._conn.close()
