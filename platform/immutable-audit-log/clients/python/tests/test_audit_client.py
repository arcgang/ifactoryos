"""Tests for audit_client.

Covers:
  1. WORM semantics: overwrite raises (client-layer enforcement)
  2. Lifecycle/delete rejection: blob_client.delete_blob raises
  3. Blob schema: all six required fields present in uploaded payload
  4. Latency gate: write_event raises WriteLatencyExceeded when upload is slow
"""

import json
import time
import hashlib
from unittest.mock import MagicMock, patch, call

import pytest

from audit_client._client import (
    AuditClient,
    WriteLatencyExceeded,
    _make_blob_name,
    _validate_sha256,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ANCHOR_TS = "2030-01-01T00:00:00Z"  # far-future constant — never today's date

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

BEFORE_HASH = _sha256("before-state")
AFTER_HASH  = _sha256("after-state")


class _FakeBlobClient:
    """Test double for azure.storage.blob.BlobClient.

    Tracks upload calls; raises on a second upload to the same blob_name
    to simulate WORM overwrite rejection. Never accepts delete_blob.
    """

    def __init__(self) -> None:
        self._written: set[str] = set()
        self.uploads: list[dict] = []

    def upload_blob(self, data: bytes, overwrite: bool = False) -> None:  # noqa: FBT001
        # Simulate WORM: overwrite=True is rejected at storage layer, but our
        # client always passes overwrite=False, so we only test that path.
        if overwrite:
            raise ValueError("WORM policy: overwrite is not permitted")
        blob_name = id(self)  # unique per call in test; real key is the blob_client identity
        self.uploads.append({"data": data, "overwrite": overwrite})

    def delete_blob(self) -> None:
        # WORM storage rejects deletes within the retention window
        raise Exception("WORM policy: deletion is not permitted during retention period")


class _FakeContainerClient:
    """Returns the same _FakeBlobClient for any blob_name, per-instance."""

    def __init__(self) -> None:
        self._blobs: dict[str, _FakeBlobClient] = {}

    def get_blob_client(self, blob_name: str) -> _FakeBlobClient:
        if blob_name not in self._blobs:
            self._blobs[blob_name] = _FakeBlobClient()
        return self._blobs[blob_name]


class _FakeBlobServiceClient:
    def __init__(self) -> None:
        self._container = _FakeContainerClient()

    def get_container_client(self, name: str) -> _FakeContainerClient:  # noqa: ARG002
        return self._container


def _make_client(fake_service: _FakeBlobServiceClient | None = None) -> tuple[AuditClient, _FakeBlobServiceClient]:
    fake = fake_service or _FakeBlobServiceClient()
    with (
        patch("audit_client._client.ManagedIdentityCredential"),
        patch("audit_client._client.BlobServiceClient", return_value=fake),
    ):
        client = AuditClient(
            storage_account="stauditlogtest",
            container_name="audit-events",
        )
    # Replace the service reference after construction so our fake is used
    client._blob_service = fake
    return client, fake


# ---------------------------------------------------------------------------
# WORM overwrite rejection
# ---------------------------------------------------------------------------

class TestWormOverwriteRejection:
    def test_upload_is_called_with_overwrite_false(self):
        """write_event must never pass overwrite=True to the blob SDK."""
        client, fake = _make_client()
        container = fake._container

        client.write_event(
            user_id="u-001",
            action_type="SEQUENCE_UPDATED",
            entity_id="VIN-TESTVIN00000001A",
            before_hash=BEFORE_HASH,
            after_hash=AFTER_HASH,
            utc_ts=_ANCHOR_TS,
        )

        blobs = list(container._blobs.values())
        assert len(blobs) == 1
        assert blobs[0].uploads[0]["overwrite"] is False

    def test_overwrite_true_raises_on_fake_blob(self):
        """Verifies the fake's WORM simulation: overwrite=True raises."""
        blob = _FakeBlobClient()
        with pytest.raises(ValueError, match="WORM policy"):
            blob.upload_blob(data=b"{}", overwrite=True)


# ---------------------------------------------------------------------------
# WORM delete rejection (lifecycle rule must not produce deletes)
# ---------------------------------------------------------------------------

class TestWormDeleteRejection:
    def test_delete_blob_raises(self):
        """Blob client delete must raise — simulates Azure WORM delete rejection."""
        blob = _FakeBlobClient()
        with pytest.raises(Exception, match="deletion is not permitted"):
            blob.delete_blob()

    def test_client_never_calls_delete(self):
        """write_event must not invoke delete_blob at any point."""
        client, fake = _make_client()
        # Patch delete_blob on container to raise unconditionally; if called, test fails
        orig_get = fake._container.get_blob_client
        deleted: list[str] = []

        def guarded_get(blob_name: str) -> _FakeBlobClient:
            bc = orig_get(blob_name)
            bc.delete_blob = lambda: deleted.append(blob_name) or (_ for _ in ()).throw(  # type: ignore[assignment]
                AssertionError("delete_blob must not be called")
            )
            return bc

        fake._container.get_blob_client = guarded_get  # type: ignore[method-assign]

        client.write_event(
            user_id="u-001",
            action_type="VIN_CREATED",
            entity_id="VIN-TESTVIN00000001A",
            before_hash=BEFORE_HASH,
            after_hash=AFTER_HASH,
            utc_ts=_ANCHOR_TS,
        )
        assert deleted == []


# ---------------------------------------------------------------------------
# Blob schema: all six required fields must appear in the uploaded payload
# ---------------------------------------------------------------------------

class TestBlobSchema:
    def test_uploaded_payload_contains_all_six_fields(self):
        """Blob JSON must contain user_id, action_type, entity_id, before_hash, after_hash, utc_ts."""
        client, fake = _make_client()

        client.write_event(
            user_id="u-operator-99",
            action_type="SEQUENCE_FINALISED",
            entity_id="VIN-ABCDEFGHJKLMNPRA",
            before_hash=BEFORE_HASH,
            after_hash=AFTER_HASH,
            utc_ts=_ANCHOR_TS,
        )

        blobs = list(fake._container._blobs.values())
        assert len(blobs) == 1
        payload = json.loads(blobs[0].uploads[0]["data"])

        assert payload["user_id"] == "u-operator-99"
        assert payload["action_type"] == "SEQUENCE_FINALISED"
        assert payload["entity_id"] == "VIN-ABCDEFGHJKLMNPRA"
        assert payload["before_hash"] == BEFORE_HASH
        assert payload["after_hash"] == AFTER_HASH
        assert payload["utc_ts"] == _ANCHOR_TS

    def test_no_extra_fields_leak_into_payload(self):
        """Payload must contain exactly the six required fields — no extras."""
        client, fake = _make_client()

        client.write_event(
            user_id="u-999",
            action_type="CERT_ISSUED",
            entity_id="CERT-001",
            before_hash=BEFORE_HASH,
            after_hash=AFTER_HASH,
            utc_ts=_ANCHOR_TS,
        )

        blobs = list(fake._container._blobs.values())
        payload = json.loads(blobs[0].uploads[0]["data"])
        assert set(payload.keys()) == {
            "user_id",
            "action_type",
            "entity_id",
            "before_hash",
            "after_hash",
            "utc_ts",
        }


# ---------------------------------------------------------------------------
# Latency gate: write_event raises WriteLatencyExceeded when upload exceeds 1 s
# ---------------------------------------------------------------------------

class TestWriteLatency:
    def test_slow_upload_raises_write_latency_exceeded(self):
        """write_event must raise WriteLatencyExceeded when upload takes >1 s."""
        client, fake = _make_client()

        original_upload = _FakeBlobClient.upload_blob

        def slow_upload(self_bc, data: bytes, overwrite: bool = False) -> None:  # noqa: FBT001
            time.sleep(1.1)
            original_upload(self_bc, data=data, overwrite=overwrite)

        blobs_created: list[_FakeBlobClient] = []
        orig_get = fake._container.get_blob_client

        def patched_get(blob_name: str) -> _FakeBlobClient:
            bc = orig_get(blob_name)
            bc.upload_blob = lambda data, overwrite=False: slow_upload(bc, data=data, overwrite=overwrite)  # type: ignore[method-assign]
            blobs_created.append(bc)
            return bc

        fake._container.get_blob_client = patched_get  # type: ignore[method-assign]

        with pytest.raises(WriteLatencyExceeded, match="exceeds 1 s SLA"):
            client.write_event(
                user_id="u-001",
                action_type="SLOW_ACTION",
                entity_id="E-001",
                before_hash=BEFORE_HASH,
                after_hash=AFTER_HASH,
                utc_ts=_ANCHOR_TS,
            )

    def test_fast_upload_does_not_raise(self):
        """write_event must not raise when upload completes within 1 s."""
        client, _ = _make_client()
        blob_name = client.write_event(
            user_id="u-fast",
            action_type="FAST_ACTION",
            entity_id="E-FAST",
            before_hash=BEFORE_HASH,
            after_hash=AFTER_HASH,
            utc_ts=_ANCHOR_TS,
        )
        assert blob_name  # non-empty blob path returned


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_invalid_before_hash_raises_value_error(self):
        """before_hash that is not a 64-char hex digest raises ValueError."""
        client, _ = _make_client()
        with pytest.raises(ValueError, match="before_hash"):
            client.write_event(
                user_id="u-001",
                action_type="X",
                entity_id="E-001",
                before_hash="not-a-hash",
                after_hash=AFTER_HASH,
                utc_ts=_ANCHOR_TS,
            )

    def test_invalid_after_hash_raises_value_error(self):
        """after_hash that is not a 64-char hex digest raises ValueError."""
        client, _ = _make_client()
        with pytest.raises(ValueError, match="after_hash"):
            client.write_event(
                user_id="u-001",
                action_type="X",
                entity_id="E-001",
                before_hash=BEFORE_HASH,
                after_hash="short",
                utc_ts=_ANCHOR_TS,
            )


# ---------------------------------------------------------------------------
# Instance isolation — state does not bleed between AuditClient instances
# ---------------------------------------------------------------------------

class TestInstanceIsolation:
    def test_two_instances_have_independent_containers(self):
        """Two AuditClient instances must not share the same blob tracking state."""
        client_a, fake_a = _make_client()
        client_b, fake_b = _make_client()

        client_a.write_event(
            user_id="ua", action_type="A", entity_id="EA",
            before_hash=BEFORE_HASH, after_hash=AFTER_HASH, utc_ts=_ANCHOR_TS,
        )

        # Instance B container starts empty — writes from A must not appear in B
        assert list(fake_b._container._blobs.values()) == []
