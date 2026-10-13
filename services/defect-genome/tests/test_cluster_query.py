"""Tests for POST /defect/cluster-query."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app

_VALID_JWT = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjk5OTk5OTk5OTl9"
    "."
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_VALID_JWT}"}


def _make_client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _valid_body(defect_class: str = "crater") -> dict[str, Any]:
    return {"defect_class": defect_class}


# ---------------------------------------------------------------------------
# POST /defect/cluster-query — happy path
# ---------------------------------------------------------------------------


class TestRunClusterQuery:
    def test_returns_200_with_required_fields(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        required = {
            "query_id", "cluster_id", "defect_class", "model_confidence",
            "low_confidence", "top_factors", "affected_vin_count", "queried_at",
        }
        for field in required:
            assert field in body, f"missing field: {field}"

    def test_defect_class_matches_request(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body("dust"), headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["defect_class"] == "dust"

    def test_query_id_and_cluster_id_are_uuids(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        uuid.UUID(body["query_id"])
        uuid.UUID(body["cluster_id"])

    def test_top_factors_non_empty(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        factors = resp.json()["top_factors"]
        assert len(factors) >= 1

    def test_top_factor_contains_required_fields(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        factor = resp.json()["top_factors"][0]
        required = {"rank", "factor_name", "factor_category", "probability_score", "supporting_vin_count"}
        for field in required:
            assert field in factor, f"missing factor field: {field}"

    def test_vin_filter_populates_affected_vins(self) -> None:
        client = _make_client()
        vins = ["WBS4E0108RG271034", "WBS4E0108RG271035"]
        resp = client.post(
            "/defect/cluster-query",
            json={"defect_class": "crater", "vin_filter": vins},
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["affected_vin_count"] == 2
        assert body["affected_vins"] == vins

    def test_high_confidence_result_has_no_advisor_fallback(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["low_confidence"] is False
        assert "advisor_v1_fallback" not in body or body.get("advisor_v1_fallback") is None

    def test_all_defect_classes_accepted(self) -> None:
        client = _make_client()
        for dc in ("dust", "orange_peel", "run", "crater", "pinhole"):
            resp = client.post("/defect/cluster-query", json=_valid_body(dc), headers=_auth())
            assert resp.status_code == 200, f"failed for defect_class={dc}"

    def test_time_window_hours_accepted(self) -> None:
        client = _make_client()
        resp = client.post(
            "/defect/cluster-query",
            json={"defect_class": "crater", "time_window_hours": 24},
            headers=_auth(),
        )
        assert resp.status_code == 200

    def test_model_confidence_between_0_and_1(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body(), headers=_auth())
        assert resp.status_code == 200
        confidence = resp.json()["model_confidence"]
        assert 0.0 <= confidence <= 1.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestRunClusterQueryValidation:
    def test_unsupported_defect_class_returns_422(self) -> None:
        client = _make_client()
        resp = client.post(
            "/defect/cluster-query",
            json={"defect_class": "scratch"},
            headers=_auth(),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "UNSUPPORTED_DEFECT_CLASS"
        assert "correlation_id" in body

    def test_missing_defect_class_returns_422(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json={}, headers=_auth())
        assert resp.status_code == 422

    def test_time_window_above_168_returns_422(self) -> None:
        client = _make_client()
        resp = client.post(
            "/defect/cluster-query",
            json={"defect_class": "crater", "time_window_hours": 169},
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_time_window_below_1_returns_422(self) -> None:
        client = _make_client()
        resp = client.post(
            "/defect/cluster-query",
            json={"defect_class": "crater", "time_window_hours": 0},
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_returns_401_without_bearer(self) -> None:
        client = _make_client()
        resp = client.post("/defect/cluster-query", json=_valid_body())
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "MISSING_TOKEN"
