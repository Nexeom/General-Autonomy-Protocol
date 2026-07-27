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
import threading
from datetime import datetime
from typing import List, Optional

from gap_kernel.crypto.signing import generate_keypair, sign, verify
from gap_kernel.models.lineage import LineageRecord

# Version of the signed-payload format. It is part of every signed message, so a
# payload sealed under one format cannot be replayed as another.
LINEAGE_SCHEMA_VERSION = 1

_RECORD_DOMAIN = "gap.lineage_record.v1"
_ANCHOR_DOMAIN = "gap.lineage_anchor.v1"

# Escape character for SQL LIKE patterns built from caller-supplied strings.
_LIKE_ESCAPE = "\\"


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
        # The connection is shared across threads (every API route is a sync
        # `def`, which Starlette runs in a worker threadpool), so an append is a
        # read-modify-write that two threads can interleave. isolation_level=None
        # hands transaction control to us: append takes this lock and wraps its
        # read of the tip and its write in a single BEGIN IMMEDIATE transaction.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def public_key_hex(self) -> str:
        """The public key an independent verifier uses to check the chain."""
        return self._public_key_hex

    @staticmethod
    def _canonical_from_dict(record_data: dict, schema_version: int) -> str:
        """Deterministic, signature-excluded serialization that is signed/verified.

        Operates on plain JSON data, never on the Pydantic model, so a field
        added to ``LineageRecord`` later cannot change the bytes a historical
        record was signed over.
        """
        payload = dict(record_data)
        payload["signature"] = ""
        payload["_domain"] = _RECORD_DOMAIN
        payload["_schema_version"] = schema_version
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def _canonical_payload(cls, record_json: str, schema_version: int) -> str:
        """Canonical form of a stored record, derived from its stored JSON text."""
        return cls._canonical_from_dict(json.loads(record_json), schema_version)

    @classmethod
    def _canonical_message(
        cls, record: LineageRecord, schema_version: int = LINEAGE_SCHEMA_VERSION
    ) -> str:
        """Canonical form of a live record object."""
        return cls._canonical_from_dict(cls._record_data(record), schema_version)

    @staticmethod
    def _record_data(record: LineageRecord) -> dict:
        """The record as plain JSON data, round-tripped through text.

        The round-trip normalizes anything ``default=str`` had to coerce, so the
        data signed at append time is byte-identical to the data reconstructed
        from the stored JSON at verification time.
        """
        return json.loads(json.dumps(record.model_dump(mode="json"), default=str))

    @staticmethod
    def _anchor_message(
        record_count: int,
        genesis_signature: Optional[str],
        tip_signature: Optional[str],
        schema_version: int,
    ) -> str:
        """Deterministic serialization of the chain anchor that is signed/verified."""
        return json.dumps(
            {
                "_domain": _ANCHOR_DOMAIN,
                "_schema_version": schema_version,
                "record_count": record_count,
                "genesis_signature": genesis_signature,
                "tip_signature": tip_signature,
            },
            sort_keys=True,
        )

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
                signed_payload TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Chain anchor (Fix 5 hardening): a single row recording the expected
        # record count and the genesis + tip signatures, updated on every append.
        # It lets verify_chain_integrity detect head/tail/whole-chain truncation,
        # which a per-record signature + neighbour-link check alone cannot (a
        # surviving prefix/suffix is internally consistent). The anchor is itself
        # signed with the lineage key, so rewriting it to match a truncated chain
        # is not enough. NOTE: in this prototype the anchor still lives in the
        # same SQLite file as the records, so an attacker holding both the file
        # and the signing key can still forge a consistent shorter history —
        # production anchors this in external/WORM storage.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chain_anchor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                record_count INTEGER NOT NULL,
                genesis_signature TEXT,
                tip_signature TEXT,
                schema_version INTEGER,
                signature TEXT
            )
        """)
        # Databases created before signed payloads / anchor signatures existed.
        # The columns are added nullable; verification treats a NULL as
        # unverifiable, which is a violation, not a pass.
        self._add_missing_columns(
            "lineage", {"signed_payload": "TEXT", "schema_version": "INTEGER"}
        )
        self._add_missing_columns(
            "chain_anchor", {"schema_version": "INTEGER", "signature": "TEXT"}
        )
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_cycle_id ON lineage(cycle_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_intent_id ON lineage(intent_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_escalated ON lineage(escalated_to_human)
        """)

    def _add_missing_columns(self, table: str, columns: dict) -> None:
        """Add any of ``columns`` (name -> SQL type) the table does not yet have."""
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def append(self, record: LineageRecord) -> LineageRecord:
        """
        Append a lineage record. Computes cryptographic hash and chains
        to the previous record.
        """
        with self._lock:
            # BEGIN IMMEDIATE takes the write lock up front, so reading the tip
            # and writing the new record cannot interleave with another append.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Get hash of previous record for chaining
                record.prior_record_hash = self._get_latest_hash()

                # Ed25519-sign the record (over its canonical, signature-excluded
                # form, which includes prior_record_hash — so the signature also
                # seals the chain link). Tampering any field invalidates the
                # signature, and it cannot be re-sealed without the lineage
                # private key. The exact signed bytes are stored alongside the
                # record so verification never has to re-derive them.
                record_data = self._record_data(record)
                signed_payload = self._canonical_from_dict(
                    record_data, LINEAGE_SCHEMA_VERSION
                )
                record.signature = sign(self._signing_key_hex, signed_payload)

                # Serialize full record for storage
                record_data["signature"] = record.signature
                full_json = json.dumps(record_data)

                self._conn.execute(
                    """
                    INSERT INTO lineage (
                        id, cycle_id, intent_id, drift_detected, drift_severity,
                        total_attempts, escalated_to_human, execution_success,
                        final_approved_proposal, resolved_at, resolution_duration_seconds,
                        priority_override_applied, deprioritized_intent,
                        signature, prior_record_hash, record_json,
                        signed_payload, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        signed_payload,
                        LINEAGE_SCHEMA_VERSION,
                    ),
                )
                self._update_anchor()
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        return record

    def _update_anchor(self) -> None:
        """Refresh the chain anchor (count, genesis sig, tip sig) after an append."""
        rows = self._conn.execute(
            "SELECT signature FROM lineage ORDER BY rowid"
        ).fetchall()
        count = len(rows)
        genesis = rows[0]["signature"] if rows else None
        tip = rows[-1]["signature"] if rows else None
        anchor_signature = sign(
            self._signing_key_hex,
            self._anchor_message(count, genesis, tip, LINEAGE_SCHEMA_VERSION),
        )
        self._conn.execute(
            "INSERT INTO chain_anchor (id, record_count, genesis_signature, tip_signature, "
            "schema_version, signature) "
            "VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "record_count = excluded.record_count, "
            "genesis_signature = excluded.genesis_signature, "
            "tip_signature = excluded.tip_signature, "
            "schema_version = excluded.schema_version, "
            "signature = excluded.signature",
            (count, genesis, tip, LINEAGE_SCHEMA_VERSION, anchor_signature),
        )

    def _get_anchor(self) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT record_count, genesis_signature, tip_signature, schema_version, signature "
            "FROM chain_anchor WHERE id = 1"
        ).fetchone()

    def _verify_anchor(self, anchor: sqlite3.Row) -> bool:
        """Check the anchor's own Ed25519 signature."""
        schema_version = anchor["schema_version"]
        anchor_signature = anchor["signature"]
        if schema_version is None or not anchor_signature:
            return False
        return verify(
            self._public_key_hex,
            self._anchor_message(
                anchor["record_count"],
                anchor["genesis_signature"],
                anchor["tip_signature"],
                schema_version,
            ),
            anchor_signature,
        )

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

    @staticmethod
    def _like_literal(value: str) -> str:
        """Escape LIKE metacharacters so ``value`` matches literally under ESCAPE."""
        return (
            value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
            .replace("%", _LIKE_ESCAPE + "%")
            .replace("_", _LIKE_ESCAPE + "_")
        )

    @staticmethod
    def _references_entity(record: LineageRecord, entity_id: str) -> bool:
        """Whether the record actually concerns ``entity_id``.

        A match is a world-model entity key/id or an action target — not any
        substring of the serialized record.
        """
        entities = record.world_state_snapshot.get("entities")
        if isinstance(entities, dict):
            if entity_id in entities:
                return True
            for entity in entities.values():
                if isinstance(entity, dict) and entity.get("entity_id") == entity_id:
                    return True
        for proposal in record.proposals:
            for action in proposal.actions:
                if action.target == entity_id:
                    return True
        return False

    def query_by_entity(self, entity_id: str) -> List[LineageRecord]:
        """All decisions affecting a specific entity.

        The JSON substring scan only narrows the candidate set; a record is
        returned only if it genuinely references the entity. ``%`` and ``_`` in
        an entity id are escaped, so they cannot act as LIKE wildcards.
        """
        # The id as it appears inside the stored JSON text (quotes/backslashes escaped).
        json_fragment = json.dumps(entity_id)[1:-1]
        rows = self._conn.execute(
            "SELECT record_json FROM lineage WHERE record_json LIKE ? ESCAPE ? ORDER BY rowid",
            (f"%{self._like_literal(json_fragment)}%", _LIKE_ESCAPE),
        ).fetchall()
        candidates = (self._deserialize(r) for r in rows)
        return [r for r in candidates if self._references_entity(r, entity_id)]

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
        public key over the record's *stored* signed payload, the stored record
        JSON must canonicalize to exactly that payload, and its
        ``prior_record_hash`` must match the previous record's signature (the
        chain link). Any failure — a mutated field, a substituted payload, or a
        broken link — returns False. A record whose signed payload or schema
        version is missing cannot be evaluated, which is a violation, not a pass.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_json, signature, prior_record_hash, signed_payload, "
                "schema_version FROM lineage ORDER BY rowid"
            ).fetchall()
            anchor = self._get_anchor()

        if anchor is None:
            # No anchor was ever written: intact only if there is no chain either.
            return not rows

        # 0. Anchor checks — detect head / tail / whole-chain truncation, which
        #    a per-record + neighbour-link check cannot (a surviving prefix or
        #    suffix is internally consistent). The anchor carries its own
        #    signature, so it cannot simply be rewritten to match a shortened
        #    chain without the lineage private key.
        if not self._verify_anchor(anchor):
            return False
        if len(rows) != anchor["record_count"]:
            return False

        if not rows:
            return True

        if rows[0]["signature"] != anchor["genesis_signature"]:
            return False
        if rows[-1]["signature"] != anchor["tip_signature"]:
            return False
        # The first surviving record must be a true genesis (no prior link);
        # otherwise a leading record was deleted and a later one promoted.
        if rows[0]["prior_record_hash"] is not None:
            return False

        for i, row in enumerate(rows):
            signed_payload = row["signed_payload"]
            schema_version = row["schema_version"]
            if signed_payload is None or schema_version is None:
                return False

            # 1. Signature must verify over the stored bytes (proves authenticity).
            if not verify(self._public_key_hex, signed_payload, row["signature"] or ""):
                return False

            # 2. The stored record must be exactly what those bytes sealed.
            try:
                record_data = json.loads(row["record_json"])
            except (TypeError, ValueError):
                return False
            if self._canonical_from_dict(record_data, schema_version) != signed_payload:
                return False

            # 3. Chain link must match the previous record's signature.
            if i > 0 and record_data.get("prior_record_hash") != rows[i - 1]["signature"]:
                return False

        return True

    def count(self) -> int:
        """Total number of lineage records."""
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM lineage").fetchone()
        return row["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
