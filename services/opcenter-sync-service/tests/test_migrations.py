"""Tests for the gate_result migration SQL file.

Per project convention, tests read the generated file itself — never a
hand-written parallel copy.
"""

from __future__ import annotations

import pathlib

_MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


def _read(filename: str) -> str:
    return (_MIGRATIONS_DIR / filename).read_text()


class TestGateResultMigration:
    _SQL = _read("20261013_001_gate_result.sql")

    def test_table_created_with_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS gate_result" in self._SQL

    def test_all_required_columns_present(self):
        for col in (
            "gate_result_id", "vin", "gate_id", "gate_type", "result",
            "measurements", "measured_at", "received_from_opcenter_at",
            "opcenter_event_id", "kafka_offset", "correlation_id",
            "created_at", "updated_at",
        ):
            assert col in self._SQL, f"Missing column: {col}"

    def test_uuid_primary_key_with_default(self):
        assert "UUID" in self._SQL
        assert "gen_random_uuid()" in self._SQL
        assert "pk_gate_result" in self._SQL

    def test_gate_type_check_constraint(self):
        assert "CHECK" in self._SQL
        for gate_type in ("dimensional", "leak", "electrical", "nvh", "adas"):
            assert gate_type in self._SQL, f"Missing gate_type: {gate_type}"

    def test_result_check_constraint(self):
        for result in ("pass", "conditional_pass", "fail"):
            assert result in self._SQL, f"Missing result: {result}"

    def test_timestamptz_columns(self):
        assert "TIMESTAMPTZ" in self._SQL

    def test_jsonb_measurements_column(self):
        assert "JSONB" in self._SQL

    def test_all_required_indexes_present(self):
        assert "idx_gate_result_vin" in self._SQL
        assert "idx_gate_result_vin_gate_type" in self._SQL
        assert "idx_gate_result_received_at" in self._SQL
        assert "idx_gate_result_conditional_pass" in self._SQL
        assert "uq_gate_result_opcenter_event_id" in self._SQL

    def test_unique_index_on_opcenter_event_id(self):
        assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_gate_result_opcenter_event_id" in self._SQL
        assert "WHERE opcenter_event_id IS NOT NULL" in self._SQL

    def test_received_at_index_is_descending(self):
        assert "received_from_opcenter_at DESC" in self._SQL

    def test_conditional_pass_partial_index(self):
        assert "WHERE result = 'conditional_pass'" in self._SQL

    def test_data_residency_classification_comment(self):
        assert "data_residency=eu-germany-west-central" in self._SQL
        assert "classification=confidential" in self._SQL

    def test_table_comment_includes_retention(self):
        assert "Retention" in self._SQL
        assert "7 years" in self._SQL

    def test_wrapped_in_transaction(self):
        assert "BEGIN;" in self._SQL
        assert "COMMIT;" in self._SQL

    def test_migration_version_is_timestamp_not_integer(self):
        filename = "20261013_001_gate_result.sql"
        assert filename[0].isdigit()
        assert len(filename.split("_")[0]) == 8  # YYYYMMDD
