module "api_gateway" {
  source      = "./api-gateway"
  environment = var.environment
}

module "uns_backbone" {
  source      = "./uns-backbone"
  environment = var.environment
}

module "edge_gateways" {
  source      = "./edge-gateways"
  environment = var.environment
}

module "vin_digital_thread" {
  source      = "./vin-digital-thread"
  environment = var.environment
}

module "bmw_entra_id" {
  source      = "./bmw-entra-id"
  environment = var.environment
}

module "spiffe_spire" {
  source      = "./spiffe-spire"
  environment = var.environment
}

module "hashicorp_vault" {
  source      = "./hashicorp-vault"
  environment = var.environment
}

module "immutable_audit_log" {
  source      = "./immutable-audit-log"
  environment = var.environment
}

module "observability_stack" {
  source      = "./observability-stack"
  environment = var.environment
}

module "postgresql_citus" {
  source      = "./postgresql-citus"
  environment = var.environment
}

module "jira_integration" {
  source      = "./jira-integration"
  environment = var.environment
}
