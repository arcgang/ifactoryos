# identity-pseudonymisation — PostgreSQL ROLE and table for GDPR pseudonymisation mapping
#
# Provisions the identity_pseudonymisation_map table in the on-prem Munich
# PostgreSQL/Citus instance (EU data residency — Germany West Central / on-prem Munich).
#
# Access is restricted to the identity-service ROLE only; no console backend
# or AI model has direct SELECT access to the raw employee_id or name columns.
#
# Design decision: eu-data-residency-azure-eu-central — all personal data on-prem Munich
# or Azure EU Central; zero cross-border transfers (GDPR US-028 / BR-13).
#
# Provider and required_providers blocks are in versions.tf; do not repeat them here.

# ---------------------------------------------------------------------------
# Variables — all sensitive values read from Vault at apply time
# ---------------------------------------------------------------------------

variable "identity_db_host" {
  type        = string
  description = "Hostname of the on-prem Munich PostgreSQL/Citus instance"
}

variable "identity_db_superuser" {
  type        = string
  description = "PostgreSQL superuser name (injected by Vault agent)"
  sensitive   = true
}

variable "identity_db_superuser_password" {
  type        = string
  description = "PostgreSQL superuser password (injected by Vault agent)"
  sensitive   = true
}

variable "identity_service_db_password" {
  type        = string
  description = "Password for the identity_service PostgreSQL ROLE. Read from Vault at apply time — never hardcoded."
  sensitive   = true
}

# ---------------------------------------------------------------------------
# PostgreSQL provider — uses superuser credentials injected by Vault agent
# ---------------------------------------------------------------------------

provider "postgresql" {
  host     = var.identity_db_host
  port     = 5432
  database = "app"
  username = var.identity_db_superuser
  password = var.identity_db_superuser_password
  sslmode  = "require"
}

# ---------------------------------------------------------------------------
# Restricted PostgreSQL ROLE — identity-service only
# No SUPERUSER / CREATEDB / CREATEROLE — minimal privilege.
# ---------------------------------------------------------------------------

resource "postgresql_role" "identity_service" {
  name     = "identity_service"
  login    = true
  password = var.identity_service_db_password
}

# ---------------------------------------------------------------------------
# pgcrypto extension — required for gen_random_uuid() used in the table DDL
# ---------------------------------------------------------------------------

resource "postgresql_extension" "pgcrypto" {
  name = "pgcrypto"
}

# ---------------------------------------------------------------------------
# Table: identity_pseudonymisation_map
#
# employee_id and name are the GDPR-sensitive columns:
#   - employee_id: NULLed by the scrubber within 30 days of tombstoned_at
#   - name: NULLed alongside employee_id by the scrubber
#   - pseudonymous_token: stable UUID; NEVER nulled — referenced by torque_log
#   - tombstoned_at: set by POST /internal/identity/deletion-request
#
# data_residency=eu-germany-west-central classification=confidential
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# identity_pseudonymisation_map table
#
# The table DDL lives in services/identity-service/migrations/001_identity_pseudonymisation_map.sql
# and is applied by the CI migration runner (psql -f) before the identity-service pod starts.
# Terraform owns the ROLE and GRANT objects; the table DDL is intentionally outside
# Terraform state to avoid state drift on column changes.
# ---------------------------------------------------------------------------

resource "postgresql_grant" "identity_service_select" {
  database    = "app"
  role        = postgresql_role.identity_service.name
  schema      = "public"
  object_type = "table"
  objects     = ["identity_pseudonymisation_map"]
  privileges  = ["SELECT", "INSERT", "UPDATE"]

  depends_on = [postgresql_role.identity_service]
}

# ---------------------------------------------------------------------------
# Outputs — consumed by identity-service Kubernetes deployment and CI
# ---------------------------------------------------------------------------

output "identity_service_db_role" {
  description = "PostgreSQL ROLE name for identity-service; used in K8s secret provisioning"
  value       = postgresql_role.identity_service.name
}

output "identity_pseudonymisation_migration_path" {
  description = "Path to the migration SQL file that creates identity_pseudonymisation_map"
  value       = "services/identity-service/migrations/001_identity_pseudonymisation_map.sql"
  sensitive   = false
}
