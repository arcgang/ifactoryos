# bmw-entra-id — Azure Active Directory / Entra ID, OIDC, OAuth 2.0
# LLD component cmp:bmw-entra-id. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

# ---------------------------------------------------------------------------
# Local values
# ---------------------------------------------------------------------------

locals {
  # Redirect URI hostnames per environment for each console application.
  redirect_uris = {
    "ifos-planning-cockpit" = {
      dev  = ["https://planning.dev.ifos.bmwgroup.net", "http://localhost:3000"]
      pp   = ["https://planning.pp.ifos.bmwgroup.net"]
      prod = ["https://planning.ifos.bmwgroup.net"]
    }
    "ifos-line-command" = {
      dev  = ["https://linecommand.dev.ifos.bmwgroup.net", "http://localhost:3001"]
      pp   = ["https://linecommand.pp.ifos.bmwgroup.net"]
      prod = ["https://linecommand.ifos.bmwgroup.net"]
    }
    "ifos-quality-studio" = {
      dev  = ["https://quality.dev.ifos.bmwgroup.net", "http://localhost:3002"]
      pp   = ["https://quality.pp.ifos.bmwgroup.net"]
      prod = ["https://quality.ifos.bmwgroup.net"]
    }
  }

  # Role-to-scope mapping — the scopes each security group's members receive.
  # Used to populate app role assignments and in the test harness.
  role_scopes = {
    "GRP-IFOS-PLANNERS-MUC"      = ["sequence.read", "sequence.write", "sequence.override", "constraint.read"]
    "GRP-IFOS-SHIFTLEADS-MUC"    = ["oee.read", "andon.read", "andon.dispatch", "handover.write"]
    "GRP-IFOS-ASSOCIATES-MUC"    = ["ar.instructions.read", "torque.write"]
    "GRP-IFOS-PAINTENGINEER-MUC" = ["defect.read", "replay.read", "defectgenome.query"]
    "GRP-IFOS-VALIDATORS-MUC"    = ["gate.read", "certificate.issue", "certificate.override"]
    "GRP-IFOS-PLATFORM-ADMIN"    = ["sequence.read", "sequence.write", "sequence.override", "constraint.read", "oee.read", "andon.read", "andon.dispatch", "handover.write", "ar.instructions.read", "torque.write", "defect.read", "replay.read", "defectgenome.query", "gate.read", "certificate.issue", "certificate.override"]
  }
}

# ---------------------------------------------------------------------------
# Security Groups (§8.4.2)
# ---------------------------------------------------------------------------

resource "azuread_group" "ifos_planners" {
  display_name     = "GRP-IFOS-PLANNERS-MUC"
  security_enabled = true
  description      = "Production Planners — access to Planning Cockpit only"
}

resource "azuread_group" "ifos_shiftleads" {
  display_name     = "GRP-IFOS-SHIFTLEADS-MUC"
  security_enabled = true
  description      = "Shift Leads — access to Line Command only"
}

resource "azuread_group" "ifos_associates" {
  display_name     = "GRP-IFOS-ASSOCIATES-MUC"
  security_enabled = true
  description      = "Assembly Associates — access to Line Command only"
}

resource "azuread_group" "ifos_paint_engineer" {
  display_name     = "GRP-IFOS-PAINTENGINEER-MUC"
  security_enabled = true
  description      = "Paint Process Engineers — access to Quality Studio only"
}

resource "azuread_group" "ifos_validators" {
  display_name     = "GRP-IFOS-VALIDATORS-MUC"
  security_enabled = true
  description      = "Quality Gate Validators — access to Quality Studio only"
}

resource "azuread_group" "ifos_platform_admin" {
  display_name     = "GRP-IFOS-PLATFORM-ADMIN"
  security_enabled = true
  description      = "Platform administrators — full cross-console access"
}

# ---------------------------------------------------------------------------
# App Registrations — one per console
# ---------------------------------------------------------------------------

