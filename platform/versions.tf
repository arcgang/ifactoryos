terraform {
  required_version = ">= 1.6"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.53"
    }
  }
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "entra_tenant_id" {
  type        = string
  description = "Azure / Entra ID tenant ID; read from ARM_TENANT_ID environment variable"
}

variable "audit_log_endpoint" {
  type        = string
  description = "Base URL of the immutable-audit-log service; read from AUDIT_LOG_ENDPOINT environment variable"
}

variable "azure_subscription_id" {
  type        = string
  description = "Azure subscription ID; read from ARM_SUBSCRIPTION_ID environment variable"
}

# ---------------------------------------------------------------------------
# jira-integration module inputs
# ---------------------------------------------------------------------------

variable "vault_addr" {
  type        = string
  description = "HashiCorp Vault address; read from TF_VAR_vault_addr at apply time."
}

variable "jira_base_url" {
  type        = string
  description = "Jira Cloud base URL (e.g. https://bmwgroup.atlassian.net); read from TF_VAR_jira_base_url."
}

variable "jira_admin_token" {
  type        = string
  sensitive   = true
  description = "Jira Admin API token; read from TF_VAR_jira_admin_token (Vault-injected at apply time)."
}

variable "jira_oauth2_client_id" {
  type        = string
  description = "OAuth 2.0 client_id for the defect-genome Jira OAuth app; read from TF_VAR_jira_oauth2_client_id."
}

variable "jira_oauth2_client_secret" {
  type        = string
  sensitive   = true
  description = "OAuth 2.0 client_secret for the defect-genome Jira OAuth app; read from TF_VAR_jira_oauth2_client_secret (Vault-injected)."
}
