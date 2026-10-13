"""Unit tests for Rule-Based Defect Advisor v1 decision trees."""

from __future__ import annotations

import pytest

from app.advisor_v1 import evaluate_advisor_v1
from app.models import DefectClass


# ---------------------------------------------------------------------------
# dust
# ---------------------------------------------------------------------------


def test_dust_rule_id_is_ADV_DUST_01() -> None:
    result = evaluate_advisor_v1(DefectClass.dust)
    assert result.rule_id == "ADV-DUST-01"


def test_dust_matches_elevated_humidity_at_stage_3() -> None:
    result = evaluate_advisor_v1(DefectClass.dust, {"boothHumidityPct": 70.0, "stageNumber": 3})
    assert "humidity_elevated" in result.matched_conditions
    assert "pre_paint_stage" in result.matched_conditions


def test_dust_matches_elevated_humidity_at_stage_4() -> None:
    result = evaluate_advisor_v1(DefectClass.dust, {"boothHumidityPct": 66.0, "stageNumber": 4})
    assert "humidity_elevated" in result.matched_conditions
    assert "pre_paint_stage" in result.matched_conditions


def test_dust_no_match_below_threshold() -> None:
    result = evaluate_advisor_v1(DefectClass.dust, {"boothHumidityPct": 60.0, "stageNumber": 5})
    assert result.matched_conditions == []


def test_dust_recommended_action_mentions_humidity() -> None:
    result = evaluate_advisor_v1(DefectClass.dust)
    assert "humidity" in result.recommended_action.lower()


# ---------------------------------------------------------------------------
# orange_peel
# ---------------------------------------------------------------------------


def test_orange_peel_rule_id_is_ADV_OP_01() -> None:
    result = evaluate_advisor_v1(DefectClass.orange_peel)
    assert result.rule_id == "ADV-OP-01"


def test_orange_peel_matches_high_temperature() -> None:
    result = evaluate_advisor_v1(DefectClass.orange_peel, {"boothTemperatureC": 25.0, "ecoatVoltageV": 300.0})
    assert "temperature_high" in result.matched_conditions


def test_orange_peel_matches_low_ecoat_voltage() -> None:
    result = evaluate_advisor_v1(DefectClass.orange_peel, {"boothTemperatureC": 22.0, "ecoatVoltageV": 260.0})
    assert "ecoat_voltage_low" in result.matched_conditions


def test_orange_peel_no_match_at_nominal() -> None:
    result = evaluate_advisor_v1(DefectClass.orange_peel, {"boothTemperatureC": 22.0, "ecoatVoltageV": 300.0})
    assert result.matched_conditions == []


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_rule_id_is_ADV_RUN_01() -> None:
    result = evaluate_advisor_v1(DefectClass.run)
    assert result.rule_id == "ADV-RUN-01"


def test_run_matches_low_phosphate_at_stage_1() -> None:
    result = evaluate_advisor_v1(DefectClass.run, {"phosphateConcentrationGL": 1.5, "stageNumber": 1})
    assert "phosphate_low" in result.matched_conditions
    assert "pre_treatment_stage" in result.matched_conditions


def test_run_matches_low_phosphate_at_stage_2() -> None:
    result = evaluate_advisor_v1(DefectClass.run, {"phosphateConcentrationGL": 1.7, "stageNumber": 2})
    assert "phosphate_low" in result.matched_conditions


def test_run_no_match_phosphate_ok() -> None:
    result = evaluate_advisor_v1(DefectClass.run, {"phosphateConcentrationGL": 2.0, "stageNumber": 1})
    assert result.matched_conditions == []


def test_run_no_match_at_stage_5() -> None:
    # Condition requires stage in (1, 2)
    result = evaluate_advisor_v1(DefectClass.run, {"phosphateConcentrationGL": 1.5, "stageNumber": 5})
    assert result.matched_conditions == []


# ---------------------------------------------------------------------------
# crater
# ---------------------------------------------------------------------------


def test_crater_rule_id_is_ADV_CRATER_01() -> None:
    result = evaluate_advisor_v1(DefectClass.crater)
    assert result.rule_id == "ADV-CRATER-01"


def test_crater_matches_low_humidity_high_temperature() -> None:
    result = evaluate_advisor_v1(DefectClass.crater, {"boothHumidityPct": 35.0, "boothTemperatureC": 25.0})
    assert "humidity_low" in result.matched_conditions
    assert "temperature_high" in result.matched_conditions


def test_crater_matches_only_low_humidity() -> None:
    result = evaluate_advisor_v1(DefectClass.crater, {"boothHumidityPct": 35.0, "boothTemperatureC": 22.0})
    assert "humidity_low" in result.matched_conditions
    assert "temperature_high" not in result.matched_conditions


def test_crater_recommended_action_mentions_humidity() -> None:
    result = evaluate_advisor_v1(DefectClass.crater)
    assert "humidity" in result.recommended_action.lower()


# ---------------------------------------------------------------------------
# pinhole
# ---------------------------------------------------------------------------


def test_pinhole_rule_id_is_ADV_PIN_01() -> None:
    result = evaluate_advisor_v1(DefectClass.pinhole)
    assert result.rule_id == "ADV-PIN-01"


def test_pinhole_matches_high_ecoat_voltage() -> None:
    result = evaluate_advisor_v1(DefectClass.pinhole, {"ecoatVoltageV": 345.0, "phosphateConcentrationGL": 2.0})
    assert "ecoat_voltage_high" in result.matched_conditions


def test_pinhole_matches_high_phosphate_at_stage_5() -> None:
    result = evaluate_advisor_v1(DefectClass.pinhole, {"ecoatVoltageV": 300.0, "stageNumber": 5, "phosphateConcentrationGL": 2.6})
    assert "phosphate_high" in result.matched_conditions


def test_pinhole_no_match_at_nominal() -> None:
    result = evaluate_advisor_v1(DefectClass.pinhole, {"ecoatVoltageV": 300.0, "stageNumber": 3, "phosphateConcentrationGL": 2.0})
    assert result.matched_conditions == []


# ---------------------------------------------------------------------------
# All five defect classes return required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("defect_class", list(DefectClass))
def test_all_defect_classes_return_required_fields(defect_class: DefectClass) -> None:
    result = evaluate_advisor_v1(defect_class)
    assert result.rule_id
    assert result.rule_description
    assert result.recommended_action
    assert isinstance(result.matched_conditions, list)
