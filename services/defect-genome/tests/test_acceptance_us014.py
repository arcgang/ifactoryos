"""
US-014 acceptance tests — seam between jira-integration infra, defect-genome backend,
and quality-studio frontend.

Each test targets a gap that unit tests in test_api.py and DefectGenomePage.test.tsx
do NOT cover: the contract at the boundary between the three merged tasks.

AC1: cluster-query returns top_factors with the exact fields the quality-studio
     TypeScript RootCauseFactor interface requires, 1–5 entries, ranks ascending.
AC2: low_confidence_threshold is read from the DB (configurable); the backend sets
     low_confidence=True and supplies advisor_v1_fallback when model confidence falls
     below that threshold — even a non-default threshold.
AC3: corrective-action endpoint builds the Jira issue description with cluster_id,
     root-cause probability, affected VIN count, and recommended prep stage adjustment;
     the response includes a prepopulated_summary referencing the cluster.
"""
from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Precondition: application must be importable before any test runs.
# ---------------------------------------------------------------------------
try:
    from fastapi.testclient import TestClient
    from app.main import create_app
except ImportError as exc:  # pragma: no cover
    pytest.fail(f"Precondition failed — cannot import app.main.create_app: {exc}")


# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"
_CLUSTER_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_VIN_A = "WBA3B3C56DF596858"

_FIVE_FACTORS = [
    {
        "rank": 1,
        "factor_name": "humidity_elevated_booth",
        "factor_category": "booth_parameter",
        "probability_score": 0.72,
        "supporting_vin_count": 3,
    },
    {
        "rank": 2,
        "factor_name": "ecoat_voltage_drop",
        "factor_category": "prep_stage",
        "probability_score": 0.55,
        "supporting_vin_count": 2,
    },
    {
        "rank": 3,
        "factor_name": "ambient_temp_spike",
        "factor_category": "climate_condition",
        "probability_score": 0.40,
        "supporting_vin_count": 2,
    },
    {
        "rank": 4,
        "factor_name": "phosphate_low",
        "factor_category": "prep_stage",
        "probability_score": 0.30,
        "supporting_vin_count": 1,
    },
    {
        "rank": 5,
        "factor_name": "dwell_time_exceeded",
        "factor_category": "prep_stage",
        "probability_score": 0.20,
        "supporting_vin_count": 1,
    },
]


def _make_jwt() -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        b'{"sub":"engineer@bmwgroup.net","exp":9999999999}'
    ).rstrip(b"=").decode()
    return f"{header}.{payload}."


_AUTH = {"Authorization": f"Bearer {_make_jwt()}"}


# ---------------------------------------------------------------------------
# Stub DB pool — instance-scoped state, same pattern as test_api.py
# ---------------------------------------------------------------------------


