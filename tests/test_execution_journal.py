"""Durable execution receipts and settlement history survive recovery failures."""

import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

import gap_kernel.verification.execution_ledger as ledger_module
from gap_kernel._time import utcnow
from gap_kernel.verification.execution_ledger import (
    STATUS_COMPLETE, STATUS_FAILED, STATUS_IN_PROGRESS,
    ExecutionLedger, ExecutionReplayError,
)


def _claim(ledger, nonce="nonce"):
    return ledger.begin(nonce, decision_id="decision-" + nonce, proposal_id="proposal-" + nonce)


def _settle(ledger, nonce="nonce", *, success=True):
    result = {"success": success, "actions_completed": [{"receipt": nonce}]}
    decision = {"id": "decision-" + nonce, "nonce": nonce}
    ledger.finish(nonce, success, result=result, decision=decision)
    return result, decision


@pytest.mark.parametrize("success", [False, True])
def test_settlement_and_authorized_outcome_survive_reopen(tmp_path, success):
    path = str(tmp_path / "journal.sqlite")
    context = {"request_id": "request", "proposal": {"id": "proposal-nonce"}}
    with closing(ExecutionLedger(path)) as ledger:
        _claim(ledger)
        ledger.set_context("nonce", context)
        result, decision = _settle(ledger, success=success)

    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.status("nonce") == (STATUS_COMPLETE if success else STATUS_FAILED)
        assert ledger.outcomes("nonce") == [{
            "attempt": 1, "result": result, "decision": decision, "context": context,
        }]
        if success:
            with pytest.raises(ExecutionReplayError):
                _claim(ledger)
        else:
            assert _claim(ledger).attempts == 2


