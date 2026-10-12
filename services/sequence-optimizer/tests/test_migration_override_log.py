"""Migration tests for override_log and vin_constraint_explanation tables.

Reads the actual SQL migration files and verifies structural content:
  - Required tables and columns are declared in the forward migration
  - CHECK constraints, indexes, and PRIMARY KEY are present
  - Data residency annotation is present
  - Rollback SQL drops the correct table

These tests assert the generated artifact directly, not a hand-written copy.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

OVERRIDE_LOG_MIGRATION = MIGRATIONS_DIR / "20261012400000_create_override_log.sql"
VIN_EXPLANATION_MIGRATION = MIGRATIONS_DIR / "20261012500000_create_vin_constraint_explanation.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# override_log migration — forward
# ---------------------------------------------------------------------------


def test_override_log_migration_file_exists() -> None:
    assert OVERRIDE_LOG_MIGRATION.exists(), f"Migration file not found: {OVERRIDE_LOG_MIGRATION}"


def test_override_log_migration_creates_table() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS override_log" in sql


def test_override_log_has_override_id_uuid_primary_key() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "override_id" in sql
    assert "UUID" in sql
    assert "PRIMARY KEY" in sql
    assert "gen_random_uuid()" in sql


def test_override_log_has_run_id_not_null() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert re.search(r"run_id\s+UUID\s+NOT NULL", sql)


def test_override_log_has_vin_varchar17_not_null() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert re.search(r"vin\s+VARCHAR\(17\)\s+NOT NULL", sql)


def test_override_log_has_original_position_check_ge_1() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "original_position" in sql
    assert "original_position >= 1" in sql


def test_override_log_has_new_position_check_ge_1() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "new_position" in sql
    assert "new_position >= 1" in sql


def test_override_log_has_justification_text_not_null() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert re.search(r"justification\s+TEXT\s+NOT NULL", sql)


def test_override_log_has_justification_min_length_check() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    # CHECK enforces the 10-char minimum
    assert "char_length(justification) >= 10" in sql


def test_override_log_has_planner_id_text_not_null() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert re.search(r"planner_id\s+TEXT\s+NOT NULL", sql)


def test_override_log_has_created_at_timestamptz_not_null() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert re.search(r"created_at\s+TIMESTAMPTZ\s+NOT NULL", sql)


def test_override_log_has_data_region_defaulting_to_eu() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "data_region" in sql
    assert "eu-de-west-central" in sql


def test_override_log_has_btree_index_on_created_at() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "idx_override_log_created_at" in sql
    assert "created_at" in sql


def test_override_log_has_composite_index_on_run_id_vin() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "idx_override_log_run_id_vin" in sql
    # Composite index covers both columns
    assert re.search(r"ON override_log\s*\(\s*run_id,\s*vin\s*\)", sql)


def test_override_log_has_data_residency_table_comment() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "COMMENT ON TABLE override_log" in sql
    assert "data_residency" in sql


def test_override_log_migration_wrapped_in_transaction() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql


def test_override_log_rollback_drops_table() -> None:
    sql = _read(OVERRIDE_LOG_MIGRATION)
    assert "DROP TABLE IF EXISTS override_log" in sql


# ---------------------------------------------------------------------------
# vin_constraint_explanation migration — forward
# ---------------------------------------------------------------------------


def test_vin_explanation_migration_file_exists() -> None:
    assert VIN_EXPLANATION_MIGRATION.exists(), (
        f"Migration file not found: {VIN_EXPLANATION_MIGRATION}"
    )


def test_vin_explanation_migration_creates_table() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS vin_constraint_explanation" in sql


def test_vin_explanation_has_composite_primary_key_run_id_vin() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "PRIMARY KEY (run_id, vin)" in sql


def test_vin_explanation_has_run_id_uuid_not_null() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert re.search(r"run_id\s+UUID\s+NOT NULL", sql)


def test_vin_explanation_has_vin_varchar17_with_format_check() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert re.search(r"vin\s+VARCHAR\(17\)\s+NOT NULL", sql)
    # VIN regex CHECK
    assert "A-HJ-NPR-Z0-9" in sql


def test_vin_explanation_has_position_not_null() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert re.search(r"position\s+INTEGER\s+NOT NULL", sql)


def test_vin_explanation_has_top_factors_jsonb_not_null() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert re.search(r"top_factors\s+JSONB\s+NOT NULL", sql)


def test_vin_explanation_has_created_at_timestamptz_not_null() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert re.search(r"created_at\s+TIMESTAMPTZ\s+NOT NULL", sql)


def test_vin_explanation_has_data_region_defaulting_to_eu() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "data_region" in sql
    assert "eu-de-west-central" in sql


def test_vin_explanation_has_index_on_run_id() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "idx_vin_explanation_run_id" in sql
    assert re.search(r"ON vin_constraint_explanation\s*\(\s*run_id\s*\)", sql)


def test_vin_explanation_has_data_residency_table_comment() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "COMMENT ON TABLE vin_constraint_explanation" in sql
    assert "data_residency" in sql


def test_vin_explanation_migration_wrapped_in_transaction() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql


def test_vin_explanation_rollback_drops_table() -> None:
    sql = _read(VIN_EXPLANATION_MIGRATION)
    assert "DROP TABLE IF EXISTS vin_constraint_explanation" in sql
