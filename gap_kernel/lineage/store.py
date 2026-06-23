"""
Decision Lineage Store — append-only, cryptographically signed + chained audit record.

Every reconciliation cycle produces one LineageRecord.

Behavioral Contract:
- Append-only. No record is ever modified or deleted.
- Each record is Ed25519-signed and chained to the previous record. Unlike a
  bare hash (which anyone could recompute after tampering), the signature
  requires the lineage private key, so a record cannot be altered and re-sealed
  without it — the chain is genuinely tamper-evident (Fix 5).
- Every record answers: What intent? What drift? What was proposed?
  What was approved/rejected? Why? What happened?
- Queryable by intent, entity, time range, escalation status, constraint violation type.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from gap_kernel.crypto.signing import generate_keypair, sign, verify
from gap_kernel.models.lineage import LineageRecord


class LineageStore:
    """
    Append-only decision lineage store.
    Prototype: SQLite. Production: PostgreSQL with row-level security + an
    external append-only anchor.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        signing_key_hex: Optional[str] = None,
        public_key_hex: Optional[str] = None,
    ):
        self.db_path = db_path
        # Ed25519 lineage signing key. Generated per-store by default; a
        # production deployment injects a managed key (and shares only the
        # public key with independent verifiers).
        if signing_key_hex and public_key_hex:
            self._signing_key_hex = signing_key_hex
            self._public_key_hex = public_key_hex
        else:
            self._signing_key_hex, self._public_key_hex = generate_keypair()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def public_key_hex(self) -> str:
        """The public key an independent verifier uses to check the chain."""
        return self._public_key_hex

    @staticmethod
    def _canonical_message(record: LineageRecord) -> str:
        """Deterministic, signature-excluded serialization that is signed/verified."""
        record_dict = record.model_dump(mode="json")
        record_dict["signature"] = ""
        record_dict["_domain"] = "gap.lineage_record.v1"
        return json.dumps(record_dict, sort_keys=True, default=str)

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
        # Chain anchor (Fix 5 hardening): a single row recording the expected
        # record count and the genesis + tip signatures, updated on every append.
        # It lets verify_chain_integrity detect head/tail/whole-chain truncation,
        # which a per-record signature + neighbour-link check alone cannot (a
        # surviving prefix/suffix is internally consistent). NOTE: in this
        # prototype the anchor lives in the same SQLite file, so it raises the bar
        # (an attacker must also rewrite the anchor) but is not absolute against
        # full DB control — production anchors this in external/WORM storage.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chain_anchor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                record_count INTEGER NOT NULL,
                genesis_signature TEXT,
                tip_signature TEXT
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

        # Ed25519-sign the record (over its canonical, signature-excluded form,
        # which includes prior_record_hash — so the signature also seals the
        # chain link). Tampering any field invalidates the signature, and it
        # cannot be re-sealed without the lineage private key.
        record.signature = sign(self._signing_key_hex, self._canonical_message(record))

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
        self._update_anchor()
        self._conn.commit()
        return record

    def _update_anchor(self) -> None:
        """Refresh the chain anchor (count, genesis sig, tip sig) after an append."""
        rows = self._conn.execute(
            "SELECT signature FROM lineage ORDER BY rowid"
        ).fetchall()
        count = len(rows)
        genesis = rows[0]["signature"] if rows else None
        tip = rows[-1]["signature"] if rows else None
        self._conn.execute(
            "INSERT INTO chain_anchor (id, record_count, genesis_signature, tip_signature) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "record_count = excluded.record_count, "
            "genesis_signature = excluded.genesis_signature, "
            "tip_signature = excluded.tip_signature",
            (count, genesis, tip),
        )

    def _get_anchor(self) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT record_count, genesis_signature, tip_signature FROM chain_anchor WHERE id = 1"
        ).fetchone()

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
        """Verify no records have been tampered with.

        For each record: the Ed25519 signature must verify against the lineage
        public key over the record's canonical form, and its ``prior_record_hash``
        must match the previous record's signature (the chain link). Either
        failure — a mutated field or a broken link — returns False.
        """
        rows = self._conn.execute(
            "SELECT record_json, signature, prior_record_hash FROM lineage ORDER BY rowid"
        ).fetchall()
        anchor = self._get_anchor()

        if not rows:
            # Empty chain is intact only if the anchor agrees it is empty.
            return anchor is None or anchor["record_count"] == 0

        # 0. Anchor checks — detect head / tail / whole-chain truncation, which
        #    a per-record + neighbour-link check cannot (a surviving prefix or
        #    suffix is internally consistent).
        if anchor is None:
            return False
        if len(rows) != anchor["record_count"]:
            return False
        if rows[0]["signature"] != anchor["genesis_signature"]:
            return False
        if rows[-1]["signature"] != anchor["tip_signature"]:
            return False
        # The first surviving record must be a true genesis (no prior link);
        # otherwise a leading record was deleted and a later one promoted.
        if rows[0]["prior_record_hash"] is not None:
            return False

        for i, row in enumerate(rows):
            record = LineageRecord.model_validate_json(row["record_json"])

            # 1. Signature must verify (proves authenticity + content integrity).
            if not verify(
                self._public_key_hex,
                self._canonical_message(record),
                record.signature or "",
            ):
                return False

            # 2. Chain link must match the previous record's signature.
            if i > 0:
                prior_sig = rows[i - 1]["signature"]
                if record.prior_record_hash != prior_sig:
                    return False

        return True

    def count(self) -> int:
        """Total number of lineage records."""
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM lineage").fetchone()
        return row["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
