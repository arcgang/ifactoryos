# Integration tests for platform/api-gateway RBAC policy configuration.
# Verifies every forbidden role-console pairing (§5.1.3, §8.4.2) and the
# within-/line-command/** assembly-associate endpoint boundary (AC3, US-022).
# Uses mock_provider so no IBM API Connect or Azure credentials are required.

mock_provider "local" {}

variables {
  environment                = "dev"
  entra_tenant_id            = "00000000-0000-0000-0000-000000000001"
  line_command_client_id     = "00000000-0000-0000-0000-000000000010"
  planning_cockpit_client_id = "00000000-0000-0000-0000-000000000011"
  quality_studio_client_id   = "00000000-0000-0000-0000-000000000012"
  audit_log_endpoint         = "https://audit.internal.ifos.example"
  grp_planners_id            = "aaa00000-0000-0000-0000-000000000001"
  grp_shiftleads_id          = "aaa00000-0000-0000-0000-000000000002"
  grp_associates_id          = "aaa00000-0000-0000-0000-000000000003"
  grp_paintengineer_id       = "aaa00000-0000-0000-0000-000000000004"
  grp_validators_id          = "aaa00000-0000-0000-0000-000000000005"
  grp_platform_admin_id      = "aaa00000-0000-0000-0000-000000000006"
}

# ---------------------------------------------------------------------------
# JWKS endpoint and JWKS refresh configuration
# ---------------------------------------------------------------------------
run "jwks_endpoint_is_entra_v2_keys_url" {
  command = plan

  assert {
    condition     = output.jwks_endpoint == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000001/discovery/v2.0/keys"
    error_message = "jwks_endpoint must be the Entra ID v2 JWKS URL for the given tenant"
  }
}

run "entra_issuer_is_v2_issuer" {
  command = plan

  assert {
    condition     = output.entra_issuer == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000001/v2.0"
    error_message = "entra_issuer must be the Entra ID v2 issuer for the given tenant"
  }
}

run "jwks_refresh_interval_is_300_seconds" {
  command = plan

  assert {
    condition     = output.gateway_properties["jwks-refresh-interval"] == "300"
    error_message = "jwks-refresh-interval must be 300 seconds to satisfy AC3 (role propagation within 5 minutes)"
  }
}

# ---------------------------------------------------------------------------
# Console access map — correct groups per console
# ---------------------------------------------------------------------------
run "planning_cockpit_permits_planners_only" {
  command = plan

  assert {
    condition     = contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000001")
    error_message = "planning-cockpit must permit GRP-IFOS-PLANNERS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000002")
    error_message = "planning-cockpit must NOT permit GRP-IFOS-SHIFTLEADS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000003")
    error_message = "planning-cockpit must NOT permit GRP-IFOS-ASSOCIATES-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000004")
    error_message = "planning-cockpit must NOT permit GRP-IFOS-PAINTENGINEER-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000005")
    error_message = "planning-cockpit must NOT permit GRP-IFOS-VALIDATORS-MUC"
  }

  assert {
    condition     = contains(output.console_access_map["planning-cockpit"], "aaa00000-0000-0000-0000-000000000006")
    error_message = "planning-cockpit must permit GRP-IFOS-PLATFORM-ADMIN"
  }

  assert {
    condition     = length(output.console_access_map["planning-cockpit"]) == 2
    error_message = "planning-cockpit must have exactly 2 permitted groups (planners + platform-admin)"
  }
}

run "line_command_permits_shiftleads_and_associates" {
  command = plan

  assert {
    condition     = contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000002")
    error_message = "line-command must permit GRP-IFOS-SHIFTLEADS-MUC"
  }

  assert {
    condition     = contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000003")
    error_message = "line-command must permit GRP-IFOS-ASSOCIATES-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000001")
    error_message = "line-command must NOT permit GRP-IFOS-PLANNERS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000004")
    error_message = "line-command must NOT permit GRP-IFOS-PAINTENGINEER-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000005")
    error_message = "line-command must NOT permit GRP-IFOS-VALIDATORS-MUC"
  }

  assert {
    condition     = contains(output.console_access_map["line-command"], "aaa00000-0000-0000-0000-000000000006")
    error_message = "line-command must permit GRP-IFOS-PLATFORM-ADMIN"
  }

  assert {
    condition     = length(output.console_access_map["line-command"]) == 3
    error_message = "line-command must have exactly 3 permitted groups (shiftleads + associates + platform-admin)"
  }
}

run "quality_studio_permits_paint_engineer_and_validators" {
  command = plan

  assert {
    condition     = contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000004")
    error_message = "quality-studio must permit GRP-IFOS-PAINTENGINEER-MUC"
  }

  assert {
    condition     = contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000005")
    error_message = "quality-studio must permit GRP-IFOS-VALIDATORS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000001")
    error_message = "quality-studio must NOT permit GRP-IFOS-PLANNERS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000002")
    error_message = "quality-studio must NOT permit GRP-IFOS-SHIFTLEADS-MUC"
  }

  assert {
    condition     = !contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000003")
    error_message = "quality-studio must NOT permit GRP-IFOS-ASSOCIATES-MUC"
  }

  assert {
    condition     = contains(output.console_access_map["quality-studio"], "aaa00000-0000-0000-0000-000000000006")
    error_message = "quality-studio must permit GRP-IFOS-PLATFORM-ADMIN"
  }

  assert {
    condition     = length(output.console_access_map["quality-studio"]) == 3
    error_message = "quality-studio must have exactly 3 permitted groups (paintengineer + validators + platform-admin)"
  }
}

