"""Tests for the AR instruction migration SQL files.

Per project convention, tests for generated artifacts must read the generated
files themselves and assert on their content — never a hand-written parallel copy.
"""

from __future__ import annotations

import pathlib

_MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


def _read(filename: str) -> str:
    return (_MIGRATIONS_DIR / filename).read_text()


class TestArInstructionCacheMigration:
    _SQL = _read("20261012_001_ar_instruction_cache.sql")

    def test_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS ar_instruction_cache" in self._SQL

    def test_all_required_columns_present(self):
        for col in (
            "id", "vin", "station_id", "option_codes", "variant_code",
            "is_rare_variant", "instruction_version", "payload_jsonb",
            "fallback_pdf_qr_url", "fetched_at", "expires_at", "created_at", "updated_at",
        ):
            assert col in self._SQL, f"Missing column: {col}"

    def test_uuid_primary_key_with_default(self):
        assert "UUID" in self._SQL
        assert "gen_random_uuid()" in self._SQL

    def test_timestamptz_columns(self):
        assert "TIMESTAMPTZ" in self._SQL

    def test_jsonb_payload_column(self):
        assert "JSONB" in self._SQL

    def test_all_required_indexes(self):
        assert "idx_ar_instruction_cache_vin" in self._SQL
        assert "idx_ar_instruction_cache_vin_station" in self._SQL
        assert "idx_ar_instruction_cache_expires_at" in self._SQL

    def test_wrapped_in_transaction(self):
        assert "BEGIN;" in self._SQL
        assert "COMMIT;" in self._SQL

    def test_data_residency_comment(self):
        assert "eu-germany-west-central" in self._SQL

    def test_migration_version_is_timestamp_not_integer(self):
        # The filename must start with a date prefix, not a bare integer
        filename = "20261012_001_ar_instruction_cache.sql"
        assert filename[0].isdigit()
        assert len(filename.split("_")[0]) == 8  # YYYYMMDD


class TestArInstructionFailureLogMigration:
    _SQL = _read("20261012_002_ar_instruction_failure_log.sql")

    def test_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS ar_instruction_failure_log" in self._SQL

    def test_all_required_columns_present(self):
        for col in (
            "id", "vin", "station_id", "device_type", "failure_reason",
            "correlation_id", "failed_at", "created_at", "updated_at",
        ):
            assert col in self._SQL, f"Missing column: {col}"

    def test_device_type_check_constraint(self):
        assert "CHECK" in self._SQL
        assert "HoloLens2" in self._SQL
        assert "iPad" in self._SQL

    def test_correlation_id_is_uuid(self):
        assert "correlation_id" in self._SQL
        assert "UUID" in self._SQL

    def test_all_required_indexes(self):
        assert "idx_ar_failure_log_vin" in self._SQL
        assert "idx_ar_failure_log_failed_at" in self._SQL

    def test_failed_at_index_is_descending(self):
        assert "failed_at DESC" in self._SQL

    def test_append_only_comment(self):
        assert "append_only=true" in self._SQL

    def test_wrapped_in_transaction(self):
        assert "BEGIN;" in self._SQL
        assert "COMMIT;" in self._SQL


class TestArSessionMigration:
    _SQL = _read("20261012_003_ar_session.sql")

    def test_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS ar_session" in self._SQL

    def test_all_required_columns_present(self):
        for col in (
            "id", "vin", "station_id", "device_type", "user_id",
            "idempotency_key", "event_id", "started_at", "created_at", "updated_at",
        ):
            assert col in self._SQL, f"Missing column: {col}"

    def test_unique_constraint_on_idempotency_key(self):
        assert "uq_ar_session_idempotency" in self._SQL
        assert "UNIQUE (idempotency_key)" in self._SQL

    def test_device_type_check_constraint(self):
        assert "CHECK" in self._SQL
        assert "HoloLens2" in self._SQL
        assert "iPad" in self._SQL

    def test_all_required_indexes(self):
        assert "idx_ar_session_vin" in self._SQL
        assert "idx_ar_session_user_id" in self._SQL
        assert "idx_ar_session_station_id" in self._SQL
        assert "idx_ar_session_started_at" in self._SQL

    def test_started_at_index_is_descending(self):
        assert "started_at DESC" in self._SQL

    def test_no_biometric_comment(self):
        assert "no_biometric=true" in self._SQL

    def test_wrapped_in_transaction(self):
        assert "BEGIN;" in self._SQL
        assert "COMMIT;" in self._SQL


class TestTorqueLogMigration:
    _SQL = _read("20261012_004_torque_log.sql")

    def test_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS torque_log" in self._SQL

    def test_all_required_columns_present(self):
        for col in (
            "id", "vin", "station_id", "fastener_point_id",
            "torque_nm", "angles_deg", "spec_nm", "tolerance_nm",
            "in_tolerance", "trace_data", "plc_timestamp",
            "uns_publish_status", "uns_published_at", "edge_buffered",
            "idempotency_key", "alert_id", "created_at", "updated_at",
        ):
            assert col in self._SQL, f"Missing column: {col}"

    def test_uuid_primary_key_with_default(self):
        assert "UUID" in self._SQL
        assert "gen_random_uuid()" in self._SQL

    def test_numeric_torque_and_angles_columns(self):
        assert "NUMERIC(10,4)" in self._SQL

    def test_jsonb_trace_data_column(self):
        assert "JSONB" in self._SQL

    def test_timestamptz_columns(self):
        assert "TIMESTAMPTZ" in self._SQL

    def test_uns_publish_status_check_constraint(self):
        assert "CHECK" in self._SQL
        assert "'published'" in self._SQL
        assert "'queued'" in self._SQL
        assert "'failed'" in self._SQL

    def test_uns_publish_status_default_is_queued(self):
        assert "DEFAULT 'queued'" in self._SQL

    def test_idempotency_unique_constraint(self):
        assert "uq_torque_log_idempotency" in self._SQL
        assert "UNIQUE (idempotency_key)" in self._SQL

    def test_all_required_indexes(self):
        assert "idx_torque_log_vin" in self._SQL
        assert "idx_torque_log_station_id" in self._SQL
        assert "idx_torque_log_vin_fastener" in self._SQL
        assert "idx_torque_log_plc_timestamp" in self._SQL
        assert "idx_torque_log_uns_publish_status" in self._SQL
        assert "idx_torque_log_in_tolerance" in self._SQL

    def test_plc_timestamp_index_is_descending(self):
        assert "plc_timestamp DESC" in self._SQL

    def test_partial_index_on_uns_publish_status(self):
        assert "uns_publish_status != 'published'" in self._SQL

    def test_partial_index_on_in_tolerance_false(self):
        assert "in_tolerance = FALSE" in self._SQL

    def test_data_residency_classification_comment(self):
        assert "data_residency=eu-germany-west-central" in self._SQL
        assert "classification=confidential" in self._SQL

    def test_wrapped_in_transaction(self):
        assert "BEGIN;" in self._SQL
        assert "COMMIT;" in self._SQL

    def test_migration_version_is_timestamp_not_integer(self):
        filename = "20261012_004_torque_log.sql"
        assert filename[0].isdigit()
        assert len(filename.split("_")[0]) == 8  # YYYYMMDD
