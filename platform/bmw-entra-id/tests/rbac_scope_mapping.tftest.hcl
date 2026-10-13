# Verifies that each IFOS security group resolves to the correct OAuth scope set.
# Uses mock_provider so no Azure or PostgreSQL credentials are required; the test
# confirms the role_scope_map output matches the §5.1.3.1 contract exactly.

mock_provider "azuread" {}
mock_provider "postgresql" {}

variables {
  environment = "dev"

  # Entra ID tenant — placeholder value; plan-only test, not applied
  entra_tenant_id = "00000000-0000-0000-0000-000000000000"

  # Identity DB — placeholder values; plan-only test, not applied
  identity_db_host               = "citus-dev.internal.ifos.bmwgroup.net"
  identity_db_superuser          = "postgres"
  identity_db_superuser_password = "placeholder"
  identity_service_db_password   = "placeholder"
}

# ---------------------------------------------------------------------------
# Production Planner scopes
# ---------------------------------------------------------------------------
run "planners_group_has_correct_scopes" {
  command = plan

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"], "sequence.read")
    error_message = "GRP-IFOS-PLANNERS-MUC must include scope sequence.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"], "sequence.write")
    error_message = "GRP-IFOS-PLANNERS-MUC must include scope sequence.write"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"], "sequence.override")
    error_message = "GRP-IFOS-PLANNERS-MUC must include scope sequence.override"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"], "constraint.read")
    error_message = "GRP-IFOS-PLANNERS-MUC must include scope constraint.read"
  }

  assert {
    condition     = length(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"]) == 4
    error_message = "GRP-IFOS-PLANNERS-MUC must have exactly 4 scopes, got ${length(output.role_scope_map["GRP-IFOS-PLANNERS-MUC"])}"
  }
}

# ---------------------------------------------------------------------------
# Shift Lead scopes
# ---------------------------------------------------------------------------
run "shiftleads_group_has_correct_scopes" {
  command = plan

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"], "oee.read")
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must include scope oee.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"], "andon.read")
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must include scope andon.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"], "andon.dispatch")
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must include scope andon.dispatch"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"], "handover.write")
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must include scope handover.write"
  }

  assert {
    condition     = length(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"]) == 4
    error_message = "GRP-IFOS-SHIFTLEADS-MUC must have exactly 4 scopes, got ${length(output.role_scope_map["GRP-IFOS-SHIFTLEADS-MUC"])}"
  }
}

# ---------------------------------------------------------------------------
# Assembly Associate scopes
# ---------------------------------------------------------------------------
run "associates_group_has_correct_scopes" {
  command = plan

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-ASSOCIATES-MUC"], "ar.instructions.read")
    error_message = "GRP-IFOS-ASSOCIATES-MUC must include scope ar.instructions.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-ASSOCIATES-MUC"], "torque.write")
    error_message = "GRP-IFOS-ASSOCIATES-MUC must include scope torque.write"
  }

  assert {
    condition     = length(output.role_scope_map["GRP-IFOS-ASSOCIATES-MUC"]) == 2
    error_message = "GRP-IFOS-ASSOCIATES-MUC must have exactly 2 scopes, got ${length(output.role_scope_map["GRP-IFOS-ASSOCIATES-MUC"])}"
  }
}

# ---------------------------------------------------------------------------
# Paint Process Engineer scopes
# ---------------------------------------------------------------------------
run "paint_engineer_group_has_correct_scopes" {
  command = plan

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PAINTENGINEER-MUC"], "defect.read")
    error_message = "GRP-IFOS-PAINTENGINEER-MUC must include scope defect.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PAINTENGINEER-MUC"], "replay.read")
    error_message = "GRP-IFOS-PAINTENGINEER-MUC must include scope replay.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-PAINTENGINEER-MUC"], "defectgenome.query")
    error_message = "GRP-IFOS-PAINTENGINEER-MUC must include scope defectgenome.query"
  }

  assert {
    condition     = length(output.role_scope_map["GRP-IFOS-PAINTENGINEER-MUC"]) == 3
    error_message = "GRP-IFOS-PAINTENGINEER-MUC must have exactly 3 scopes, got ${length(output.role_scope_map["GRP-IFOS-PAINTENGINEER-MUC"])}"
  }
}

# ---------------------------------------------------------------------------
# Quality Gate Validator scopes
# ---------------------------------------------------------------------------
run "validators_group_has_correct_scopes" {
  command = plan

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-VALIDATORS-MUC"], "gate.read")
    error_message = "GRP-IFOS-VALIDATORS-MUC must include scope gate.read"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-VALIDATORS-MUC"], "certificate.issue")
    error_message = "GRP-IFOS-VALIDATORS-MUC must include scope certificate.issue"
  }

  assert {
    condition     = contains(output.role_scope_map["GRP-IFOS-VALIDATORS-MUC"], "certificate.override")
    error_message = "GRP-IFOS-VALIDATORS-MUC must include scope certificate.override"
  }

  assert {
    condition     = length(output.role_scope_map["GRP-IFOS-VALIDATORS-MUC"]) == 3
    error_message = "GRP-IFOS-VALIDATORS-MUC must have exactly 3 scopes, got ${length(output.role_scope_map["GRP-IFOS-VALIDATORS-MUC"])}"
  }
}

# ---------------------------------------------------------------------------
# All six groups are provisioned
# ---------------------------------------------------------------------------
run "all_six_security_groups_exist" {
  command = plan

  assert {
    condition     = length(keys(output.role_scope_map)) == 6
    error_message = "Expected exactly 6 security group entries in role_scope_map, got ${length(keys(output.role_scope_map))}"
  }

  assert {
    condition = alltrue([
      contains(keys(output.role_scope_map), "GRP-IFOS-PLANNERS-MUC"),
      contains(keys(output.role_scope_map), "GRP-IFOS-SHIFTLEADS-MUC"),
      contains(keys(output.role_scope_map), "GRP-IFOS-ASSOCIATES-MUC"),
      contains(keys(output.role_scope_map), "GRP-IFOS-PAINTENGINEER-MUC"),
      contains(keys(output.role_scope_map), "GRP-IFOS-VALIDATORS-MUC"),
      contains(keys(output.role_scope_map), "GRP-IFOS-PLATFORM-ADMIN"),
    ])
    error_message = "One or more expected IFOS security groups are missing from role_scope_map"
  }
}

# ---------------------------------------------------------------------------
# GDPR claim mapping output — pseudonymous_token replaces raw employee_id
# ---------------------------------------------------------------------------
run "pseudonymous_token_claim_mapping_is_documented" {
  command = plan

  assert {
    condition     = output.pseudonymous_token_claim_mapping["source_claim"] == "oid"
    error_message = "source_claim must be 'oid' (Entra ID v2 object ID claim)"
  }

  assert {
    condition     = output.pseudonymous_token_claim_mapping["resolved_field"] == "pseudonymous_token"
    error_message = "resolved_field must be 'pseudonymous_token'"
  }

  assert {
    condition     = output.pseudonymous_token_claim_mapping["audit_field"] == "user_id"
    error_message = "audit_field must be 'user_id' — the field name used in audit_client.write_event"
  }

  assert {
    condition     = output.pseudonymous_token_claim_mapping["rejection_pattern"] == "^\\d{8}$"
    error_message = "rejection_pattern must match the 8-digit employee ID pattern rejected by audit_client SDK"
  }
}
