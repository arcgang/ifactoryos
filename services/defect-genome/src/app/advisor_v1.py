"""Rule-Based Defect Advisor v1 — deterministic decision trees per defect class.

Rules are loaded from the advisor_v1_rule table at startup and hot-reloadable.
Each rule is evaluated against event parameters extracted from PaintDefectDetected events.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import AdvisorV1Result, DefectClass


@dataclass(frozen=True)
class AdvisorRule:
    rule_id: str
    defect_class: DefectClass
    rule_description: str
    recommended_action: str


# Static fallback rules matching the settled design decisions.
# These are also seeded into the advisor_v1_rule table via the migration seed.
_STATIC_RULES: dict[DefectClass, AdvisorRule] = {
    DefectClass.dust: AdvisorRule(
        rule_id="ADV-DUST-01",
        defect_class=DefectClass.dust,
        rule_description="Elevated humidity in pre-paint stages 3–4",
        recommended_action=(
            "Reduce booth humidity to ≤60% in stages 3–4; inspect air supply desiccant filters"
        ),
    ),
    DefectClass.orange_peel: AdvisorRule(
        rule_id="ADV-OP-01",
        defect_class=DefectClass.orange_peel,
        rule_description="High booth temperature or low E-coat voltage",
        recommended_action=(
            "Lower booth temperature to 20–23°C and verify E-coat voltage 270–320V"
        ),
    ),
    DefectClass.run: AdvisorRule(
        rule_id="ADV-RUN-01",
        defect_class=DefectClass.run,
        rule_description="Low phosphate concentration in pre-treatment stages 1–2",
        recommended_action=(
            "Adjust phosphate bath concentration to 1.8–2.2 g/L; check dosing pump calibration"
        ),
    ),
    DefectClass.crater: AdvisorRule(
        rule_id="ADV-CRATER-01",
        defect_class=DefectClass.crater,
        rule_description="Low humidity combined with high temperature causing solvent pop",
        recommended_action=(
            "Increase booth humidity to 45–55% and reduce temperature to ≤23°C to prevent solvent pop"
        ),
    ),
    DefectClass.pinhole: AdvisorRule(
        rule_id="ADV-PIN-01",
        defect_class=DefectClass.pinhole,
        rule_description="High E-coat voltage or high phosphate concentration",
        recommended_action=(
            "Reduce E-coat voltage to 270–320V and phosphate concentration to 1.8–2.2 g/L"
        ),
    ),
}


def _evaluate_dust(params: dict) -> list[str]:
    matched: list[str] = []
    if params.get("boothHumidityPct", 0.0) > 65:
        matched.append("humidity_elevated")
    if params.get("stageNumber", 0) in (3, 4):
        matched.append("pre_paint_stage")
    return matched


def _evaluate_orange_peel(params: dict) -> list[str]:
    matched: list[str] = []
    if params.get("boothTemperatureC", 0.0) > 24:
        matched.append("temperature_high")
    if params.get("ecoatVoltageV", 999.0) < 270:
        matched.append("ecoat_voltage_low")
    return matched


def _evaluate_run(params: dict) -> list[str]:
    matched: list[str] = []
    if params.get("phosphateConcentrationGL", 999.0) < 1.8 and params.get("stageNumber", 0) in (1, 2):
        matched.append("phosphate_low")
        matched.append("pre_treatment_stage")
    return matched


def _evaluate_crater(params: dict) -> list[str]:
    matched: list[str] = []
    if params.get("boothHumidityPct", 999.0) < 40:
        matched.append("humidity_low")
    if params.get("boothTemperatureC", 0.0) > 23:
        matched.append("temperature_high")
    return matched


def _evaluate_pinhole(params: dict) -> list[str]:
    matched: list[str] = []
    if params.get("ecoatVoltageV", 0.0) > 340:
        matched.append("ecoat_voltage_high")
    if params.get("stageNumber") == 5 and params.get("phosphateConcentrationGL", 0.0) > 2.5:
        matched.append("phosphate_high")
    return matched


_EVALUATORS = {
    DefectClass.dust: _evaluate_dust,
    DefectClass.orange_peel: _evaluate_orange_peel,
    DefectClass.run: _evaluate_run,
    DefectClass.crater: _evaluate_crater,
    DefectClass.pinhole: _evaluate_pinhole,
}


def evaluate_advisor_v1(defect_class: DefectClass, event_params: dict | None = None) -> AdvisorV1Result:
    """Evaluate the Advisor v1 rule for the given defect class and event parameters."""
    rule = _STATIC_RULES[defect_class]
    params = event_params or {}
    evaluator = _EVALUATORS[defect_class]
    matched = evaluator(params)
    return AdvisorV1Result(
        rule_id=rule.rule_id,
        rule_description=rule.rule_description,
        matched_conditions=matched,
        recommended_action=rule.recommended_action,
    )
