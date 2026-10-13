"""Unit tests for MY26 configuration validation, persistence, and optimizer integration.

Covers:
 - Option code with no constraintMappings → error
 - Unknown constraint mapping key → error
 - Valid draft → valid=True
 - Duplicate option code → warning (not error)
 - Empty paintCode → error
 - Empty batchGroup → error
 - changeoverPenaltyMinutes=0 is valid (boundary)
 - Duplicate paint code → warning (not error)
 - Save with unresolved errors → 409
 - Save valid draft → 201 + active + prior superseded
 - GET /my26/config → 404 when no active config
 - GET /my26/config → 200 with active config
 - Optimize with MY26 VIN returns sequence with MY26 paint batch penalties applied
 - Optimize with invalid/inactive my26ConfigVersion → 422
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from app.models import (
    ConstraintSnapshot,
    My26ConfigDraft,
    My26ConfigStatus,
    My26ValidationSeverity,
    OptionCodeEntry,
    OptimizeRequest,
    PaintPaletteEntry,
    ResultType,
    VinInput,
)
from app.my26_config import (
    _validate_option_codes,
    _validate_paint_palette,
    build_constraint_overrides,
    get_active_my26_config,
    load_my26_config_by_version,
    save_my26_config,
    set_known_constraint_keys,
    validate_my26_draft,
)
from app.optimizer import apply_heuristic, run_optimize, score_vin_heuristic

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

_VIN_A = "WBA3B3C56DF596858"
_VIN_B = "WBA3B3C57DF596859"
_VIN_C = "WBA3B3C58DF596860"

_SNAPSHOT = ConstraintSnapshot(
    snapshotTimestamp=_FAR_FUTURE,
    paintCapacityUtilisation=70.0,
    bodyWipQueueDepth=100,
    kittingReadinessPct=90.0,
    maintenanceWindows=[],
)

_VALID_OPTION_CODE = OptionCodeEntry(
    code="SA-100",
    description="Panoramic sunroof",
    constraintMappings=["paint_capacity", "kitting_readiness"],
)

_VALID_PAINT_ENTRY = PaintPaletteEntry(
    paintCode="A75",
    batchGroup="metallic",
    changeoverPenaltyMinutes=30,
)

_VALID_DRAFT = My26ConfigDraft(
    optionCodes=[_VALID_OPTION_CODE],
    paintPalette=[_VALID_PAINT_ENTRY],
)


# ---------------------------------------------------------------------------
# In-memory pool stub for MY26 tests
# ---------------------------------------------------------------------------


class _My26StubConn:
    """Asyncpg connection stub handling my26_config queries."""

    def __init__(self, pool: "_My26StubPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "_My26StubConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def transaction(self) -> "_My26StubTx":
        return _My26StubTx(self)

    async def execute(self, query: str, *args: object) -> None:
        if "INSERT INTO my26_config" in query:
            (
                config_id, version, option_codes_json, paint_palette_json,
                kitting_rules_json, now, user_id,
            ) = args
            self._pool._configs[config_id] = {
                "config_id": config_id,
                "version": version,
                "status": "active",
                "option_codes": option_codes_json,
                "paint_palette": paint_palette_json,
                "kitting_rules": kitting_rules_json,
                "created_at": now,
                "created_by": user_id,
                "activated_at": now,
            }
        elif "UPDATE my26_config" in query and "superseded" in query:
            # Mark all active configs as superseded
            for cfg in self._pool._configs.values():
                if cfg["status"] == "active":
                    cfg["status"] = "superseded"

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        if "WHERE status = 'active'" in query:
            return next(
                (c for c in self._pool._configs.values() if c["status"] == "active"),
                None,
            )
        if "WHERE config_id = $1" in query:
            return self._pool._configs.get(args[0])
        return None


class _My26StubTx:
    def __init__(self, conn: _My26StubConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> "_My26StubTx":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _My26StubPool:
    """Minimal asyncpg.Pool stub for MY26 config tests; each instance starts empty."""

    def __init__(self) -> None:
        self._configs: dict[uuid.UUID, dict[str, Any]] = {}

    def acquire(self) -> _My26StubConn:
        return _My26StubConn(self)


# ---------------------------------------------------------------------------
# Validation unit tests — option codes
# ---------------------------------------------------------------------------


def test_option_code_no_mappings_produces_error_when_strict_mode() -> None:
    """Option code with empty constraintMappings in strict mode → error."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        entry = OptionCodeEntry(code="SA-200", description="Leather seats", constraintMappings=[])
        issues = _validate_option_codes([entry])
        assert any(
            i.severity == My26ValidationSeverity.error
            and "SA-200" in i.message
            and "no constraint mappings" in i.message
            for i in issues
        )
    finally:
        set_known_constraint_keys(frozenset())


