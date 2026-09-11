"""Shared execution ledgers initialize atomically without losing old authority."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gap_kernel.verification.execution_ledger import ExecutionLedger, STATUS_COMPLETE


def _legacy_database(path):
    db = sqlite3.connect(path)
    try:
        db.execute("""CREATE TABLE executions (
            nonce TEXT PRIMARY KEY, decision_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL, first_seen_at TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL, finished_at TEXT)""")
        db.execute("""CREATE TABLE execution_actions (
            nonce TEXT NOT NULL, action_key TEXT NOT NULL, completed_at TEXT NOT NULL,
            PRIMARY KEY (nonce, action_key))""")
        db.execute("INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("spent", "decision", "proposal", STATUS_COMPLETE, 1,
                    "original-time", "original-time", "original-time"))
        db.execute("INSERT INTO execution_actions VALUES (?, ?, ?)",
                   ("spent", "0:original-action", "original-time"))
        db.commit()
    finally:
        db.close()


def _schema(connection):
    return connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "legacy"])
def test_simultaneous_constructors_preserve_shared_execution_state(tmp_path, monkeypatch, legacy):
    path = str(tmp_path / "shared.sqlite")
    if legacy:
        _legacy_database(path)
    start = Barrier(2)
    unprotected_reads = Barrier(2)
    real_connect = sqlite3.connect
    connections = []

    class MigrationConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            cursor = super().execute(sql, *args)
            if sql == "PRAGMA table_info(execution_actions)":
                snapshot = cursor.fetchall()
                if not self.in_transaction:
                    # Force both old, unprotected constructors to observe the
                    # missing column before either ALTER. With a transaction,
                    # SQLite excludes the other initializer from this section.
                    unprotected_reads.wait(timeout=5)
                return snapshot
            return cursor

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=MigrationConnection)
        connections.append(connection)
        return connection

    def initialize():
        start.wait(timeout=5)
        return ExecutionLedger(path)

    monkeypatch.setattr("gap_kernel.verification.execution_ledger.sqlite3.connect", connect)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(initialize) for _ in range(2)]
            first, second = [future.result(timeout=10) for future in futures]
        assert len(connections) == 2
        assert all(not connection.in_transaction for connection in connections)
        if legacy:
            assert first.status("spent") == STATUS_COMPLETE
            assert second.action_results("spent") == {"0:original-action": None}
        first.begin("new", decision_id="new-decision", proposal_id="new-proposal")
        first.record_action("new", "0:new-action", result={"receipt": "saved"})
        first.finish("new", success=True)
        assert second.status("new") == STATUS_COMPLETE
        assert second.action_results("new") == {"0:new-action": {"receipt": "saved"}}
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "legacy"])
def test_failed_schema_change_rolls_back_and_next_constructor_can_retry(
    tmp_path, monkeypatch, legacy,
):
    path = str(tmp_path / "retry.sqlite")
    if legacy:
        _legacy_database(path)
    real_connect = sqlite3.connect
    inspector = real_connect(path)
    original_schema = _schema(inspector)
    connections = []
    fail_once = [True]

    class FailingMigrationConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if "CREATE TABLE IF NOT EXISTS execution_outcomes" in sql and fail_once[0]:
                fail_once[0] = False
                # Table creation and the legacy ALTER have already run. The
                # database must return to its exact pre-migration schema.
                raise sqlite3.OperationalError("injected schema storage failure")
            return super().execute(sql, *args)

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=FailingMigrationConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr("gap_kernel.verification.execution_ledger.sqlite3.connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected schema storage failure"):
            ExecutionLedger(path)
        assert not connections[0].in_transaction
        assert _schema(inspector) == original_schema
        if legacy:
            assert inspector.execute("SELECT * FROM execution_actions").fetchall() == [
                ("spent", "0:original-action", "original-time"),
            ]
        # Leave the failed connection open: successful retry also proves its
        # rollback released the cross-connection write lock.
        retried = ExecutionLedger(path)
        assert not retried._conn.in_transaction
        assert {row[0] for row in _schema(inspector)} == {
            "executions", "execution_actions", "execution_contexts",
            "execution_outcomes", "execution_attempts",
        }
        if legacy:
            assert retried.status("spent") == STATUS_COMPLETE
            assert retried.action_results("spent") == {"0:original-action": None}
        retried.begin("usable", decision_id="decision", proposal_id="proposal")
        retried.finish("usable", success=True)
        assert retried.status("usable") == STATUS_COMPLETE
    finally:
        inspector.close()
        for connection in connections:
            connection.close()
