# jira-integration — Jira REST API v3, OAuth 2.0
# LLD component cmp:jira-integration. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

variable "vault_addr" {
  type        = string
  description = "HashiCorp Vault address (e.g. https://vault.internal.ifos.example)"
}

variable "jira_base_url" {
  type        = string
  description = "Jira Cloud base URL (e.g. https://bmwgroup.atlassian.net)"
}

variable "jira_admin_token" {
  type        = string
  sensitive   = true
  description = "Jira Admin API token used by Terraform to provision the project and custom fields. Read from Vault at apply time."
}

variable "jira_oauth2_client_id" {
  type        = string
  description = "OAuth 2.0 client_id for the defect-genome Jira OAuth app (created in Atlassian developer console)."
}

variable "jira_oauth2_client_secret" {
  type        = string
  sensitive   = true
  description = "OAuth 2.0 client_secret for the defect-genome Jira OAuth app. Read from environment/Vault at apply time; never hardcoded."
}

# ---------------------------------------------------------------------------
# Terraform provider configuration
# ---------------------------------------------------------------------------

terraform {
  required_providers {
    vault = {
      source  = "hashicorp/vault"
      version = "~> 4.0"
    }
    jira = {
      source  = "fourplusone/jira"
      version = "~> 0.1"
    }
  }
}

provider "vault" {
  address = var.vault_addr
  # Token is injected via VAULT_TOKEN environment variable — never hardcoded here.
}

provider "jira" {
  url   = var.jira_base_url
  token = var.jira_admin_token
}

# ---------------------------------------------------------------------------
# HashiCorp Vault — Jira OAuth2 client secret for defect-genome
#
# The Vault Agent Injector injects this secret into the defect-genome pod at
# the path /vault/secrets/jira-client-secret as a flat key=value file:
#
#   client_id     = <value>
#   client_secret = <value>
#
# Consumed by services/defect-genome/src/app/config.py via environment vars
# JIRA_OAUTH2_CLIENT_ID and JIRA_OAUTH2_CLIENT_SECRET (written by the injector
# template).
# ---------------------------------------------------------------------------

resource "vault_kv_secret_v2" "jira_quality_studio_client_secret" {
  mount               = "secret"
  name                = "jira/quality-studio-client-secret"
  delete_all_versions = false

  data_json = jsonencode({
    client_id     = var.jira_oauth2_client_id
    client_secret = var.jira_oauth2_client_secret
    scopes        = "write:jira-work read:jira-work"
    token_url     = "https://auth.atlassian.com/oauth/token"
    environment   = var.environment
  })
}

# ---------------------------------------------------------------------------
# Jira — IFOS-QUALITY project
# ---------------------------------------------------------------------------

resource "jira_project" "ifos_quality" {
  key          = "IFOSQUALITY"
  name         = "IFOS-QUALITY"
  project_type = "software"
  description  = "iFactoryOS quality engineering — corrective actions for DefectGenome paint defect clusters"

  # All issue/field data are vehicle-level aggregate (cluster_id, confidence score, VIN count).
  # No personally-identifiable operator data per GDPR / Works Council §87 BetrVG.
}

# ---------------------------------------------------------------------------
# Jira — Corrective Action issue type
# ---------------------------------------------------------------------------

resource "jira_issue_type" "corrective_action" {
  name        = "Corrective Action"
  description = "Tracks a corrective action raised in response to a DefectGenome cluster alert. Contains only aggregate paint-quality data — no PII, no VINs, no operator identifiers."
}

resource "jira_issue_type_scheme" "ifos_quality_scheme" {
  name            = "IFOS-QUALITY Issue Type Scheme"
  issue_type_ids  = [jira_issue_type.corrective_action.id]
  default_issue_type_id = jira_issue_type.corrective_action.id
}

resource "jira_issue_type_scheme_association" "ifos_quality_association" {
  project_key       = jira_project.ifos_quality.key
  issue_type_scheme = jira_issue_type_scheme.ifos_quality_scheme.id
}

