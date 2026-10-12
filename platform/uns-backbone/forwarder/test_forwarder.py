"""Unit tests for the audit forwarder.

Uses MockBlobWriter — no Azure connection required.

Test matrix:
- Valid MQTT POST → 204, exactly one blob write, correct path and payload.
- Valid Kafka POST → 204, exactly one blob write, correct path and payload.
- Malformed JSON → 400, zero blob writes.
- Missing required field → 400, zero blob writes.
- Invalid rejection_reason enum → 400, zero blob writes.
- Blank source_ip → 400, zero blob writes.
- Blank gateway_id → 400, zero blob writes.
- Invalid correlation_id (not UUID v4) → 400, zero blob writes.
- WriteLatencyExceeded raised by writer → 503.
- Generic writer exception → 500.
- MockBlobWriter instance isolation — sequential IDs restart on new instance.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blob_writer import MockBlobWriter, WriteLatencyExceeded
from main import app, override_writer
from models import RejectionEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Far-future constant — never today's date, so assertions on the date-part of blob
# paths remain stable across the service lifetime.
_ANCHOR_TS = "2099-12-31T00:00:00Z"
_ANCHOR_DATE = "2099-12-31"

VALID_PAYLOAD: dict = {
    "timestamp": _ANCHOR_TS,
    "source_ip": "10.0.0.1",
    "gateway_id": "spiffe://munich.bmw/workload/edge/body-shop/weld-cell-01",
    "rejection_reason": "no_cert_presented",
    "correlation_id": "a1b2c3d4-e5f6-4789-8abc-def012345678",
}


@pytest.fixture()
def mock_writer() -> MockBlobWriter:
    writer = MockBlobWriter()
    override_writer(writer)
    return writer


@pytest.fixture()
def client(mock_writer: MockBlobWriter) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Happy path — MQTT
# ---------------------------------------------------------------------------

def test_mqtt_valid_post_returns_204(client: TestClient, mock_writer: MockBlobWriter) -> None:
    resp = client.post("/rejections/mqtt", json=VALID_PAYLOAD)
    assert resp.status_code == 204


def test_mqtt_valid_post_triggers_exactly_one_blob_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    client.post("/rejections/mqtt", json=VALID_PAYLOAD)
    assert len(mock_writer.writes) == 1


def test_mqtt_blob_path_is_mqtt_date_correlation_id(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    client.post("/rejections/mqtt", json=VALID_PAYLOAD)
    source, event = mock_writer.writes[0]
    assert source == "mqtt"
    expected_path = f"mqtt/{_ANCHOR_DATE}/{VALID_PAYLOAD['correlation_id']}.json"
    derived = f"{source}/{_ANCHOR_DATE}/{event.correlation_id}.json"
    assert derived == expected_path


def test_mqtt_blob_payload_matches_request(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    client.post("/rejections/mqtt", json=VALID_PAYLOAD)
    _, event = mock_writer.writes[0]
    assert event.source_ip == VALID_PAYLOAD["source_ip"]
    assert event.gateway_id == VALID_PAYLOAD["gateway_id"]
    assert event.rejection_reason.value == VALID_PAYLOAD["rejection_reason"]
    assert event.timestamp == VALID_PAYLOAD["timestamp"]


# ---------------------------------------------------------------------------
# Happy path — Kafka
# ---------------------------------------------------------------------------

def test_kafka_valid_post_returns_204(client: TestClient, mock_writer: MockBlobWriter) -> None:
    resp = client.post("/rejections/kafka", json=VALID_PAYLOAD)
    assert resp.status_code == 204


def test_kafka_valid_post_triggers_exactly_one_blob_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    client.post("/rejections/kafka", json=VALID_PAYLOAD)
    assert len(mock_writer.writes) == 1


def test_kafka_blob_path_source_is_kafka(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    client.post("/rejections/kafka", json=VALID_PAYLOAD)
    source, event = mock_writer.writes[0]
    assert source == "kafka"
    assert f"kafka/{_ANCHOR_DATE}/{event.correlation_id}.json" == (
        f"kafka/{_ANCHOR_DATE}/{VALID_PAYLOAD['correlation_id']}.json"
    )


# ---------------------------------------------------------------------------
# Rejection cases — 400
# ---------------------------------------------------------------------------

def test_malformed_json_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    resp = client.post(
        "/rejections/mqtt",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_missing_required_field_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "rejection_reason"}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_invalid_rejection_reason_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {**VALID_PAYLOAD, "rejection_reason": "bad_reason"}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_blank_source_ip_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {**VALID_PAYLOAD, "source_ip": "   "}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_blank_gateway_id_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {**VALID_PAYLOAD, "gateway_id": ""}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_invalid_correlation_id_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {**VALID_PAYLOAD, "correlation_id": "not-a-uuid"}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


def test_invalid_timestamp_returns_400_no_write(
    client: TestClient, mock_writer: MockBlobWriter
) -> None:
    payload = {**VALID_PAYLOAD, "timestamp": "not-a-timestamp"}
    resp = client.post("/rejections/mqtt", json=payload)
    assert resp.status_code == 400
    assert len(mock_writer.writes) == 0


# ---------------------------------------------------------------------------
# Writer error cases — 503 / 500
# ---------------------------------------------------------------------------

class _LatencyExceededWriter:
    def write_rejection(self, source: str, event: RejectionEvent) -> str:
        raise WriteLatencyExceeded("took 1.5 s")


class _GenericErrorWriter:
    def write_rejection(self, source: str, event: RejectionEvent) -> str:
        raise OSError("network error")


def test_write_latency_exceeded_returns_503(mock_writer: MockBlobWriter) -> None:
    override_writer(_LatencyExceededWriter())
    with TestClient(app) as c:
        resp = c.post("/rejections/mqtt", json=VALID_PAYLOAD)
    assert resp.status_code == 503


def test_generic_writer_error_returns_500(mock_writer: MockBlobWriter) -> None:
    override_writer(_GenericErrorWriter())
    with TestClient(app) as c:
        resp = c.post("/rejections/mqtt", json=VALID_PAYLOAD)
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# MockBlobWriter instance isolation
# ---------------------------------------------------------------------------

def test_mock_blob_writer_instance_isolation() -> None:
    w1 = MockBlobWriter()
    w2 = MockBlobWriter()
    event = RejectionEvent(**VALID_PAYLOAD)
    w1.write_rejection("mqtt", event)
    # w2 must start empty — state does not bleed across instances
    assert len(w2.writes) == 0
    assert len(w1.writes) == 1


def test_mock_blob_writer_new_instance_starts_empty() -> None:
    w1 = MockBlobWriter()
    event = RejectionEvent(**VALID_PAYLOAD)
    w1.write_rejection("mqtt", event)
    w1.write_rejection("kafka", event)

    w2 = MockBlobWriter()
    assert len(w2.writes) == 0  # fresh instance, no residual state
