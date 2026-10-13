"""In-memory cluster state maintained by the streaming Kafka consumer.

DBSCAN clustering runs against this pre-indexed state so that HTTP inference
calls only need to invoke XGBoost scoring (< 100 ms p99 NFR).
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Shared mutable state — updated by the consumer, read by the HTTP handlers.
# Each entry keyed by defect_class holds a list of recent event dicts.
_event_window: dict[str, list[dict[str, Any]]] = defaultdict(list)

# Cluster state per defect_class — maps root_cause_signature → list of VINs
_cluster_members: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

# Most recent event timestamp per VIN per defect_class
_vin_last_seen: dict[str, dict[str, str]] = defaultdict(dict)


def record_event(event: dict[str, Any]) -> None:
    """Append an incoming PaintDefectDetected event to the rolling window."""
    defect_class: str = event.get("defectClass", "")
    if not defect_class:
        return
    _event_window[defect_class].append(event)
    vin: str = event.get("vin", "")
    if vin:
        _vin_last_seen[defect_class][vin] = event.get("detectedAt", "")
    _prune_old_events(defect_class, window_hours=168)


def _prune_old_events(defect_class: str, window_hours: int) -> None:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
    events = _event_window[defect_class]
    _event_window[defect_class] = [
        e for e in events
        if _parse_iso(e.get("detectedAt", "")) >= cutoff
    ]


def _parse_iso(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def get_recent_events(defect_class: str, time_window_hours: int, vin_filter: list[str] | None = None) -> list[dict[str, Any]]:
    """Return events within the rolling window, optionally filtered by VIN list."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=time_window_hours)
    events = [
        e for e in _event_window.get(defect_class, [])
        if _parse_iso(e.get("detectedAt", "")) >= cutoff
    ]
    if vin_filter:
        vin_set = set(vin_filter)
        events = [e for e in events if e.get("vin") in vin_set]
    return events


