variable "environment" {
  type        = string
  description = "Deployment environment (dev, pp, prod)."
}

variable "vault_addr" {
  type        = string
  description = "HashiCorp Vault address (e.g. https://vault.internal.ifos.example). Read from TF_VAR_vault_addr at apply time."
}

variable "jira_base_url" {
  type        = string
  description = "Jira Cloud base URL (e.g. https://bmwgroup.atlassian.net). Read from TF_VAR_jira_base_url at apply time."
}

variable "jira_admin_token" {
  type        = string
  sensitive   = true
  description = "Jira Admin API token used by Terraform to provision the project and custom fields. Read from Vault at apply time via TF_VAR_jira_admin_token; never hardcoded."
}

variable "jira_oauth2_client_id" {
  type        = string
  description = "OAuth 2.0 client_id for the defect-genome Jira OAuth app. Created in the Atlassian Developer Console (manual pre-step) and passed as TF_VAR_jira_oauth2_client_id."
}

variable "jira_oauth2_client_secret" {
  type        = string
  sensitive   = true
  description = "OAuth 2.0 client_secret for the defect-genome Jira OAuth app. Created in the Atlassian Developer Console (manual pre-step). Read from Vault at apply time via TF_VAR_jira_oauth2_client_secret; never hardcoded."
}
