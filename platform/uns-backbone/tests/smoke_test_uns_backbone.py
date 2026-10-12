"""
Smoke tests for the UNS backbone (cmp:uns-backbone).

Acceptance criteria validated:
  (a) A well-formed Sparkplug B DDATA payload on a registered schema reaches a
      test consumer within 1,000 ms p99 under simulated 500 K-tag load.
  (b) A payload with an unregistered tag lands on the DLQ AND the SchemaViolation
      Alertmanager alert fires within 60 seconds.

These tests execute against a running UNS backbone stack. Set the environment
variables below (or export them before running) to point at the deployed cluster.

Usage:
    pytest platform/uns-backbone/tests/smoke_test_uns_backbone.py -v
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests
from confluent_kafka import Consumer, KafkaError, Producer
from confluent_kafka.admin import AdminClient

# ---------------------------------------------------------------------------
# Configuration — read from environment; no hardcoded credentials or addresses
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP = os.environ.get(
    "UNS_KAFKA_BOOTSTRAP",
    "uns-kafka-bootstrap.confluent.svc.cluster.local:9092",
)
KAFKA_SASL_USERNAME = os.environ["KAFKA_SMOKE_USERNAME"]
KAFKA_SASL_PASSWORD = os.environ["KAFKA_SMOKE_PASSWORD"]
SCHEMA_REGISTRY_URL = os.environ.get(
    "UNS_SCHEMA_REGISTRY_URL",
    "https://uns-schema-registry.confluent.svc.cluster.local:8081",
)
ALERTMANAGER_URL = os.environ.get(
    "UNS_ALERTMANAGER_URL",
    "https://alertmanager.ifos.munich.internal/api/v2/alerts",
)
ALERTMANAGER_CA_CERT = os.environ.get("UNS_ALERTMANAGER_CA_CERT", "")

# Test thresholds
P99_LATENCY_BUDGET_MS = 1_000
SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS = 60
LOAD_TAG_COUNT = 500_000
LOAD_SAMPLE_SIZE = 1_000  # messages sampled for p99 measurement


# ---------------------------------------------------------------------------
# Kafka client helpers
# ---------------------------------------------------------------------------

def _kafka_common_config() -> dict[str, Any]:
    return {
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-512",
        "sasl.username": KAFKA_SASL_USERNAME,
        "sasl.password": KAFKA_SASL_PASSWORD,
    }


def _make_producer() -> Producer:
    return Producer(_kafka_common_config())


def _make_consumer(group_id: str, topic: str) -> Consumer:
    cfg = {
        **_kafka_common_config(),
        "group.id": group_id,
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(cfg)
    consumer.subscribe([topic])
    return consumer


def _drain_consumer(consumer: Consumer, timeout_s: float = 2.0) -> None:
    """Discard any messages already on the topic before the test publishes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = consumer.poll(0.1)
        if msg is None or msg.error():
            continue


# ---------------------------------------------------------------------------
# Sparkplug B DDATA fixture helpers
# ---------------------------------------------------------------------------

@dataclass
class SparkplugMetric:
    name: str
    value: Any
    data_type: str = "Double"
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


def _build_ddata_payload(
    shop: str,
    edge_node_id: str,
    device_id: str,
    metrics: list[SparkplugMetric],
    seq: int = 1,
) -> bytes:
    """Build a minimal JSON-serialised Avro envelope for smoke-test purposes.

    In production the bridge handles real Protobuf Sparkplug B encoding;
    these tests use the JSON Avro envelope the Schema Registry validates.
    """
    payload = {
        "shop": shop,
        "edgeNodeId": edge_node_id,
        "deviceId": device_id,
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "dataType": m.data_type,
                "timestamp": m.timestamp,
            }
            for m in metrics
        ],
        "timestamp": int(time.time() * 1000),
        "seq": seq,
    }
    return json.dumps(payload).encode()


def _build_unregistered_tag_payload(shop: str) -> bytes:
    """Build a payload with a tag name that is not in the schema registry."""
    payload = {
        "shop": shop,
        "edgeNodeId": f"edge-{shop}-01",
        "deviceId": f"device-{shop}-unknown",
        "metrics": [
            {
                "name": f"__unregistered_tag_{uuid.uuid4().hex}__",
                "value": 42.0,
                "dataType": "Double",
                "timestamp": int(time.time() * 1000),
            }
        ],
        "timestamp": int(time.time() * 1000),
        "seq": 9999,
    }
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# Acceptance criterion (a): p99 latency ≤ 1,000 ms under 500 K-tag load
# ---------------------------------------------------------------------------

