"""US-028 acceptance tests — EU data residency and PII protection (AC1, AC2).

Cross-task seams tested here (not covered by any existing unit test):
  AC1: Startup wires assert_eu_residency into _lifespan — a non-EU Neo4j URI combined
       with a configured allowlist raises RuntimeError before any traffic is served.
  AC1: ingest_event → scrub_parameters → identity-service (bmw-entra-id) → only the
       pseudonymous token, never the raw employee ID, is written to Neo4j or PostgreSQL.
  AC1: platform/immutable-audit-log/main.tf provisions all audit storage in
       germanywestcentral and binds an Azure Policy that allows no non-EU region.
  AC2: platform/immutable-audit-log/workbooks/article30_gdpr.json exists, is valid JSON,
       and contains every GDPR Art. 30 mandatory field (data categories, purposes,
       legal bases, retention periods, EU storage location, 5-business-day export SLA).
  AC2: platform/immutable-audit-log/main.tf provisions the Article 30 workbook resource.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Repo paths — navigate from this file upward to the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_AUDIT_LOG_TF = _REPO_ROOT / "platform" / "immutable-audit-log" / "main.tf"
_ARTICLE30_WORKBOOK = _REPO_ROOT / "platform" / "immutable-audit-log" / "workbooks" / "article30_gdpr.json"

_VIN = "WBA3A5C55FK123456"
# JWT sub=svc-uns-consumer, roles=[thread-ingestor], exp far-future, alg=none
_BEARER_WITH_ROLE = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJzdmMtdW5zLWNvbnN1bWVyIiwicm9sZXMiOlsidGhyZWFkLWluZ2VzdG9yIl0sImV4cCI6OTk5OTk5OTk5OX0"
    "."
)
_PSEUDONYMOUS_TOKEN = "cafebabe-0001-4000-a000-000000000001"

_SCHEMA_NAMES = [
    {"name": "vin_unique"}, {"name": "event_unique"},
    {"name": "event_by_type_time"}, {"name": "event_by_stage_number"},
    {"name": "event_by_shop"},
]


# ---------------------------------------------------------------------------
# Fake infrastructure — capturing variants for post-request inspection
# ---------------------------------------------------------------------------

class _CapturingConnection:
    """asyncpg connection fake that records the JSONB parameters arg from INSERT."""

    def __init__(self) -> None:
        self.insert_params_json: str | None = None

    def transaction(self):
        class _Tx:
            async def __aenter__(_self): return _self
            async def __aexit__(_self, *_): pass
        return _Tx()

    async def fetchrow(self, _query: str, *_args) -> None:
        return None

    async def execute(self, query: str, *args) -> None:
        # $9 (index 8) is _jsonb_params(scrubbed_parameters) in the INSERT
        if "INSERT INTO THREAD_EVENT_INGEST_LOG" in query.upper() and len(args) > 8:
            self.insert_params_json = str(args[8])


class _CapturingPool:
    def __init__(self) -> None:
        self.conn = _CapturingConnection()

    def acquire(self):
        conn = self.conn
        class _CM:
            async def __aenter__(_self): return conn
            async def __aexit__(_self, *_): pass
        return _CM()


class _CapturingNeo4jSession:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    async def run(self, query: str, **kwargs):
        self._calls.append(kwargs)
        class _R:
            async def data(_self):
                if "db.constraints" in query or "db.indexes" in query:
                    return _SCHEMA_NAMES
                return []
        return _R()

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


class _CapturingNeo4jDriver:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def session(self): return _CapturingNeo4jSession(self.calls)

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


class _FakeKafkaProducer:
    async def send_and_wait(self, *a, **kw): pass


def _make_settings(**overrides) -> MagicMock:
    s = MagicMock()
    s.neo4j_uri = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    s.neo4j_password = "localpassword"
    s.citus_url = "postgresql://localhost/test"
    s.kafka_bootstrap_servers = "localhost:9092"
    s.oidc_issuer_url = "http://localhost:8090/default"
    s.oidc_client_id = "local"
    s.identity_service_url = "http://identity-svc"
    s.identity_service_timeout_s = 2.0
    s.neo4j_eu_host_allowlist = ""
    s.ingestor_role = "thread-ingestor"
    s.reader_role_set.return_value = {"quality-engineer"}
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _identity_client_mock(token: str = _PSEUDONYMOUS_TOKEN) -> MagicMock:
    """Mock httpx.AsyncClient returning *token* from the identity-service lookup."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"token": token})
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ===========================================================================
# AC1 — EU data residency: startup enforcement
# ===========================================================================

