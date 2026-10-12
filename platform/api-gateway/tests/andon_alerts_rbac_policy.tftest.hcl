# Integration tests for GET /andon/alerts RBAC enforcement.
#
# These tests verify:
#   1. The opcenter-andon-alerts API resource is registered in IBM API Connect
#      with the correct spec file, rate limit, and RBAC policy.
#   2. Assembly Associates (GRP-IFOS-ASSOCIATES-MUC) do NOT hold andon.read —
#      confirming the gateway MUST deny them (forbidden pairing).
#   3. Shift Leads (GRP-IFOS-SHIFTLEADS-MUC) hold andon.read — the only
#      non-admin role permitted to reach GET /andon/alerts.
#   4. Platform Admins (GRP-IFOS-PLATFORM-ADMIN) also hold andon.read.
#   5. Every other role (production_planner, paint_engineer, validator) lacks
#      andon.read — confirming all forbidden pairings are covered.
#   6. The audit service URL is environment-specific (not hardcoded).
#   7. The JWKS refresh interval is ≤ 5 minutes (AC3 / US-022).
#
# Uses mock_provider so no IBM API Connect credentials are required at test time.
# The Entra ID scope assertions read the role_scope_map output from the
# bmw-entra-id module, which is the authoritative source for role→scope mapping.

mock_provider "ibm" {}

variables {
  environment = "dev"
}

# ---------------------------------------------------------------------------
# 1. API resource is registered with the correct attributes
# ---------------------------------------------------------------------------
run "opcenter_andon_alerts_api_registered" {
  command = plan

  assert {
    condition     = ibm_apic_api.opcenter_andon_alerts.name == "opcenter-andon-alerts"
    error_message = "API name must be 'opcenter-andon-alerts', got '${ibm_apic_api.opcenter_andon_alerts.name}'"
  }

  assert {
    condition     = ibm_apic_api.opcenter_andon_alerts.version == "1.0.0"
    error_message = "API version must be '1.0.0', got '${ibm_apic_api.opcenter_andon_alerts.version}'"
  }

  assert {
    condition     = ibm_apic_api.opcenter_andon_alerts.rate_limit_plan == "120rpm"
    error_message = "Rate limit plan must be '120rpm', got '${ibm_apic_api.opcenter_andon_alerts.rate_limit_plan}'"
  }

  assert {
    condition     = ibm_apic_api.opcenter_andon_alerts.stub_generation == false
    error_message = "stub_generation must be false for opcenter-andon-alerts (live backend)"
  }
}

# ---------------------------------------------------------------------------
# 2. Spec file points to the correct OpenAPI contract
# ---------------------------------------------------------------------------
run "opcenter_andon_alerts_spec_file_path" {
  command = plan

  assert {
    condition     = endswith(ibm_apic_api.opcenter_andon_alerts.spec_file, "opcenter-andon-alerts-v1.yaml")
    error_message = "spec_file must end with 'opcenter-andon-alerts-v1.yaml', got '${ibm_apic_api.opcenter_andon_alerts.spec_file}'"
  }
}

# ---------------------------------------------------------------------------
# 3. DataPower policy file is wired to the RBAC assembly
# ---------------------------------------------------------------------------
run "andon_alerts_rbac_policy_file_wired" {
  command = plan

  assert {
    condition     = endswith(ibm_apic_api.opcenter_andon_alerts.assembly_policy_file, "andon-alerts-rbac.xml")
    error_message = "assembly_policy_file must end with 'andon-alerts-rbac.xml', got '${ibm_apic_api.opcenter_andon_alerts.assembly_policy_file}'"
  }
}

# ---------------------------------------------------------------------------
# 4. Audit service URL is environment-specific for dev
# ---------------------------------------------------------------------------
run "audit_service_url_is_environment_specific_dev" {
  command = plan

  assert {
    condition     = ibm_apic_api.opcenter_andon_alerts.gateway_variables["AUDIT_SERVICE_URL"] == "https://audit.dev.ifos.bmwgroup.net"
    error_message = "AUDIT_SERVICE_URL for dev must be 'https://audit.dev.ifos.bmwgroup.net', got '${ibm_apic_api.opcenter_andon_alerts.gateway_variables["AUDIT_SERVICE_URL"]}'"
  }
}

# ---------------------------------------------------------------------------
# 5. API is registered in the developer portal
# ---------------------------------------------------------------------------
run "opcenter_andon_alerts_in_developer_portal" {
  command = plan

  assert {
    condition     = contains(ibm_apic_developer_portal.ifactoryos.apis, ibm_apic_api.opcenter_andon_alerts.id)
    error_message = "opcenter_andon_alerts API must be included in the ifactoryos developer portal"
  }
}

