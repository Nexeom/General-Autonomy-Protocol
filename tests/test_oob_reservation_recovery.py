"""Crash-safe approval reservation without transferring authority to another execution."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gap_kernel.verification.oob_ledger import OOBLedger, ReplayError


def test_bound_reservation_survives_restart_and_keeps_original_use_time(tmp_path):
    path = str(tmp_path / "approvals.sqlite")
    ledger = OOBLedger(path)
    ledger.record_use("decision", "signature", "approver", execution_nonce="execution")
    original = ledger._conn.execute("SELECT * FROM oob_authorizations").fetchone()
    ledger.close()

    recovered = OOBLedger(path)
    try:
        # A crash before the execution ledger's reservation marker must not
        # strand an approval already reserved for this exact execution.
        recovered.record_use("decision", "signature", "approver", execution_nonce="execution")
        assert recovered.has_been_used("decision", "signature")
        assert recovered._conn.execute("SELECT * FROM oob_authorizations").fetchall() == [original]
    finally:
        recovered.close()


@pytest.mark.parametrize("approver,nonce", [
    ("approver", "another-execution"),
    ("another-approver", "execution"),
    ("approver", None),
])
def test_reservation_cannot_transfer_authority_and_rejection_releases_transaction(approver, nonce):
    ledger = OOBLedger()
    try:
        ledger.record_use("decision", "signature", "approver", execution_nonce="execution")
        with pytest.raises(ReplayError):
            ledger.record_use("decision", "signature", approver, execution_nonce=nonce)
        assert not ledger._conn.in_transaction
        # A duplicate rejection must not leave an implicit transaction open.
        ledger.record_use("fresh", "fresh-signature", "approver", execution_nonce="fresh-nonce")
        assert ledger.has_been_used("fresh", "fresh-signature")
    finally:
        ledger.close()


@pytest.mark.parametrize("nonce", [None, "execution"])
def test_legacy_schema_migrates_without_making_old_reservations_reusable(tmp_path, nonce):
    path = str(tmp_path / "legacy.sqlite")
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE oob_authorizations (
            decision_id TEXT NOT NULL, signature TEXT NOT NULL,
            approver_key_id TEXT NOT NULL, used_at TEXT NOT NULL,
            PRIMARY KEY (decision_id, signature))""")
        db.execute("INSERT INTO oob_authorizations VALUES (?, ?, ?, ?)",
                   ("decision", "signature", "approver", "original-time"))
    db.close()

    ledger = OOBLedger(path)
    try:
        with pytest.raises(ReplayError):
            ledger.record_use("decision", "signature", "approver", execution_nonce=nonce)
        assert ledger._conn.execute(
            "SELECT used_at, execution_nonce FROM oob_authorizations"
        ).fetchall() == [("original-time", None)]
        assert not ledger._conn.in_transaction
    finally:
        ledger.close()


def test_new_unbound_reservations_remain_single_use():
    ledger = OOBLedger()
    try:
        ledger.record_use("decision", "signature", "approver")
        with pytest.raises(ReplayError):
            ledger.record_use("decision", "signature", "approver")
        with pytest.raises(ReplayError):
            ledger.record_use("decision", "signature", "approver", execution_nonce="execution")
    finally:
        ledger.close()


@pytest.mark.parametrize("shared_connection", [True, False])
@pytest.mark.parametrize("same_nonce", [True, False])
def test_concurrent_reservations_preserve_one_execution_binding(
    tmp_path, shared_connection, same_nonce,
):
    path = str(tmp_path / "concurrent.sqlite")
    first = OOBLedger(path)
    second = first if shared_connection else OOBLedger(path)
    barrier = Barrier(2)

    def reserve(ledger, nonce):
        barrier.wait(timeout=5)
        try:
            ledger.record_use("decision", "signature", "approver", execution_nonce=nonce)
            return "reserved"
        except ReplayError:
            return "rejected"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(reserve, first, "execution-a")
            b = pool.submit(reserve, second, "execution-a" if same_nonce else "execution-b")
            outcomes = [a.result(timeout=10), b.result(timeout=10)]
        assert outcomes.count("reserved") == (2 if same_nonce else 1)
        assert first._conn.execute("SELECT count(*) FROM oob_authorizations").fetchone()[0] == 1
        assert not first._conn.in_transaction
        assert not second._conn.in_transaction
    finally:
        if second is not first:
            second.close()
        first.close()


def test_storage_failure_rolls_back_and_does_not_poison_later_reservations():
    ledger = OOBLedger()
    try:
        ledger._conn.execute("""CREATE TRIGGER fail_one_reservation
            BEFORE INSERT ON oob_authorizations WHEN NEW.decision_id = 'broken'
            BEGIN SELECT RAISE(ABORT, 'simulated_storage_failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="simulated_storage_failure"):
            ledger.record_use("broken", "signature", "approver", execution_nonce="execution")
        assert not ledger._conn.in_transaction
        assert not ledger.has_been_used("broken", "signature")
        ledger.record_use("working", "signature", "approver", execution_nonce="execution")
        assert ledger.has_been_used("working", "signature")
    finally:
        ledger.close()
