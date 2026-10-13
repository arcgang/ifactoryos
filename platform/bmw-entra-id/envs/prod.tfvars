environment = "prod"

# Identity pseudonymisation DB — on-prem Munich PostgreSQL/Citus (EU data residency)
identity_db_host      = "citus-prod.internal.ifos.bmwgroup.net"
identity_db_superuser = "postgres"

entra_tenant_id = ""  # populated from TF_VAR_entra_tenant_id at apply time