def test_ac1_startup_blocks_non_eu_neo4j_when_allowlist_is_configured() -> None:
    """AC1: lifespan raises RuntimeError when Neo4j URI is outside the EU allowlist.

    Proves the end-to-end wiring across tasks:
      settings.neo4j_eu_host_allowlist (configured by immutable-audit-log/bmw-entra-id ops)
      → _lifespan calls assert_eu_residency
      → RuntimeError propagates before any ASGI worker serves traffic.
    """
    import app.config as config_mod
    from app.main import create_app

    settings = _make_settings(
        neo4j_uri="bolt://neo4j-prod-us-east-1.example.com:7687",
        neo4j_eu_host_allowlist="neo4j.germanywestcentral.azure.com,neo4j-munich-onprem.internal",
    )

    with patch.object(config_mod, "_settings", settings):
        app_instance = create_app()
        with pytest.raises(RuntimeError, match="EU residency check failed"):
            with TestClient(app_instance):
                pass


def test_ac1_startup_invokes_assert_eu_residency_with_configured_neo4j_uri() -> None:
    """AC1: _lifespan passes neo4j_uri and neo4j_eu_host_allowlist to assert_eu_residency.

    The seam: vin-digital-thread-api reads the configured allowlist at startup and
    passes it to the enforcement function — this is what prevents cross-border writes.
    """
    import app.config as config_mod
    import app.db as db_mod
    import app.eu_residency as eu_mod
    import app.kafka_consumer as kafka_consumer_mod
    import app.kafka_producer as kafka_mod
    import app.torque_consumer as torque_consumer_mod
    from app.main import create_app

    settings = _make_settings(
        neo4j_uri="bolt://neo4j.germanywestcentral.azure.com:7687",
        neo4j_eu_host_allowlist="neo4j.germanywestcentral.azure.com",
    )
    fake_pool = _CapturingPool()
    fake_neo4j = _CapturingNeo4jDriver()
    fake_kafka = _FakeKafkaProducer()

    async def _pool(): return fake_pool
    async def _producer(): return fake_kafka
    async def _noop(): pass

    eu_calls: list[dict] = []

    def _capture_eu_check(neo4j_uri: str, allowlist_csv: str) -> None:
        eu_calls.append({"neo4j_uri": neo4j_uri, "allowlist_csv": allowlist_csv})

    with (
        patch.object(config_mod, "_settings", settings),
        patch.object(db_mod, "get_pool", _pool),
        patch.object(kafka_mod, "get_producer", _producer),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=fake_neo4j),
        patch.object(eu_mod, "assert_eu_residency", side_effect=_capture_eu_check),
        patch.object(torque_consumer_mod, "start_torque_consumer", _noop),
        patch.object(torque_consumer_mod, "stop_torque_consumer", _noop),
        patch.object(kafka_consumer_mod, "start_consumer", _noop),
        patch.object(kafka_consumer_mod, "stop_consumer", _noop),
    ):
        app_instance = create_app()
        with TestClient(app_instance, raise_server_exceptions=False):
            pass

    assert len(eu_calls) == 1, "assert_eu_residency must be called exactly once at startup"
    assert eu_calls[0]["neo4j_uri"] == "bolt://neo4j.germanywestcentral.azure.com:7687"
    assert eu_calls[0]["allowlist_csv"] == "neo4j.germanywestcentral.azure.com"


# ===========================================================================
# AC1 — EU data residency: PII scrub before any data store write (cross-task seam)
# ===========================================================================

@pytest.mark.asyncio
async def test_ac1_torque_log_operator_id_pseudonymised_before_neo4j_write() -> None:
    """AC1: raw operator_id is replaced by a pseudonymous token before Neo4j write.

    End-to-end seam:
      ingest_event (vin-digital-thread-api) with operator_id="12345678"
      → calls scrub_parameters
      → scrub_parameters calls identity-service (bmw-entra-id) GET /internal/identity/pseudonymise
      → Neo4j MERGE receives the pseudonymous token, never the raw employee ID.
    """
    import app.config as config_mod
    from app.ingest_service import ingest_event
    from app.models import EventType, ThreadEventIngestRequest

    settings = _make_settings()
    fake_pool = _CapturingPool()
    fake_neo4j = _CapturingNeo4jDriver()

    request = ThreadEventIngestRequest(
        idempotencyKey=uuid.uuid4(),
        eventType=EventType.TORQUE_LOG,
        shop="BODY_SHOP",
        station="STATION_01",
        occurredAt="2026-01-15T08:00:00Z",
        dataSource="torque-tool",
        parameters={"operator_id": "12345678", "torque_nm": 42.5},
    )

    async def _fake_kafka_publish(*_a, **_kw): pass

    with (
        patch.object(config_mod, "_settings", settings),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=fake_neo4j),
        patch("app.pii_scrubber.httpx.AsyncClient", return_value=_identity_client_mock()),
        patch("app.ingest_service._publish_event_linked", _fake_kafka_publish),
        patch("app.ingest_service._refresh_completeness_and_alert", _fake_kafka_publish),
    ):
        result = await ingest_event(
            vin=_VIN,
            request=request,
            user_id="svc-uns-consumer",
            pool=fake_pool,
        )

    assert result.vin == _VIN

    # Find the Neo4j MERGE write — the entry that carries the 'parameters' kwarg
    merge_entries = [e for e in fake_neo4j.calls if "parameters" in e]
    assert len(merge_entries) >= 1, (
        "Neo4j MERGE write must have occurred; no 'parameters' kwarg found in session calls"
    )
    neo4j_params = json.loads(merge_entries[0]["parameters"])

    assert "12345678" not in json.dumps(neo4j_params), (
        "Raw employee ID '12345678' must not be written to Neo4j"
    )
    assert neo4j_params.get("operator_id") == _PSEUDONYMOUS_TOKEN, (
        f"operator_id in Neo4j must be the pseudonymous token '{_PSEUDONYMOUS_TOKEN}' "
        "returned by identity-service (bmw-entra-id), not the raw employee ID"
    )


