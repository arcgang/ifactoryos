# Terraform tests for platform/jira-integration — POST /rest/api/3/issue contract.
#
# Assertions:
#   1. Vault secret path matches the canonical path consumed by defect-genome (AC-1)
#   2. Vault secret contains the required OAuth2 fields (client_id, client_secret, scopes) (AC-2)
#   3. Jira project key matches IFOSQUALITY (consumed by defect-genome JIRA_PROJECT_KEY) (AC-3)
#   4. Corrective Action issue type is provisioned (AC-4)
#   5. All four required custom fields are provisioned with correct Jira field types (AC-5)
#   6. All four custom fields are associated with the IFOS-QUALITY project screen (AC-6)
#   7. Canonical output names expected by defect-genome service are present (AC-7)
#
# Uses mock_provider so no live Vault or Jira credentials are required.

mock_provider "vault" {}
mock_provider "jira" {}

variables {
  environment               = "dev"
  vault_addr                = "https://vault.internal.ifos.example"
  jira_base_url             = "https://bmwgroup.atlassian.net"
  jira_admin_token          = "test-admin-token"
  jira_oauth2_client_id     = "test-client-id"
  jira_oauth2_client_secret = "test-client-secret"
}

# ---------------------------------------------------------------------------
# AC-1: Vault secret path matches the canonical path used by defect-genome
# ---------------------------------------------------------------------------

run "vault_secret_path_is_canonical" {
  command = plan

  assert {
    condition     = vault_kv_secret_v2.jira_quality_studio_client_secret.name == "jira/quality-studio-client-secret"
    error_message = "Vault secret name must be 'jira/quality-studio-client-secret' — consumed by defect-genome Vault Agent Injector"
  }
}

run "vault_secret_mount_is_secret" {
  command = plan

  assert {
    condition     = vault_kv_secret_v2.jira_quality_studio_client_secret.mount == "secret"
    error_message = "Vault secret must be under the 'secret' KV mount (standard platform mount)"
  }
}

# ---------------------------------------------------------------------------
# AC-2: Vault secret data contains required OAuth2 fields
# ---------------------------------------------------------------------------

run "vault_secret_scopes_include_write_jira_work" {
  command = plan

  assert {
    condition     = strcontains(vault_kv_secret_v2.jira_quality_studio_client_secret.data_json, "write:jira-work")
    error_message = "Vault secret must declare 'write:jira-work' scope — required for POST /rest/api/3/issue"
  }
}

run "vault_secret_scopes_include_read_jira_work" {
  command = plan

  assert {
    condition     = strcontains(vault_kv_secret_v2.jira_quality_studio_client_secret.data_json, "read:jira-work")
    error_message = "Vault secret must declare 'read:jira-work' scope — required for GET /rest/api/3/issue/{issueId}"
  }
}

run "vault_secret_token_url_is_atlassian" {
  command = plan

  assert {
    condition     = strcontains(vault_kv_secret_v2.jira_quality_studio_client_secret.data_json, "auth.atlassian.com/oauth/token")
    error_message = "Vault secret token_url must be the Atlassian OAuth token endpoint"
  }
}

# ---------------------------------------------------------------------------
# AC-3: Jira project key is IFOSQUALITY (consumed by JIRA_PROJECT_KEY env var)
# ---------------------------------------------------------------------------

run "jira_project_key_is_ifosquality" {
  command = plan

  assert {
    condition     = jira_project.ifos_quality.key == "IFOSQUALITY"
    error_message = "Jira project key must be 'IFOSQUALITY' — consumed by defect-genome JIRA_PROJECT_KEY and POST /rest/api/3/issue fields"
  }
}

run "jira_project_name_is_ifos_quality" {
  command = plan

  assert {
    condition     = jira_project.ifos_quality.name == "IFOS-QUALITY"
    error_message = "Jira project name must be 'IFOS-QUALITY'"
  }
}

# ---------------------------------------------------------------------------
# AC-4: Corrective Action issue type is provisioned
# ---------------------------------------------------------------------------

run "corrective_action_issue_type_name" {
  command = plan

  assert {
    condition     = jira_issue_type.corrective_action.name == "Corrective Action"
    error_message = "Issue type name must be 'Corrective Action' — referenced in POST /rest/api/3/issue fields by defect-genome"
  }
}

run "corrective_action_scheme_associates_correct_issue_type" {
  command = plan

  assert {
    condition     = contains(jira_issue_type_scheme.ifos_quality_scheme.issue_type_ids, jira_issue_type.corrective_action.id)
    error_message = "Issue type scheme must include the Corrective Action issue type ID"
  }
}

run "corrective_action_scheme_is_associated_with_ifosquality_project" {
  command = plan

  assert {
    condition     = jira_issue_type_scheme_association.ifos_quality_association.project_key == jira_project.ifos_quality.key
    error_message = "Issue type scheme must be associated with the IFOS-QUALITY project"
  }
}

# ---------------------------------------------------------------------------
# AC-5: All four required custom fields are provisioned with correct Jira types
# ---------------------------------------------------------------------------

run "cluster_id_field_is_textfield" {
  command = plan

  assert {
    condition     = jira_custom_field.defect_genome_cluster_id.type == "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
    error_message = "DefectGenome Cluster ID must be a textfield — stores an opaque UUID, no PII"
  }
}