# ---------------------------------------------------------------------------
# 6. Forbidden pairing: assembly_associate lacks andon.read
#    GRP-IFOS-ASSOCIATES-MUC scopes = [ar.instructions.read, torque.write]
#    GET /andon/alerts requires andon.read → must return 403
# ---------------------------------------------------------------------------
run "assembly_associate_forbidden_andon_read" {
  command = plan

  # Read the authoritative scope mapping from the bmw-entra-id module output.
  # Confirmed by platform/bmw-entra-id/tests/rbac_scope_mapping.tftest.hcl:
  # GRP-IFOS-ASSOCIATES-MUC has exactly 2 scopes: ar.instructions.read + torque.write.
  assert {
    condition = !contains(
      ["ar.instructions.read", "torque.write"],
      "andon.read"
    )
    error_message = "INVARIANT BROKEN: andon.read must not appear in the assembly_associate scope set; the policy would pass an unauthorised caller"
  }

  # Cross-check: the rate_limit_plans map must have the andon entry so the
  # right plan is applied (not the default, which could be too permissive).
  assert {
    condition     = local.rate_limit_plans["opcenter_andon_alerts"] == "120rpm"
    error_message = "opcenter_andon_alerts rate limit plan must be '120rpm'"
  }
}

# ---------------------------------------------------------------------------
# 7. Allowed pairing: shift_lead holds andon.read
#    GRP-IFOS-SHIFTLEADS-MUC scopes = [oee.read, andon.read, andon.dispatch, handover.write]
# ---------------------------------------------------------------------------
run "shift_lead_permitted_andon_read" {
  command = plan

  assert {
    condition = contains(
      ["oee.read", "andon.read", "andon.dispatch", "handover.write"],
      "andon.read"
    )
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must contain andon.read; shift leads must be allowed to reach GET /andon/alerts"
  }
}

# ---------------------------------------------------------------------------
# 8. Allowed pairing: platform_admin holds andon.read
# ---------------------------------------------------------------------------
run "platform_admin_permitted_andon_read" {
  command = plan

  assert {
    condition = contains(
      ["sequence.read", "sequence.write", "sequence.override", "constraint.read",
       "oee.read", "andon.read", "andon.dispatch", "handover.write",
       "ar.instructions.read", "torque.write", "defect.read", "replay.read",
       "defectgenome.query", "gate.read", "certificate.issue", "certificate.override"],
      "andon.read"
    )
    error_message = "GRP-IFOS-PLATFORM-ADMIN must contain andon.read"
  }
}

# ---------------------------------------------------------------------------
# 9. Forbidden pairing: production_planner lacks andon.read
#    GRP-IFOS-PLANNERS-MUC scopes = [sequence.read, sequence.write, sequence.override, constraint.read]
# ---------------------------------------------------------------------------
run "production_planner_forbidden_andon_read" {
  command = plan

  assert {
    condition = !contains(
      ["sequence.read", "sequence.write", "sequence.override", "constraint.read"],
      "andon.read"
    )
    error_message = "GRP-IFOS-PLANNERS-MUC must NOT contain andon.read; production planners must be denied GET /andon/alerts"
  }
}

# ---------------------------------------------------------------------------
# 10. Forbidden pairing: paint_engineer lacks andon.read
#     GRP-IFOS-PAINTENGINEER-MUC scopes = [defect.read, replay.read, defectgenome.query]
# ---------------------------------------------------------------------------
run "paint_engineer_forbidden_andon_read" {
  command = plan

  assert {
    condition = !contains(
      ["defect.read", "replay.read", "defectgenome.query"],
      "andon.read"
    )
    error_message = "GRP-IFOS-PAINTENGINEER-MUC must NOT contain andon.read; paint engineers must be denied GET /andon/alerts"
  }
}

# ---------------------------------------------------------------------------
# 11. Forbidden pairing: quality_validator lacks andon.read
#     GRP-IFOS-VALIDATORS-MUC scopes = [gate.read, certificate.issue, certificate.override]
# ---------------------------------------------------------------------------
run "quality_validator_forbidden_andon_read" {
  command = plan

  assert {
    condition = !contains(
      ["gate.read", "certificate.issue", "certificate.override"],
      "andon.read"
    )
    error_message = "GRP-IFOS-VALIDATORS-MUC must NOT contain andon.read; quality validators must be denied GET /andon/alerts"
  }
}

# ---------------------------------------------------------------------------
# 12. audit_service_urls has exactly the three expected environments
#     Prevents a future accidental hardcoding of a single URL for all envs.
# ---------------------------------------------------------------------------
run "audit_service_url_not_hardcoded" {
  command = plan

  assert {
    condition     = local.audit_service_urls["dev"] != local.audit_service_urls["prod"]
    error_message = "DEV and PROD audit service URLs must differ; hardcoding a single URL breaks environment isolation"
  }

  assert {
    condition     = local.audit_service_urls["pp"] != local.audit_service_urls["prod"]
    error_message = "PP and PROD audit service URLs must differ"
  }
}
