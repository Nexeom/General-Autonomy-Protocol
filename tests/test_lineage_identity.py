"""Lineage chain integrity — concurrency, forward compatibility, anchor authenticity."""

import sqlite3
import threading
import time
from uuid import uuid4

import pytest

from gap_kernel import _time
from gap_kernel.lineage import store as store_module
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal


def _make_lineage_record(
    record_id: str = "lin_1",
    cycle_id: str = "cycle_1",
    intent_id: str = "intent_1",
    action_target: str = "target_1",
    entity_ids=(),
    drift_detected: str = "Test drift",
) -> LineageRecord:
    now = _time.utcnow()
    intent = IntentVector(
        id=intent_id,
        objective="Test objective",
        priority=50,
        hard_constraints=[],
        soft_constraints=[],
        created_by="test",
        created_at=now,
    )
    proposal = StrategyProposal(
        id=f"prop_{record_id}",
        intent_id=intent_id,
        attempt_number=1,
        plan_description="Test plan",
        actions=[
            PlannedAction(
                action_type="test_action",
                target=action_target,
                parameters={},
                risk_score=2,
            )
        ],
        estimated_cost=0.5,
        rationale="Test rationale",
        generated_at=now,
    )
    decision = GovernanceDecision(
        id=f"dec_{record_id}",
        proposal_id=proposal.id,
        verdict=GovernanceVerdict.APPROVED,
        evaluated_at=now,
    )
    entities = {
        entity_id: {
            "entity_type": "lead",
            "entity_id": entity_id,
            "properties": {},
            "last_updated": now.isoformat(),
            "source": "test",
            "confidence": 1.0,
            "obligations": [],
        }
        for entity_id in entity_ids
    }
    return LineageRecord(
        id=record_id,
        cycle_id=cycle_id,
        intent=intent,
        drift_detected=drift_detected,
        drift_severity=5,
        world_state_snapshot={"entities": entities},
        proposals=[proposal],
        governance_decisions=[decision],
        total_attempts=1,
        escalated_to_human=False,
        execution_success=True,
        resolved_at=now,
    )


def _unique_db(tmp_path) -> str:
    return str(tmp_path / f"lineage_{uuid4().hex}.db")


def _write_legacy_database(db_path: str) -> None:
    """A lineage database in the pre-signed-payload layout, as found on disk."""
    record = _make_lineage_record(record_id="lin_legacy", cycle_id="cycle_legacy")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE lineage (
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
    conn.execute("""
        CREATE TABLE chain_anchor (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            record_count INTEGER NOT NULL,
            genesis_signature TEXT,
            tip_signature TEXT
        )
    """)
    signature = "ab" * 64
    conn.execute(
        "INSERT INTO lineage (id, cycle_id, intent_id, drift_detected, drift_severity, "
        "total_attempts, signature, prior_record_hash, record_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        (
            record.id,
            record.cycle_id,
            record.intent.id,
            record.drift_detected,
            record.drift_severity,
            record.total_attempts,
            signature,
            record.model_dump_json(),
        ),
    )
    conn.execute(
        "INSERT INTO chain_anchor (id, record_count, genesis_signature, tip_signature) "
        "VALUES (1, 1, ?, ?)",
        (signature, signature),
    )
    conn.commit()
    conn.close()


def _append_concurrently(store: LineageStore, thread_count: int, per_thread: int) -> None:
    """Run ``thread_count`` real threads that all start appending at the same instant."""
    barrier = threading.Barrier(thread_count)
    failures: list = []

    def worker(worker_index: int) -> None:
        try:
            barrier.wait(timeout=30)
            for j in range(per_thread):
                store.append(
                    _make_lineage_record(
                        record_id=f"lin_{worker_index}_{j}",
                        cycle_id=f"cycle_{worker_index}_{j}",
                    )
                )
        except BaseException as exc:  # surfaced on the main thread below
            failures.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not [t for t in threads if t.is_alive()], "append deadlocked"
    assert failures == [], f"append raised under concurrency: {failures[0]!r}"


