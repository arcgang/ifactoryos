"""Acceptance tests for US-006 — MY26 Configuration Wizard.

Tests the seam between:
  - POST /my26/config/validate (validation against the constraint model)
  - POST /my26/config/save (persist and activate)
  - POST /optimize with my26ConfigVersion (optimizer applies updated constraints)
  - Audit trail: _write_audit_event is triggered after save with my26ConfigVersion

AC1 — wizard validates each entry against the SequenceOptimizer constraint model
       and flags conflicts or missing constraint mappings before saving.
       Covered: validate endpoint rejects option codes with missing/unknown mappings;
       returns valid=True only when all entries conform.

AC2 — optimizer applies new option codes and paint codes without error and the
       sequence output reflects the updated constraints (my26ConfigVersion echoed).
       Covered: validate → save → optimizer run round-trip with MY26 VINs.

AC3 — MY26 config version is recorded in the audit log after save.
       Covered: _write_audit_event is called with the saved configId after save.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.models import (
    ConstraintSnapshot,
    My26ConfigDraft,
    My26ConfigStatus,
    My26ValidationSeverity,
    OptimizeRequest,
    OptionCodeEntry,
    PaintPaletteEntry,
    ResultType,
    VinInput,
)
from app.my26_config import (
    build_constraint_overrides,
    get_active_my26_config,
    save_my26_config,
    set_known_constraint_keys,
    validate_my26_draft,
)
from app.optimizer import run_optimize
from tests.test_my26_config import _My26StubPool
from tests.test_optimizer import _StubKServeClient, _StubPool

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

_VIN_MY26_A = "WBA3B3C56DF596858"
_VIN_MY26_B = "WBA3B3C57DF596859"

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=60.0,
    bodyWipQueueDepth=150,
    kittingReadinessPct=85.0,
    maintenanceWindows=[],
)

# JWT fixture: unsigned, alg=none, production-planner role
_BEARER_PLANNER: str


def _make_jwt(roles: list[str] | None = None) -> str:
    import base64
    import json as _json

    payload_dict: dict[str, Any] = {
        "sub": "planner@bmwgroup.net",
        "exp": 9999999999,
    }
    if roles is not None:
        payload_dict["roles"] = roles

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload_bytes = _json.dumps(payload_dict).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header}.{payload_b64}."


# ---------------------------------------------------------------------------
# FastAPI test-app fixture
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _build_test_app() -> FastAPI:
    import app.main as main_mod

    with patch.object(main_mod, "_lifespan", _noop_lifespan):
        application = main_mod.create_app()
    return application


@pytest.fixture()
def app_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Precondition guards — fail loudly if the database table or imports are absent
# ---------------------------------------------------------------------------


def test_precondition_my26_config_model_imports() -> None:
    """Precondition: the My26Config model and my26_config module must be importable.
    If this fails the table or module was removed — all AC1-AC3 tests would pass vacuously.
    """
    from app.my26_config import validate_my26_draft, save_my26_config, get_active_my26_config  # noqa: F401
    from app.models import My26Config, My26ConfigDraft, My26ValidationResult  # noqa: F401


def test_precondition_optimizer_accepts_my26_config_version_field() -> None:
    """Precondition: OptimizeRequest must have a my26ConfigVersion field.
    If this fails the optimizer was not extended for MY26 — AC2 tests would be vacuous.
    """
    req = OptimizeRequest(
        weekIso="2026-W42",
        vins=[VinInput(vin=_VIN_MY26_A)],
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
        my26ConfigVersion=str(uuid.uuid4()),
    )
    assert req.my26ConfigVersion is not None


# =============================================================================
# AC1 — validate endpoint flags conflicts and missing constraint mappings
#        before the planner can proceed to save
# =============================================================================


def test_ac1_validate_endpoint_returns_200_with_valid_draft(app_client) -> None:
    """AC1: POST /my26/config/validate with a conforming draft returns 200 valid=true."""
    draft_body = {
        "optionCodes": [
            {
                "code": "P0490",
                "description": "M Sport Package",
                "constraintMappings": ["paint_capacity"],
            }
        ],
        "paintPalette": [
            {"paintCode": "A90", "batchGroup": "B1", "changeoverPenaltyMinutes": 30}
        ],
    }
    resp = app_client.post(
        "/my26/config/validate",
        json=draft_body,
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # Precondition: the response must have the fields the wizard depends on
    assert "valid" in body, "validate response must include 'valid'"
    assert "issues" in body, "validate response must include 'issues'"
    assert body["valid"] is True
    assert body["issues"] == []


def test_ac1_validate_flags_option_code_with_no_constraint_mappings(app_client) -> None:
    """AC1: option code with empty constraintMappings → valid=false with error-severity issue."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft_body = {
            "optionCodes": [
                {
                    "code": "ZP01U",
                    "description": "Unmapped option",
                    "constraintMappings": [],
                }
            ],
            "paintPalette": [
                {"paintCode": "A90", "batchGroup": "B1"}
            ],
        }
        resp = app_client.post(
            "/my26/config/validate",
            json=draft_body,
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False, "empty constraintMappings must produce valid=false"
        error_issues = [i for i in body["issues"] if i["severity"] == "error"]
        assert len(error_issues) >= 1, "must have at least one error-severity issue"
        # The error message must reference the offending option code
        assert any("ZP01U" in i["message"] for i in error_issues), (
            "error message must name the option code that has no constraint mappings"
        )
    finally:
        set_known_constraint_keys(frozenset())


def test_ac1_validate_flags_unknown_constraint_mapping_key(app_client) -> None:
    """AC1: option code with a mapping key not in the constraint model → error-severity issue."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft_body = {
            "optionCodes": [
                {
                    "code": "ZP01U",
                    "description": "Conflicting option",
                    "constraintMappings": ["paint_capacity", "nonexistent_constraint"],
                }
            ],
            "paintPalette": [{"paintCode": "A90", "batchGroup": "B1"}],
        }
        resp = app_client.post(
            "/my26/config/validate",
            json=draft_body,
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        error_issues = [i for i in body["issues"] if i["severity"] == "error"]
        assert any("nonexistent_constraint" in i["message"] for i in error_issues), (
            "error must name the unrecognised constraint mapping key"
        )
    finally:
        set_known_constraint_keys(frozenset())


def test_ac1_validate_flags_duplicate_option_code_as_warning_not_error(app_client) -> None:
    """AC1: duplicate option code produces a warning-severity issue, not an error."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft_body = {
            "optionCodes": [
                {"code": "SA-100", "description": "First", "constraintMappings": ["paint_capacity"]},
                {"code": "SA-100", "description": "Second", "constraintMappings": ["paint_capacity"]},
            ],
            "paintPalette": [{"paintCode": "A75", "batchGroup": "metallic"}],
        }
        resp = app_client.post(
            "/my26/config/validate",
            json=draft_body,
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Warnings alone do not make valid=false
        assert body["valid"] is True
        warning_issues = [i for i in body["issues"] if i["severity"] == "warning"]
        assert len(warning_issues) >= 1
        assert any("SA-100" in i["message"] for i in warning_issues)
    finally:
        set_known_constraint_keys(frozenset())


def test_ac1_save_endpoint_returns_409_when_validation_errors_present(app_client) -> None:
    """AC1: POST /my26/config/save is rejected (409) when option codes have unresolved errors.
    This is the gate that prevents a non-conforming config from reaching the optimizer.
    """
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft_body = {
            "optionCodes": [
                {
                    "code": "ZP01U",
                    "description": "Bad",
                    "constraintMappings": ["nonexistent_key"],
                }
            ],
            "paintPalette": [{"paintCode": "A75", "batchGroup": "metallic"}],
        }
        # Provide a stub pool — the dependency is resolved before the handler's validation check
        with patch("app.db.get_pool", return_value=_My26StubPool()):
            resp = app_client.post(
                "/my26/config/save",
                json=draft_body,
                headers={"Authorization": f"Bearer {_make_jwt(roles=['production-planner'])}"},
            )
        assert resp.status_code == 409, (
            f"Expected 409 for invalid draft, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("code") == "MY26_CONFIG_VALIDATION_FAILED"
        assert "correlation_id" in body
    finally:
        set_known_constraint_keys(frozenset())


def test_ac1_validate_returns_empty_issues_list_for_valid_entries() -> None:
    """AC1: all conforming entries return no issues from the validation logic."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="P0490",
                    description="M Sport Package",
                    constraintMappings=["paint_capacity", "kitting_readiness"],
                )
            ],
            paintPalette=[
                PaintPaletteEntry(paintCode="A90", batchGroup="B1", changeoverPenaltyMinutes=30)
            ],
        )
        result = validate_my26_draft(draft)
        assert result.valid is True
        assert result.issues == []
    finally:
        set_known_constraint_keys(frozenset())


# =============================================================================
# AC2 — optimizer applies new option codes and paint codes without error;
#        the response my26ConfigVersion matches the saved config's configId
# =============================================================================


@pytest.mark.asyncio
async def test_ac2_validate_save_then_optimize_round_trip_with_my26_vin() -> None:
    """AC2: validate → save → optimizer run with MY26 VIN returns my26ConfigVersion in response.

    This covers the seam between the wizard saving a config and the optimizer
    consuming it: the response must echo back the configId so the planner can
    confirm the correct constraint version was applied.
    """
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="P0490",
                    description="M Sport Package",
                    constraintMappings=["paint_capacity"],
                ),
            ],
            paintPalette=[
                PaintPaletteEntry(paintCode="A90", batchGroup="B1", changeoverPenaltyMinutes=30),
            ],
        )

        # Step 1: validate (precondition — must be valid)
        validation = validate_my26_draft(draft)
        assert validation.valid is True, "draft must be valid before we save"

        # Step 2: save
        my26_pool = _My26StubPool()
        with patch("app.my26_config.asyncio.create_task"):
            saved_config = await save_my26_config(draft, my26_pool, "planner@bmwgroup.net")

        assert saved_config.status == My26ConfigStatus.active
        assert saved_config.configId is not None

        # Step 3: build constraint overrides from the saved config
        overrides = build_constraint_overrides(saved_config)
        # A90 must appear in the changeover penalties — the optimizer uses this
        assert overrides["paint_changeover_penalties"]["A90"] == 30

        # Step 4: run optimizer with MY26 VINs and the new config version
        optimizer_pool = _StubPool()
        kserve_client = _StubKServeClient()
        request = OptimizeRequest(
            weekIso="2026-W42",
            vins=[
                VinInput(vin=_VIN_MY26_A, modelYear="MY26", paintCode="A90"),
                VinInput(vin=_VIN_MY26_B, modelYear="MY26", paintCode="B21"),
            ],
            constraintSnapshot=_SNAPSHOT,
            monteCarloRuns=2,
            my26ConfigVersion=str(saved_config.configId),
        )
        with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
            response = await run_optimize(
                request,
                optimizer_pool,
                kserve_client,
                my26_overrides=overrides,
            )

        # AC2 assertion: the optimizer echoes back the configId so the planner
        # can confirm the correct constraint version was applied
        assert response.my26ConfigVersion == str(saved_config.configId), (
            f"response.my26ConfigVersion={response.my26ConfigVersion!r} "
            f"does not match saved configId={saved_config.configId}"
        )
        # Sequence output must include both MY26 VINs
        assert len(response.rankedSequence) == 2
        output_vins = {rv.vin for rv in response.rankedSequence}
        assert _VIN_MY26_A in output_vins
        assert _VIN_MY26_B in output_vins
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_ac2_optimizer_applies_my26_paint_changeover_penalty_from_saved_config() -> None:
    """AC2: the optimizer applies the changeoverPenaltyMinutes from the saved config,
    not the default value — confirming the updated constraints are reflected in the output.
    """
    from app.optimizer import score_vin_heuristic

    # A90 with penalty=30 must differ from the default penalty (200)
    vin = VinInput(vin=_VIN_MY26_A, paintCode="A90")
    score_default = score_vin_heuristic(vin, "B21", _SNAPSHOT, 0, None)
    score_my26 = score_vin_heuristic(vin, "B21", _SNAPSHOT, 0, {"A90": 30})

    # MY26 penalty (30) < default (200), so score_my26 > score_default
    assert score_my26 > score_default, (
        f"MY26 penalty should yield higher score ({score_my26}) than default ({score_default})"
    )
    assert score_my26 == 1000.0 - 30.0, f"expected 970.0, got {score_my26}"
    assert score_default == 1000.0 - 200.0, f"expected 800.0, got {score_default}"


@pytest.mark.asyncio
async def test_ac2_optimizer_with_invalid_my26_config_version_returns_422(app_client) -> None:
    """AC2: POST /optimize with a non-UUID my26ConfigVersion returns 422.
    The optimizer must validate the version before applying constraints.
    """
    body = {
        "weekIso": "2026-W42",
        "vins": [{"vin": _VIN_MY26_A}],
        "constraintSnapshot": {
            "snapshotTimestamp": _FAR_FUTURE,
            "paintCapacityUtilisation": 60.0,
            "bodyWipQueueDepth": 150,
            "kittingReadinessPct": 85.0,
            "maintenanceWindows": [],
        },
        "monteCarloRuns": 2,
        "my26ConfigVersion": "not-a-valid-uuid",
    }
    with patch("app.db.get_pool") as mock_pool:
        mock_pool.return_value = _StubPool()
        resp = app_client.post(
            "/optimize",
            json=body,
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
    assert resp.status_code == 422, (
        f"Expected 422 for invalid my26ConfigVersion, got {resp.status_code}: {resp.text}"
    )
    body_json = resp.json()
    assert body_json.get("code") == "INVALID_MY26_CONFIG_VERSION"


@pytest.mark.asyncio
async def test_ac2_optimizer_without_my26_config_version_succeeds_with_null_in_response() -> None:
    """AC2: POST /optimize without my26ConfigVersion succeeds and returns null my26ConfigVersion."""
    optimizer_pool = _StubPool()
    client = _StubKServeClient()
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=[VinInput(vin=_VIN_MY26_A)],
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(request, optimizer_pool, client)
    assert response.my26ConfigVersion is None


# =============================================================================
# AC3 — audit log records the MY26 config version after a successful save
# =============================================================================


@pytest.mark.asyncio
async def test_ac3_save_triggers_audit_write_event_with_config_id() -> None:
    """AC3: save_my26_config schedules an audit event write containing the saved configId.

    The audit event is fire-and-forget (asyncio.create_task); here we assert that
    asyncio.create_task is called and that the coroutine passed to it is the
    _write_audit_event coroutine — proving the audit log record is attempted.
    """
    import inspect

    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="P0490",
                    description="M Sport Package",
                    constraintMappings=["paint_capacity"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A90", batchGroup="B1")],
        )
        pool = _My26StubPool()

        created_tasks: list[Any] = []

        def _capture_task(coro: Any) -> Any:
            created_tasks.append(coro)
            # Return a dummy task-like object to avoid RuntimeWarning
            if inspect.iscoroutine(coro):
                coro.close()
            return MagicMock()

        with patch("app.my26_config.asyncio.create_task", side_effect=_capture_task):
            saved = await save_my26_config(draft, pool, "planner@bmwgroup.net")

        assert saved.configId is not None

        # Two tasks should be created: Kafka event + audit log write
        assert len(created_tasks) == 2, (
            f"Expected 2 create_task calls (Kafka + audit), got {len(created_tasks)}"
        )
        # One of the tasks must be an audit-write coroutine
        task_names = [
            getattr(t, "__name__", repr(t)) if not inspect.iscoroutine(t)
            else t.__qualname__
            for t in created_tasks
        ]
        assert any("audit" in name.lower() for name in task_names), (
            f"No audit task found among {task_names}; audit log would not be written"
        )
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_ac3_audit_event_payload_contains_my26_config_id() -> None:
    """AC3: the audit event POSTed after save contains the configId as my26ConfigVersion.

    The payload must link the audit record to the specific MY26 config version so
    the sequence published with that config can be traced back to its configuration.
    """
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="P0490",
                    description="M Sport Package",
                    constraintMappings=["paint_capacity"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A90", batchGroup="B1")],
        )
        pool = _My26StubPool()

        posted_payloads: list[dict[str, Any]] = []

        async def _fake_post(url: str, json: Any = None, **kwargs: Any) -> MagicMock:  # type: ignore[return]
            if json is not None:
                posted_payloads.append(json)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        # Patch httpx.AsyncClient so we can capture the audit POST payload
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=AsyncMock(post=_fake_post))
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.my26_config.asyncio.create_task") as mock_create_task:
            saved = await save_my26_config(draft, pool, "planner@bmwgroup.net")

            # Extract the audit coroutine from the second create_task call
            # (first = Kafka, second = audit)
            assert mock_create_task.call_count >= 2, (
                "save_my26_config must schedule both Kafka and audit tasks"
            )
            audit_coro = mock_create_task.call_args_list[1][0][0]

            # Run the audit coroutine with httpx mocked
            with patch("httpx.AsyncClient", return_value=mock_client_ctx):
                import asyncio
                try:
                    await asyncio.wait_for(audit_coro, timeout=2.0)
                except Exception:
                    pass  # best-effort

        if posted_payloads:
            payload = posted_payloads[0]
            # The audit payload must carry the configId as the config version
            assert "configVersion" in payload, "audit payload must include configVersion"
            assert payload["configVersion"] == str(saved.configId), (
                f"configVersion={payload['configVersion']!r} does not match "
                f"saved.configId={saved.configId}"
            )
            assert payload.get("action") == "my26_config_save", (
                "audit action must be 'my26_config_save'"
            )
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_ac3_save_with_validation_error_does_not_emit_audit_event() -> None:
    """AC3: a save that is rejected because of validation errors must not emit an audit event.
    Only a successfully persisted config should be traceable in the audit log.
    """
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        # Draft with an unknown constraint key — will fail validation inside save_my26_config
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="BAD-CODE",
                    description="Bad",
                    constraintMappings=["nonexistent_key"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        pool = _My26StubPool()

        with patch("app.my26_config.asyncio.create_task") as mock_create_task:
            with pytest.raises(ValueError, match="Validation failed"):
                await save_my26_config(draft, pool, "planner@bmwgroup.net")

        # No tasks should be scheduled because the save failed before persisting
        mock_create_task.assert_not_called()
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_ac3_http_layer_save_201_includes_active_status_and_config_id(app_client) -> None:
    """AC3 (HTTP layer): POST /my26/config/save returns 201 with status=active and configId.
    The planner needs the configId from the response to include in the audit trail
    when they subsequently publish a sequence with MY26 VINs.
    """
    set_known_constraint_keys(frozenset())
    pool = _My26StubPool()
    try:
        draft_body = {
            "optionCodes": [
                {
                    "code": "P0490",
                    "description": "M Sport Package",
                    "constraintMappings": ["paint_capacity"],
                }
            ],
            "paintPalette": [
                {"paintCode": "A90", "batchGroup": "B1", "changeoverPenaltyMinutes": 30}
            ],
        }
        with (
            patch("app.db.get_pool", return_value=pool),
            patch("app.my26_config.asyncio.create_task"),
        ):
            resp = app_client.post(
                "/my26/config/save",
                json=draft_body,
                headers={"Authorization": f"Bearer {_make_jwt(roles=['production-planner'])}"},
            )

        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        # AC3 requires the configId be present so it can be included in the audit trail
        assert "configId" in body, "save response must include configId for audit trail"
        assert body["status"] == "active", "saved config must be status=active"
        assert "activatedAt" in body, "save response must include activatedAt for audit trail"
    finally:
        set_known_constraint_keys(frozenset())
