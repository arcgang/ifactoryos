# Provider configurations for the platform root module.
# Credentials are read from environment variables; no secrets are hardcoded here.
# See .env.example at the repo root for required variable names.

# azuread: consumed by the bmw-entra-id child module.
# ARM_CLIENT_ID, ARM_CLIENT_SECRET, ARM_TENANT_ID must be set at apply time.
provider "azuread" {}