class _StubConn:
    def __init__(self, pool: "_StubPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "_StubConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def execute(self, query: str, *args: object) -> None:
        pass

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        if "confidence_threshold_config" in query:
            return self._pool._threshold_row
        if "cluster_query_result" in query and "idempotency_key" in query:
            return None
        if "defect_cluster" in query and "cluster_id = $1" in query:
            cid = str(args[0]) if args else ""
            return self._pool._clusters.get(cid)
        return None

    async def fetch(self, query: str, *args: object) -> list:
        if "defect_cluster" in query:
            return list(self._pool._clusters.values())
        return []

    async def fetchval(self, query: str, *args: object) -> Any:
        if "UPDATE defect_cluster" in query:
            return str(_CLUSTER_ID)
        return None

    def transaction(self) -> "_TxCtx":
        return _TxCtx(self)


class _TxCtx:
    def __init__(self, conn: _StubConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> "_TxCtx":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _StubPool:
    """Stub pool with a configurable confidence threshold and cluster store.
    Each instance starts clean — no module-level state, satisfying the
    'mutable state in private instance fields' convention."""

    def __init__(self, low_confidence_threshold: float = 0.60) -> None:
        self._clusters: dict[str, dict] = {}
        self._threshold_row: dict = {
            "low_confidence_threshold": low_confidence_threshold,
            "cluster_alert_threshold": 0.80,
            "cluster_alert_min_vins": 3,
            "cluster_alert_window_hours": 4,
            "updated_at": None,
        }

    def acquire(self) -> _StubConn:
        return _StubConn(self)

    def seed_cluster(self, cluster_id: uuid.UUID | None = None) -> None:
        cid = str(cluster_id or _CLUSTER_ID)
        self._clusters[cid] = {
            "cluster_id": cid,
            "defect_class": "dust",
            "status": "open",
            "root_cause_signature": "stage=3:hum=2",
            "confidence_score": 0.85,
            "affected_vin_count": 3,
            "most_recent_vin": _VIN_A,
            "detected_at": _FAR_FUTURE,
            "created_at": _FAR_FUTURE,
            "updated_at": _FAR_FUTURE,
            "top_factors": json.dumps([_FIVE_FACTORS[0]]),
            "process_replay_url": (
                f"https://quality-studio.internal.bmw-factory-os.eu/process-replay/{_VIN_A}"
            ),
            "corrective_action_jira_id": None,
        }


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC1 — top_factors shape: each entry must carry the fields the quality-studio
#        TypeScript RootCauseFactor interface declares.
# ---------------------------------------------------------------------------


def test_ac1_top_factors_have_all_required_fields(client: TestClient) -> None:
    """AC1: top_factors in the cluster-query response carry rank, factor_name,
    factor_category, probability_score, and supporting_vin_count — the exact
    fields the RootCauseFactor TypeScript interface requires."""
    pool = _StubPool()
    pool.seed_cluster()
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch(
            "app.cluster_service.score_cluster_xgboost",
            return_value=(_FIVE_FACTORS, 0.72),
        ),
        patch("app.cluster_state._event_window", {}),
        patch("app.cluster_service._threshold_cache", None),
    ):
        resp = client.post(
            "/defect/cluster-query", json={"defect_class": "dust"}, headers=_AUTH
        )

    # Precondition: the endpoint must succeed before shape assertions.
    assert resp.status_code == 200, f"Precondition: expected 200, got {resp.status_code}: {resp.text}"
    factors = resp.json()["top_factors"]
    assert 1 <= len(factors) <= 5, f"top_factors must have 1–5 entries, got {len(factors)}"

    required_fields = {
        "rank", "factor_name", "factor_category", "probability_score", "supporting_vin_count"
    }
    valid_categories = {"prep_stage", "booth_parameter", "climate_condition"}

    for f in factors:
        missing = required_fields - f.keys()
        assert not missing, f"factor missing fields {missing}: {f}"
        assert 0.0 <= f["probability_score"] <= 1.0, (
            f"probability_score {f['probability_score']} must be in [0, 1]"
        )
        assert f["supporting_vin_count"] >= 0, (
            f"supporting_vin_count must be non-negative, got {f['supporting_vin_count']}"
        )
        assert f["factor_category"] in valid_categories, (
            f"factor_category '{f['factor_category']}' not in {valid_categories}"
        )


def test_ac1_top_factors_sorted_by_rank_ascending(client: TestClient) -> None:
    """AC1: top_factors are returned in ascending rank order (1..N) so the
    frontend can render them as a ranked list without client-side re-sorting."""
    pool = _StubPool()
    pool.seed_cluster()
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch(
            "app.cluster_service.score_cluster_xgboost",
            return_value=(_FIVE_FACTORS, 0.72),
        ),
        patch("app.cluster_state._event_window", {}),
        patch("app.cluster_service._threshold_cache", None),
    ):
        resp = client.post(
            "/defect/cluster-query", json={"defect_class": "dust"}, headers=_AUTH
        )

    assert resp.status_code == 200
    factors = resp.json()["top_factors"]
    ranks = [f["rank"] for f in factors]
    assert ranks == list(range(1, len(factors) + 1)), (
        f"ranks must be 1..N in ascending order, got {ranks}"
    )


# ---------------------------------------------------------------------------
# AC2 — configurable confidence threshold is truly read from the DB, not hardcoded.
# ---------------------------------------------------------------------------


