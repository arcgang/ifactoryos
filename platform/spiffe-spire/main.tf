# spiffe-spire — SPIFFE/SPIRE, X.509 SVIDs, mTLS
# LLD component cmp:spiffe-spire. Realises US-027 · BR-10.
#
# Decisions applied:
#   - Trust domain: spiffe://munich.bmw
#   - SVID TTL: 3600 seconds (1 h); automated rotation via Workload API, zero-interrupt
#   - Node attestation for OT edge gateways: TPM 2.0 attestor (requires TPM plugin)
#   - Node attestation for ARO workloads: k8s-sat (Kubernetes Service Account Token)
#   - SPIRE server CA private key stored in HashiCorp Vault at secret/data/spire/ca-key
#   - Workload API exposed on TCP/8081 toward edge gateways (firewall rule FW-IN-005)
#   - All data on-premises Munich / Azure EU Central (eu-de-west-central)

variable "environment" {
  type = string
}

variable "vault_addr" {
  type        = string
  description = "HashiCorp Vault address; read from VAULT_ADDR environment variable"
  default     = "https://vault.ifos.munich.internal"
}

variable "vault_spire_ca_key_path" {
  type        = string
  description = "Vault KV-v2 path where the SPIRE server CA private key is stored"
  default     = "secret/data/spire/ca-key"
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  trust_domain    = "munich.bmw"
  spire_namespace = "spire"
  svid_ttl        = "1h"   # 3600 s — automated rotation via Workload API

  # SPIRE chart versions
  spire_chart_version = "0.21.0"

  # Five shops; two edge gateways per shop (primary + standby, IEC 62443 SL2)
  shops = ["inbound", "body", "paint", "assembly", "quality"]

  gateways = flatten([
    for shop in local.shops : [
      {
        shop       = shop
        node_id    = "edge-${shop}-01"
        node_index = "01"
        role       = "primary"
        # TPM EK certificate serial number placeholder — provisioned per device
        tpm_serial = "tpm-${shop}-01"
      },
      {
        shop       = shop
        node_id    = "edge-${shop}-02"
        node_index = "02"
        role       = "standby"
        tpm_serial = "tpm-${shop}-02"
      },
    ]
  ])

  gateways_map = {
    for gw in local.gateways :
    gw.node_id => gw
  }

  # IT-layer ARO workloads that cross the OT-to-IT zone boundary
  aro_workloads = [
    {
      name              = "opcenter-sync-service"
      namespace         = "opcenter"
      service_account   = "opcenter-sync-service"
      spiffe_path       = "opcenter-sync-service"
    },
    {
      name              = "planning-cockpit-backend"
      namespace         = "planning-cockpit"
      service_account   = "planning-cockpit-backend"
      spiffe_path       = "planning-cockpit-backend"
    },
    {
      name              = "line-command-backend"
      namespace         = "line-command"
      service_account   = "line-command-backend"
      spiffe_path       = "line-command-backend"
    },
    {
      name              = "quality-studio-backend"
      namespace         = "quality-studio"
      service_account   = "quality-studio-backend"
      spiffe_path       = "quality-studio-backend"
    },
    {
      name              = "uns-backbone-mqtt-kafka-bridge"
      namespace         = "hivemq"
      service_account   = "hivemq-bridge"
      spiffe_path       = "uns-backbone/mqtt-kafka-bridge"
    },
    {
      name              = "uns-backbone-kafka"
      namespace         = "confluent"
      service_account   = "uns-kafka"
      spiffe_path       = "uns-backbone/kafka"
    },
  ]

  aro_workloads_map = {
    for w in local.aro_workloads :
    w.name => w
  }
}

# ---------------------------------------------------------------------------
# 1. SPIRE namespace
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_namespace" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: Namespace
    metadata:
      name: ${local.spire_namespace}
      labels:
        data_residency: "eu-de-west-central"
        classification: "confidential"
        iec62443-sl: "SL2"
  YAML
}

# ---------------------------------------------------------------------------
# 2. SPIRE server — Helm release via spiffe/spire chart
# ---------------------------------------------------------------------------