# ifos-planning-cockpit
resource "azuread_application" "planning_cockpit" {
  display_name = "ifos-planning-cockpit"

  web {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-planning-cockpit"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-planning-cockpit"]["pp"] : local.redirect_uris["ifos-planning-cockpit"]["prod"]
    )

    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = false
    }
  }

  # PKCE — public client must be true to allow Auth Code + PKCE without a client secret.
  public_client {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-planning-cockpit"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-planning-cockpit"]["pp"] : local.redirect_uris["ifos-planning-cockpit"]["prod"]
    )
  }

  api {
    # Token version 2 emits scp claim; required for OBO flow.
    requested_access_token_version = 2

    dynamic "oauth2_permission_scope" {
      for_each = toset(["sequence.read", "sequence.write", "sequence.override", "constraint.read"])
      content {
        admin_consent_description  = "Allows ${oauth2_permission_scope.value} on Planning Cockpit resources"
        admin_consent_display_name = oauth2_permission_scope.value
        enabled                    = true
        id                         = uuidv5("6ba7b810-9dad-11d1-80b4-00c04fd430c8", "ifos-planning-cockpit.${oauth2_permission_scope.value}")
        type                       = "User"
        value                      = oauth2_permission_scope.value
      }
    }
  }

  tags = ["ifos", var.environment]
}

resource "azuread_service_principal" "planning_cockpit" {
  client_id = azuread_application.planning_cockpit.client_id
}

# ifos-line-command
resource "azuread_application" "line_command" {
  display_name = "ifos-line-command"

  web {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-line-command"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-line-command"]["pp"] : local.redirect_uris["ifos-line-command"]["prod"]
    )

    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = false
    }
  }

  public_client {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-line-command"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-line-command"]["pp"] : local.redirect_uris["ifos-line-command"]["prod"]
    )
  }

  api {
    requested_access_token_version = 2

    dynamic "oauth2_permission_scope" {
      for_each = toset(["oee.read", "andon.read", "andon.dispatch", "handover.write", "ar.instructions.read", "torque.write"])
      content {
        admin_consent_description  = "Allows ${oauth2_permission_scope.value} on Line Command resources"
        admin_consent_display_name = oauth2_permission_scope.value
        enabled                    = true
        id                         = uuidv5("6ba7b810-9dad-11d1-80b4-00c04fd430c8", "ifos-line-command.${oauth2_permission_scope.value}")
        type                       = "User"
        value                      = oauth2_permission_scope.value
      }
    }
  }

  tags = ["ifos", var.environment]
}

resource "azuread_service_principal" "line_command" {
  client_id = azuread_application.line_command.client_id
}

# ifos-quality-studio
resource "azuread_application" "quality_studio" {
  display_name = "ifos-quality-studio"

  web {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-quality-studio"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-quality-studio"]["pp"] : local.redirect_uris["ifos-quality-studio"]["prod"]
    )

    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = false
    }
  }

  public_client {
    redirect_uris = contains(["dev"], var.environment) ? local.redirect_uris["ifos-quality-studio"]["dev"] : (
      var.environment == "pp" ? local.redirect_uris["ifos-quality-studio"]["pp"] : local.redirect_uris["ifos-quality-studio"]["prod"]
    )
  }

  api {
    requested_access_token_version = 2

    dynamic "oauth2_permission_scope" {
      for_each = toset(["defect.read", "replay.read", "defectgenome.query", "gate.read", "certificate.issue", "certificate.override"])
      content {
        admin_consent_description  = "Allows ${oauth2_permission_scope.value} on Quality Studio resources"
        admin_consent_display_name = oauth2_permission_scope.value
        enabled                    = true
        id                         = uuidv5("6ba7b810-9dad-11d1-80b4-00c04fd430c8", "ifos-quality-studio.${oauth2_permission_scope.value}")
        type                       = "User"
        value                      = oauth2_permission_scope.value
      }
    }
  }

  tags = ["ifos", var.environment]
}

resource "azuread_service_principal" "quality_studio" {
  client_id = azuread_application.quality_studio.client_id
}

# ---------------------------------------------------------------------------
# Conditional Access Policy — MFA + device compliance for all IFOS sessions
# ---------------------------------------------------------------------------

# 'compliantDevice' built-in control enforces Intune compliance for all targeted
# app sessions including HoloLens 2 (Windows Holographic) and iPad Pro (iOS).
# BMW Group IT must create the Intune compliance policy named
# 'IFOS-AR-Device-Compliance' (min requirements: OS ≥ HoloLens 22H2 / iPadOS 16,
# device encryption enabled, Intune-managed, no jailbreak) before this CA policy
# can be satisfied at sign-in.

