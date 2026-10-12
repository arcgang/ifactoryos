environment = "dev"

# Identity pseudonymisation DB — on-prem Munich PostgreSQL/Citus (EU data residency)
# Sensitive values (identity_db_superuser_password, identity_service_db_password,
# identity_db_connection_string) are read from Vault at apply time — never hardcoded.
identity_db_host      = "citus-dev.internal.ifos.bmwgroup.net"
identity_db_superuser = "postgres"

# Entra ID tenant — matches TF_VAR_entra_tenant_id and ARM_TENANT_ID in .env.example
entra_tenant_id = ""  # populated from TF_VAR_entra_tenant_id at apply time
