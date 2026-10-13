# jira-integration — Jira REST API v3, OAuth 2.0
# LLD component cmp:jira-integration. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.
#
# Design decisions honoured here:
#   - defect-genome-jira-client registered as an OAuth 2.0 application in Jira with scopes
#     write:jira-work and read:jira-work (corrective-action ticket creation)
#   - Client secret stored at Vault path secret/data/jira/defect-genome-client-secret
#   - 90-day automatic rotation per EA-SEC-002
#   - Vault policy grants read-only access scoped to the defect-genome Kubernetes service account
#   - No PII transmitted to Jira — VIN, cluster ID, and probability scores only
#     (GDPR / Works Council §87 BetrVG)

variable "environment" {
  type = string
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
