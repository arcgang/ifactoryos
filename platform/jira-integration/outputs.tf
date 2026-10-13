# Outputs consumed by defect-genome service configuration and CI verification.
# These values define the Jira resource IDs referenced by
# POST /defect/clusters/{clusterId}/corrective-action when pre-populating tickets.

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