class TestConcurrentAppend:
    """B1 — append is a read-modify-write shared across threadpool workers."""

    def test_concurrent_appends_preserve_every_record(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            _append_concurrently(store, thread_count=8, per_thread=6)
            assert store.count() == 48
            assert store.verify_chain_integrity() is True
        finally:
            store.close()

    def test_concurrent_appends_survive_a_widened_race_window(self, tmp_path, monkeypatch):
        """Signing is slow enough in production to interleave; force that window."""
        real_sign = store_module.sign

        def slow_sign(private_key_hex: str, message: str) -> str:
            time.sleep(0.005)
            return real_sign(private_key_hex, message)

        monkeypatch.setattr(store_module, "sign", slow_sign)

        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            _append_concurrently(store, thread_count=6, per_thread=3)
            assert store.count() == 18
            ids = {r.id for r in store.query_recent(limit=100)}
            assert len(ids) == 18
            assert store.verify_chain_integrity() is True
        finally:
            store.close()

    def test_concurrent_chain_links_are_unique_and_ordered(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            _append_concurrently(store, thread_count=4, per_thread=5)
            rows = store._conn.execute(
                "SELECT signature, prior_record_hash FROM lineage ORDER BY rowid"
            ).fetchall()
            assert rows[0]["prior_record_hash"] is None
            links = [r["prior_record_hash"] for r in rows[1:]]
            assert len(set(links)) == len(links)
            for i in range(1, len(rows)):
                assert rows[i]["prior_record_hash"] == rows[i - 1]["signature"]
        finally:
            store.close()


class _ExtendedLineageRecord(LineageRecord):
    """A future revision of the model, with one added field."""

    regulatory_basis: str = "unspecified"


class TestForwardCompatibleSignatures:
    """B2 — the signed bytes are persisted, so history is not re-derived."""

    def test_chain_survives_close_and_reopen(self, tmp_path):
        db_path = _unique_db(tmp_path)
        store = LineageStore(db_path=db_path)
        signing_key = store._signing_key_hex
        public_key = store.public_key_hex
        for i in range(5):
            store.append(_make_lineage_record(record_id=f"lin_{i}", cycle_id=f"cycle_{i}"))
        assert store.verify_chain_integrity() is True
        store.close()

        reopened = LineageStore(
            db_path=db_path,
            signing_key_hex=signing_key,
            public_key_hex=public_key,
        )
        try:
            assert reopened.count() == 5
            assert reopened.verify_chain_integrity() is True
            reopened.append(_make_lineage_record(record_id="lin_5", cycle_id="cycle_5"))
            assert reopened.verify_chain_integrity() is True
        finally:
            reopened.close()

    def test_added_model_field_does_not_invalidate_history(self, tmp_path, monkeypatch):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            for i in range(3):
                store.append(
                    _make_lineage_record(record_id=f"lin_{i}", cycle_id=f"cycle_{i}")
                )
            assert store.verify_chain_integrity() is True

            raw_json = store._conn.execute(
                "SELECT record_json FROM lineage ORDER BY rowid LIMIT 1"
            ).fetchone()["record_json"]
            widened = _ExtendedLineageRecord.model_validate_json(raw_json)
            assert "regulatory_basis" in widened.model_dump(mode="json")

            monkeypatch.setattr(store_module, "LineageRecord", _ExtendedLineageRecord)
            assert store.verify_chain_integrity() is True
        finally:
            store.close()

    def test_schema_version_is_recorded_for_every_record(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            store.append(_make_lineage_record())
            row = store._conn.execute(
                "SELECT signed_payload, schema_version FROM lineage"
            ).fetchone()
            assert row["schema_version"] == store_module.LINEAGE_SCHEMA_VERSION
            assert row["signed_payload"]
            assert '"_schema_version"' in row["signed_payload"]
        finally:
            store.close()

    def test_pre_migration_records_fail_closed(self, tmp_path):
        """A database written before signed payloads existed cannot be verified."""
        db_path = _unique_db(tmp_path)
        _write_legacy_database(db_path)

        store = LineageStore(db_path=db_path)
        try:
            columns = {
                row["name"]
                for row in store._conn.execute("PRAGMA table_info(lineage)")
            }
            assert {"signed_payload", "schema_version"} <= columns
            assert store.count() == 1
            assert store.verify_chain_integrity() is False
        finally:
            store.close()

    def test_substituted_record_json_is_rejected(self, tmp_path):
        """Swapping a record's body while keeping its authentic signed payload fails."""
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            for i in range(3):
                store.append(
                    _make_lineage_record(record_id=f"lin_{i}", cycle_id=f"cycle_{i}")
                )
            other_json = store._conn.execute(
                "SELECT record_json FROM lineage WHERE id = 'lin_0'"
            ).fetchone()["record_json"]
            store._conn.execute(
                "UPDATE lineage SET record_json = ? WHERE id = 'lin_1'", (other_json,)
            )
            assert store.verify_chain_integrity() is False
        finally:
            store.close()


class TestSignedChainAnchor:
    """B3 — the truncation anchor is itself signed."""

    def _store_with_three(self, tmp_path) -> LineageStore:
        store = LineageStore(db_path=_unique_db(tmp_path))
        for i in range(3):
            store.append(_make_lineage_record(record_id=f"lin_{i}", cycle_id=f"cycle_{i}"))
        assert store.verify_chain_integrity() is True
        return store

    def test_anchor_is_signed(self, tmp_path):
        store = self._store_with_three(tmp_path)
        try:
            anchor = store._get_anchor()
            assert anchor["signature"]
            assert store._verify_anchor(anchor) is True
        finally:
            store.close()

    def test_rewritten_anchor_cannot_hide_truncation(self, tmp_path):
        """Deleting the tip and re-pointing the anchor at the new tip is detected."""
        store = self._store_with_three(tmp_path)
        try:
            store._conn.execute("DELETE FROM lineage WHERE id = 'lin_2'")
            surviving_tip = store._conn.execute(
                "SELECT signature FROM lineage ORDER BY rowid DESC LIMIT 1"
            ).fetchone()["signature"]
            store._conn.execute(
                "UPDATE chain_anchor SET record_count = 2, tip_signature = ? WHERE id = 1",
                (surviving_tip,),
            )
            assert store.verify_chain_integrity() is False
        finally:
            store.close()

    def test_unsigned_anchor_fails_closed(self, tmp_path):
        store = self._store_with_three(tmp_path)
        try:
            store._conn.execute("UPDATE chain_anchor SET signature = NULL WHERE id = 1")
            assert store.verify_chain_integrity() is False
        finally:
            store.close()

    def test_missing_anchor_with_records_fails_closed(self, tmp_path):
        store = self._store_with_three(tmp_path)
        try:
            store._conn.execute("DELETE FROM chain_anchor")
            assert store.verify_chain_integrity() is False
        finally:
            store.close()

    def test_empty_chain_is_intact(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            assert store.verify_chain_integrity() is True
        finally:
            store.close()


class TestQueryByEntityPrecision:
    """An audit query must not answer with records that merely mention the id."""

    def test_underscore_in_entity_id_is_not_a_wildcard(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            store.append(
                _make_lineage_record(
                    record_id="lin_real",
                    cycle_id="cycle_real",
                    action_target="lead_4821",
                    entity_ids=("lead_4821",),
                )
            )
            store.append(
                _make_lineage_record(
                    record_id="lin_decoy",
                    cycle_id="cycle_decoy",
                    action_target="leadX4821",
                    entity_ids=("leadX4821",),
                )
            )
            results = store.query_by_entity("lead_4821")
            assert [r.id for r in results] == ["lin_real"]
        finally:
            store.close()

    def test_percent_in_entity_id_is_not_a_wildcard(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            store.append(
                _make_lineage_record(
                    record_id="lin_a",
                    cycle_id="cycle_a",
                    action_target="acct-a-zzz-b",
                    entity_ids=("acct-a-zzz-b",),
                )
            )
            assert store.query_by_entity("acct-a%b") == []
        finally:
            store.close()

    def test_incidental_mention_is_not_a_match(self, tmp_path):
        """An id appearing only in free text is not a decision about that entity."""
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            store.append(
                _make_lineage_record(
                    record_id="lin_mention",
                    cycle_id="cycle_mention",
                    action_target="ticket-9",
                    entity_ids=("ticket-9",),
                    drift_detected="Blocked because lead-4821 has no consent",
                )
            )
            assert store.query_by_entity("lead-4821") == []
        finally:
            store.close()

    def test_exact_entity_and_action_target_still_match(self, tmp_path):
        store = LineageStore(db_path=_unique_db(tmp_path))
        try:
            store.append(
                _make_lineage_record(
                    record_id="lin_entity",
                    cycle_id="cycle_entity",
                    action_target="other-target",
                    entity_ids=("lead-1",),
                )
            )
            store.append(
                _make_lineage_record(
                    record_id="lin_target",
                    cycle_id="cycle_target",
                    action_target="lead-1",
                    entity_ids=(),
                )
            )
            assert {r.id for r in store.query_by_entity("lead-1")} == {
                "lin_entity",
                "lin_target",
            }
        finally:
            store.close()


@pytest.mark.parametrize("entity_id", ["a%b", "a_b", "a\\b", 'a"b'])
def test_query_by_entity_handles_metacharacters(tmp_path, entity_id):
    store = LineageStore(db_path=_unique_db(tmp_path))
    try:
        store.append(
            _make_lineage_record(
                record_id="lin_meta",
                cycle_id="cycle_meta",
                action_target=entity_id,
                entity_ids=(entity_id,),
            )
        )
        store.append(
            _make_lineage_record(
                record_id="lin_other",
                cycle_id="cycle_other",
                action_target="unrelated",
                entity_ids=("unrelated",),
            )
        )
        assert [r.id for r in store.query_by_entity(entity_id)] == ["lin_meta"]
    finally:
        store.close()
