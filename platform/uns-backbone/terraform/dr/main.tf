# uns-backbone — Germany North warm-standby ARO cluster (DR)
# Logical name: glbl-pr-ifos-euno-rgp
# Region: germanyNorth (northeurope)
# This cluster is provisioned at 50% replica count; zero active workload traffic at rest.
# Realises: US-025 · BR-16
# Decision applied: active-passive DR, Germany North secondary; ExpressRoute Global Reach for DB/Kafka replication

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.110"
    }
    azapi = {
      source  = "azure/azapi"
      version = "~> 1.13"
    }
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment (preprod, prod)"
}

variable "primary_resource_group" {
  type        = string
  description = "Resource group of the primary West-Europe cluster"
}

variable "primary_vnet_id" {
  type        = string
  description = "VNet ID of the primary cluster (for ExpressRoute Global Reach peering)"
}

variable "pull_secret" {
  type        = string
  sensitive   = true
  description = "Red Hat pull secret JSON"
}

locals {
  location       = "germanyNorth"
  rg_name        = "glbl-pr-ifos-euno-rgp"
  cluster_name   = "ifos-dr-euno-aro"
  domain         = "ifos-dr-euno"
  aro_version    = "4.16.0"

  # 50% warm-standby: primary runs 6 workers (3 per AZ × 2 AZs); DR runs 3 workers
  worker_count   = 3
  worker_vm_sku  = "Standard_D8s_v5"
  master_vm_sku  = "Standard_D8s_v5"

  tags = {
    component      = "uns-backbone"
    environment    = var.environment
    dr_role        = "secondary"
    data_residency = "eu-de-north"
    managed_by     = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Resource group
# ---------------------------------------------------------------------------

resource "azurerm_resource_group" "dr" {
  name     = local.rg_name
  location = local.location
  tags     = local.tags
}

# ---------------------------------------------------------------------------
# Networking — VNet + subnets required by ARO
# ---------------------------------------------------------------------------

resource "azurerm_virtual_network" "dr" {
  name                = "vnet-ifos-dr-euno"
  resource_group_name = azurerm_resource_group.dr.name
  location            = local.location
  address_space       = ["10.64.0.0/14"]
  tags                = local.tags
}

resource "azurerm_subnet" "master" {
  name                 = "snet-aro-master"
  resource_group_name  = azurerm_resource_group.dr.name
  virtual_network_name = azurerm_virtual_network.dr.name
  address_prefixes     = ["10.64.0.0/23"]

  service_endpoints = ["Microsoft.ContainerRegistry"]

  # ARO requires private link policies disabled on master subnet
  private_link_service_network_policies_enabled = false
}

resource "azurerm_subnet" "worker" {
  name                 = "snet-aro-worker"
  resource_group_name  = azurerm_resource_group.dr.name
  virtual_network_name = azurerm_virtual_network.dr.name
  address_prefixes     = ["10.64.2.0/23"]

  service_endpoints = ["Microsoft.ContainerRegistry", "Microsoft.Storage"]
}

# ---------------------------------------------------------------------------
# ExpressRoute connection — extends primary circuit to Germany North
# Private peering over Global Reach carries MirrorMaker 2 and DB WAL replication
# ---------------------------------------------------------------------------

resource "azurerm_express_route_gateway" "dr" {
  name                = "ergw-ifos-dr-euno"
  resource_group_name = azurerm_resource_group.dr.name
  location            = local.location

  virtual_hub_id = azurerm_virtual_hub.dr.id
  scale_units    = 2

  tags = local.tags
}

resource "azurerm_virtual_hub" "dr" {
  name                = "vhub-ifos-dr-euno"
  resource_group_name = azurerm_resource_group.dr.name
  location            = local.location
  sku                 = "Standard"
  address_prefix      = "10.68.0.0/23"
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# Azure Red Hat OpenShift 4.16 — Germany North DR cluster
# ---------------------------------------------------------------------------

resource "azapi_resource" "aro_cluster" {
  type      = "Microsoft.RedHatOpenShift/openShiftClusters@2023-11-22"
  name      = local.cluster_name
  location  = local.location
  parent_id = azurerm_resource_group.dr.id
  tags      = local.tags

  body = jsonencode({
    properties = {
      clusterProfile = {
        domain      = local.domain
        version     = local.aro_version
        pullSecret  = var.pull_secret
        # FipsValidatedModules enabled for BMW security requirements
        fipsValidatedModules = "Enabled"
      }
      networkProfile = {
        podCidr     = "10.128.0.0/14"
        serviceCidr = "172.30.0.0/16"
        # Internal load-balancer only; no public API server
        outboundType = "UserDefinedRouting"
      }
      masterProfile = {
        vmSize           = local.master_vm_sku
        subnetId         = azurerm_subnet.master.id
        encryptionAtHost = "Enabled"
      }
      workerProfiles = [
        {
          name             = "worker"
          vmSize           = local.worker_vm_sku
          subnetId         = azurerm_subnet.worker.id
          diskSizeGB       = 256
          count            = local.worker_count
          encryptionAtHost = "Enabled"
        }
      ]
      apiserverProfile = {
        visibility = "Private"
      }
      ingressProfiles = [
        {
          name       = "default"
          visibility = "Private"
        }
      ]
    }
  })

  # Cluster creation can take up to 40 minutes
  timeouts {
    create = "60m"
    delete = "60m"
  }
}

# ---------------------------------------------------------------------------
# Outputs — consumed by ArgoCD and SPIRE DR modules
# ---------------------------------------------------------------------------

output "dr_cluster_name" {
  description = "ARO DR cluster name"
  value       = local.cluster_name
}

output "dr_resource_group" {
  description = "DR resource group name"
  value       = azurerm_resource_group.dr.name
}

output "dr_location" {
  description = "DR Azure region"
  value       = local.location
}

output "dr_worker_subnet_id" {
  description = "Worker subnet ID for DR cluster"
  value       = azurerm_subnet.worker.id
}

output "dr_vnet_id" {
  description = "DR VNet ID (for Traffic Manager and peering)"
  value       = azurerm_virtual_network.dr.id
}
