"""Persistent, append-only ledger of consumed OOB authorizations (Fix 4).

Replaces the in-memory replay ``set`` that the Execution Fabric previously used.
A given ``(decision_id, signature)`` pair may be consumed at most once; the
PRIMARY KEY enforces non-replayability even across a restarted Execution Fabric
when the ledger is backed by a file rather than ``:memory:``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


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
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oob_authorizations (
                decision_id     TEXT NOT NULL,
                signature       TEXT NOT NULL,
                approver_key_id TEXT NOT NULL,
                used_at         TEXT NOT NULL,
                PRIMARY KEY (decision_id, signature)
            )
            """
        )
        self._conn.commit()

    def has_been_used(self, decision_id: str, signature: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM oob_authorizations WHERE decision_id = ? AND signature = ?",
            (decision_id, signature),
        ).fetchone()
        return row is not None

    def record_use(
        self, decision_id: str, signature: str, approver_key_id: str
    ) -> None:
        """Consume an authorization. Raises :class:`ReplayError` if already used."""
        try:
            self._conn.execute(
                "INSERT INTO oob_authorizations "
                "(decision_id, signature, approver_key_id, used_at) "
                "VALUES (?, ?, ?, ?)",
                (decision_id, signature, approver_key_id, datetime.utcnow().isoformat()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ReplayError(
                f"OOB authorization for decision {decision_id} has already been used."
            )
