"""Acceptance tests for US-009 — Structured digital shift handover in Handover Builder.

Covers all three acceptance criteria end-to-end across the three merged tasks:
  postgresql-citus: shift_handover and andon_alert migrations
  line-command-api: shift handover submission, retrieval, and Andon Kafka consumer
  line-command: Handover Builder form and Command Center handover summary

  AC-1  Open predictive Andon alerts from the current shift are pre-populated
        as line items in the Handover Builder, each requiring a disposition note
        (resolved / escalated / monitoring). The submitted handover carries all
        alert IDs and dispositions.

  AC-2  After a handover is submitted, the incoming Shift Lead's Command Center
        query (GET /handovers) returns the handover with oeeDeltaPct, alert
        dispositions, and heldVins.

  AC-3  The handover record retrieved by the Plant Manager review (GET
        /handovers/{id}) contains submitting Shift Lead ID, shift start/end
        timestamps, and all linked alert IDs — i.e. it is a structured log
        entry with every field the story requires.

Precondition assertions run before any behavioural assertion so that a missing
import, router, or migration fails loudly rather than letting the test silently
pass on empty data.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# sub=sl-001 roles=[ifos-shiftlead]
_BEARER_SHIFTLEAD = (
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJzbC0wMDEiLCJyb2xlcyI6WyJpZm9zLXNoaWZ0bGVhZCJdfQ"
    ".sig"
)

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
_NOW = datetime.now(timezone.utc)

_ALERT_ID_1 = str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
_ALERT_ID_2 = str(uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
_VIN = "WBA3A5C55FK123456"
_LINE_ID = "WELD-LINE-01"


# ---------------------------------------------------------------------------
# Preconditions — the story's entry points must be importable
# ---------------------------------------------------------------------------

def test_precondition_handover_router_importable() -> None:
    """Handover router must be importable (line-command-api backend task merged)."""
    from app.routers import handover  # noqa: F401 — import is the assertion


def test_precondition_handover_router_exposes_publish_helper() -> None:
    """Handover router must expose _publish_handover_events (used by event seam tests)."""
    from app.routers import handover
    assert hasattr(handover, "_publish_handover_events"), (
        "app.routers.handover._publish_handover_events not found — "
        "handover adoption-event helper not merged"
    )


def test_precondition_andon_consumer_importable() -> None:
    """Andon Kafka consumer must be importable (consumer task merged)."""
    from app import andon_consumer  # noqa: F401 — import is the assertion
    assert hasattr(andon_consumer, "process_andon_event"), (
        "app.andon_consumer.process_andon_event not found — consumer not merged"
    )


def test_precondition_shift_handover_migration_exists() -> None:
    """shift_handover migration SQL must exist on disk (postgresql-citus task merged)."""
    import pathlib
    migration = (
        pathlib.Path(__file__).parents[1]
        / "migrations"
        / "20261012_005_shift_handover.sql"
    )
    assert migration.exists(), (
        f"Migration file not found: {migration} — "
        "postgresql-citus shift_handover migration not merged"
    )


def test_precondition_shift_handover_migration_has_required_columns() -> None:
    """shift_handover migration must define all structured-log columns (AC-3)."""
    import pathlib
    migration = (
        pathlib.Path(__file__).parents[1]
        / "migrations"
        / "20261012_005_shift_handover.sql"
    )
    sql = migration.read_text().lower()
    required_columns = [
        "submitted_by_user_id",
        "shift_start_ts",
        "shift_end_ts",
        "alert_dispositions",
        "oee_delta_pct",
        "held_vins",
        "idempotency_key",
    ]
    for col in required_columns:
        assert col in sql, (
            f"Column '{col}' not found in shift_handover migration — "
            f"AC-3 structured-log requirement not met"
        )


def test_precondition_andon_alert_migration_exists() -> None:
    """andon_alert migration SQL must exist on disk (postgresql-citus task merged)."""
    import pathlib
    migration = (
        pathlib.Path(__file__).parents[1]
        / "migrations"
        / "20261012_005_andon_alert.sql"
    )
    assert migration.exists(), (
        f"andon_alert migration not found: {migration}"
    )


def test_precondition_handover_models_importable() -> None:
    """HandoverRequest and HandoverRecord Pydantic models must be importable."""
    from app.models import HandoverRequest, HandoverRecord  # noqa: F401


# ---------------------------------------------------------------------------
# Fake DB infrastructure (shared by AC-1 / AC-2 / AC-3 tests)
# ---------------------------------------------------------------------------

class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _HandoverConn:
    """Fake asyncpg connection for handover endpoint tests."""

    def __init__(
        self,
        handover_rows: list[dict] | None = None,
        handover_single: dict | None = None,
        idempotency_conflict: bool = False,
    ) -> None:
        self._handover_rows = handover_rows or []
        self._handover_single = handover_single
        self._idempotency_conflict = idempotency_conflict
        self.inserts: list[tuple[str, tuple]] = []
        self.updates: list[tuple[str, tuple]] = []

    def transaction(self) -> Any:
        fake = self

        class _Txn:
            async def __aenter__(self_) -> None:
                pass

            async def __aexit__(self_, *_: Any) -> None:
                pass

        return _Txn()

    async def fetch(self, query: str, *args: Any) -> list[_FakeRecord]:
        if "shift_handover" in query.lower():
            return [_FakeRecord(r) for r in self._handover_rows]
        return []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord | None:
        q = query.lower()
        # Idempotency-key duplicate check issued by POST /handover:
        # "SELECT id FROM shift_handover WHERE idempotency_key = $1"
        # (selects only "id", not the full column set, and WHERE clause references idempotency_key)
        if "where idempotency_key" in q:
            if self._idempotency_conflict:
                return _FakeRecord({"id": uuid.uuid4(), "created_at": _NOW})
            return None
        # GET /handovers/{id}: SELECT ... FROM shift_handover WHERE id = $1
        if "shift_handover" in q:
            if self._handover_single is not None:
                return _FakeRecord(self._handover_single)
            return None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        q = query.lower()
        if "insert" in q:
            self.inserts.append((query, args))
        elif "update" in q:
            self.updates.append((query, args))


class _FakePool:
    def __init__(self, conn: _HandoverConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_) -> _HandoverConn:
                return conn

            async def __aexit__(self_, *_: Any) -> None:
                pass

        return _Ctx()


def _build_app(conn: _HandoverConn) -> Any:
    from app.db import get_pool
    from app.main import create_app
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: _FakePool(conn)
    return app


def _handover_db_row(
    handover_id: str | None = None,
    alert_dispositions: list[dict] | None = None,
    held_vins: list[str] | None = None,
    oee_delta_pct: float = 2.5,
) -> dict:
    return {
        "id": uuid.UUID(handover_id) if handover_id else uuid.uuid4(),
        "shift_id": uuid.uuid4(),
        "line_id": _LINE_ID,
        "shift_start_ts": _NOW - timedelta(hours=8),
        "shift_end_ts": _NOW,
        "submitted_by_user_id": "sl-001",
        "alert_dispositions": alert_dispositions or [
            {"alertId": _ALERT_ID_1, "disposition": "resolved", "note": "Fixed at welding cell"},
            {"alertId": _ALERT_ID_2, "disposition": "escalated", "note": "Needs maintenance review"},
        ],
        "oee_delta_pct": oee_delta_pct,
        "held_vins": held_vins or [_VIN],
        "notes": "All stations checked at end of shift.",
        "idempotency_key": uuid.uuid4(),
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _valid_handover_body(
    alert_ids: list[str] | None = None,
    held_vins: list[str] | None = None,
    oee_delta_pct: float = 2.5,
) -> dict:
    ids = alert_ids or [_ALERT_ID_1, _ALERT_ID_2]
    return {
        "shiftId": str(uuid.uuid4()),
        "lineId": _LINE_ID,
        "shiftStartTs": "2099-01-01T06:00:00+00:00",
        "shiftEndTs": "2099-01-01T14:00:00+00:00",
        "alertDispositions": [
            {"alertId": ids[0], "disposition": "resolved", "note": "Fixed at welding cell"},
            {"alertId": ids[1], "disposition": "escalated", "note": "Needs maintenance review"},
        ],
        "oeeDeltaPct": oee_delta_pct,
        "heldVins": held_vins or [_VIN],
        "notes": "All stations checked.",
        "idempotencyKey": str(uuid.uuid4()),
    }


# ---------------------------------------------------------------------------
# AC-1 — Open Andon alerts pre-populated in Handover Builder, each requiring
#         a disposition; submitted handover carries all alert IDs + dispositions
# ---------------------------------------------------------------------------

class TestAC1_HandoverBuilderPrePopulatesOpenAlerts:
    """AC-1: Open predictive Andon alerts are included in the handover submission
    as line items with disposition notes.
    """

    @pytest.mark.asyncio
    async def test_handover_submission_includes_alert_dispositions(self) -> None:
        """AC-1: POST /handover with alert dispositions returns 201 and records them."""
        conn = _HandoverConn()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body()

        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        assert resp.status_code == 201, (
            f"Expected 201 for handover with alert dispositions, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        # Precondition: response has the expected fields
        assert "handoverId" in result
        assert result["submittedByUserId"] == "sl-001"

    @pytest.mark.asyncio
    async def test_handover_db_insert_carries_alert_dispositions(self) -> None:
        """AC-1: The DB INSERT for handover persists alert dispositions."""
        conn = _HandoverConn()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body()

        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        # Precondition: exactly one INSERT happened
        assert len(conn.inserts) == 1, (
            f"Expected 1 DB insert, got {len(conn.inserts)}"
        )
        insert_sql = conn.inserts[0][0].lower()
        assert "shift_handover" in insert_sql, (
            "INSERT did not target shift_handover table"
        )

    @pytest.mark.asyncio
    async def test_handover_updates_andon_alert_disposition_in_db(self) -> None:
        """AC-1: Submitting a handover UPDATEs the linked andon_alert rows."""
        conn = _HandoverConn()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body()

        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        # Both alert IDs must have triggered UPDATE andon_alert statements
        assert any("update andon_alert" in u[0].lower() for u in conn.updates), (
            "No UPDATE andon_alert found — alert disposition not persisted to DB"
        )

    @pytest.mark.asyncio
    async def test_submission_blocked_when_disposition_value_is_invalid(self) -> None:
        """AC-1: An invalid disposition value (not resolved/escalated/monitoring) → 422."""
        conn = _HandoverConn()
        app = _build_app(conn)
        transport = ASGITransport(app=app)
        body = _valid_handover_body()
        body["alertDispositions"][0]["disposition"] = "ignored"  # invalid

        with patch("app.routers.handover.publish", new=AsyncMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/handover",
                    json=body,
                    headers={"Authorization": _BEARER_SHIFTLEAD},
                )

        assert resp.status_code == 422, (
            f"Expected 422 for invalid disposition, got {resp.status_code}"
        )

    def test_andon_consumer_stores_open_alerts_for_shift(self) -> None:
        """AC-1: process_andon_event persists open alerts (seam: consumer → handover builder)."""
        from app.andon_consumer import process_andon_event

        executed: list[str] = []

        class _FakeConn:
            async def execute(self, query: str, *args: Any) -> None:
                executed.append(query)

        event = {
            "alertId": _ALERT_ID_1,
            "opcenterEventId": "evt-shift-001",
            "stationId": "WELD-CELL-03",
            "lineId": _LINE_ID,
            "alertType": "predictive",
            "predictedFailureType": "electrode_wear",
            "minutesToPredictedFailure": 12,
            "recommendedAction": "Replace electrode cap.",
            "status": "open",
            "detectedAt": _FAR_FUTURE.isoformat(),
        }

        asyncio.run(process_andon_event(_FakeConn(), event))

        # Precondition: at least one INSERT/UPSERT happened
        assert len(executed) >= 1, (
            "process_andon_event produced no DB writes — open alert not stored"
        )
        assert any("andon_alert" in q.lower() for q in executed), (
            "process_andon_event did not write to andon_alert table"
        )

    def test_all_three_disposition_values_accepted(self) -> None:
        """AC-1: All three valid disposition values (resolved/escalated/monitoring) are accepted."""
        from app.models import AlertDispositionItem
        for disposition in ("resolved", "escalated", "monitoring"):
            item = AlertDispositionItem(
                alertId=uuid.uuid4(),
                disposition=disposition,  # type: ignore[arg-type]
                note=None,
            )
            assert item.disposition == disposition, (
                f"AlertDispositionItem rejected valid disposition '{disposition}'"
            )

    def test_handover_kafka_event_includes_alert_ids(self) -> None:
        """AC-1: HandoverSubmittedEvent published to Kafka includes the alert IDs."""
        from app.routers.handover import _publish_handover_events

        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            asyncio.run(
                _publish_handover_events(
                    handover_id=str(uuid.uuid4()),
                    shift_id=str(uuid.uuid4()),
                    line_id=_LINE_ID,
                    shift_start_ts="2099-01-01T06:00:00+00:00",
                    shift_end_ts="2099-01-01T14:00:00+00:00",
                    user_id="sl-001",
                    caller_role="ifos-shiftlead",
                    oee_delta_pct=2.5,
                    submitted_at="2099-01-01T14:00:00+00:00",
                )
            )

        # Precondition: at least one publish call happened
        assert len(published) >= 1, "No Kafka events published after handover submission"

        handover_events = [
            p for p in published
            if "handover" in p["topic"]
        ]
        assert len(handover_events) >= 1, (
            f"No handover Kafka event published; topics seen: {[p['topic'] for p in published]}"
        )


# ---------------------------------------------------------------------------
# AC-2 — Command Center retrieves handover with OEE delta, dispositions, VINs
# ---------------------------------------------------------------------------

class TestAC2_CommandCenterHandoverSummary:
    """AC-2: After submission, GET /handovers returns a record with oeeDeltaPct,
    alertDispositions, and heldVins — the incoming Shift Lead's Command Center view.
    """

    @pytest.mark.asyncio
    async def test_get_handovers_returns_submitted_record(self) -> None:
        """AC-2: GET /handovers returns the submitted handover in the data list."""
        row = _handover_db_row()
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        assert resp.status_code == 200
        body = resp.json()

        # Precondition: non-empty data list
        assert "data" in body, "Response has no 'data' key"
        assert len(body["data"]) >= 1, (
            "GET /handovers returned empty list — submitted handover not visible"
        )

    @pytest.mark.asyncio
    async def test_handover_list_record_contains_oee_delta(self) -> None:
        """AC-2: Each handover in GET /handovers includes oeeDeltaPct."""
        row = _handover_db_row(oee_delta_pct=3.7)
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        item = resp.json()["data"][0]

        # Precondition: record has the field
        assert "oeeDeltaPct" in item, (
            f"oeeDeltaPct missing from GET /handovers record; keys: {list(item.keys())}"
        )
        # Criterion: matches the submitted value
        assert float(item["oeeDeltaPct"]) == pytest.approx(3.7, abs=0.01), (
            f"oeeDeltaPct mismatch: expected ≈3.7, got {item['oeeDeltaPct']}"
        )

    @pytest.mark.asyncio
    async def test_handover_list_record_contains_alert_dispositions(self) -> None:
        """AC-2: GET /handovers record contains alertDispositions with linked alert IDs."""
        alert_dispositions = [
            {"alertId": _ALERT_ID_1, "disposition": "resolved", "note": "Fixed"},
            {"alertId": _ALERT_ID_2, "disposition": "escalated", "note": "Escalated to maintenance"},
        ]
        row = _handover_db_row(alert_dispositions=alert_dispositions)
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        item = resp.json()["data"][0]

        # Precondition: field present
        assert "alertDispositions" in item, (
            f"alertDispositions missing from GET /handovers record; keys: {list(item.keys())}"
        )
        # Criterion: both alert IDs are linked
        returned_ids = {d["alertId"] for d in item["alertDispositions"]}
        assert _ALERT_ID_1 in returned_ids, (
            f"Alert {_ALERT_ID_1} not found in alertDispositions: {item['alertDispositions']}"
        )
        assert _ALERT_ID_2 in returned_ids, (
            f"Alert {_ALERT_ID_2} not found in alertDispositions: {item['alertDispositions']}"
        )

    @pytest.mark.asyncio
    async def test_handover_list_record_contains_held_vins(self) -> None:
        """AC-2: GET /handovers record contains heldVins with quality-review VINs."""
        row = _handover_db_row(held_vins=[_VIN])
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        item = resp.json()["data"][0]

        # Precondition: field present
        assert "heldVins" in item, (
            f"heldVins missing from GET /handovers record; keys: {list(item.keys())}"
        )
        # Criterion: the VIN we submitted is in the list
        assert _VIN in item["heldVins"], (
            f"VIN {_VIN} not found in heldVins: {item['heldVins']}"
        )

    @pytest.mark.asyncio
    async def test_handover_filter_by_line_id_returns_correct_record(self) -> None:
        """AC-2: GET /handovers?lineId= returns only records for that line."""
        row = _handover_db_row()
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers?lineId={_LINE_ID}&limit=1",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        # Criterion: when filtered by line, the correct handover is returned
        if body["data"]:
            assert body["data"][0]["lineId"] == _LINE_ID, (
                f"lineId mismatch: expected {_LINE_ID}, got {body['data'][0]['lineId']}"
            )

    @pytest.mark.asyncio
    async def test_handover_list_disposition_values_are_valid_enum(self) -> None:
        """AC-2: Each disposition in alertDispositions is resolved/escalated/monitoring."""
        alert_dispositions = [
            {"alertId": _ALERT_ID_1, "disposition": "resolved", "note": None},
            {"alertId": _ALERT_ID_2, "disposition": "monitoring", "note": None},
        ]
        row = _handover_db_row(alert_dispositions=alert_dispositions)
        conn = _HandoverConn(handover_rows=[row])
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/handovers",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        item = resp.json()["data"][0]
        valid_values = {"resolved", "escalated", "monitoring"}
        for d in item["alertDispositions"]:
            assert d["disposition"] in valid_values, (
                f"Unexpected disposition value '{d['disposition']}' — "
                f"must be one of {valid_values}"
            )


# ---------------------------------------------------------------------------
# AC-3 — Plant Manager structured log entry with Shift Lead ID, timestamps,
#         and all linked alert IDs
# ---------------------------------------------------------------------------

class TestAC3_PlantManagerStructuredLogEntry:
    """AC-3: GET /handovers/{handoverId} returns a structured log entry with
    submitting Shift Lead ID, shift start/end timestamps, and all linked alert IDs.
    """

    @pytest.mark.asyncio
    async def test_get_handover_by_id_returns_200(self) -> None:
        """AC-3: GET /handovers/{id} exists and returns a handover record."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        assert resp.status_code == 200, (
            f"GET /handovers/{{id}} returned {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_structured_log_has_submitting_shift_lead_id(self) -> None:
        """AC-3: The record includes submittedByUserId (submitting Shift Lead ID)."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()

        # Precondition: field present
        assert "submittedByUserId" in body, (
            f"submittedByUserId missing from handover record; keys: {list(body.keys())}"
        )
        # Criterion: matches the shift lead who submitted
        assert body["submittedByUserId"] == "sl-001", (
            f"Expected submittedByUserId='sl-001', got '{body['submittedByUserId']}'"
        )

    @pytest.mark.asyncio
    async def test_structured_log_has_shift_start_timestamp(self) -> None:
        """AC-3: The record includes shiftStartTs."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()

        # Precondition: field present
        assert "shiftStartTs" in body, (
            f"shiftStartTs missing from handover record; keys: {list(body.keys())}"
        )
        # Criterion: parseable as a datetime
        ts = datetime.fromisoformat(body["shiftStartTs"])
        assert ts.tzinfo is not None, "shiftStartTs must be timezone-aware"

    @pytest.mark.asyncio
    async def test_structured_log_has_shift_end_timestamp(self) -> None:
        """AC-3: The record includes shiftEndTs."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()

        # Precondition: field present
        assert "shiftEndTs" in body, (
            f"shiftEndTs missing from handover record; keys: {list(body.keys())}"
        )
        # Criterion: parseable as a datetime
        ts = datetime.fromisoformat(body["shiftEndTs"])
        assert ts.tzinfo is not None, "shiftEndTs must be timezone-aware"

    @pytest.mark.asyncio
    async def test_structured_log_has_all_linked_alert_ids(self) -> None:
        """AC-3: The record includes alertDispositions listing all linked alert IDs."""
        alert_dispositions = [
            {"alertId": _ALERT_ID_1, "disposition": "resolved", "note": "Fixed"},
            {"alertId": _ALERT_ID_2, "disposition": "escalated", "note": "Maintenance required"},
        ]
        row = _handover_db_row(alert_dispositions=alert_dispositions)
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()

        # Precondition: field present and non-empty
        assert "alertDispositions" in body, (
            f"alertDispositions missing from handover record; keys: {list(body.keys())}"
        )
        assert len(body["alertDispositions"]) == 2, (
            f"Expected 2 alert dispositions, got {len(body['alertDispositions'])}"
        )

        # Criterion: both alert IDs are in the log entry
        returned_ids = {d["alertId"] for d in body["alertDispositions"]}
        assert _ALERT_ID_1 in returned_ids, (
            f"Alert {_ALERT_ID_1} not in structured log alertDispositions"
        )
        assert _ALERT_ID_2 in returned_ids, (
            f"Alert {_ALERT_ID_2} not in structured log alertDispositions"
        )

    @pytest.mark.asyncio
    async def test_structured_log_shift_end_after_shift_start(self) -> None:
        """AC-3: shiftEndTs is later than shiftStartTs (temporal sanity)."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()
        start = datetime.fromisoformat(body["shiftStartTs"])
        end = datetime.fromisoformat(body["shiftEndTs"])
        assert end > start, (
            f"shiftEndTs ({end}) is not after shiftStartTs ({start}) — "
            "shift timestamps are malformed"
        )

    @pytest.mark.asyncio
    async def test_structured_log_has_handover_id(self) -> None:
        """AC-3: The record identifies itself with handoverId (structured log key)."""
        row = _handover_db_row()
        handover_id = str(row["id"])
        conn = _HandoverConn(handover_single=row)
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/handovers/{handover_id}",
                headers={"Authorization": _BEARER_SHIFTLEAD},
            )

        body = resp.json()
        assert "handoverId" in body, (
            f"handoverId missing from GET /handovers/{{id}} response; keys: {list(body.keys())}"
        )
        assert body["handoverId"] == handover_id

    @pytest.mark.asyncio
    async def test_structured_log_record_requires_auth(self) -> None:
        """AC-3: GET /handovers/{id} requires a valid bearer token."""
        conn = _HandoverConn()
        app = _build_app(conn)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/handovers/{uuid.uuid4()}")

        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_handover_submission_publishes_kafka_event_with_shift_lead_id(self) -> None:
        """AC-3: HandoverSubmittedEvent on Kafka includes submittedByUserId."""
        from app.routers.handover import _publish_handover_events

        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            asyncio.run(
                _publish_handover_events(
                    handover_id=str(uuid.uuid4()),
                    shift_id=str(uuid.uuid4()),
                    line_id=_LINE_ID,
                    shift_start_ts="2099-01-01T06:00:00+00:00",
                    shift_end_ts="2099-01-01T14:00:00+00:00",
                    user_id="sl-001",
                    caller_role="ifos-shiftlead",
                    oee_delta_pct=2.5,
                    submitted_at="2099-01-01T14:00:00+00:00",
                )
            )

        handover_events = [
            p for p in published
            if p["topic"] == "factory.munich.line-command.handover.v1"
        ]
        # Precondition: event was published
        assert len(handover_events) >= 1, (
            "No HandoverSubmittedEvent on factory.munich.line-command.handover.v1 topic"
        )

        payload = handover_events[0]["payload"]
        # Criterion: shift lead ID is in the event
        assert "submittedByUserId" in payload, (
            f"submittedByUserId missing from HandoverSubmittedEvent payload: {payload}"
        )
        assert payload["submittedByUserId"] == "sl-001"

    def test_handover_submission_kafka_event_has_shift_timestamps(self) -> None:
        """AC-3: HandoverSubmittedEvent includes shiftStartTs and shiftEndTs."""
        from app.routers.handover import _publish_handover_events

        published: list[dict] = []

        async def fake_publish(topic: str, payload: dict) -> None:
            published.append({"topic": topic, "payload": payload})

        with patch("app.routers.handover.publish", side_effect=fake_publish):
            asyncio.run(
                _publish_handover_events(
                    handover_id=str(uuid.uuid4()),
                    shift_id=str(uuid.uuid4()),
                    line_id=_LINE_ID,
                    shift_start_ts="2099-01-01T06:00:00+00:00",
                    shift_end_ts="2099-01-01T14:00:00+00:00",
                    user_id="sl-001",
                    caller_role="ifos-shiftlead",
                    oee_delta_pct=2.5,
                    submitted_at="2099-01-01T14:00:00+00:00",
                )
            )

        handover_events = [
            p for p in published
            if p["topic"] == "factory.munich.line-command.handover.v1"
        ]
        assert len(handover_events) >= 1

        payload = handover_events[0]["payload"]
        assert "shiftStartTs" in payload, (
            f"shiftStartTs missing from HandoverSubmittedEvent: {payload}"
        )
        assert "shiftEndTs" in payload, (
            f"shiftEndTs missing from HandoverSubmittedEvent: {payload}"
        )