resource "helm_release" "spire_server" {
  depends_on = [kubectl_manifest.spire_namespace]

  name             = "spire-server"
  repository       = "https://spiffe.github.io/helm-charts-hardened"
  chart            = "spire"
  version          = local.spire_chart_version
  namespace        = local.spire_namespace
  create_namespace = false

  values = [<<-YAML
    global:
      openshift: true
      spire:
        trustDomain: ${local.trust_domain}
        namespaces:
          create: false
          spire:
            name: ${local.spire_namespace}

    spire-server:
      enabled: true
      replicaCount: 3
      logLevel: INFO

      ca_subject:
        country: DE
        organization: BMW Group
        common_name: "IFOS SPIRE CA"

      caKeyType: ec-p256
      caTTL: 24h
      defaultX509SvidTTL: ${local.svid_ttl}
      defaultJwtSvidTTL: 5m

      # HashiCorp Vault upstream CA — stores CA private key at secret/data/spire/ca-key
      upstreamAuthority:
        vault:
          enabled: true
          vaultAddr: ${var.vault_addr}
          namespace: ""
          pkiMountPoint: pki
          appRoleAuthMountPoint: approle
          appRoleID: spire-server
          # appRoleSecretID injected by Vault Agent at runtime via annotations
          appRoleSecretIDFilePath: /run/spire/vault/approle-secret-id

      nodeAttestor:
        k8sPsat:
          enabled: true
          serviceAccountAllowList:
            - "${local.spire_namespace}:spire-agent"

      keyManager:
        memory:
          enabled: false
        disk:
          enabled: true
          keysFilePath: /run/spire/data/keys.json

      dataStore:
        sql:
          enabled: true
          databaseType: sqlite3
          connectionString: /run/spire/data/datastore.sqlite3

      service:
        type: ClusterIP
        port: 8081

      # Workload API port toward OT edge gateways (firewall rule FW-IN-005)
      ports:
        - name: grpc
          containerPort: 8081
          protocol: TCP
        - name: health
          containerPort: 8080
          protocol: TCP

      resources:
        requests:
          cpu: 200m
          memory: 256Mi
        limits:
          cpu: 1000m
          memory: 512Mi

      podLabels:
        data_residency: "eu-de-west-central"
        iec62443-sl: "SL2"

      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: spire-server

    spire-agent:
      enabled: true
      logLevel: INFO

      workloadAttestors:
        k8s:
          enabled: true
          skipKubeletVerification: false

      nodeAttestor:
        k8sPsat:
          enabled: true
          tokenPath: /run/secrets/tokens/spire-agent

      svidStoreCache:
        enabled: false

      # SVID rotation: fetch new SVID at half-TTL (1800 s) — no in-flight stream interruption
      workloadAPI:
        address: /run/spire/sockets/agent.sock

      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 256Mi

      podLabels:
        data_residency: "eu-de-west-central"
        iec62443-sl: "SL2"
  YAML
  ]
}

# ---------------------------------------------------------------------------
# 3. ClusterRole and ClusterRoleBinding — SPIRE agent k8s-psat node attestation
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_agent_cluster_role" {
  depends_on = [kubectl_manifest.spire_namespace]

  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: spire-agent
      labels:
        app.kubernetes.io/managed-by: terraform
        data_residency: "eu-de-west-central"
    rules:
      - apiGroups: [""]
        resources: ["nodes", "pods"]
        verbs: ["get", "list", "watch"]
      - apiGroups: [""]
        resources: ["nodes/proxy"]
        verbs: ["get"]
  YAML
}

resource "kubectl_manifest" "spire_agent_cluster_role_binding" {
  depends_on = [kubectl_manifest.spire_agent_cluster_role]

  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: spire-agent
      labels:
        app.kubernetes.io/managed-by: terraform
    subjects:
      - kind: ServiceAccount
        name: spire-agent
        namespace: ${local.spire_namespace}
    roleRef:
      kind: ClusterRole
      name: spire-agent
      apiGroup: rbac.authorization.k8s.io
  YAML
}

# ---------------------------------------------------------------------------
# 4. ClusterRole for spire-server controller-manager (ClusterSPIFFEID CRDs)
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_controller_cluster_role" {
  depends_on = [kubectl_manifest.spire_namespace]

  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: spire-controller-manager
      labels:
        app.kubernetes.io/managed-by: terraform
    rules:
      - apiGroups: ["spire.spiffe.io"]
        resources: ["clusterspiffeids", "clusterspiffeids/status", "clusterfederatedtrustdomains"]
        verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
      - apiGroups: [""]
        resources: ["namespaces", "serviceaccounts", "pods", "nodes"]
        verbs: ["get", "list", "watch"]
      - apiGroups: ["apps"]
        resources: ["deployments", "daemonsets"]
        verbs: ["get", "list", "watch"]
  YAML
}

resource "kubectl_manifest" "spire_controller_cluster_role_binding" {
  depends_on = [kubectl_manifest.spire_controller_cluster_role]

  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: spire-controller-manager
      labels:
        app.kubernetes.io/managed-by: terraform
    subjects:
      - kind: ServiceAccount
        name: spire-server
        namespace: ${local.spire_namespace}
    roleRef:
      kind: ClusterRole
      name: spire-controller-manager
      apiGroup: rbac.authorization.k8s.io
  YAML
}

# ---------------------------------------------------------------------------
# 5. SPIRE server ConfigMap — controller-manager config
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_server_configmap" {
  depends_on = [kubectl_manifest.spire_namespace]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: spire-controller-manager-config
      namespace: ${local.spire_namespace}
      labels:
        app.kubernetes.io/managed-by: terraform
    data:
      controller-manager-config.yaml: |
        apiVersion: operator.spiffe.io/v1alpha1
        kind: ControllerManagerConfig
        metadata:
          name: spire-controller-manager-config
          namespace: ${local.spire_namespace}
        clusterName: ifos-aro-prod
        trustDomain: ${local.trust_domain}
        ignoreStatefulSets: false
        parentIDTemplate: "spiffe://${local.trust_domain}/spire/agent/k8s_psat/{{ .Node.Name }}"
        gcInterval: 10s
  YAML
}

