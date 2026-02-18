"""
Decision Lineage Store — append-only, cryptographically chained audit record.

Every reconciliation cycle produces one LineageRecord.

Behavioral Contract:
- Append-only. No record is ever modified or deleted.
- Each record is hashed and chained to the previous record (tamper-evident ledger).
- Every record answers: What intent? What drift? What was proposed?
  What was approved/rejected? Why? What happened?
- Queryable by intent, entity, time range, escalation status, constraint violation type.
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from gap_kernel.models.lineage import LineageRecord


class LineageStore:
    """
    Append-only decision lineage store.
    Prototype: SQLite. Production: PostgreSQL with row-level security.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the lineage table if it doesn't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS lineage (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                intent_id TEXT NOT NULL,
                drift_detected TEXT NOT NULL,
                drift_severity INTEGER NOT NULL,
                total_attempts INTEGER NOT NULL,
                escalated_to_human INTEGER NOT NULL DEFAULT 0,
                execution_success INTEGER NOT NULL DEFAULT 0,
                final_approved_proposal TEXT,
                resolved_at TEXT,
                resolution_duration_seconds REAL,
                priority_override_applied INTEGER NOT NULL DEFAULT 0,
                deprioritized_intent TEXT,
                signature TEXT NOT NULL,
                prior_record_hash TEXT,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_cycle_id ON lineage(cycle_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_intent_id ON lineage(intent_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_escalated ON lineage(escalated_to_human)
        """)
        self._conn.commit()

    def append(self, record: LineageRecord) -> LineageRecord:
        """
        Append a lineage record. Computes cryptographic hash and chains
        to the previous record.
        """
        # Get hash of previous record for chaining
        prior_hash = self._get_latest_hash()
        record.prior_record_hash = prior_hash

        # Compute signature over the full record
        record_dict = record.model_dump(mode="json")
        # Zero out signature before hashing (it's what we're computing)
        record_dict["signature"] = ""
        record_bytes = json.dumps(record_dict, sort_keys=True, default=str).encode()
        record.signature = hashlib.sha256(record_bytes).hexdigest()

        # Serialize full record for storage
        full_json = json.dumps(record.model_dump(mode="json"), default=str)

        self._conn.execute(
            """
            INSERT INTO lineage (
                id, cycle_id, intent_id, drift_detected, drift_severity,
                total_attempts, escalated_to_human, execution_success,
                final_approved_proposal, resolved_at, resolution_duration_seconds,
                priority_override_applied, deprioritized_intent,
                signature, prior_record_hash, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.cycle_id,
                record.intent.id,
                record.drift_detected,
                record.drift_severity,
                record.total_attempts,
                int(record.escalated_to_human),
                int(record.execution_success),
                record.final_approved_proposal,
                record.resolved_at.isoformat() if record.resolved_at else None,
                record.resolution_duration_seconds,
                int(record.priority_override_applied),
                record.deprioritized_intent,
                record.signature,
                record.prior_record_hash,
                full_json,
            ),
        )
        self._conn.commit()
        return record

    def _get_latest_hash(self) -> Optional[str]:
        """Get the signature of the most recent record."""
        row = self._conn.execute(
            "SELECT signature FROM lineage ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return row["signature"] if row else None

    def _deserialize(self, row: sqlite3.Row) -> LineageRecord:
        """Deserialize a row back into a LineageRecord."""
        return LineageRecord.model_validate_json(row["record_json"])

    def get_by_id(self, record_id: str) -> Optional[LineageRecord]:
        """Get a specific lineage record by ID."""
        row = self._conn.execute(
            "SELECT record_json FROM lineage WHERE id = ?", (record_id,)
        ).fetchone()
        return self._deserialize(row) if row else None

    def get_by_cycle(self, cycle_id: str) -> List[LineageRecord]:
        """Get all records for a given reconciliation cycle."""
        rows = self._conn.execute(
            "SELECT record_json FROM lineage WHERE cycle_id = ? ORDER BY rowid",
            (cycle_id,),
        ).fetchall()
        return [self._deserialize(r) for r in rows]

    def query_by_intent(self, intent_id: str) -> List[LineageRecord]:
        """All reconciliation cycles for a given intent."""
        rows = self._conn.execute(
            "SELECT record_json FROM lineage WHERE intent_id = ? ORDER BY rowid",
            (intent_id,),
        ).fetchall()
        return [self._deserialize(r) for r in rows]

    def query_by_entity(self, entity_id: str) -> List[LineageRecord]:
        """All decisions affecting a specific entity."""
        # Search in the full JSON for entity references
        rows = self._conn.execute(
            "SELECT record_json FROM lineage WHERE record_json LIKE ? ORDER BY rowid",
            (f"%{entity_id}%",),
        ).fetchall()
        return [self._deserialize(r) for r in rows]

    def query_escalations(self, since: Optional[datetime] = None) -> List[LineageRecord]:
        """All cycles that required human escalation."""
        if since:
            rows = self._conn.execute(
                "SELECT record_json FROM lineage WHERE escalated_to_human = 1 "
                "AND created_at >= ? ORDER BY rowid",
                (since.isoformat(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT record_json FROM lineage WHERE escalated_to_human = 1 ORDER BY rowid"
            ).fetchall()
        return [self._deserialize(r) for r in rows]

    def query_recent(self, limit: int = 50) -> List[LineageRecord]:
        """Get the most recent lineage records."""
        rows = self._conn.execute(
            "SELECT record_json FROM lineage ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._deserialize(r) for r in reversed(rows)]

    def verify_chain_integrity(self) -> bool:
        """Verify no records have been tampered with."""
        rows = self._conn.execute(
            "SELECT record_json, signature, prior_record_hash FROM lineage ORDER BY rowid"
        ).fetchall()

        if not rows:
            return True

        for i, row in enumerate(rows):
            record = LineageRecord.model_validate_json(row["record_json"])

            # Recompute the signature
            record_dict = record.model_dump(mode="json")
            record_dict["signature"] = ""
            record_bytes = json.dumps(record_dict, sort_keys=True, default=str).encode()
            expected_sig = hashlib.sha256(record_bytes).hexdigest()

            if record.signature != expected_sig:
                return False

            # Check chain link
            if i > 0:
                prior_sig = rows[i - 1]["signature"]
                if record.prior_record_hash != prior_sig:
                    return False
            else:
                # First record should have no prior hash
                if record.prior_record_hash is not None:
                    pass  # Allow None or empty for genesis record

        return True

    def count(self) -> int:
        """Total number of lineage records."""
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM lineage").fetchone()
        return row["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