def run_dbscan(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Simplified DBSCAN-style clustering by grouping events on stage + parameter thresholds.

    Returns a dict mapping root_cause_signature → list of events in that cluster.
    Full scikit-learn DBSCAN would run here in production; this deterministic
    approximation is sufficient for unit-testable service logic.
    """
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        sig = _compute_signature(event)
        clusters[sig].append(event)
    return dict(clusters)


def _compute_signature(event: dict[str, Any]) -> str:
    """Derive a canonical root-cause signature from paint-process parameters."""
    stage = event.get("stageNumber", 0)
    humidity_band = _band(event.get("boothHumidityPct"), [40, 60, 75])
    temp_band = _band(event.get("boothTemperatureC"), [20, 23, 26])
    voltage_band = _band(event.get("ecoatVoltageV"), [270, 320, 350])
    phosphate_band = _band(event.get("phosphateConcentrationGL"), [1.8, 2.2, 2.6])
    return f"stage={stage}:hum={humidity_band}:temp={temp_band}:v={voltage_band}:ph={phosphate_band}"


def _band(value: float | None, boundaries: list[float]) -> str:
    if value is None:
        return "?"
    for i, b in enumerate(boundaries):
        if value < b:
            return str(i)
    return str(len(boundaries))


def score_cluster_xgboost(
    cluster_events: list[dict[str, Any]],
    defect_class: str,
    model_version: str = "1.0.0",
) -> tuple[list[dict[str, Any]], float]:
    """Deterministic XGBoost-style scoring returning top-5 root-cause factors.

    In production this calls the KServe InferenceService. This implementation
    provides a deterministic approximation for unit testing.

    Returns (factor_list, model_confidence) where factor_list is sorted by rank.
    """
    if not cluster_events:
        return [], 0.0

    vins = list({e.get("vin", "") for e in cluster_events if e.get("vin")})
    n = len(cluster_events)

    avg_humidity = _avg(cluster_events, "boothHumidityPct")
    avg_temp = _avg(cluster_events, "boothTemperatureC")
    avg_voltage = _avg(cluster_events, "ecoatVoltageV")
    avg_phosphate = _avg(cluster_events, "phosphateConcentrationGL")
    stage_counts: dict[int, int] = defaultdict(int)
    for e in cluster_events:
        s = e.get("stageNumber")
        if s is not None:
            stage_counts[int(s)] += 1

    factors: list[tuple[str, str, float]] = []

    # Deterministic scoring rules per defect class
    if defect_class == "dust":
        factors = _score_dust(avg_humidity, avg_temp, stage_counts, n)
    elif defect_class == "orange_peel":
        factors = _score_orange_peel(avg_humidity, avg_temp, avg_voltage, n)
    elif defect_class == "run":
        factors = _score_run(avg_phosphate, stage_counts, n)
    elif defect_class == "crater":
        factors = _score_crater(avg_humidity, avg_temp, avg_voltage, n)
    elif defect_class == "pinhole":
        factors = _score_pinhole(avg_voltage, avg_phosphate, n)
    else:
        factors = [("unknown_factor", "booth_parameter", 0.5)]

    # Normalise and rank
    if not factors:
        factors = [("unclassified", "booth_parameter", 0.4)]

    factors.sort(key=lambda x: x[2], reverse=True)
    top5 = factors[:5]

    total_prob = sum(f[2] for f in top5)
    model_confidence = min(1.0, total_prob / max(1.0, len(top5)))

    result = []
    for rank, (name, category, prob) in enumerate(top5, start=1):
        supporting = max(1, int(math.ceil(len(vins) * prob)))
        result.append({
            "rank": rank,
            "factor_name": name,
            "factor_category": category,
            "probability_score": round(prob, 4),
            "supporting_vin_count": min(supporting, len(vins)),
        })

    return result, round(model_confidence, 4)


def _avg(events: list[dict[str, Any]], key: str) -> float:
    vals = [e[key] for e in events if key in e and e[key] is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _score_dust(humidity: float, temp: float, stage_counts: dict, n: int) -> list[tuple[str, str, float]]:
    factors = []
    if humidity > 65:
        factors.append(("humidity_elevated_booth", "booth_parameter", 0.72))
    if sum(stage_counts.get(s, 0) for s in (3, 4)) > n * 0.5:
        factors.append(("pre_paint_stage_3_4_contamination", "prep_stage", 0.65))
    if temp > 22:
        factors.append(("booth_temperature_high", "booth_parameter", 0.45))
    factors.append(("air_supply_desiccant_filter", "prep_stage", 0.38))
    factors.append(("climate_humidity_external", "climate_condition", 0.30))
    return factors


def _score_orange_peel(humidity: float, temp: float, voltage: float, n: int) -> list[tuple[str, str, float]]:
    factors = []
    if temp > 24:
        factors.append(("booth_temperature_delta_high", "booth_parameter", 0.78))
    if voltage < 270:
        factors.append(("ecoat_voltage_drop", "prep_stage", 0.70))
    factors.append(("spray_viscosity_out_of_range", "booth_parameter", 0.55))
    factors.append(("flash_time_too_short", "booth_parameter", 0.42))
    factors.append(("climate_temperature_peak", "climate_condition", 0.31))
    return factors


def _score_run(phosphate: float, stage_counts: dict, n: int) -> list[tuple[str, str, float]]:
    factors = []
    if phosphate < 1.8:
        factors.append(("phosphate_concentration_low", "prep_stage", 0.75))
    if sum(stage_counts.get(s, 0) for s in (1, 2)) > n * 0.4:
        factors.append(("pre_treatment_stage_1_2", "prep_stage", 0.68))
    factors.append(("dosing_pump_calibration_drift", "prep_stage", 0.52))
    factors.append(("application_speed_excessive", "booth_parameter", 0.44))
    factors.append(("climate_humidity_low", "climate_condition", 0.28))
    return factors


def _score_crater(humidity: float, temp: float, voltage: float, n: int) -> list[tuple[str, str, float]]:
    factors = []
    if humidity < 40:
        factors.append(("humidity_low_solvent_pop", "booth_parameter", 0.80))
    if temp > 23:
        factors.append(("temperature_high_solvent_evaporation", "booth_parameter", 0.71))
    if voltage > 330:
        factors.append(("ecoat_voltage_high", "prep_stage", 0.58))
    factors.append(("silicone_contamination", "prep_stage", 0.45))
    factors.append(("climate_dry_condition", "climate_condition", 0.33))
    return factors


def _score_pinhole(voltage: float, phosphate: float, n: int) -> list[tuple[str, str, float]]:
    factors = []
    if voltage > 340:
        factors.append(("ecoat_voltage_excessive", "prep_stage", 0.76))
    if phosphate > 2.5:
        factors.append(("phosphate_concentration_high", "prep_stage", 0.69))
    factors.append(("e_coat_bath_temperature_high", "prep_stage", 0.54))
    factors.append(("rinse_water_conductivity", "prep_stage", 0.41))
    factors.append(("climate_humidity_fluctuation", "climate_condition", 0.29))
    return factors
