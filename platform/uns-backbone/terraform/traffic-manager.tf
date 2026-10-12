# Azure Traffic Manager — priority routing for DR failover
# Primary: West Europe Application Gateway
# Secondary: North Europe (Germany North) Application Gateway
# Failover DNS propagation target: ~5 minutes (TTL=60s + probe interval)
# Realises: US-025 · BR-16

variable "primary_app_gateway_fqdn" {
  type        = string
  description = "FQDN of the primary West Europe Application Gateway"
}

variable "dr_app_gateway_fqdn" {
  type        = string
  description = "FQDN of the DR Germany North Application Gateway"
}

variable "traffic_manager_resource_group" {
  type        = string
  description = "Resource group for the Traffic Manager profile (typically shared/global RG)"
}

locals {
  tm_profile_name       = "atm-ifos-${var.environment}"
  tm_dns_name           = "ifos-${var.environment}"
  # TTL 60 s: clients refresh DNS every minute, bounding failover propagation to ~5 min
  tm_ttl_seconds        = 60
  # HTTP probe every 10 s; 3 consecutive failures → endpoint degraded
  probe_interval_s      = 10
  probe_timeout_s       = 5
  probe_tolerated_fails = 3
}

resource "azurerm_traffic_manager_profile" "ifos" {
  name                   = local.tm_profile_name
  resource_group_name    = var.traffic_manager_resource_group
  traffic_routing_method = "Priority"

  dns_config {
    relative_name = local.tm_dns_name
    ttl           = local.tm_ttl_seconds
  }

  monitor_config {
    protocol                     = "HTTPS"
    port                         = 443
    path                         = "/health"
    interval_in_seconds          = local.probe_interval_s
    timeout_in_seconds           = local.probe_timeout_s
    tolerated_number_of_failures = local.probe_tolerated_fails

    expected_status_code_ranges = ["200-200"]

    custom_header {
      name  = "x-health-check"
      value = "traffic-manager"
    }
  }

  tags = {
    component      = "uns-backbone"
    environment    = var.environment
    dr_role        = "traffic-manager"
    data_residency = "eu"
    managed_by     = "terraform"
  }
}

# Primary endpoint — West Europe Application Gateway (priority 1 = preferred)
resource "azurerm_traffic_manager_external_endpoint" "primary" {
  name       = "primary-west-europe"
  profile_id = azurerm_traffic_manager_profile.ifos.id
  priority   = 1
  weight     = 100
  target     = var.primary_app_gateway_fqdn

  # Geographic scope — EU only (data residency)
  geo_mappings = ["GEO-EU"]
}

# Secondary endpoint — Germany North Application Gateway (priority 2 = failover)
resource "azurerm_traffic_manager_external_endpoint" "dr" {
  name       = "secondary-germany-north"
  profile_id = azurerm_traffic_manager_profile.ifos.id
  priority   = 2
  weight     = 100
  target     = var.dr_app_gateway_fqdn

  geo_mappings = ["GEO-EU"]
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "traffic_manager_fqdn" {
  description = "Public FQDN of the Traffic Manager profile"
  value       = azurerm_traffic_manager_profile.ifos.fqdn
}

output "traffic_manager_profile_id" {
  description = "Resource ID of the Traffic Manager profile"
  value       = azurerm_traffic_manager_profile.ifos.id
}