run "cluster_id_field_name_is_correct" {
  command = plan

  assert {
    condition     = jira_custom_field.defect_genome_cluster_id.name == "DefectGenome Cluster ID"
    error_message = "Custom field name must be 'DefectGenome Cluster ID' — matched by defect-genome JIRA_FIELD_CLUSTER_ID"
  }
}

run "root_cause_probability_field_is_float" {
  command = plan

  assert {
    condition     = jira_custom_field.root_cause_probability.type == "com.atlassian.jira.plugin.system.customfieldtypes:float"
    error_message = "Root Cause Probability must be a float field — holds 0.0–1.0 XGBoost confidence score"
  }
}

run "root_cause_probability_field_name_is_correct" {
  command = plan

  assert {
    condition     = jira_custom_field.root_cause_probability.name == "Root Cause Probability"
    error_message = "Custom field name must be 'Root Cause Probability'"
  }
}

run "affected_vin_count_field_is_float" {
  command = plan

  assert {
    condition     = jira_custom_field.affected_vin_count.type == "com.atlassian.jira.plugin.system.customfieldtypes:float"
    error_message = "Affected VIN Count must be a float field — aggregate count only, no individual VIN data"
  }
}

run "affected_vin_count_field_name_is_correct" {
  command = plan

  assert {
    condition     = jira_custom_field.affected_vin_count.name == "Affected VIN Count"
    error_message = "Custom field name must be 'Affected VIN Count'"
  }
}

run "prep_stage_adjustment_field_is_textfield" {
  command = plan

  assert {
    condition     = jira_custom_field.recommended_prep_stage_adjustment.type == "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
    error_message = "Recommended Prep Stage Adjustment must be a textfield — freetext process recommendation, no PII"
  }
}

run "prep_stage_adjustment_field_name_is_correct" {
  command = plan

  assert {
    condition     = jira_custom_field.recommended_prep_stage_adjustment.name == "Recommended Prep Stage Adjustment"
    error_message = "Custom field name must be 'Recommended Prep Stage Adjustment'"
  }
}

# ---------------------------------------------------------------------------
# AC-6: All four custom fields are associated with the IFOS-QUALITY project screen
# ---------------------------------------------------------------------------

run "cluster_id_screen_association_uses_correct_project" {
  command = plan

  assert {
    condition     = jira_custom_field_option.cluster_id_screen.project_key == jira_project.ifos_quality.key
    error_message = "DefectGenome Cluster ID must be associated with the IFOS-QUALITY project screen"
  }
}

run "probability_screen_association_uses_correct_project" {
  command = plan

  assert {
    condition     = jira_custom_field_option.probability_screen.project_key == jira_project.ifos_quality.key
    error_message = "Root Cause Probability must be associated with the IFOS-QUALITY project screen"
  }
}

run "vin_count_screen_association_uses_correct_project" {
  command = plan

  assert {
    condition     = jira_custom_field_option.vin_count_screen.project_key == jira_project.ifos_quality.key
    error_message = "Affected VIN Count must be associated with the IFOS-QUALITY project screen"
  }
}

run "prep_stage_screen_association_uses_correct_project" {
  command = plan

  assert {
    condition     = jira_custom_field_option.prep_stage_screen.project_key == jira_project.ifos_quality.key
    error_message = "Recommended Prep Stage Adjustment must be associated with the IFOS-QUALITY project screen"
  }
}

# ---------------------------------------------------------------------------
# AC-7: Canonical output names expected by defect-genome and CI verification
# ---------------------------------------------------------------------------

run "output_vault_secret_path_is_present" {
  command = plan

  assert {
    condition     = output.vault_secret_path == vault_kv_secret_v2.jira_quality_studio_client_secret.path
    error_message = "output.vault_secret_path must equal the Vault secret resource path"
  }
}

run "output_jira_project_key_is_ifosquality" {
  command = plan

  assert {
    condition     = output.jira_project_key == "IFOSQUALITY"
    error_message = "output.jira_project_key must be 'IFOSQUALITY' — consumed by defect-genome JIRA_PROJECT_KEY"
  }
}

run "output_jira_issue_type_id_is_present" {
  command = plan

  assert {
    condition     = output.jira_issue_type_id == jira_issue_type.corrective_action.id
    error_message = "output.jira_issue_type_id must equal the Corrective Action issue type ID"
  }
}

run "output_custom_field_cluster_id_is_present" {
  command = plan

  assert {
    condition     = output.custom_field_cluster_id == jira_custom_field.defect_genome_cluster_id.id
    error_message = "output.custom_field_cluster_id must equal the DefectGenome Cluster ID field ID"
  }
}

run "output_custom_field_root_cause_probability_is_present" {
  command = plan

  assert {
    condition     = output.custom_field_root_cause_probability == jira_custom_field.root_cause_probability.id
    error_message = "output.custom_field_root_cause_probability must equal the Root Cause Probability field ID"
  }
}

run "output_custom_field_affected_vin_count_is_present" {
  command = plan

  assert {
    condition     = output.custom_field_affected_vin_count == jira_custom_field.affected_vin_count.id
    error_message = "output.custom_field_affected_vin_count must equal the Affected VIN Count field ID"
  }
}

run "output_custom_field_prep_stage_adjustment_is_present" {
  command = plan

  assert {
    condition     = output.custom_field_prep_stage_adjustment == jira_custom_field.recommended_prep_stage_adjustment.id
    error_message = "output.custom_field_prep_stage_adjustment must equal the Recommended Prep Stage Adjustment field ID"
  }
}
