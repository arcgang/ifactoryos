"""MY26 configuration service — validate, save, and load MY26 constraint config.

Public API
----------
validate_my26_draft(draft)                      → My26ValidationResult
get_active_my26_config(pool)                    → My26Config | None
save_my26_config(draft, pool, user_id)          → My26Config
load_my26_config_by_version(config_id, pool)    → My26Config | None
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.models import (
    My26Config,
    My26ConfigDraft,
    My26ConfigStatus,
    My26ValidationIssue,
    My26ValidationResult,
    My26ValidationSeverity,
    OptionCodeEntry,
    PaintPaletteEntry,
)

logger = logging.getLogger(__name__)

# Known constraint model keys; empty set = permissive (tests override via set_known_constraint_keys).
_KNOWN_CONSTRAINT_KEYS: frozenset[str] = frozenset()


def set_known_constraint_keys(keys: frozenset[str]) -> None:
    """Override the known constraint key set (called from config/startup)."""
    global _KNOWN_CONSTRAINT_KEYS
    _KNOWN_CONSTRAINT_KEYS = keys


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_option_codes(
    option_codes: list[OptionCodeEntry],
) -> list[My26ValidationIssue]:
    issues: list[My26ValidationIssue] = []
    seen_codes: set[str] = set()

    for entry in option_codes:
        if entry.code in seen_codes:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.warning,
                    field=f"optionCodes[{entry.code}]",
                    message=f"Duplicate option code '{entry.code}' — only the first entry will be used",
                )
            )
        seen_codes.add(entry.code)

        if not entry.constraintMappings:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.error,
                    field=f"optionCodes[{entry.code}].constraintMappings",
                    message=f"Option code '{entry.code}' has no constraint mappings",
                )
            )
        elif _KNOWN_CONSTRAINT_KEYS:
            for mapping in entry.constraintMappings:
                if mapping not in _KNOWN_CONSTRAINT_KEYS:
                    issues.append(
                        My26ValidationIssue(
                            severity=My26ValidationSeverity.error,
                            field=f"optionCodes[{entry.code}].constraintMappings",
                            message=f"Constraint mapping '{mapping}' is not a recognised key in the constraint model",
                        )
                    )

    return issues


def _validate_paint_palette(
    paint_palette: list[PaintPaletteEntry],
) -> list[My26ValidationIssue]:
    issues: list[My26ValidationIssue] = []
    seen_codes: set[str] = set()

    for entry in paint_palette:
        if not entry.paintCode:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.error,
                    field="paintPalette[].paintCode",
                    message="paintCode must be a non-empty string",
                )
            )
        if not entry.batchGroup:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.error,
                    field=f"paintPalette[{entry.paintCode}].batchGroup",
                    message=f"batchGroup for paint code '{entry.paintCode}' must be a non-empty string",
                )
            )
        if entry.changeoverPenaltyMinutes is not None and entry.changeoverPenaltyMinutes < 0:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.error,
                    field=f"paintPalette[{entry.paintCode}].changeoverPenaltyMinutes",
                    message=f"changeoverPenaltyMinutes for paint code '{entry.paintCode}' must be >= 0",
                )
            )
        if entry.paintCode in seen_codes:
            issues.append(
                My26ValidationIssue(
                    severity=My26ValidationSeverity.warning,
                    field=f"paintPalette[{entry.paintCode}]",
                    message=f"Duplicate paint code '{entry.paintCode}' — only the first entry will be used",
                )
            )
        seen_codes.add(entry.paintCode)

    return issues


def validate_my26_draft(draft: My26ConfigDraft) -> My26ValidationResult:
    """Validate a MY26 config draft without touching the database."""
    issues: list[My26ValidationIssue] = []
    issues.extend(_validate_option_codes(draft.optionCodes))
    issues.extend(_validate_paint_palette(draft.paintPalette))

    has_errors = any(i.severity == My26ValidationSeverity.error for i in issues)
    return My26ValidationResult(valid=not has_errors, issues=issues)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _row_to_my26_config(row: asyncpg.Record) -> My26Config:
    option_codes_raw = json.loads(row["option_codes"])
    paint_palette_raw = json.loads(row["paint_palette"])
    kitting_rules_raw = json.loads(row["kitting_rules"])

    option_codes = [OptionCodeEntry(**item) for item in option_codes_raw]
    paint_palette = [PaintPaletteEntry(**item) for item in paint_palette_raw]

    activated_at = row["activated_at"]
    if isinstance(activated_at, datetime):
        activated_at_str: str | None = activated_at.isoformat()
    elif activated_at is not None:
        activated_at_str = str(activated_at)
    else:
        activated_at_str = None

    created_at = row["created_at"]
    if isinstance(created_at, datetime):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)

    return My26Config(
        configId=row["config_id"],
        version=row["version"],
        status=My26ConfigStatus(row["status"]),
        optionCodes=option_codes,
        paintPalette=paint_palette,
        kittingRules=kitting_rules_raw,
        createdAt=created_at_str,
        createdBy=row["created_by"],
        activatedAt=activated_at_str,
    )


async def get_active_my26_config(pool: asyncpg.Pool) -> My26Config | None:
    """Return the current active MY26 config, or None if none exists."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT config_id, version, status, option_codes, paint_palette,
                   kitting_rules, created_at, created_by, activated_at
            FROM my26_config
            WHERE status = 'active'
            LIMIT 1
            """,
        )
    if row is None:
        return None
    return _row_to_my26_config(row)


async def load_my26_config_by_version(
    config_id: uuid.UUID,
    pool: asyncpg.Pool,
) -> My26Config | None:
    """Return a MY26 config by UUID (any status)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT config_id, version, status, option_codes, paint_palette,
                   kitting_rules, created_at, created_by, activated_at
            FROM my26_config
            WHERE config_id = $1
            LIMIT 1
            """,
            config_id,
        )
    if row is None:
        return None
    return _row_to_my26_config(row)


async def save_my26_config(
    draft: My26ConfigDraft,
    pool: asyncpg.Pool,
    user_id: str,
) -> My26Config:
    """Persist a validated MY26 config draft, supersede any prior active config, and publish events.

    Raises ValueError if validation finds error-severity issues.
    """
    validation = validate_my26_draft(draft)
    if not validation.valid:
        raise ValueError("Validation failed — unresolved errors present")

    config_id = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    version = _generate_version()

    option_codes_json = json.dumps([e.model_dump(mode="json") for e in draft.optionCodes])
    paint_palette_json = json.dumps([e.model_dump(mode="json") for e in draft.paintPalette])
    kitting_rules_json = json.dumps(draft.kittingRules)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Supersede any existing active config
            await conn.execute(
                """
                UPDATE my26_config
                SET status = 'superseded'
                WHERE status = 'active'
                """,
            )
            # Insert new active config
            await conn.execute(
                """
                INSERT INTO my26_config (
                    config_id, version, status, option_codes, paint_palette,
                    kitting_rules, created_at, created_by, activated_at
                ) VALUES ($1, $2, 'active', $3, $4, $5, $6, $7, $6)
                """,
                config_id,
                version,
                option_codes_json,
                paint_palette_json,
                kitting_rules_json,
                now,
                user_id,
            )

    config = My26Config(
        configId=config_id,
        version=version,
        status=My26ConfigStatus.active,
        optionCodes=draft.optionCodes,
        paintPalette=draft.paintPalette,
        kittingRules=draft.kittingRules,
        createdAt=now.isoformat(),
        createdBy=user_id,
        activatedAt=now.isoformat(),
    )

    asyncio.create_task(_publish_activated_event(config, user_id))
    asyncio.create_task(_write_audit_event(config, user_id))

    return config


def _generate_version() -> str:
    """Generate a simple sequential version string based on current UTC time."""
    now = datetime.now(tz=timezone.utc)
    return f"{now.year}.{now.month}.{now.day}"


# ---------------------------------------------------------------------------
# Kafka event publishing
# ---------------------------------------------------------------------------


async def _publish_activated_event(config: My26Config, activated_by: str) -> None:
    """Publish My26ConfigActivatedEvent to Kafka — best-effort, logged on failure."""
    try:
        import aiokafka

        from app.config import get_settings

        cfg = get_settings()
        producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=cfg.kafka_bootstrap_servers,
            request_timeout_ms=5000,
        )
        await producer.start()
        try:
            event_payload = {
                "configId": str(config.configId),
                "version": config.version,
                "optionCodeCount": len(config.optionCodes),
                "paintPaletteCount": len(config.paintPalette),
                "activatedBy": activated_by,
                "activatedAt": config.activatedAt,
            }
            await asyncio.wait_for(
                producer.send(
                    cfg.my26_config_activated_topic,
                    value=json.dumps(event_payload).encode(),
                ),
                timeout=5.0,
            )
            logger.info(
                "Published My26ConfigActivatedEvent configId=%s version=%s",
                config.configId,
                config.version,
            )
        finally:
            await producer.stop()
    except ImportError as exc:
        logger.exception(
            "aiokafka not available for publish configId=%s: %s", config.configId, exc
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.exception(
            "Failed to publish My26ConfigActivatedEvent configId=%s: %s", config.configId, exc
        )


# ---------------------------------------------------------------------------
# Audit log write
# ---------------------------------------------------------------------------


async def _write_audit_event(config: My26Config, user_id: str) -> None:
    """POST audit event to immutable audit log — best-effort, 1 s timeout."""
    try:
        import httpx

        from app.config import get_settings

        cfg = get_settings()
        payload: dict[str, Any] = {
            "userId": user_id,
            "action": "my26_config_save",
            "configVersion": str(config.configId),
            "timestamp": config.activatedAt,
        }
        async with httpx.AsyncClient(timeout=cfg.audit_log_timeout_s) as client:
            await client.post(cfg.audit_log_url, json=payload)
    except ImportError as exc:
        logger.exception(
            "httpx not available for audit write configId=%s: %s", config.configId, exc
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.exception(
            "Failed to write audit event for configId=%s: %s", config.configId, exc
        )


# ---------------------------------------------------------------------------
# Constraint matrix merge
# ---------------------------------------------------------------------------


def build_constraint_overrides(config: My26Config) -> dict[str, Any]:
    """Build a constraint override dict from a MY26 config for use in optimizer runs.

    Returns a dict with:
      - paint_changeover_penalties: {paintCode: changeoverPenaltyMinutes}
      - option_code_mappings: {code: [constraintMappings]}
    """
    paint_changeover: dict[str, int] = {}
    for entry in config.paintPalette:
        if entry.changeoverPenaltyMinutes is not None:
            paint_changeover[entry.paintCode] = entry.changeoverPenaltyMinutes

    option_mappings: dict[str, list[str]] = {
        entry.code: entry.constraintMappings for entry in config.optionCodes
    }

    return {
        "paint_changeover_penalties": paint_changeover,
        "option_code_mappings": option_mappings,
    }
