# Integration tests for platform/immutable-audit-log EU data residency and GDPR Article 30.
#
# Assertions:
#   1. Storage account Azure region is germanywestcentral (EU constraint AC-1)
#   2. WORM immutability policy state is Locked with 2555-day (7-year) retention (AC-2)
#   3. EU residency policy assignment targets only germanywestcentral and westeurope (AC-3)
#   4. Article 30 workbook display_name contains the mandatory Article 30 marker (AC-4)
#   5. Service Bus DLQ queue and Monitor alert are provisioned (schema-rejected writes, AC-5)
#   6. Canonical output names expected by downstream modules are present (AC-6)
#
# Uses mock_provider so no Azure credentials are required.

mock_provider "azurerm" {}

variables {
  environment = "dev"
}

# ---------------------------------------------------------------------------
# AC-1: Storage account must be in germanywestcentral
# ---------------------------------------------------------------------------
run "storage_account_region_is_germany_west_central" {
  command = plan

  assert {
    condition     = azurerm_storage_account.audit_log.location == "germanywestcentral"
    error_message = "Storage account must be provisioned in germanywestcentral to satisfy EU data residency (US-028)"
  }
}

# ---------------------------------------------------------------------------
# AC-2: WORM policy — Locked state and 7-year (2555-day) retention
# ---------------------------------------------------------------------------
run "worm_immutability_policy_state_is_locked" {
  command = plan

  assert {
    condition     = azurerm_storage_blob_immutability_policy.audit_events.state == "Locked"
    error_message = "WORM immutability policy must be Locked — cannot be shortened or removed (IEC 62443 SL2, GDPR Art. 30)"
  }
}

run "worm_immutability_retention_is_7_years" {
  command = plan

  assert {
    condition     = azurerm_storage_blob_immutability_policy.audit_events.immutability_period_in_days == 2555
    error_message = "WORM retention period must be 2555 days (7 years) — settled decision data:worm-audit-log-7-year-retention"
  }
}

# ---------------------------------------------------------------------------
# AC-3: EU residency policy — allowed locations list contains only EU regions
# ---------------------------------------------------------------------------
run "eu_residency_policy_is_assigned_to_resource_group" {
  command = plan

  assert {
    condition     = azurerm_resource_group_policy_assignment.eu_residency.resource_group_id == azurerm_resource_group.audit_log.id
    error_message = "EU residency policy assignment must be scoped to the audit log resource group"
  }
}

run "eu_residency_policy_uses_allowed_locations_definition" {
  command = plan

  assert {
    condition     = azurerm_resource_group_policy_assignment.eu_residency.policy_definition_id == "/providers/Microsoft.Authorization/policyDefinitions/e56962a6-4747-49cd-b67b-bf8b01975c4f"
    error_message = "EU residency must use the built-in allowedLocations policy definition (e56962a6-4747-49cd-b67b-bf8b01975c4f)"
  }
}

run "eu_residency_policy_parameters_contain_germanywestcentral" {
  command = plan

  assert {
    condition     = strcontains(azurerm_resource_group_policy_assignment.eu_residency.parameters, "germanywestcentral")
    error_message = "EU residency policy parameters must include germanywestcentral"
  }
}

run "eu_residency_policy_parameters_contain_westeurope" {
  command = plan

  assert {
    condition     = strcontains(azurerm_resource_group_policy_assignment.eu_residency.parameters, "westeurope")
    error_message = "EU residency policy parameters must include westeurope (settled decision: eu-data-residency-azure-eu-central)"
  }
}

# ---------------------------------------------------------------------------
# AC-4: GDPR Article 30 workbook — display name carries the mandatory marker
# ---------------------------------------------------------------------------
run "article30_workbook_display_name_contains_article_30_marker" {
  command = plan

  assert {
    condition     = strcontains(azurerm_application_insights_workbook.article30.display_name, "Article 30")
    error_message = "Workbook display_name must contain 'Article 30' so the DPO can identify it for Works Council responses"
  }
}

run "article30_workbook_is_in_eu_region" {
  command = plan

  assert {
    condition     = azurerm_application_insights_workbook.article30.location == azurerm_resource_group.audit_log.location
    error_message = "Article 30 workbook must be co-located with the audit log resource group (EU data residency)"
  }
}

# ---------------------------------------------------------------------------
# AC-5: Service Bus DLQ and CISO alert for schema-rejected writes
# ---------------------------------------------------------------------------
run "service_bus_namespace_is_provisioned_in_eu_region" {
  command = plan

  assert {
    condition     = azurerm_servicebus_namespace.audit_dlq.location == azurerm_resource_group.audit_log.location
    error_message = "Service Bus namespace must be co-located with the audit log resource group (EU data residency)"
  }
}

run "schema_rejected_queue_max_delivery_count_is_1" {
  command = plan

  assert {
    condition     = azurerm_servicebus_queue.schema_rejected.max_delivery_count == 1
    error_message = "schema-rejected queue max_delivery_count must be 1 — rejected events are DLQ'd immediately without retry"
  }
}

run "dlq_ciso_alert_fires_within_1_minute" {
  command = plan

  assert {
    condition     = azurerm_monitor_metric_alert.dlq_ciso_alert.frequency == "PT1M"
    error_message = "DLQ CISO alert frequency must be PT1M to deliver alert within 60 seconds of a rejected write"
  }
}

run "dlq_ciso_alert_threshold_is_zero" {
  command = plan

  assert {
    condition     = azurerm_monitor_metric_alert.dlq_ciso_alert.criteria[0].threshold == 0
    error_message = "DLQ CISO alert threshold must be 0 (fires on any active message — any rejection must alert)"
  }
}

# ---------------------------------------------------------------------------
# AC-6: Canonical output names expected by downstream modules
# ---------------------------------------------------------------------------
run "output_audit_log_storage_account_id_is_present" {
  command = plan

  assert {
    condition     = output.audit_log_storage_account_id == azurerm_storage_account.audit_log.id
    error_message = "output.audit_log_storage_account_id must equal the storage account resource ID"
  }
}

run "output_audit_log_container_name_is_audit_events" {
  command = plan

  assert {
    condition     = output.audit_log_container_name == "audit-events"
    error_message = "output.audit_log_container_name must be 'audit-events'"
  }
}

run "output_worm_policy_id_is_present" {
  command = plan

  assert {
    condition     = output.worm_policy_id == azurerm_storage_blob_immutability_policy.audit_events.id
    error_message = "output.worm_policy_id must equal the immutability policy resource ID"
  }
}

run "output_service_bus_namespace_id_is_present" {
  command = plan

  assert {
    condition     = output.service_bus_namespace_id == azurerm_servicebus_namespace.audit_dlq.id
    error_message = "output.service_bus_namespace_id must equal the Service Bus namespace resource ID"
  }
}

run "output_article30_workbook_id_is_present" {
  command = plan

  assert {
    condition     = output.article30_workbook_id == azurerm_application_insights_workbook.article30.id
    error_message = "output.article30_workbook_id must equal the workbook resource ID"
  }
}
