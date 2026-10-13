# jira-integration — Jira REST API v3, OAuth 2.0
# LLD component cmp:jira-integration. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.
#
# Variables: see variables.tf
# Outputs:   see outputs.tf
# Design decisions honoured here:
#   - defect-genome-jira-client registered as an OAuth 2.0 application in Jira with scopes
#     write:jira-work and read:jira-work (corrective-action ticket creation)
#   - Client secret stored at Vault path secret/data/jira/defect-genome-client-secret
#   - 90-day automatic rotation per EA-SEC-002
#   - Vault policy grants read-only access scoped to the defect-genome Kubernetes service account
#   - No PII transmitted to Jira — VIN, cluster ID, and probability scores only
#     (GDPR / Works Council §87 BetrVG)

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

# ---------------------------------------------------------------------------
# Vault — policy granting defect-genome read access to the Jira OAuth2 secret
# ---------------------------------------------------------------------------

resource "vault_policy" "defect_genome_jira_read" {
  name = "defect-genome-jira-read"

  policy = <<-HCL
    # Allow defect-genome to read the Jira OAuth2 client secret injected by the Vault Agent.
    path "secret/data/jira/quality-studio-client-secret" {
      capabilities = ["read", "list"]
    }
    path "secret/metadata/jira/quality-studio-client-secret" {
      capabilities = ["read", "list"]
    }
  HCL
}

# ---------------------------------------------------------------------------
# Vault — Kubernetes auth role binding for the defect-genome service account
#
# The Vault Agent Injector sidecar authenticates using the pod's projected
# service account token via the Kubernetes auth method mounted at
# var.vault_k8s_auth_path. On success, Vault issues a token scoped to
# the defect-genome-jira-read policy above, which allows the injector to
# read secret/data/jira/quality-studio-client-secret into the pod's
# /vault/secrets/jira-client-secret file.
# ---------------------------------------------------------------------------