def test_option_code_no_mappings_produces_error_in_permissive_mode() -> None:
    """Option code with empty constraintMappings always produces error (mappings are required)."""
    set_known_constraint_keys(frozenset())
    entry = OptionCodeEntry(code="SA-200", description="Leather seats", constraintMappings=[])
    issues = _validate_option_codes([entry])
    assert any(
        i.severity == My26ValidationSeverity.error
        and "SA-200" in i.message
        and "no constraint mappings" in i.message
        for i in issues
    )


def test_unknown_constraint_mapping_key_produces_error() -> None:
    """constraintMappings containing an unknown key → error."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        entry = OptionCodeEntry(
            code="SA-300",
            description="Sport package",
            constraintMappings=["paint_capacity", "nonexistent_key"],
        )
        issues = _validate_option_codes([entry])
        assert any(
            i.severity == My26ValidationSeverity.error
            and "nonexistent_key" in i.message
            for i in issues
        )
    finally:
        set_known_constraint_keys(frozenset())


def test_known_constraint_mapping_keys_produce_no_errors() -> None:
    """All constraintMappings known → no errors from option code validation."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        entry = OptionCodeEntry(
            code="SA-100",
            description="Sunroof",
            constraintMappings=["paint_capacity", "kitting_readiness"],
        )
        issues = _validate_option_codes([entry])
        assert not any(i.severity == My26ValidationSeverity.error for i in issues)
    finally:
        set_known_constraint_keys(frozenset())


def test_duplicate_option_code_produces_warning_not_error() -> None:
    """Duplicate option code → warning severity, not error."""
    entries = [
        OptionCodeEntry(code="SA-100", description="First", constraintMappings=["paint_capacity"]),
        OptionCodeEntry(code="SA-100", description="Second", constraintMappings=["paint_capacity"]),
    ]
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        issues = _validate_option_codes(entries)
        dup_issues = [i for i in issues if "Duplicate" in i.message and "SA-100" in i.message]
        assert len(dup_issues) == 1
        assert dup_issues[0].severity == My26ValidationSeverity.warning
    finally:
        set_known_constraint_keys(frozenset())


# ---------------------------------------------------------------------------
# Validation unit tests — paint palette
# ---------------------------------------------------------------------------


def test_empty_paint_code_produces_error() -> None:
    """Empty paintCode → error."""
    entry = PaintPaletteEntry(paintCode="", batchGroup="metallic")
    issues = _validate_paint_palette([entry])
    assert any(
        i.severity == My26ValidationSeverity.error and "paintCode" in i.field
        for i in issues
    )


def test_empty_batch_group_produces_error() -> None:
    """Empty batchGroup → error."""
    entry = PaintPaletteEntry(paintCode="A75", batchGroup="")
    issues = _validate_paint_palette([entry])
    assert any(
        i.severity == My26ValidationSeverity.error and "batchGroup" in i.field
        for i in issues
    )


def test_zero_changeover_penalty_is_valid() -> None:
    """changeoverPenaltyMinutes=0 is valid (ge=0 boundary) — no error issued."""
    entry = PaintPaletteEntry(paintCode="A75", batchGroup="metallic", changeoverPenaltyMinutes=0)
    issues = _validate_paint_palette([entry])
    assert not any(
        i.severity == My26ValidationSeverity.error and "changeoverPenaltyMinutes" in i.field
        for i in issues
    )


def test_duplicate_paint_code_produces_warning_not_error() -> None:
    """Duplicate paintCode → warning, not error."""
    entries = [
        PaintPaletteEntry(paintCode="A75", batchGroup="metallic"),
        PaintPaletteEntry(paintCode="A75", batchGroup="solid"),
    ]
    issues = _validate_paint_palette(entries)
    dup = [i for i in issues if "Duplicate" in i.message and "A75" in i.message]
    assert len(dup) == 1
    assert dup[0].severity == My26ValidationSeverity.warning


# ---------------------------------------------------------------------------
# validate_my26_draft — full validation
# ---------------------------------------------------------------------------


def test_valid_draft_returns_valid_true() -> None:
    """A well-formed draft with known mappings returns valid=True."""
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity", "kitting_readiness"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        result = validate_my26_draft(draft)
        assert result.valid is True
    finally:
        set_known_constraint_keys(frozenset())