def test_ac2_low_confidence_threshold_is_configurable_from_db(client: TestClient) -> None:
    """AC2: The backend reads low_confidence_threshold from the DB.
    A model_confidence of 0.70 is HIGH confidence at the default 0.60 threshold
    but LOW confidence when the DB row stores 0.75 — proving the value is not
    hardcoded."""
    pool = _StubPool(low_confidence_threshold=0.75)  # non-default threshold
    pool.seed_cluster()
    model_confidence = 0.70  # above 0.60 but below 0.75

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch(
            "app.cluster_service.score_cluster_xgboost",
            return_value=([_FIVE_FACTORS[0]], model_confidence),
        ),
        patch("app.cluster_state._event_window", {}),
        patch("app.cluster_service._threshold_cache", None),
    ):
        resp = client.post(
            "/defect/cluster-query", json={"defect_class": "dust"}, headers=_AUTH
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_confidence"] == pytest.approx(model_confidence, abs=0.01)
    assert body["low_confidence"] is True, (
        "low_confidence must be True when model_confidence (0.70) < DB-configured threshold (0.75)"
    )
    assert body["advisor_v1_fallback"] is not None, (
        "advisor_v1_fallback must be present when low_confidence is true"
    )
    assert body["advisor_v1_fallback"]["rule_id"], "advisor_v1_fallback must include a rule_id"
    assert body["advisor_v1_fallback"]["recommended_action"], (
        "advisor_v1_fallback must include a recommended_action"
    )


def test_ac2_above_db_threshold_no_low_confidence_and_no_fallback(client: TestClient) -> None:
    """AC2: When model_confidence meets the configured threshold, low_confidence=false
    and advisor_v1_fallback is absent — the DB threshold of 0.60 is honoured."""
    pool = _StubPool(low_confidence_threshold=0.60)
    pool.seed_cluster()

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service.asyncio.create_task"),
        patch(
            "app.cluster_service.score_cluster_xgboost",
            return_value=([_FIVE_FACTORS[0]], 0.85),
        ),
        patch("app.cluster_state._event_window", {}),
        patch("app.cluster_service._threshold_cache", None),
    ):
        resp = client.post(
            "/defect/cluster-query", json={"defect_class": "dust"}, headers=_AUTH
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["low_confidence"] is False
    assert body.get("advisor_v1_fallback") is None


# ---------------------------------------------------------------------------
# AC3 — Jira ticket is pre-populated with cluster_id, root-cause probability,
#        affected VIN count, and recommended prep stage adjustment.
# ---------------------------------------------------------------------------


def test_ac3_jira_payload_contains_required_prepopulated_fields(client: TestClient) -> None:
    """AC3: The Jira issue description sent by the corrective-action endpoint
    must contain the cluster_id, root-cause probability, affected VIN count,
    and recommended prep stage adjustment — all four fields required by the
    acceptance criterion."""
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    mock_jira = AsyncMock(
        return_value=("DEFECTGENOME-1", "https://jira.bmwgroup.net/browse/DEFECTGENOME-1")
    )

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service._call_jira_create_issue", mock_jira),
        patch("app.cluster_service.asyncio.create_task"),
    ):
        resp = client.post(
            f"/defect/clusters/{_CLUSTER_ID}/corrective-action",
            json={
                "assigned_root_cause_factor_rank": 1,
                "confirmed_by_user_id": "engineer@bmwgroup.net",
            },
            headers=_AUTH,
        )

    assert resp.status_code == 201, resp.text
    assert mock_jira.called, "Precondition: _call_jira_create_issue must have been called"

    _cfg_arg, jira_payload = mock_jira.call_args.args
    description: str = (
        jira_payload["fields"]["description"]["content"][0]["content"][0]["text"]
    )

    assert str(_CLUSTER_ID) in description, (
        f"Jira description must include cluster_id '{_CLUSTER_ID}'"
    )
    assert "probability" in description.lower(), (
        "Jira description must include root-cause probability"
    )
    assert "Affected VINs" in description, (
        "Jira description must include affected VIN count"
    )
    assert "prep stage" in description.lower(), (
        "Jira description must include recommended prep stage adjustment"
    )


def test_ac3_response_includes_prepopulated_summary_referencing_cluster(
    client: TestClient,
) -> None:
    """AC3: The CorrectiveActionResult response body includes prepopulated_summary
    that contains the cluster_id, jira_ticket_id, jira_ticket_url, and created_at —
    confirming the Jira ticket is pre-populated before the engineer clicks confirm."""
    pool = _StubPool()
    pool.seed_cluster(_CLUSTER_ID)
    mock_jira = AsyncMock(
        return_value=("DEFECTGENOME-42", "https://jira.bmwgroup.net/browse/DEFECTGENOME-42")
    )

    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.cluster_service._call_jira_create_issue", mock_jira),
        patch("app.cluster_service.asyncio.create_task"),
    ):
        resp = client.post(
            f"/defect/clusters/{_CLUSTER_ID}/corrective-action",
            json={
                "assigned_root_cause_factor_rank": 1,
                "confirmed_by_user_id": "engineer@bmwgroup.net",
            },
            headers=_AUTH,
        )

    assert resp.status_code == 201
    body = resp.json()

    assert "prepopulated_summary" in body, "response must include prepopulated_summary"
    assert body["prepopulated_summary"], "prepopulated_summary must not be empty"
    assert str(_CLUSTER_ID) in body["prepopulated_summary"], (
        "prepopulated_summary must reference the cluster_id"
    )
    assert body["cluster_id"] == str(_CLUSTER_ID)
    assert body["jira_ticket_id"] == "DEFECTGENOME-42"
    assert body["jira_ticket_url"] == "https://jira.bmwgroup.net/browse/DEFECTGENOME-42"
    assert "created_at" in body


# ---------------------------------------------------------------------------
# Jira integration infra seam — credentials sourced from environment, not hardcoded.
# ---------------------------------------------------------------------------


def test_jira_client_credentials_have_empty_defaults_injected_by_vault() -> None:
    """The jira-integration task stores client_id and client_secret in Vault at
    secret/data/jira/defect-genome-client-secret.  The application config must
    default these to empty strings so a misconfigured pod fails fast rather than
    using a hardcoded credential."""
    from app.config import Settings

    settings = Settings()
    assert settings.jira_client_id == "", (
        "jira_client_id must default to '' — real value is injected by Vault at runtime"
    )
    assert settings.jira_client_secret == "", (
        "jira_client_secret must default to '' — real value is injected by Vault at runtime"
    )
