terraform {
  required_version = ">= 1.6"
}

# Provider credentials for this module are inherited from the root platform/ workspace.
# Secrets (audit_log_endpoint, group object IDs) are injected via HashiCorp Vault agent
# injector — never hardcoded. See .env.example at repo root for required variable names.
