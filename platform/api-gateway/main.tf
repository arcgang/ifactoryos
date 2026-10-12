# api-gateway — IBM API Connect, IBM DataPower Gateway, OAuth 2.0 / OIDC
# LLD component cmp:api-gateway. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

variable "entra_tenant_id" {
  type        = string
  description = "Azure / Entra ID tenant ID; used to construct the JWKS and issuer URLs"
}

variable "line_command_client_id" {
  type        = string
  description = "Client ID of the ifos-line-command app registration (from bmw-entra-id output)"
}

variable "planning_cockpit_client_id" {
  type        = string
  description = "Client ID of the ifos-planning-cockpit app registration (from bmw-entra-id output)"
}

variable "quality_studio_client_id" {
  type        = string
  description = "Client ID of the ifos-quality-studio app registration (from bmw-entra-id output)"
}

variable "audit_log_endpoint" {
  type        = string
  description = "Base URL of the immutable-audit-log service (e.g. https://audit.internal.ifos)"
}

# Security group object IDs — received as outputs from the bmw-entra-id module.
# The gateway loads them as properties so DataPower can compare JWT groups claims at runtime.
variable "grp_planners_id" {
  type        = string
  description = "Object ID of GRP-IFOS-PLANNERS-MUC"
}

variable "grp_shiftleads_id" {
  type        = string
  description = "Object ID of GRP-IFOS-SHIFTLEADS-MUC"
}

variable "grp_associates_id" {
  type        = string
  description = "Object ID of GRP-IFOS-ASSOCIATES-MUC"
}

variable "grp_paintengineer_id" {
  type        = string
  description = "Object ID of GRP-IFOS-PAINTENGINEER-MUC"
}

variable "grp_validators_id" {
  type        = string
  description = "Object ID of GRP-IFOS-VALIDATORS-MUC"
}

variable "grp_platform_admin_id" {
  type        = string
  description = "Object ID of GRP-IFOS-PLATFORM-ADMIN"
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  # Entra ID v2 JWKS endpoint; DataPower refreshes this key set every 300 seconds
  # so group assignment changes propagate within 5 minutes (AC3 / NFR-06).
  jwks_endpoint = "https://login.microsoftonline.com/${var.entra_tenant_id}/discovery/v2.0/keys"

  # Token issuer for each console application — Entra ID v2 issuer.
  entra_issuer = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"

  # Gateway properties passed to DataPower at deploy time.
  # Group IDs and endpoint URLs are injected here; secrets via Vault agent injector.
  gateway_properties = {
    "jwks-endpoint"          = local.jwks_endpoint
    "jwks-refresh-interval"  = "300"
    "entra-issuer"           = local.entra_issuer
    "entra-audience"         = var.line_command_client_id
    "audit-log-endpoint"     = var.audit_log_endpoint
    "grp-planners-id"        = var.grp_planners_id
    "grp-shiftleads-id"      = var.grp_shiftleads_id
    "grp-associates-id"      = var.grp_associates_id
    "grp-paintengineer-id"   = var.grp_paintengineer_id
    "grp-validators-id"      = var.grp_validators_id
    "grp-platform-admin-id"  = var.grp_platform_admin_id
  }

  # Permitted console → group IDs mapping; used in tests to assert coverage.
  console_access_map = {
    "planning-cockpit" = [var.grp_planners_id, var.grp_platform_admin_id]
    "line-command"     = [var.grp_shiftleads_id, var.grp_associates_id, var.grp_platform_admin_id]
    "quality-studio"   = [var.grp_paintengineer_id, var.grp_validators_id, var.grp_platform_admin_id]
  }

  # Within /line-command/** — endpoints accessible only to assembly-associates.
  associate_allowed_endpoints = [
    "GET /line-command/api/v1/ar/instructions/{vin}",
    "POST /line-command/api/v1/torque/log",
  ]

  # Within /line-command/** — endpoints DENIED to assembly-associates (403 + audit write).
  associate_denied_endpoints = [
    "GET /line-command/oee/snapshots",
    "GET /line-command/andon/alerts",
    "POST /line-command/alerts/{alertId}/acknowledge",
    "GET /line-command/api/v1/handover",
    "POST /line-command/api/v1/handover",
  ]
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "jwks_endpoint" {
  description = "Entra ID JWKS endpoint DataPower uses for JWT signature validation"
  value       = local.jwks_endpoint
}

output "entra_issuer" {
  description = "Entra ID v2 issuer claim expected in every validated JWT"
  value       = local.entra_issuer
}

output "console_access_map" {
  description = "Console path prefix → list of permitted Entra ID group object IDs"
  value       = local.console_access_map
}

output "associate_allowed_endpoints" {
  description = "Endpoints assembly-associates may reach within /line-command/**"
  value       = local.associate_allowed_endpoints
}

output "associate_denied_endpoints" {
  description = "Endpoints assembly-associates are forbidden from within /line-command/**"
  value       = local.associate_denied_endpoints
}

output "gateway_properties" {
  description = "Gateway properties map injected into DataPower at deploy time"
  value       = local.gateway_properties
  sensitive   = false
}