resource "azuread_conditional_access_policy" "ifos_mfa_device_compliance" {
  display_name = "IFOS-MFA-DeviceCompliance-${var.environment}"
  state        = "enabled"

  conditions {
    applications {
      included_applications = [
        azuread_service_principal.planning_cockpit.client_id,
        azuread_service_principal.line_command.client_id,
        azuread_service_principal.quality_studio.client_id,
      ]
    }

    users {
      included_groups = [
        azuread_group.ifos_planners.object_id,
        azuread_group.ifos_shiftleads.object_id,
        azuread_group.ifos_associates.object_id,
        azuread_group.ifos_paint_engineer.object_id,
        azuread_group.ifos_validators.object_id,
        azuread_group.ifos_platform_admin.object_id,
      ]
    }

    # HoloLens 2 (Windows Holographic) and iPad Pro (iOS) require device compliance.
    platforms {
      included_platforms = ["all"]
    }

    client_app_types = ["browser", "mobileAppsAndDesktopClients"]
  }

  grant_controls {
    operator = "AND"
    built_in_controls = [
      "mfa",
      "compliantDevice",
    ]
  }
}

# ---------------------------------------------------------------------------
# Group membership claim configuration
# Groups claim emitted in JWT so API Connect can introspect roles within ≤5 min.
# Token configuration is set on the application objects above; the 'groupMembershipClaims'
# property is set to 'SecurityGroup' so every security group object ID flows into
# the groups claim of issued tokens.
# ---------------------------------------------------------------------------

resource "azuread_application_optional_claims" "planning_cockpit_groups" {
  application_id = azuread_application.planning_cockpit.id

  access_token {
    name = "groups"
  }

  id_token {
    name = "groups"
  }
}

resource "azuread_application_optional_claims" "line_command_groups" {
  application_id = azuread_application.line_command.id

  access_token {
    name = "groups"
  }

  id_token {
    name = "groups"
  }
}

resource "azuread_application_optional_claims" "quality_studio_groups" {
  application_id = azuread_application.quality_studio.id

  access_token {
    name = "groups"
  }

  id_token {
    name = "groups"
  }
}

# ---------------------------------------------------------------------------
# Pseudonymous token claim mapping — GDPR US-028 / BR-13
#
# Analytics-path audit log user_id must carry the pseudonymous_token UUID,
# not the raw employee_id (oid claim).  The flow is:
#
#   Entra ID JWT (oid=<employee_id>) → identity-service
#     GET /internal/identity/pseudonymise?employee_id=<oid>
#     → { pseudonymous_token: "<uuid>" }
#   Caller uses pseudonymous_token as user_id in audit writes and Neo4j nodes.
#
# The oid claim is emitted automatically by Entra ID v2 tokens for all three
# console applications; no additional optional claim is required.
# Mapping is documented as a module output so the api-gateway module can
# reference it in DataPower policy configuration.
#
# Variable reference: see .env.example for TF_VAR_entra_tenant_id.
# ---------------------------------------------------------------------------

variable "entra_tenant_id" {
  type        = string
  description = "Azure Entra ID tenant ID — used to document the oid claim source"
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "group_object_ids" {
  description = "Entra ID object IDs for the six IFOS security groups"
  value = {
    planners       = azuread_group.ifos_planners.object_id
    shiftleads     = azuread_group.ifos_shiftleads.object_id
    associates     = azuread_group.ifos_associates.object_id
    paint_engineer = azuread_group.ifos_paint_engineer.object_id
    validators     = azuread_group.ifos_validators.object_id
    platform_admin = azuread_group.ifos_platform_admin.object_id
  }
}

output "app_client_ids" {
  description = "Client IDs for the three IFOS app registrations"
  value = {
    planning_cockpit = azuread_application.planning_cockpit.client_id
    line_command     = azuread_application.line_command.client_id
    quality_studio   = azuread_application.quality_studio.client_id
  }
}

output "role_scope_map" {
  description = "Authoritative mapping of security group display name to OAuth scopes — used by api-gateway and tests"
  value       = local.role_scopes
}

output "pseudonymous_token_claim_mapping" {
  description = <<-EOT
    Documents the GDPR claim mapping for analytics/audit user_id fields (US-028/BR-13).
    Raw employee IDs (oid claim, 8-digit numeric) must be resolved to pseudonymous_token
    via identity-service GET /internal/identity/pseudonymise before any audit or VIN write.
    Only the pseudonymous_token UUID may appear in exported datasets or audit blobs.
  EOT
  value = {
    source_claim         = "oid"
    source_description   = "Entra ID v2 object ID — maps to 8-digit numeric employee ID"
    resolved_field       = "pseudonymous_token"
    resolved_description = "UUID from identity_pseudonymisation_map; stable after Article 17 scrub"
    resolution_endpoint  = "GET http://identity-service.ifos-identity.svc.cluster.local:8080/internal/identity/pseudonymise"
    audit_field          = "user_id"
    rejection_pattern    = "^\\d{8}$"
    rejection_error      = "EmployeeIdRejected — raw numeric IDs rejected by audit_client SDK"
  }
}