# ---------------------------------------------------------------------------
# Assembly-associate allowed endpoints (AR Workbench paths)
# ---------------------------------------------------------------------------
run "associate_may_access_ar_instructions" {
  command = plan

  assert {
    condition     = contains(output.associate_allowed_endpoints, "GET /line-command/api/v1/ar/instructions/{vin}")
    error_message = "associate_allowed_endpoints must include GET /line-command/api/v1/ar/instructions/{vin}"
  }
}

run "associate_may_access_torque_log" {
  command = plan

  assert {
    condition     = contains(output.associate_allowed_endpoints, "POST /line-command/api/v1/torque/log")
    error_message = "associate_allowed_endpoints must include POST /line-command/api/v1/torque/log"
  }
}

run "associate_allowed_endpoints_count_is_2" {
  command = plan

  assert {
    condition     = length(output.associate_allowed_endpoints) == 2
    error_message = "associate_allowed_endpoints must have exactly 2 entries"
  }
}

# ---------------------------------------------------------------------------
# Assembly-associate DENIED endpoints — each must trigger 403 + audit write
# These assertions verify the deny list is complete and correctly named
# (the policy XML enforces them at runtime; the list here is the declarative contract).
# ---------------------------------------------------------------------------
run "associate_denied_oee_snapshots" {
  command = plan

  assert {
    condition     = contains(output.associate_denied_endpoints, "GET /line-command/oee/snapshots")
    error_message = "associate_denied_endpoints must include GET /line-command/oee/snapshots (Command Center OEE)"
  }
}

run "associate_denied_andon_alerts" {
  command = plan

  assert {
    condition     = contains(output.associate_denied_endpoints, "GET /line-command/andon/alerts")
    error_message = "associate_denied_endpoints must include GET /line-command/andon/alerts"
  }
}

run "associate_denied_alert_acknowledge" {
  command = plan

  assert {
    condition     = contains(output.associate_denied_endpoints, "POST /line-command/alerts/{alertId}/acknowledge")
    error_message = "associate_denied_endpoints must include POST /line-command/alerts/{alertId}/acknowledge"
  }
}

run "associate_denied_handover_get" {
  command = plan

  assert {
    condition     = contains(output.associate_denied_endpoints, "GET /line-command/api/v1/handover")
    error_message = "associate_denied_endpoints must include GET /line-command/api/v1/handover (Handover Builder)"
  }
}

run "associate_denied_handover_post" {
  command = plan

  assert {
    condition     = contains(output.associate_denied_endpoints, "POST /line-command/api/v1/handover")
    error_message = "associate_denied_endpoints must include POST /line-command/api/v1/handover (Handover Builder)"
  }
}

run "associate_denied_endpoints_count_is_5" {
  command = plan

  assert {
    condition     = length(output.associate_denied_endpoints) == 5
    error_message = "associate_denied_endpoints must have exactly 5 entries covering all forbidden associate paths"
  }
}

# ---------------------------------------------------------------------------
# Audit log endpoint is wired into gateway properties
# ---------------------------------------------------------------------------
run "audit_log_endpoint_is_in_gateway_properties" {
  command = plan

  assert {
    condition     = output.gateway_properties["audit-log-endpoint"] == "https://audit.internal.ifos.example"
    error_message = "gateway_properties must carry audit-log-endpoint so DataPower can emit NFR-06 audit events"
  }
}

# ---------------------------------------------------------------------------
# All six group IDs are injected as gateway properties
# ---------------------------------------------------------------------------
run "all_group_ids_present_in_gateway_properties" {
  command = plan

  assert {
    condition = alltrue([
      contains(keys(output.gateway_properties), "grp-planners-id"),
      contains(keys(output.gateway_properties), "grp-shiftleads-id"),
      contains(keys(output.gateway_properties), "grp-associates-id"),
      contains(keys(output.gateway_properties), "grp-paintengineer-id"),
      contains(keys(output.gateway_properties), "grp-validators-id"),
      contains(keys(output.gateway_properties), "grp-platform-admin-id"),
    ])
    error_message = "All six IFOS security group IDs must be present in gateway_properties"
  }
}

run "gateway_properties_group_ids_match_variables" {
  command = plan

  assert {
    condition     = output.gateway_properties["grp-associates-id"] == "aaa00000-0000-0000-0000-000000000003"
    error_message = "gateway_properties grp-associates-id must equal the GRP-IFOS-ASSOCIATES-MUC object ID"
  }

  assert {
    condition     = output.gateway_properties["grp-shiftleads-id"] == "aaa00000-0000-0000-0000-000000000002"
    error_message = "gateway_properties grp-shiftleads-id must equal the GRP-IFOS-SHIFTLEADS-MUC object ID"
  }
}