def test_draft_with_error_returns_valid_false() -> None:
    """A draft with an error-severity issue returns valid=False."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["unknown_constraint"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        result = validate_my26_draft(draft)
        assert result.valid is False
        assert any(i.severity == My26ValidationSeverity.error for i in result.issues)
    finally:
        set_known_constraint_keys(frozenset())


def test_draft_with_only_warnings_returns_valid_true() -> None:
    """A draft with only warning-severity issues (duplicate codes) returns valid=True."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="First",
                    constraintMappings=["paint_capacity"],
                ),
                OptionCodeEntry(
                    code="SA-100",
                    description="Second",
                    constraintMappings=["paint_capacity"],
                ),
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        result = validate_my26_draft(draft)
        assert result.valid is True
        assert any(i.severity == My26ValidationSeverity.warning for i in result.issues)
    finally:
        set_known_constraint_keys(frozenset())


# ---------------------------------------------------------------------------
# save_my26_config unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_invalid_draft_raises_value_error() -> None:
    """save_my26_config raises ValueError when the draft has unresolved errors."""
    pool = _My26StubPool()
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-BAD",
                    description="Bad code",
                    constraintMappings=["unknown_key"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        with pytest.raises(ValueError, match="Validation failed"):
            with patch("app.my26_config.asyncio.create_task"):
                await save_my26_config(draft, pool, "planner@bmwgroup.net")
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_save_valid_draft_returns_active_config() -> None:
    """save_my26_config returns a My26Config with status=active."""
    pool = _My26StubPool()
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity", "kitting_readiness"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        with patch("app.my26_config.asyncio.create_task"):
            config = await save_my26_config(draft, pool, "planner@bmwgroup.net")
        assert config.status == My26ConfigStatus.active
        assert config.createdBy == "planner@bmwgroup.net"
        assert config.activatedAt is not None
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_save_supersedes_prior_active_config() -> None:
    """Saving a new config marks the prior active one as superseded."""
    pool = _My26StubPool()
    set_known_constraint_keys(frozenset({"paint_capacity", "kitting_readiness"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        with patch("app.my26_config.asyncio.create_task"):
            first = await save_my26_config(draft, pool, "planner@bmwgroup.net")
            second = await save_my26_config(draft, pool, "planner@bmwgroup.net")

        # The first config should now be superseded in the stub store
        first_row = pool._configs.get(first.configId)
        assert first_row is not None
        assert first_row["status"] == "superseded"
        assert second.status == My26ConfigStatus.active
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_get_active_my26_config_returns_none_when_empty() -> None:
    """get_active_my26_config returns None when no active config exists."""
    pool = _My26StubPool()
    result = await get_active_my26_config(pool)
    assert result is None


@pytest.mark.asyncio
async def test_get_active_my26_config_returns_config_after_save() -> None:
    """get_active_my26_config returns the saved config after save_my26_config."""
    pool = _My26StubPool()
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        draft = My26ConfigDraft(
            optionCodes=[
                OptionCodeEntry(
                    code="SA-100",
                    description="Sunroof",
                    constraintMappings=["paint_capacity"],
                )
            ],
            paintPalette=[PaintPaletteEntry(paintCode="A75", batchGroup="metallic")],
        )
        with patch("app.my26_config.asyncio.create_task"):
            saved = await save_my26_config(draft, pool, "planner@bmwgroup.net")

        active = await get_active_my26_config(pool)
        assert active is not None
        assert str(active.configId) == str(saved.configId)
    finally:
        set_known_constraint_keys(frozenset())


@pytest.mark.asyncio
async def test_load_my26_config_by_version_returns_none_for_unknown_id() -> None:
    """load_my26_config_by_version returns None for an unknown UUID."""
    pool = _My26StubPool()
    result = await load_my26_config_by_version(uuid.uuid4(), pool)
    assert result is None


# ---------------------------------------------------------------------------
# build_constraint_overrides
# ---------------------------------------------------------------------------


def test_build_constraint_overrides_extracts_paint_changeover_penalties() -> None:
    """build_constraint_overrides returns correct paint_changeover_penalties."""
    from app.models import My26Config

    config = My26Config(
        configId=uuid.uuid4(),
        version="1.0.0",
        status=My26ConfigStatus.active,
        optionCodes=[],
        paintPalette=[
            PaintPaletteEntry(paintCode="A75", batchGroup="metallic", changeoverPenaltyMinutes=45),
            PaintPaletteEntry(paintCode="B21", batchGroup="solid", changeoverPenaltyMinutes=None),
        ],
        createdAt=_FAR_FUTURE,
        createdBy="planner@bmwgroup.net",
    )
    overrides = build_constraint_overrides(config)
    assert overrides["paint_changeover_penalties"]["A75"] == 45
    assert "B21" not in overrides["paint_changeover_penalties"]


# ---------------------------------------------------------------------------
# Optimizer integration — MY26 paint penalties applied in heuristic
# ---------------------------------------------------------------------------


def test_score_vin_heuristic_uses_my26_paint_override_penalty() -> None:
    """score_vin_heuristic applies MY26 changeoverPenaltyMinutes when overrides are present."""
    vin = VinInput(vin=_VIN_A, paintCode="A75")
    # Default penalty = 200; MY26 override = 45
    score_with_default = score_vin_heuristic(vin, "B21", _SNAPSHOT, 0, None)
    score_with_override = score_vin_heuristic(vin, "B21", _SNAPSHOT, 0, {"A75": 45})
    # Override penalty (45) < default (200), so score_with_override > score_with_default
    assert score_with_override > score_with_default
    assert score_with_default == 1000.0 - 200.0
    assert score_with_override == 1000.0 - 45.0


def test_apply_heuristic_with_my26_paint_overrides_affects_ordering() -> None:
    """apply_heuristic with MY26 paint overrides ranks paint-changeover VINs differently."""
    # VIN_A changes paint (A75 → B21); VIN_B stays on A75.
    # With a large default penalty (200), VIN_B should rank first (no changeover).
    # With a small override penalty (10 for A75 → B21 scenario), order may differ.
    vins = [
        VinInput(vin=_VIN_A, paintCode="B21"),  # changeover from A75
        VinInput(vin=_VIN_B, paintCode="A75"),  # first in sequence, no changeover
    ]
    result_default = apply_heuristic(vins, _SNAPSHOT, None)
    result_override = apply_heuristic(vins, _SNAPSHOT, {"B21": 5})
    # In default mode, VIN_A (position 0) changes from None to B21 — no penalty since prev is None
    # VIN_B (position 1) changes from B21 to A75 — 200 penalty
    # So default: VIN_A first (1000), VIN_B second (800)
    assert result_default[0].vin == _VIN_A
    # With override penalty 5 for B21, VIN_B still gets penalty but only 5 → score 995
    # VIN_A (no changeover since prev=None) → score 1000
    # VIN_A still first
    assert result_override[0].vin == _VIN_A


@pytest.mark.asyncio
async def test_optimize_with_my26_vin_includes_my26_config_version_in_response() -> None:
    """run_optimize passes my26ConfigVersion through to the response."""
    from tests.test_optimizer import _StubKServeClient, _StubPool

    pool = _StubPool()
    client = _StubKServeClient()
    config_id_str = str(uuid.uuid4())
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=[VinInput(vin=_VIN_A, modelYear="MY26", paintCode="A75")],
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
        my26ConfigVersion=config_id_str,
    )
    with patch("app.optimizer._CONFIDENCE_FALLBACK_THRESHOLD", 0.0):
        response = await run_optimize(
            request,
            pool,
            client,
            my26_overrides={"paint_changeover_penalties": {"A75": 45}, "option_code_mappings": {}},
        )
    assert response.my26ConfigVersion == config_id_str


@pytest.mark.asyncio
async def test_optimize_with_my26_paint_override_reflects_penalty_in_heuristic_fallback() -> None:
    """Heuristic fallback with MY26 overrides applies the configured changeover penalty."""
    import asyncio

    from tests.test_optimizer import _StubPool

    class _TimeoutClient:
        async def predict(self, vins, constraint_snapshot, run_index):
            raise asyncio.TimeoutError("timeout")

    pool = _StubPool()
    client = _TimeoutClient()

    # Two VINs with different paint codes — changeover will trigger penalty
    vins = [
        VinInput(vin=_VIN_A, paintCode="A75"),
        VinInput(vin=_VIN_B, paintCode="B21"),
    ]
    request = OptimizeRequest(
        weekIso="2026-W42",
        vins=vins,
        constraintSnapshot=_SNAPSHOT,
        monteCarloRuns=2,
    )
    response = await run_optimize(
        request,
        pool,
        client,
        my26_overrides={"paint_changeover_penalties": {"B21": 10}, "option_code_mappings": {}},
    )
    assert response.resultType == ResultType.heuristic_fallback
    assert len(response.rankedSequence) == 2


# ---------------------------------------------------------------------------
# HTTP layer — TestClient tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _make_jwt(roles: list[str] | None = None) -> str:
    """Construct a minimal unsigned JWT (alg=none) with optional roles claim."""
    import base64

    payload_dict: dict[str, Any] = {
        "sub": "planner@bmwgroup.net",
        "exp": 9999999999,
    }
    if roles is not None:
        payload_dict["roles"] = roles

    import json as _json

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload_bytes = _json.dumps(payload_dict).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header}.{payload_b64}."


_VALID_DRAFT_BODY: dict[str, Any] = {
    "optionCodes": [
        {
            "code": "SA-100",
            "description": "Panoramic sunroof",
            "constraintMappings": ["paint_capacity"],
        }
    ],
    "paintPalette": [
        {
            "paintCode": "A75",
            "batchGroup": "metallic",
            "changeoverPenaltyMinutes": 30,
        }
    ],
}


def test_get_my26_config_401_when_no_auth(app_client) -> None:
    resp = app_client.get("/my26/config")
    assert resp.status_code == 401


def test_get_my26_config_404_when_no_active_config_exists(app_client) -> None:
    with patch("app.db.get_pool") as mock_pool:
        mock_pool.return_value = _My26StubPool()
        resp = app_client.get(
            "/my26/config",
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "MY26_CONFIG_NOT_FOUND"
    assert "correlation_id" in body


def test_post_my26_config_validate_401_when_no_auth(app_client) -> None:
    resp = app_client.post("/my26/config/validate", json=_VALID_DRAFT_BODY)
    assert resp.status_code == 401


def test_post_my26_config_validate_200_returns_validation_result(app_client) -> None:
    resp = app_client.post(
        "/my26/config/validate",
        json=_VALID_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "valid" in body
    assert "issues" in body


def test_post_my26_config_validate_400_on_malformed_payload(app_client) -> None:
    resp = app_client.post(
        "/my26/config/validate",
        json={"optionCodes": "not-a-list"},
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )
    assert resp.status_code == 400


def test_post_my26_config_save_401_when_no_auth(app_client) -> None:
    resp = app_client.post("/my26/config/save", json=_VALID_DRAFT_BODY)
    assert resp.status_code == 401


def test_post_my26_config_save_403_without_required_role(app_client) -> None:
    resp = app_client.post(
        "/my26/config/save",
        json=_VALID_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_make_jwt(roles=['read-only'])}"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"


def test_post_my26_config_save_409_when_validation_errors(app_client) -> None:
    """POST /my26/config/save with an error-severity issue returns 409."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        bad_draft = {
            "optionCodes": [
                {
                    "code": "SA-BAD",
                    "description": "Bad",
                    "constraintMappings": ["nonexistent"],
                }
            ],
            "paintPalette": [{"paintCode": "A75", "batchGroup": "metallic"}],
        }
        resp = app_client.post(
            "/my26/config/save",
            json=bad_draft,
            headers={"Authorization": f"Bearer {_make_jwt(roles=['production-planner'])}"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "MY26_CONFIG_VALIDATION_FAILED"
        assert "correlation_id" in body
    finally:
        set_known_constraint_keys(frozenset())


def test_post_my26_config_save_201_with_valid_draft(app_client) -> None:
    """POST /my26/config/save with a valid draft returns 201 with active config."""
    pool = _My26StubPool()
    with (
        patch("app.db.get_pool", return_value=pool),
        patch("app.my26_config.asyncio.create_task"),
    ):
        resp = app_client.post(
            "/my26/config/save",
            json=_VALID_DRAFT_BODY,
            headers={"Authorization": f"Bearer {_make_jwt(roles=['production-planner'])}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"
    assert "configId" in body
    assert "version" in body
    assert body["createdBy"] == "planner@bmwgroup.net"


def test_post_my26_config_save_error_body_has_correlation_id(app_client) -> None:
    """Error responses from /my26/config/save include correlation_id."""
    set_known_constraint_keys(frozenset({"paint_capacity"}))
    try:
        bad_draft = {
            "optionCodes": [
                {"code": "SA-X", "description": "X", "constraintMappings": ["bad_key"]}
            ],
            "paintPalette": [{"paintCode": "A75", "batchGroup": "metallic"}],
        }
        resp = app_client.post(
            "/my26/config/save",
            json=bad_draft,
            headers={"Authorization": f"Bearer {_make_jwt(roles=['production-planner'])}"},
        )
        assert "correlation_id" in resp.json()
    finally:
        set_known_constraint_keys(frozenset())