@pytest.mark.asyncio
async def test_ac1_torque_log_raw_employee_id_absent_from_postgresql_ingest_log() -> None:
    """AC1: raw operator_id is absent from the PostgreSQL thread_event_ingest_log INSERT.

    The PII scrub happens before the PostgreSQL INSERT — neither data store receives raw PII.
    This verifies the cross-task seam: vin-digital-thread-api calls pii_scrubber which
    calls identity-service (bmw-entra-id), and the result is what reaches the database.
    """
    import app.config as config_mod
    from app.ingest_service import ingest_event
    from app.models import EventType, ThreadEventIngestRequest

    settings = _make_settings()
    fake_pool = _CapturingPool()
    fake_neo4j = _CapturingNeo4jDriver()

    request = ThreadEventIngestRequest(
        idempotencyKey=uuid.uuid4(),
        eventType=EventType.TORQUE_LOG,
        shop="BODY_SHOP",
        station="STATION_01",
        occurredAt="2026-01-15T08:00:00Z",
        dataSource="torque-tool",
        parameters={"operator_id": "12345678", "torque_nm": 42.5},
    )

    async def _fake_kafka_publish(*_a, **_kw): pass

    with (
        patch.object(config_mod, "_settings", settings),
        patch("neo4j.AsyncGraphDatabase.driver", return_value=fake_neo4j),
        patch("app.pii_scrubber.httpx.AsyncClient", return_value=_identity_client_mock()),
        patch("app.ingest_service._publish_event_linked", _fake_kafka_publish),
        patch("app.ingest_service._refresh_completeness_and_alert", _fake_kafka_publish),
    ):
        await ingest_event(
            vin=_VIN,
            request=request,
            user_id="svc-uns-consumer",
            pool=fake_pool,
        )

    pg_params = fake_pool.conn.insert_params_json
    assert pg_params is not None, (
        "No INSERT into thread_event_ingest_log was captured; "
        "verify _CapturingConnection.execute intercepts the INSERT query"
    )
    assert "12345678" not in pg_params, (
        "Raw employee ID '12345678' must not be written to PostgreSQL thread_event_ingest_log; "
        "scrub_parameters must have been called before the INSERT"
    )


# ===========================================================================
# AC1 — Terraform infrastructure: EU location enforcement and Azure Policy
# ===========================================================================

def test_ac1_immutable_audit_log_terraform_config_exists() -> None:
    """AC1 (precondition): platform/immutable-audit-log/main.tf must exist."""
    assert _AUDIT_LOG_TF.is_file(), (
        f"platform/immutable-audit-log/main.tf not found at {_AUDIT_LOG_TF}; "
        "immutable-audit-log task appears unmerged"
    )


def test_ac1_audit_log_storage_account_provisioned_in_eu_germanywestcentral() -> None:
    """AC1: Terraform provisions the audit log storage account in germanywestcentral (EU-DE)."""
    assert _AUDIT_LOG_TF.is_file(), f"Precondition: {_AUDIT_LOG_TF} must exist"
    tf_text = _AUDIT_LOG_TF.read_text()

    assert 'location = "germanywestcentral"' in tf_text, (
        "Audit log storage account must be provisioned in germanywestcentral; "
        "this is the GDPR data residency hard constraint"
    )