class TestSparkplugDdataLatency:
    """
    Publish LOAD_SAMPLE_SIZE well-formed DDATA messages against a registered
    schema and measure the p99 end-to-end latency from producer send() to
    consumer receipt.  Simulates the 500 K-tag load scenario via concurrent
    metric counts per message.
    """

    TOPIC = "bmw.munich.uns.ddata.assembly.v1"
    GROUP_ID = f"smoke-latency-{uuid.uuid4().hex[:8]}"
    # Each message carries 500 metrics to approximate the 500 K-tag workload
    # across the full 1 K-message sample (500 metrics × 1000 msgs = 500 K entries).
    METRICS_PER_MESSAGE = 500

    def test_p99_latency_within_budget(self) -> None:
        consumer = _make_consumer(self.GROUP_ID, self.TOPIC)
        producer = _make_producer()

        _drain_consumer(consumer)

        send_times: dict[str, int] = {}
        for seq in range(LOAD_SAMPLE_SIZE):
            correlation_id = uuid.uuid4().hex
            metrics = [
                SparkplugMetric(
                    name=f"station.{seq % 14}.torque_nm",
                    value=round(120.5 + (seq % 10) * 0.1, 3),
                )
                for _ in range(self.METRICS_PER_MESSAGE)
            ]
            payload = _build_ddata_payload(
                shop="assembly",
                edge_node_id="edge-assembly-01",
                device_id=f"robot-arm-{seq % 10:02d}",
                metrics=metrics,
                seq=seq,
            )
            send_times[correlation_id] = int(time.time() * 1000)
            producer.produce(
                self.TOPIC,
                value=payload,
                key=correlation_id.encode(),
                on_delivery=lambda err, msg: None,
            )
            producer.poll(0)

        producer.flush(timeout=10.0)

        latencies_ms: list[int] = []
        deadline = time.monotonic() + 30.0
        while len(latencies_ms) < LOAD_SAMPLE_SIZE and time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                pytest.fail(f"Consumer error: {msg.error()}")
            key = msg.key().decode() if msg.key() else None
            if key and key in send_times:
                recv_time = int(time.time() * 1000)
                latencies_ms.append(recv_time - send_times[key])

        consumer.close()

        assert len(latencies_ms) >= LOAD_SAMPLE_SIZE * 0.99, (
            f"Only {len(latencies_ms)}/{LOAD_SAMPLE_SIZE} messages received within 30 s"
        )

        p99_ms = int(statistics.quantiles(latencies_ms, n=100)[98])
        assert p99_ms <= P99_LATENCY_BUDGET_MS, (
            f"p99 latency {p99_ms} ms exceeds budget of {P99_LATENCY_BUDGET_MS} ms"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion (b): unregistered tag → DLQ + SchemaViolation alert ≤ 60 s
# ---------------------------------------------------------------------------

class TestSchemaViolationRouting:
    """
    Publish a Sparkplug B DDATA payload containing an unregistered tag name.
    Assert that:
      1. The message appears on the DLQ topic within 60 seconds.
      2. The DLQ envelope includes originalTopic, tagName, rawPayload, arrivalTimestamp.
      3. The SchemaViolation Alertmanager alert fires within 60 seconds.
    """

    DLQ_TOPIC = "uns.munich.dlq"
    SOURCE_TOPIC = "bmw.munich.uns.ddata.paint.v1"
    GROUP_ID = f"smoke-dlq-{uuid.uuid4().hex[:8]}"

    def test_unregistered_tag_lands_on_dlq(self) -> None:
        consumer = _make_consumer(self.GROUP_ID, self.DLQ_TOPIC)
        producer = _make_producer()

        _drain_consumer(consumer, timeout_s=3.0)

        raw_payload = _build_unregistered_tag_payload("paint")
        # The DLQ envelope's tagName should contain this sentinel
        sentinel = json.loads(raw_payload)["metrics"][0]["name"]

        producer.produce(self.SOURCE_TOPIC, value=raw_payload)
        producer.flush(timeout=10.0)

        dlq_message = None
        deadline = time.monotonic() + SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS
        while time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                pytest.fail(f"DLQ consumer error: {msg.error()}")
            dlq_message = msg
            break

        consumer.close()

        assert dlq_message is not None, (
            f"Unregistered-tag payload did not reach DLQ within "
            f"{SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS} seconds"
        )

        envelope = json.loads(dlq_message.value())

        assert "originalTopic" in envelope, "DLQ envelope missing 'originalTopic'"
        assert "tagName" in envelope, "DLQ envelope missing 'tagName'"
        assert "rawPayload" in envelope, "DLQ envelope missing 'rawPayload'"
        assert "arrivalTimestamp" in envelope, "DLQ envelope missing 'arrivalTimestamp'"
        assert envelope["tagName"] == sentinel, (
            f"DLQ tagName '{envelope['tagName']}' != expected sentinel '{sentinel}'"
        )
        # rawPayload must be non-empty base64
        decoded = base64.b64decode(envelope["rawPayload"])
        assert len(decoded) > 0, "DLQ rawPayload is empty after base64 decode"

    def test_schema_violation_alert_fires_within_60_seconds(self) -> None:
        """Assert the SchemaViolation alert appears in Alertmanager within 60 s."""
        producer = _make_producer()
        raw_payload = _build_unregistered_tag_payload("paint")

        t0 = time.monotonic()
        producer.produce(self.SOURCE_TOPIC, value=raw_payload)
        producer.flush(timeout=10.0)

        verify_kwargs: dict[str, Any] = {}
        if ALERTMANAGER_CA_CERT:
            verify_kwargs["verify"] = ALERTMANAGER_CA_CERT

        alert_found = False
        deadline = time.monotonic() + SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS
        while time.monotonic() < deadline and not alert_found:
            try:
                resp = requests.get(
                    ALERTMANAGER_URL,
                    params={"filter": 'alertname="SchemaViolation"'},
                    timeout=5,
                    **verify_kwargs,
                )
                resp.raise_for_status()
                alerts = resp.json()
                if isinstance(alerts, list) and any(
                    a.get("labels", {}).get("alertname") == "SchemaViolation"
                    for a in alerts
                ):
                    alert_found = True
            except requests.RequestException:
                pass
            if not alert_found:
                time.sleep(2)

        elapsed_s = time.monotonic() - t0
        assert alert_found, (
            f"SchemaViolation alert did not appear in Alertmanager within "
            f"{SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS} seconds"
        )
        assert elapsed_s <= SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS, (
            f"Alert fired but after budget: {elapsed_s:.1f} s > "
            f"{SCHEMA_VIOLATION_ALERT_BUDGET_SECONDS} s"
        )


# ---------------------------------------------------------------------------
# Admin check: verify all UNS topics exist on the cluster
# ---------------------------------------------------------------------------

class TestKafkaTopologyIntegrity:
    """Verify that every topic declared in main.tf locals.topics is present."""

    EXPECTED_TOPICS = [
        "bmw.munich.uns.ddata.inbound.v1",
        "bmw.munich.uns.ddata.body.v1",
        "bmw.munich.uns.ddata.paint.v1",
        "bmw.munich.uns.ddata.assembly.v1",
        "bmw.munich.uns.ddata.quality.v1",
        "uns.munich.dlq",
        "bmw.ifos.munich.uns.andon.alerts.v1",
        "factory.munich.opcenter.andon.alert.v1",
        "factory.munich.opcenter.oee.snapshot.v1",
        "bmw.munich.i4.sequence.optimized.v1",
        "bmw.munich.i4.uns.constraints.live.v1",
        "bmw.munich.paint.defect.events.v1",
        "bmw.munich.defectgenome.cluster.alerts.v1",
        "factory.munich.line-command.adoption.v1",
        "factory.munich.line-command.maintenance-dispatch.v1",
        "factory.munich.uns.manufacturing.event.v1",
    ]

    def test_all_uns_topics_exist(self) -> None:
        admin = AdminClient(_kafka_common_config())
        metadata = admin.list_topics(timeout=15)
        existing = set(metadata.topics.keys())

        missing = [t for t in self.EXPECTED_TOPICS if t not in existing]
        assert missing == [], (
            f"The following UNS topics are missing from the Kafka cluster: {missing}"
        )

    def test_dlq_topic_has_minimum_replicas(self) -> None:
        admin = AdminClient(_kafka_common_config())
        metadata = admin.list_topics(timeout=15)
        dlq_meta = metadata.topics.get("uns.munich.dlq")
        assert dlq_meta is not None, "DLQ topic 'uns.munich.dlq' not found"

        for _partition_id, partition_meta in dlq_meta.partitions.items():
            assert len(partition_meta.replicas) >= 3, (
                f"DLQ partition {_partition_id} has fewer than 3 replicas: "
                f"{len(partition_meta.replicas)}"
            )