# ---------------------------------------------------------------------------
# Jira — Custom fields on the Corrective Action issue type
#
# Field definitions contain only aggregate / cluster-level data.
# GDPR / Works Council §87 BetrVG compliance note:
#   - DefectGenome Cluster ID: opaque UUID — no PII
#   - Root Cause Probability: 0–1 float — no PII
#   - Affected VIN Count: integer count — no individual VIN, no operator data
#   - Recommended Prep Stage Adjustment: freetext process recommendation — no PII
# ---------------------------------------------------------------------------

resource "jira_custom_field" "defect_genome_cluster_id" {
  name        = "DefectGenome Cluster ID"
  description = "UUID of the DefectGenome defect cluster that triggered this corrective action. No PII."
  type        = "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
}

resource "jira_custom_field" "root_cause_probability" {
  name        = "Root Cause Probability"
  description = "XGBoost confidence score (0.0–1.0) for the root-cause signature assigned to this cluster. No PII."
  type        = "com.atlassian.jira.plugin.system.customfieldtypes:float"
}

resource "jira_custom_field" "affected_vin_count" {
  name        = "Affected VIN Count"
  description = "Number of VINs that share the root-cause signature for this cluster. Aggregate count only — no individual VINs stored in Jira."
  type        = "com.atlassian.jira.plugin.system.customfieldtypes:float"
}

resource "jira_custom_field" "recommended_prep_stage_adjustment" {
  name        = "Recommended Prep Stage Adjustment"
  description = "Freetext description of the recommended prep-stage process adjustment. No PII."
  type        = "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
}

# Associate all four custom fields with the IFOS-QUALITY project screen
resource "jira_custom_field_option" "cluster_id_screen" {
  custom_field = jira_custom_field.defect_genome_cluster_id.id
  project_key  = jira_project.ifos_quality.key
}

resource "jira_custom_field_option" "probability_screen" {
  custom_field = jira_custom_field.root_cause_probability.id
  project_key  = jira_project.ifos_quality.key
}

resource "jira_custom_field_option" "vin_count_screen" {
  custom_field = jira_custom_field.affected_vin_count.id
  project_key  = jira_project.ifos_quality.key
}

resource "jira_custom_field_option" "prep_stage_screen" {
  custom_field = jira_custom_field.recommended_prep_stage_adjustment.id
  project_key  = jira_project.ifos_quality.key
}

# ---------------------------------------------------------------------------
# Outputs — consumed by downstream services and CI verification
# ---------------------------------------------------------------------------

output "vault_secret_path" {
  value       = vault_kv_secret_v2.jira_quality_studio_client_secret.path
  description = "Vault KV v2 path where the Jira OAuth2 client secret is stored. Consumed by defect-genome via Vault Agent Injector."
}

output "jira_project_key" {
  value       = jira_project.ifos_quality.key
  description = "Jira project key for IFOS-QUALITY. Used by defect-genome to create Corrective Action issues."
}

output "jira_issue_type_id" {
  value       = jira_issue_type.corrective_action.id
  description = "Jira issue type ID for 'Corrective Action'. Passed by defect-genome when creating issues via the Jira REST API."
}

output "custom_field_cluster_id" {
  value       = jira_custom_field.defect_genome_cluster_id.id
  description = "Jira custom field ID for 'DefectGenome Cluster ID'."
}

output "custom_field_root_cause_probability" {
  value       = jira_custom_field.root_cause_probability.id
  description = "Jira custom field ID for 'Root Cause Probability'."
}

output "custom_field_affected_vin_count" {
  value       = jira_custom_field.affected_vin_count.id
  description = "Jira custom field ID for 'Affected VIN Count'."
}

output "custom_field_prep_stage_adjustment" {
  value       = jira_custom_field.recommended_prep_stage_adjustment.id
  description = "Jira custom field ID for 'Recommended Prep Stage Adjustment'."
}