def test_ac1_azure_policy_restricts_audit_log_to_eu_regions_only() -> None:
    """AC1: Azure Policy allowedLocations contains only EU regions; no non-EU region present."""
    assert _AUDIT_LOG_TF.is_file(), f"Precondition: {_AUDIT_LOG_TF} must exist"
    tf_text = _AUDIT_LOG_TF.read_text()

    assert "azurerm_resource_group_policy_assignment" in tf_text, (
        "Azure Policy assignment resource must exist to enforce EU data residency"
    )
    assert '"germanywestcentral"' in tf_text, (
        "EU allowedLocations policy must include germanywestcentral"
    )
    assert '"westeurope"' in tf_text, (
        "EU allowedLocations policy must include westeurope as secondary EU location"
    )
    for non_eu in ('"eastus"', '"westus"', '"southeastasia"', '"australiaeast"', '"centralus"'):
        assert non_eu not in tf_text, (
            f"{non_eu} (non-EU region) must not appear in the EU residency policy parameters"
        )


# ===========================================================================
# AC2 — GDPR Article 30 record of processing activities
# ===========================================================================

def test_ac2_article30_workbook_file_exists() -> None:
    """AC2 (precondition): Article 30 workbook JSON must exist before content assertions."""
    assert _ARTICLE30_WORKBOOK.is_file(), (
        f"Article 30 workbook not found at {_ARTICLE30_WORKBOOK}; "
        "immutable-audit-log task may not be merged"
    )


def test_ac2_article30_workbook_is_valid_json_with_items_array() -> None:
    """AC2 (precondition): workbook file must be valid JSON with a non-empty items array."""
    assert _ARTICLE30_WORKBOOK.is_file(), f"Workbook missing: {_ARTICLE30_WORKBOOK}"
    content = json.loads(_ARTICLE30_WORKBOOK.read_text())
    assert "items" in content, "Workbook top-level object must have an 'items' key"
    assert len(content["items"]) > 0, "Workbook items array must be non-empty"


def test_ac2_article30_workbook_lists_all_required_data_categories() -> None:
    """AC2: workbook lists all GDPR Art. 30(1)(b) data categories: audit, session, torque, VIN."""
    assert _ARTICLE30_WORKBOOK.is_file(), f"Workbook missing: {_ARTICLE30_WORKBOOK}"
    text = _ARTICLE30_WORKBOOK.read_text()

    for category in ("torque", "VIN", "session", "audit"):
        assert category in text, (
            f"Article 30 workbook must list '{category}' data category "
            f"as required by GDPR Art. 30(1)(b)"
        )


def test_ac2_article30_workbook_includes_legal_bases_and_retention_periods() -> None:
    """AC2: workbook includes GDPR legal bases (Art. 6) and retention periods (Art. 30(1)(f))."""
    assert _ARTICLE30_WORKBOOK.is_file(), f"Workbook missing: {_ARTICLE30_WORKBOOK}"
    text = _ARTICLE30_WORKBOOK.read_text()

    assert "Art. 6(1)(c)" in text, (
        "Workbook must cite Art. 6(1)(c) (legal obligation) as a legal basis"
    )
    assert "Art. 6(1)(f)" in text, (
        "Workbook must cite Art. 6(1)(f) (legitimate interest) as a legal basis"
    )
    assert "7 year" in text or "7-year" in text, (
        "Workbook must document the 7-year WORM retention period"
    )
    assert "Germany West Central" in text or "germanywestcentral" in text, (
        "Workbook must document EU storage location (Germany West Central) per Art. 30(1)(f)"
    )


def test_ac2_article30_workbook_states_5_business_day_availability_sla() -> None:
    """AC2: workbook export instructions commit to availability within 5 business days."""
    assert _ARTICLE30_WORKBOOK.is_file(), f"Workbook missing: {_ARTICLE30_WORKBOOK}"
    text = _ARTICLE30_WORKBOOK.read_text()

    assert "5 business days" in text, (
        "Article 30 workbook must state the DPO can respond to Works Council requests "
        "within 5 business days, as required by the acceptance criterion"
    )


def test_ac2_terraform_provisions_article30_workbook_resource_from_json_file() -> None:
    """AC2: Terraform creates the Azure Monitor workbook resource backed by article30_gdpr.json."""
    assert _AUDIT_LOG_TF.is_file(), f"Precondition: {_AUDIT_LOG_TF} must exist"
    tf_text = _AUDIT_LOG_TF.read_text()

    assert "azurerm_application_insights_workbook" in tf_text, (
        "Terraform must provision an Azure Monitor workbook for the Article 30 inventory"
    )
    assert "GDPR Article 30" in tf_text, (
        "Terraform workbook display_name must reference 'GDPR Article 30'"
    )
    assert "article30_gdpr.json" in tf_text, (
        "Terraform workbook data_json must load from article30_gdpr.json"
    )
