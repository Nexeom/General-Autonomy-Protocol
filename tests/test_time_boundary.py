"""
Timestamps crossing into GAP from outside must not be able to crash a
governance control.

Entity properties are populated from upstream systems. Real CRMs emit
timezone-aware ISO-8601; plenty of other sources emit naive. The drift watcher
subtracts an entity timestamp from the current time, and mixing a naive and an
aware datetime raises TypeError. The `try/except` in `_check_sla_drift` guards
only the *parse*, not the arithmetic, so an unnormalized timestamp propagates
out of the watcher and, from there, out of the reconciler heartbeat.

These tests pin both polarities so the normalization cannot regress in either
direction.
"""

from datetime import datetime, timedelta, timezone

from gap_kernel._time import ensure_utc, utcnow
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.world import EntityState
from gap_kernel.reconciler.loop import DriftWatcher


def _sla_intent() -> IntentVector:
    return IntentVector(
        id="lead_response_sla",
        objective="Respond to high-value leads within 10 minutes",
        priority=80,
        hard_constraints=[],
        soft_constraints=[],
        created_by="test",
        created_at=utcnow(),
    )


def _entity_with_created_at(created_at: str) -> EntityState:
    return EntityState(
        entity_type="lead",
        entity_id="lead_tz",
        properties={"created_at": created_at},
        last_updated=utcnow(),
        source="crm",
        obligations=["lead_response_sla"],
    )


class TestTimeHelpers:
    def test_utcnow_is_timezone_aware(self):
        assert utcnow().tzinfo is not None

    def test_ensure_utc_treats_naive_as_utc(self):
        naive = datetime(2026, 3, 1, 12, 0, 0)
        assert ensure_utc(naive) == datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_ensure_utc_converts_offset_to_utc(self):
        offset = datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert ensure_utc(offset) == datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_ensure_utc_is_idempotent(self):
        now = utcnow()
        assert ensure_utc(ensure_utc(now)) == ensure_utc(now)


class TestDriftWatcherTimestampBoundary:
    """A poisoned timestamp must not escape the watcher as an exception."""

    def test_aware_entity_timestamp_does_not_raise(self):
        # The normal output of any real CRM.
        created = (utcnow() - timedelta(minutes=30)).isoformat()
        events = DriftWatcher().check(
            _entity_with_created_at(created), [_sla_intent()]
        )
        assert isinstance(events, list)

    def test_naive_entity_timestamp_does_not_raise(self):
        # The migration to aware internal time must not merely flip which
        # polarity crashes.
        created = (
            (utcnow() - timedelta(minutes=30)).replace(tzinfo=None).isoformat()
        )
        events = DriftWatcher().check(
            _entity_with_created_at(created), [_sla_intent()]
        )
        assert isinstance(events, list)

    def test_offset_entity_timestamp_is_normalized_not_misread(self):
        # +02:00 thirty minutes ago is still thirty minutes ago; an SLA breach
        # must be detected on the instant, not on the wall-clock reading.
        created = (
            (utcnow() - timedelta(minutes=30))
            .astimezone(timezone(timedelta(hours=2)))
            .isoformat()
        )
        events = DriftWatcher().check(
            _entity_with_created_at(created), [_sla_intent()]
        )
        assert len(events) == 1, "a 30-minute-old lead breaches a 10-minute SLA"

    def test_unparseable_timestamp_is_skipped_quietly(self):
        events = DriftWatcher().check(
            _entity_with_created_at("not-a-timestamp"), [_sla_intent()]
        )
        assert events == []