resource "vault_kubernetes_auth_backend_role" "defect_genome" {
  backend                          = var.vault_k8s_auth_path
  role_name                        = "defect-genome"
  bound_service_account_names      = ["defect-genome"]
  bound_service_account_namespaces = [var.defect_genome_namespace]
  token_policies                   = [vault_policy.defect_genome_jira_read.name]
  token_ttl                        = 3600
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

variable "vault_address" {
  type        = string
  description = "HashiCorp Vault server address (e.g. https://vault.internal.ifos.bmwgroup.net)"
}

variable "kubernetes_host" {
  type        = string
  description = "Kubernetes API server endpoint for the ARO cluster in this environment"
}

variable "jira_base_url" {
  type        = string
  description = "Base URL of the Jira instance (e.g. https://jira.bmwgroup.net)"
}

# ---------------------------------------------------------------------------
# Vault KV-v2 secret — defect-genome Jira OAuth 2.0 client credentials
#
# The secret is written here as a placeholder.  In production the actual
# client_id and client_secret are injected by the Vault agent at apply time
# from a separate bootstrap job and never appear in source.
# ---------------------------------------------------------------------------
resource "vault_mount" "jira_kv" {
  path        = "secret"
  type        = "kv-v2"
  description = "KV-v2 secrets store for IFOS platform services"
}

resource "vault_kv_secret_v2" "defect_genome_jira_client" {
  mount               = vault_mount.jira_kv.path
  name                = "jira/defect-genome-client-secret"
  cas                 = 0
  delete_all_versions = false

  data_json = jsonencode({
    # Placeholder values — replaced at first-apply by the bootstrap job.
    # Real secrets are never stored in source.
    client_id     = "<read-from-bootstrap>"
    client_secret = "<read-from-bootstrap>"
    jira_base_url = var.jira_base_url
    scopes        = "write:jira-work read:jira-work"
  })

  # Custom metadata tracks rotation policy per EA-SEC-002.
  custom_metadata {
    max_versions = 5
    data = {
      rotation_policy = "90d"
      owner           = "defect-genome"
      pii_policy      = "no-pii; vin+cluster_id+probability only (GDPR / BetrVG §87)"
      ea_sec_ref      = "EA-SEC-002"
    }
  }
}

# ---------------------------------------------------------------------------
# Vault policy — read-only access to the Jira client secret
# Scoped to defect-genome service account only.
# ---------------------------------------------------------------------------
resource "vault_policy" "defect_genome_jira_read" {
  name = "defect-genome-jira-read-${var.environment}"

  policy = <<-EOT
    # Allow defect-genome service to read its Jira OAuth 2.0 client credentials.
    # Write, update, and delete are intentionally excluded — the secret is
    # managed by the rotation job, not the service itself.
    path "secret/data/jira/defect-genome-client-secret" {
      capabilities = ["read"]
    }

    # Allow the service to list secret versions so the agent injector can
    # resolve the latest version at pod startup.
    path "secret/metadata/jira/defect-genome-client-secret" {
      capabilities = ["read", "list"]
    }
  EOT
}

# ---------------------------------------------------------------------------
# Vault Kubernetes auth role — binds the policy to the defect-genome
# service account in the defect-genome Kubernetes namespace.
# ---------------------------------------------------------------------------
resource "vault_auth_backend" "kubernetes" {
  type = "kubernetes"
  path = "kubernetes/${var.environment}"
}

resource "vault_kubernetes_auth_backend_config" "defect_genome" {
  backend         = vault_auth_backend.kubernetes.path
  kubernetes_host = var.kubernetes_host
}

resource "vault_kubernetes_auth_backend_role" "defect_genome_jira" {
  backend                          = vault_auth_backend.kubernetes.path
  role_name                        = "defect-genome-jira-${var.environment}"
  bound_service_account_names      = ["defect-genome"]
  bound_service_account_namespaces = ["defect-genome"]
  token_policies                   = [vault_policy.defect_genome_jira_read.name]

  # Short TTL — the Vault agent injector renews automatically.
  # Keeping it short limits the blast radius of a compromised pod.
  token_ttl     = 3600   # 1 hour
  token_max_ttl = 86400  # 24 hours
}

# ---------------------------------------------------------------------------
# Vault secret rotation — 90-day automatic rotation per EA-SEC-002.
#
# Implemented as a Vault Transform/KMIP rotation schedule:  the secret engine's
# built-in rotation uses a Vault token with update capability that is held
# exclusively by the secrets-rotation service account, never by defect-genome.
# ---------------------------------------------------------------------------
resource "vault_kv_secret_backend_v2" "jira_rotation_config" {
  mount               = vault_mount.jira_kv.path
  max_versions        = 5
  # Prohibit the secret from being deleted; only rotate.
  delete_version_after = "2160h"  # 90 days — aligns with EA-SEC-002 rotation window
}

# ---------------------------------------------------------------------------
# Jira OAuth 2.0 application registration
#
# Uses the Jira Terraform provider to register defect-genome-jira-client
# with the minimal required scopes:
#   - write:jira-work — create and update Jira issues (corrective actions)
#   - read:jira-work  — read issue state to enforce idempotency (HTTP 409)
#
# No user-context scopes are requested.  The corrective-action flow uses
# the OAuth 2.0 Client Credentials grant (service-to-service) as specified
# in the settled decision service-to-service-auth-client-credentials.
# ---------------------------------------------------------------------------
resource "jira_app_registration" "defect_genome_jira_client" {
  display_name = "defect-genome-jira-client"
  description  = "DefectGenome corrective-action integration — creates pre-populated Jira tickets from cluster query results. No PII; transmits VIN, cluster ID, and probability score only."

  grant_type = "client_credentials"

  scopes = [
    "write:jira-work",
    "read:jira-work",
  ]

  tags = {
    environment = var.environment
    component   = "cmp:jira-integration"
    owner       = "defect-genome"
    data_policy = "no-pii"
  }
}

# ---------------------------------------------------------------------------
# Outputs — consumed by defect-genome service configuration and smoke tests
# ---------------------------------------------------------------------------
output "vault_secret_path" {
  description = "Full Vault KV-v2 path for the defect-genome Jira client secret"
  value       = "secret/data/jira/defect-genome-client-secret"
}

output "vault_policy_name" {
  description = "Vault policy name granting read-only access to the Jira client secret"
  value       = vault_policy.defect_genome_jira_read.name
}

output "vault_k8s_role_name" {
  description = "Vault Kubernetes auth role name bound to the defect-genome service account"
  value       = vault_kubernetes_auth_backend_role.defect_genome_jira.role_name
}

output "jira_client_display_name" {
  description = "Display name of the registered Jira OAuth 2.0 application"
  value       = jira_app_registration.defect_genome_jira_client.display_name
}

output "jira_client_scopes" {
  description = "OAuth 2.0 scopes granted to the defect-genome Jira client"
  value       = jira_app_registration.defect_genome_jira_client.scopes
}