@pytest.mark.parametrize("success", [False, True])
def test_failed_outcome_write_rolls_back_settlement(tmp_path, success):
    """A disk-level journal failure cannot leave status claiming settlement."""
    path = str(tmp_path / "atomic.sqlite")
    with closing(ExecutionLedger(path)) as ledger:
        _claim(ledger)
        receipt = {"committed": "tool-effect"}
        ledger.record_action("nonce", "action", result=receipt)
        ledger._conn.execute("""CREATE TRIGGER fail_outcome BEFORE INSERT ON execution_outcomes
            BEGIN SELECT RAISE(ABORT, 'injected outcome write failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected outcome"):
            _settle(ledger, success=success)

    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.status("nonce") == STATUS_IN_PROGRESS
        assert ledger.outcomes("nonce") == []
        assert ledger.action_results("nonce") == {"action": receipt}
        with pytest.raises(ExecutionReplayError, match="in flight"):
            _claim(ledger)
        ledger._conn.execute("DROP TRIGGER fail_outcome")
        _settle(ledger, success=success)
        assert ledger.status("nonce") == (STATUS_COMPLETE if success else STATUS_FAILED)
        assert len(ledger.outcomes("nonce")) == 1


def test_outcome_without_decision_does_not_settle(tmp_path):
    with closing(ExecutionLedger(str(tmp_path / "missing-decision.sqlite"))) as ledger:
        _claim(ledger)
        with pytest.raises(ValueError, match="authorized decision"):
            ledger.finish("nonce", True, result={"success": True})
        assert ledger.status("nonce") == STATUS_IN_PROGRESS
        assert ledger.outcomes("nonce") == []


def test_context_is_immutable_and_canonical_across_reopen(tmp_path):
    path = str(tmp_path / "context.sqlite")
    original = {"request_id": "original", "proposal": {"target": "demo", "cost": 1}}
    with closing(ExecutionLedger(path)) as ledger:
        # The gateway pins context before attempting dispatch.
        ledger.set_context("nonce", original)
        _claim(ledger)

    with closing(ExecutionLedger(path)) as ledger:
        ledger.set_context("nonce", {
            "proposal": {"cost": 1, "target": "demo"}, "request_id": "original",
        })
        with pytest.raises(ExecutionReplayError, match="context cannot change"):
            ledger.set_context("nonce", {"request_id": "replacement"})
        _settle(ledger)
        assert ledger.outcomes("nonce")[0]["context"] == original


def test_action_receipts_roundtrip_without_overwriting_first_result(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    receipt = {"data": {"note_id": 42, "text": "Reviewed ✓", "values": [None, True, 0]}}
    with closing(ExecutionLedger(path)) as ledger:
        _claim(ledger)
        ledger.record_action("nonce", "with-receipt", result=receipt)
        ledger.record_action("nonce", "legacy-marker")
        ledger.record_action("nonce", "with-receipt", result={"data": "replacement"})
        ledger.finish("nonce", False)

    with closing(ExecutionLedger(path)) as ledger:
        expected = {"with-receipt": receipt, "legacy-marker": None}
        assert ledger.action_results("nonce") == expected
        resumed = _claim(ledger)
        assert resumed.completed_actions == frozenset(expected)
        assert resumed.resumed


def test_retry_keeps_each_authorized_attempt_in_order(tmp_path):
    path = str(tmp_path / "attempts.sqlite")
    context = {"request_id": "request"}
    with closing(ExecutionLedger(path)) as ledger:
        ledger.set_context("nonce", context)
        _claim(ledger)
        first_result, first_decision = _settle(ledger, success=False)
        assert _claim(ledger).attempts == 2
        second_result, second_decision = _settle(ledger, success=True)

    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.outcomes("nonce") == [
            {"attempt": 1, "result": first_result, "decision": first_decision, "context": context},
            {"attempt": 2, "result": second_result, "decision": second_decision, "context": context},
        ]
        assert ledger.status("nonce") == STATUS_COMPLETE


def test_successor_retains_receipts_but_requires_its_own_approval_reservation(tmp_path):
    path = str(tmp_path / "successor.sqlite")
    receipt = {"data": {"note_id": 17}}
    with closing(ExecutionLedger(path)) as ledger:
        _claim(ledger, "old")
        ledger.record_action("old", "action", result=receipt)
        ledger.record_action("old", "legacy-action")
        ledger.record_action("old", "__oob_reservation__")
        _settle(ledger, "old", success=False)
        ledger.seed_successor(
            "new", decision_id="decision-new", proposal_id="proposal-new",
            completed=ledger.action_results("old"),
        )

    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.status("new") == STATUS_FAILED
        assert ledger.outcomes("new") == []
        assert ledger.action_results("new") == {"action": receipt, "legacy-action": None}
        claim = _claim(ledger, "new")
        assert claim.attempts == 1
        assert claim.completed_actions == frozenset({"action", "legacy-action"})
        assert "__oob_reservation__" in ledger.completed_actions("old")
        assert len(ledger.outcomes("old")) == 1


def test_successor_receipt_failure_rolls_back_entire_seed(tmp_path):
    with closing(ExecutionLedger(str(tmp_path / "seed-rollback.sqlite"))) as ledger:
        with pytest.raises(ValueError):
            ledger.seed_successor(
                "new", decision_id="decision-new", proposal_id="proposal-new",
                completed={"valid": {"note": 1}, "invalid": {"cost": float("nan")}},
            )
        assert ledger.status("new") is None
        assert ledger.action_results("new") == {}
        assert not _claim(ledger, "new").resumed


def test_legacy_schema_migration_preserves_completion_and_supports_new_journal(tmp_path):
    path = str(tmp_path / "legacy.sqlite")
    now = utcnow().isoformat()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("""CREATE TABLE executions (
            nonce TEXT PRIMARY KEY, decision_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL, first_seen_at TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL, finished_at TEXT)""")
        connection.execute("""CREATE TABLE execution_actions (
            nonce TEXT NOT NULL, action_key TEXT NOT NULL, completed_at TEXT NOT NULL,
            PRIMARY KEY (nonce, action_key))""")
        connection.execute("INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            "old", "decision-old", "proposal-old", STATUS_COMPLETE, 1, now, now, now,
        ))
        connection.execute("INSERT INTO execution_actions VALUES (?, ?, ?)", ("old", "action", now))
        connection.commit()

    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.status("old") == STATUS_COMPLETE
        assert ledger.action_results("old") == {"action": None}
        assert ledger.outcomes("old") == []
        with pytest.raises(ExecutionReplayError):
            _claim(ledger, "old")
        _claim(ledger, "new")
        ledger.set_context("new", {"request_id": "new-request"})
        ledger.record_action("new", "action", result={"data": "receipt"})
        _settle(ledger, "new")

    # Migration is repeatable and newly recorded data survives another startup.
    with closing(ExecutionLedger(path)) as ledger:
        assert ledger.action_results("new") == {"action": {"data": "receipt"}}
        assert ledger.outcomes("new")[0]["context"] == {"request_id": "new-request"}
        assert ledger.completed_actions("old") == frozenset({"action"})


def test_prune_removes_old_receipts_outcomes_and_context_together(tmp_path, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(ledger_module, "utcnow", lambda: now)
    with closing(ExecutionLedger(str(tmp_path / "prune.sqlite"))) as ledger:
        for nonce in ("old-complete", "old-failed"):
            _claim(ledger, nonce)
            ledger.set_context(nonce, {"request_id": nonce})
            ledger.record_action(nonce, "action", result={"receipt": nonce})
            _settle(ledger, nonce, success=nonce.endswith("complete"))
        now += timedelta(hours=2)
        _claim(ledger, "recent")
        ledger.set_context("recent", {"request_id": "recent"})
        _settle(ledger, "recent")

        assert ledger.prune(max_age_seconds=3600) == 2
        for nonce in ("old-complete", "old-failed"):
            assert ledger.status(nonce) is None
            assert ledger.action_results(nonce) == {}
            assert ledger.outcomes(nonce) == []
            ledger.set_context(nonce, {"request_id": "new-context"})
            assert not _claim(ledger, nonce).resumed
            _settle(ledger, nonce)
            assert ledger.outcomes(nonce)[0]["context"] == {"request_id": "new-context"}
        assert ledger.status("recent") == STATUS_COMPLETE
        assert len(ledger.outcomes("recent")) == 1


def test_prune_failure_keeps_execution_and_its_journal(tmp_path, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(ledger_module, "utcnow", lambda: now)
    with closing(ExecutionLedger(str(tmp_path / "prune-rollback.sqlite"))) as ledger:
        _claim(ledger)
        ledger.set_context("nonce", {"request_id": "request"})
        ledger.record_action("nonce", "action", result={"receipt": "kept"})
        _settle(ledger)
        now += timedelta(hours=2)
        ledger._conn.execute("""CREATE TRIGGER fail_prune BEFORE DELETE ON execution_contexts
            BEGIN SELECT RAISE(ABORT, 'injected prune failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected prune"):
            ledger.prune(max_age_seconds=3600)
        assert ledger.status("nonce") == STATUS_COMPLETE
        assert ledger.action_results("nonce") == {"action": {"receipt": "kept"}}
        assert ledger.outcomes("nonce")[0]["context"] == {"request_id": "request"}
