"""Verify that migration SQL files exist and contain required DDL.

Tests read the actual migration files rather than a hand-written copy.
"""

from __future__ import annotations

import pathlib

_MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


def _read(filename: str) -> str:
    return (_MIGRATIONS_DIR / filename).read_text()


class TestFabricAnchorAttemptMigration:
    def test_file_exists(self):
        assert (_MIGRATIONS_DIR / "20261013_001_fabric_anchor_attempt.sql").exists()

    def test_creates_table_idempotently(self):
        sql = _read("20261013_001_fabric_anchor_attempt.sql")
        assert "CREATE TABLE IF NOT EXISTS fabric_anchor_attempt" in sql

    def test_status_column_has_check_constraint(self):
        sql = _read("20261013_001_fabric_anchor_attempt.sql")
        assert "pending" in sql
        assert "committed" in sql
        assert "timeout" in sql

    def test_wrapped_in_transaction(self):
        sql = _read("20261013_001_fabric_anchor_attempt.sql")
        assert "BEGIN;" in sql
        assert "COMMIT;" in sql

    def test_index_on_certificate_id(self):
        sql = _read("20261013_001_fabric_anchor_attempt.sql")
        assert "idx_fabric_anchor_attempt_certificate_id" in sql


class TestFabricCertificateMigration:
    def test_file_exists(self):
        assert (_MIGRATIONS_DIR / "20261013_002_fabric_certificate.sql").exists()

    def test_creates_table_idempotently(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "CREATE TABLE IF NOT EXISTS fabric_certificate" in sql

    def test_has_source_system_column_with_check(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "source_system" in sql
        assert "quality-studio" in sql
        assert "legacy" in sql

    def test_has_shift_id_column(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "shift_id" in sql

    def test_has_index_on_shift_id(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "idx_fabric_certificate_shift_id" in sql

    def test_uses_timestamptz_not_naive_timestamp(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "TIMESTAMPTZ" in sql
        assert "TIMESTAMP " not in sql.upper().replace("TIMESTAMPTZ", "")

    def test_wrapped_in_transaction(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "BEGIN;" in sql
        assert "COMMIT;" in sql

    def test_unique_constraint_on_fabric_transaction_id(self):
        sql = _read("20261013_002_fabric_certificate.sql")
        assert "uq_fabric_certificate_tx" in sql