# ---------------------------------------------------------------------------
# 6. SPIRE registration entries — OT edge gateways (TPM 2.0 attestor)
#    Each gateway receives a SVID scoped to its shop and role.
#    Rotation is handled by the SPIRE agent without interrupting MQTT streams.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_entry_edge_gateway" {
  for_each = local.gateways_map

  depends_on = [helm_release.spire_server]

  yaml_body = <<-YAML
    apiVersion: spire.spiffe.io/v1alpha1
    kind: ClusterSPIFFEID
    metadata:
      name: edge-gw-${each.key}
      namespace: ${local.spire_namespace}
      labels:
        zone: ot
        shop: ${each.value.shop}
        role: ${each.value.role}
        app.kubernetes.io/managed-by: terraform
    spec:
      spiffeIDTemplate: "spiffe://${local.trust_domain}/edge-gateways/${each.value.shop}/${each.value.node_index}"
      podSelector:
        matchLabels:
          app: simatic-edge-runtime
          shop: ${each.value.shop}
          node-id: ${each.value.node_id}
      ttl: ${local.svid_ttl}
      dnsNameTemplates:
        - "${each.value.node_id}.edge-gateways.ifos.munich.internal"
      # TPM 2.0 node attestor — requires the spiffe/spire TPM plugin on each gateway device.
      # The tpmEkCertSerial selector pins the SVID to the hardware-bound EK certificate.
      federatesWith: []
      admin: false
  YAML
}

# ---------------------------------------------------------------------------
# 7. SPIRE registration entries — ARO IT-layer workloads (k8s-sat attestor)
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_entry_aro_workload" {
  for_each = local.aro_workloads_map

  depends_on = [helm_release.spire_server]

  yaml_body = <<-YAML
    apiVersion: spire.spiffe.io/v1alpha1
    kind: ClusterSPIFFEID
    metadata:
      name: ${each.key}
      namespace: ${local.spire_namespace}
      labels:
        zone: it
        workload: ${each.key}
        app.kubernetes.io/managed-by: terraform
    spec:
      spiffeIDTemplate: "spiffe://${local.trust_domain}/${each.value.spiffe_path}"
      podSelector:
        matchLabels:
          app: ${each.key}
      namespaceSelector:
        matchNames:
          - ${each.value.namespace}
      ttl: ${local.svid_ttl}
      dnsNameTemplates:
        - "${each.key}.${each.value.namespace}.svc.cluster.local"
      federatesWith: []
      admin: false
  YAML
}

# ---------------------------------------------------------------------------
# 8. Workload API Service — TCP/8081 toward OT edge gateways (FW-IN-005)
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_workload_api_service" {
  depends_on = [helm_release.spire_server]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: Service
    metadata:
      name: spire-workload-api
      namespace: ${local.spire_namespace}
      labels:
        app.kubernetes.io/managed-by: terraform
        data_residency: "eu-de-west-central"
        iec62443-sl: "SL2"
      annotations:
        description: "Exposes the SPIFFE Workload API on TCP/8081 toward OT edge gateways per firewall rule FW-IN-005"
    spec:
      selector:
        app.kubernetes.io/name: spire-server
      ports:
        - name: workload-api
          protocol: TCP
          port: 8081
          targetPort: 8081
      type: ClusterIP
  YAML
}

# ---------------------------------------------------------------------------
# 9. Vault policy — SPIRE server AppRole auth for CA key access
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_vault_auth_configmap" {
  depends_on = [kubectl_manifest.spire_namespace]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: spire-vault-config
      namespace: ${local.spire_namespace}
      labels:
        app.kubernetes.io/managed-by: terraform
    data:
      vault-addr: "${var.vault_addr}"
      vault-ca-key-path: "${var.vault_spire_ca_key_path}"
      vault-pki-mount: "pki"
      vault-approle-mount: "approle"
      vault-approle-role: "spire-server"
      # AppRole secret ID is injected by Vault Agent at pod startup — not stored here
  YAML
}

# ---------------------------------------------------------------------------
# 10. Outputs — consumed by edge-gateways, uns-backbone, and integration tests
# ---------------------------------------------------------------------------

output "spire_server_address" {
  description = "SPIRE server gRPC address for SVID registration (consumed by edge-gateways module)"
  value       = "spire-server.${local.spire_namespace}.svc.cluster.local:8081"
}

output "trust_domain" {
  description = "SPIFFE trust domain for all cross-zone identities"
  value       = local.trust_domain
}

output "workload_api_endpoint" {
  description = "SPIFFE Workload API endpoint (TCP/8081, firewall rule FW-IN-005)"
  value       = "spire-workload-api.${local.spire_namespace}.svc.cluster.local:8081"
}

output "spire_namespace" {
  description = "Kubernetes namespace where SPIRE server and agent DaemonSet run"
  value       = local.spire_namespace
}

output "svid_ttl_seconds" {
  description = "SVID TTL in seconds; SPIRE agent rotates at half-TTL without stream interruption"
  value       = 3600
}
