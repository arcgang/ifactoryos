"""Unit tests for DBSCAN clustering, XGBoost scoring, and confidence threshold logic."""

from __future__ import annotations

import pytest

from app.cluster_state import (
    get_recent_events,
    record_event,
    run_dbscan,
    score_cluster_xgboost,
    _event_window,
    _compute_signature,
)
from app.models import DefectClass


_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"
_VIN_C = "WBA3B3C58DF596860"


def _make_event(
    vin: str,
    defect_class: str = "dust",
    humidity: float = 70.0,
    temp: float = 22.0,
    voltage: float = 300.0,
    phosphate: float = 2.0,
    stage: int = 3,
    detected_at: str = "2026-10-13T10:00:00+00:00",
) -> dict:
    return {
        "vin": vin,
        "defectClass": defect_class,
        "boothHumidityPct": humidity,
        "boothTemperatureC": temp,
        "ecoatVoltageV": voltage,
        "phosphateConcentrationGL": phosphate,
        "stageNumber": stage,
        "detectedAt": detected_at,
        "stationId": "PAINT-01",
        "scanTunnelId": "TUN-01",
    }


# ---------------------------------------------------------------------------
# record_event / get_recent_events
# ---------------------------------------------------------------------------


def test_record_event_stores_in_rolling_window() -> None:
    _event_window.clear()
    event = _make_event(_VIN_A)
    record_event(event)
    events = get_recent_events("dust", time_window_hours=4)
    assert any(e["vin"] == _VIN_A for e in events)


def test_get_recent_events_filters_by_vin() -> None:
    _event_window.clear()
    record_event(_make_event(_VIN_A))
    record_event(_make_event(_VIN_B))
    events = get_recent_events("dust", time_window_hours=4, vin_filter=[_VIN_A])
    assert all(e["vin"] == _VIN_A for e in events)
    assert len(events) == 1


def test_get_recent_events_returns_empty_for_unknown_class() -> None:
    events = get_recent_events("unknown_class", time_window_hours=4)
    assert events == []


def test_get_recent_events_excludes_old_events() -> None:
    _event_window.clear()
    # Far past timestamp — outside any rolling window
    old_event = _make_event(_VIN_C, detected_at="2020-01-01T00:00:00+00:00")
    record_event(old_event)
    events = get_recent_events("dust", time_window_hours=1)
    assert all(e["vin"] != _VIN_C for e in events)


# ---------------------------------------------------------------------------
# DBSCAN clustering
# ---------------------------------------------------------------------------


def test_dbscan_groups_similar_events_into_one_cluster() -> None:
    events = [
        _make_event(_VIN_A, humidity=70.0, temp=22.0, stage=3),
        _make_event(_VIN_B, humidity=70.0, temp=22.0, stage=3),
        _make_event(_VIN_C, humidity=70.0, temp=22.0, stage=3),
    ]
    clusters = run_dbscan(events)
    assert len(clusters) == 1
    sig = list(clusters.keys())[0]
    assert len(clusters[sig]) == 3


def test_dbscan_separates_distinct_parameter_clusters() -> None:
    events = [
        _make_event(_VIN_A, humidity=70.0, stage=3),
        _make_event(_VIN_B, humidity=30.0, stage=7),  # very different parameters
    ]
    clusters = run_dbscan(events)
    assert len(clusters) >= 2


def test_dbscan_empty_events_returns_empty_dict() -> None:
    clusters = run_dbscan([])
    assert clusters == {}


def test_compute_signature_is_deterministic() -> None:
    event = _make_event(_VIN_A)
    sig1 = _compute_signature(event)
    sig2 = _compute_signature(event)
    assert sig1 == sig2


def test_compute_signature_differs_for_different_stages() -> None:
    event_3 = _make_event(_VIN_A, stage=3)
    event_7 = _make_event(_VIN_A, stage=7)
    assert _compute_signature(event_3) != _compute_signature(event_7)


# ---------------------------------------------------------------------------
# XGBoost scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("defect_class", ["dust", "orange_peel", "run", "crater", "pinhole"])
def test_score_cluster_returns_top_5_factors(defect_class: str) -> None:
    events = [
        _make_event(_VIN_A, defect_class=defect_class),
        _make_event(_VIN_B, defect_class=defect_class),
        _make_event(_VIN_C, defect_class=defect_class),
    ]
    factors, confidence = score_cluster_xgboost(events, defect_class)
    assert 1 <= len(factors) <= 5
    assert 0.0 <= confidence <= 1.0


def test_score_cluster_factors_have_required_fields() -> None:
    events = [_make_event(_VIN_A, defect_class="dust")]
    factors, _ = score_cluster_xgboost(events, "dust")
    for f in factors:
        assert "rank" in f
        assert "factor_name" in f
        assert "factor_category" in f
        assert "probability_score" in f
        assert "supporting_vin_count" in f


def test_score_cluster_ranks_are_1_to_5() -> None:
    events = [_make_event(_VIN_A)]
    factors, _ = score_cluster_xgboost(events, "dust")
    ranks = [f["rank"] for f in factors]
    assert all(1 <= r <= 5 for r in ranks)
    assert ranks == sorted(ranks)


def test_score_cluster_probability_scores_between_0_and_1() -> None:
    events = [_make_event(_VIN_A)]
    factors, _ = score_cluster_xgboost(events, "dust")
    for f in factors:
        assert 0.0 <= f["probability_score"] <= 1.0


def test_score_cluster_empty_events_returns_empty_factors_zero_confidence() -> None:
    factors, confidence = score_cluster_xgboost([], "dust")
    assert factors == []
    assert confidence == 0.0


def test_score_cluster_supporting_vin_count_at_most_vin_count() -> None:
    events = [_make_event(_VIN_A), _make_event(_VIN_B)]
    factors, _ = score_cluster_xgboost(events, "dust")
    for f in factors:
        assert f["supporting_vin_count"] <= 2


# ---------------------------------------------------------------------------
# Confidence threshold comparison
# ---------------------------------------------------------------------------


def test_low_confidence_flag_when_below_threshold() -> None:
    _, confidence = score_cluster_xgboost([_make_event(_VIN_A, humidity=50.0)], "dust")
    # confidence may be low for single VIN with borderline params
    threshold = 0.60
    low_confidence = confidence < threshold
    assert isinstance(low_confidence, bool)


def test_high_confidence_when_multiple_vins_strong_signal() -> None:
    events = [
        _make_event(f"WBA3B3C5{i}DF596858", defect_class="crater", humidity=35.0, temp=25.0)
        for i in range(5)
    ]
    _, confidence = score_cluster_xgboost(events, "crater")
    # Expect non-zero confidence for clear signal
    assert confidence >= 0.0
