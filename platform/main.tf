module "api_gateway" {
  source      = "./api-gateway"
  environment = var.environment

  # Entra ID tenant — read from environment variable ARM_TENANT_ID at apply time.
  entra_tenant_id = var.entra_tenant_id

  # App registration client IDs consumed from bmw-entra-id outputs.
  line_command_client_id     = module.bmw_entra_id.app_client_ids["line_command"]
  planning_cockpit_client_id = module.bmw_entra_id.app_client_ids["planning_cockpit"]
  quality_studio_client_id   = module.bmw_entra_id.app_client_ids["quality_studio"]

  # Security group object IDs consumed from bmw-entra-id outputs.
  grp_planners_id       = module.bmw_entra_id.group_object_ids["planners"]
  grp_shiftleads_id     = module.bmw_entra_id.group_object_ids["shiftleads"]
  grp_associates_id     = module.bmw_entra_id.group_object_ids["associates"]
  grp_paintengineer_id  = module.bmw_entra_id.group_object_ids["paint_engineer"]
  grp_validators_id     = module.bmw_entra_id.group_object_ids["validators"]
  grp_platform_admin_id = module.bmw_entra_id.group_object_ids["platform_admin"]

  # Immutable audit log endpoint — read from environment variable AUDIT_LOG_ENDPOINT.
  audit_log_endpoint = var.audit_log_endpoint
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
