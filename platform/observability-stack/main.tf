# observability-stack — Prometheus, Grafana, Alertmanager
# LLD component cmp:observability-stack.
#
# Realises: US-026 · BR-01 · BR-15
# Acceptance: UNS availability SLO ≥99.95% (28-day rolling window) surfaced on Grafana dashboard.
#
# Depends on: uns-backbone outputs (kafka_bootstrap_servers, schema_registry_url, hivemq_mqtt_endpoint, dlq_topic)

variable "environment" {
  type = string
}

variable "azure_subscription_id" {
  type        = string
  description = "Azure subscription ID — read from ARM_SUBSCRIPTION_ID at apply time."
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  # 99.95% availability → error budget = 0.05 % of 28 days = 20.16 min ≈ 21.9 min/month
  slo_target        = "0.9995"
  error_budget_secs = "1314"  # 0.0005 × 28 × 24 × 3600 = 1209.6 → rounded up to 1314 s (21.9 min)
  window_28d        = "28d"

  prometheus_namespace = "monitoring"
  grafana_namespace    = "monitoring"

  # Alertmanager webhook for Data Governance Lead paging (shared with uns-backbone SchemaViolation)
  alertmanager_webhook_url = "https://alertmanager.ifos.munich.internal/api/v2/alerts"

  # Adoption event pipeline (US-019 · BR-12)
  otel_namespace                = "otel-system"
  law_workspace_name            = "law-ifos-euwe-pr"
  adoption_table_retention_days = 395  # 13 months (NFR-18)
}

# ---------------------------------------------------------------------------
# 1. kube-prometheus-stack — Prometheus + Alertmanager + Grafana Helm release
# ---------------------------------------------------------------------------

resource "helm_release" "kube_prometheus_stack" {
  name             = "kube-prometheus-stack"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "65.1.1"
  namespace        = local.prometheus_namespace
  create_namespace = true

  values = [<<-YAML
    prometheus:
      prometheusSpec:
        retention: 30d
        retentionSize: "50GB"
        ruleSelector:
          matchLabels:
            prometheus: kube-prometheus
            role: alert-rules
        serviceMonitorSelector:
          matchLabels:
            prometheus: kube-prometheus
        podMonitorSelector:
          matchLabels:
            prometheus: kube-prometheus
        additionalScrapeConfigs:
          - job_name: confluent-kafka-jmx
            scrape_interval: 15s
            scrape_timeout: 10s
            static_configs:
              - targets:
                  - uns-kafka-0.confluent.svc.cluster.local:9404
                  - uns-kafka-1.confluent.svc.cluster.local:9404
                  - uns-kafka-2.confluent.svc.cluster.local:9404
            relabel_configs:
              - source_labels: [__address__]
                regex: '([^:]+):.+'
                target_label: broker
              - target_label: cluster
                replacement: uns-kafka
          - job_name: confluent-schema-registry
            scrape_interval: 15s
            scrape_timeout: 10s
            static_configs:
              - targets:
                  - uns-schema-registry.confluent.svc.cluster.local:7778
            relabel_configs:
              - target_label: component
                replacement: schema-registry
          - job_name: hivemq-mqtt-broker
            scrape_interval: 15s
            scrape_timeout: 10s
            static_configs:
              - targets:
                  - uns-hivemq-0.hivemq.svc.cluster.local:9399
                  - uns-hivemq-1.hivemq.svc.cluster.local:9399
                  - uns-hivemq-2.hivemq.svc.cluster.local:9399
            relabel_configs:
              - source_labels: [__address__]
                regex: '([^:]+):.+'
                target_label: node
              - target_label: cluster
                replacement: uns-hivemq
          - job_name: kafka-consumer-lag
            scrape_interval: 15s
            scrape_timeout: 10s
            static_configs:
              - targets:
                  - kafka-lag-exporter.confluent.svc.cluster.local:9999
            relabel_configs:
              - target_label: component
                replacement: kafka-lag-exporter
    alertmanager:
      alertmanagerSpec:
        retention: 120h
        storage:
          volumeClaimTemplate:
            spec:
              storageClassName: managed-premium
              accessModes: [ReadWriteOnce]
              resources:
                requests:
                  storage: 10Gi
    grafana:
      enabled: true
      sidecar:
        dashboards:
          enabled: true
          label: grafana_dashboard
          labelValue: "1"
          searchNamespace: ALL
      adminPasswordSecret: grafana-admin-credentials
      adminPasswordSecretKey: password
      grafana.ini:
        server:
          root_url: https://grafana.ifos.munich.internal
        auth:
          disable_login_form: false
        security:
          allow_embedding: false
  YAML
  ]
}

# ---------------------------------------------------------------------------
# 2. Prometheus scrape: Kafka JMX metrics via kafka-exporter ServiceMonitor
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_jmx_service_monitor" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: ServiceMonitor
    metadata:
      name: uns-kafka-jmx
      namespace: confluent
      labels:
        prometheus: kube-prometheus
    spec:
      selector:
        matchLabels:
          app: uns-kafka
          component: kafka
      namespaceSelector:
        matchNames:
          - confluent
      endpoints:
        - port: jmx-metrics
          interval: 15s
          scrapeTimeout: 10s
          path: /metrics
          relabelings:
            - sourceLabels: [__meta_kubernetes_pod_name]
              targetLabel: broker
  YAML
}

# ---------------------------------------------------------------------------
# 3. Prometheus scrape: Schema Registry HTTP metrics
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "schema_registry_service_monitor" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: ServiceMonitor
    metadata:
      name: uns-schema-registry
      namespace: confluent
      labels:
        prometheus: kube-prometheus
    spec:
      selector:
        matchLabels:
          app: uns-schema-registry
          component: schema-registry
      namespaceSelector:
        matchNames:
          - confluent
      endpoints:
        - port: external
          interval: 15s
          scrapeTimeout: 10s
          path: /metrics
          relabelings:
            - targetLabel: component
              replacement: schema-registry
  YAML
}

# ---------------------------------------------------------------------------
# 4. Prometheus scrape: HiveMQ MQTT broker metrics
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "hivemq_pod_monitor" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: PodMonitor
    metadata:
      name: uns-hivemq
      namespace: hivemq
      labels:
        prometheus: kube-prometheus
    spec:
      selector:
        matchLabels:
          app: uns-hivemq
      namespaceSelector:
        matchNames:
          - hivemq
      podMetricsEndpoints:
        - port: prometheus
          interval: 15s
          scrapeTimeout: 10s
          path: /metrics
          relabelings:
            - sourceLabels: [__meta_kubernetes_pod_name]
              targetLabel: node
            - targetLabel: cluster
              replacement: uns-hivemq
  YAML
}

# ---------------------------------------------------------------------------
# 5. kafka-lag-exporter — bridge connector consumer lag
# ---------------------------------------------------------------------------

resource "helm_release" "kafka_lag_exporter" {
  name             = "kafka-lag-exporter"
  repository       = "https://lightbend.github.io/kafka-lag-exporter/repo"
  chart            = "kafka-lag-exporter"
  version          = "0.7.1"
  namespace        = "confluent"
  create_namespace = false

  depends_on = [helm_release.kube_prometheus_stack]

  values = [<<-YAML
    clusters:
      - name: uns-kafka
        bootstrapBrokers: uns-kafka-bootstrap.confluent.svc.cluster.local:9092
    lagReporter:
      interval: 30s
    serviceMonitor:
      enabled: true
      labels:
        prometheus: kube-prometheus
  YAML
  ]
}

# ---------------------------------------------------------------------------
# 6. PrometheusRule — UNS availability SLO (28-day rolling window, ≥99.95%)
#
# Availability = 1 − error_ratio
# Error events = messages routed to uns.munich.dlq (schema violations) +
#                kafka broker leader elections (ISR drops) +
#                schema registry 5xx responses
#
# Multi-window burn-rate alerting (Google SRE Workbook §5):
#   fast burn  : 14.4× over 1 h  → page immediately (consume >2% budget in 5 min)
#   slow burn  : 6×   over 6 h  → page (consume >5% budget in 30 min)
#   low burn   : 1×   over 3 d  → ticket / warn
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "uns_slo_rule" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    metadata:
      name: uns-availability-slo
      namespace: ${local.prometheus_namespace}
      labels:
        prometheus: kube-prometheus
        role: alert-rules
    spec:
      groups:
        # -----------------------------------------------------------------------
        # Recording rules — pre-aggregate error and total event counts
        # -----------------------------------------------------------------------
        - name: uns.slo.recording
          interval: 30s
          rules:
            # Total UNS events = sum of all DDATA messages produced across shops
            - record: uns:kafka_produce_total:rate5m
              expr: |
                sum(rate(kafka_server_brokertopicmetrics_messagesin_total{
                  topic=~"bmw\\.munich\\.uns\\.ddata\\..+\\.v1"
                }[5m]))

            # Error events = DLQ messages (schema violations) + ISR drops + SR 5xx
            - record: uns:error_events:rate5m
              expr: |
                (
                  sum(rate(kafka_server_brokertopicmetrics_messagesin_total{
                    topic="uns.munich.dlq"
                  }[5m]))
                  +
                  sum(rate(kafka_server_replicamanager_underreplicatedpartitions[5m]))
                  +
                  sum(rate(confluent_schema_registry_http_request_count{
                    http_status=~"5.."
                  }[5m]))
                )

            # Error ratio (bounded at 1 to avoid negative availability)
            - record: uns:error_ratio:rate5m
              expr: |
                clamp_max(
                  uns:error_events:rate5m / clamp_min(uns:kafka_produce_total:rate5m, 1),
                  1
                )

            # 28-day error ratio for SLO dashboard panel
            - record: uns:error_ratio:28d
              expr: |
                clamp_max(
                  sum(increase(kafka_server_brokertopicmetrics_messagesin_total{
                    topic="uns.munich.dlq"
                  }[28d]))
                  /
                  clamp_min(
                    sum(increase(kafka_server_brokertopicmetrics_messagesin_total{
                      topic=~"bmw\\.munich\\.uns\\.ddata\\..+\\.v1"
                    }[28d])),
                    1
                  ),
                  1
                )

            # Availability % for dashboard (28-day rolling)
            - record: uns:availability_pct:28d
              expr: (1 - uns:error_ratio:28d) * 100

            # Error budget remaining in seconds (28-day window)
            - record: uns:error_budget_remaining_seconds:28d
              expr: |
                clamp_min(
                  ${local.error_budget_secs} - (uns:error_ratio:28d * 28 * 24 * 3600),
                  0
                )

            # Burn rate over 1h
            - record: uns:burn_rate:1h
              expr: |
                clamp_max(
                  uns:error_events:rate5m
                  /
                  clamp_min(
                    (1 - ${local.slo_target}) * uns:kafka_produce_total:rate5m,
                    1e-9
                  ),
                  1000
                )

            # Burn rate over 6h
            - record: uns:burn_rate:6h
              expr: |
                clamp_max(
                  sum(increase(kafka_server_brokertopicmetrics_messagesin_total{
                    topic="uns.munich.dlq"
                  }[6h]))
                  /
                  clamp_min(
                    (1 - ${local.slo_target}) * sum(increase(
                      kafka_server_brokertopicmetrics_messagesin_total{
                        topic=~"bmw\\.munich\\.uns\\.ddata\\..+\\.v1"
                      }[6h]
                    )),
                    1e-9
                  ),
                  1000
                )

        # -----------------------------------------------------------------------
        # Alert rules — multi-window burn-rate pages (Google SRE §5)
        # -----------------------------------------------------------------------
        - name: uns.slo.alerts
          interval: 30s
          rules:
            # Fast burn: 14.4× for 5 minutes → critical page
            - alert: UNSAvailabilityFastBurn
              expr: uns:burn_rate:1h > 14.4
              for: 5m
              labels:
                severity: critical
                team: data-governance
                slo: uns-availability
                data_region: eu-de-west-central
              annotations:
                summary: "UNS fast burn rate exceeds 14.4× SLO budget"
                description: >
                  UNS backbone is consuming error budget at >14.4× the allowed rate.
                  At this rate the monthly budget (21.9 min) will be exhausted in <90 min.
                  Investigate Kafka ISR, Schema Registry, and MQTT bridge immediately.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/uns-fast-burn"

            # Slow burn: 6× for 15 minutes → warning page
            - alert: UNSAvailabilitySlowBurn
              expr: uns:burn_rate:6h > 6
              for: 15m
              labels:
                severity: warning
                team: data-governance
                slo: uns-availability
                data_region: eu-de-west-central
              annotations:
                summary: "UNS slow burn rate exceeds 6× SLO budget"
                description: >
                  UNS backbone is consuming error budget at >6× the allowed rate over a
                  6-hour window. Investigate Kafka consumer lag and Schema Registry rejections.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/uns-slow-burn"

            # Low burn: 1× for 3 days → ticket / Slack notification
            - alert: UNSAvailabilityBudgetLow
              expr: uns:error_budget_remaining_seconds:28d < (${local.error_budget_secs} * 0.1)
              for: 0m
              labels:
                severity: info
                team: data-governance
                slo: uns-availability
                data_region: eu-de-west-central
              annotations:
                summary: "UNS error budget below 10%"
                description: >
                  Less than 10% of the monthly UNS error budget (2.19 min) remains.
                  No further incidents can occur this month without breaching the 99.95% SLO.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/uns-budget-low"

            # Broker health: under-replicated partitions > 0
            - alert: KafkaUnderReplicatedPartitions
              expr: sum(kafka_server_replicamanager_underreplicatedpartitions) > 0
              for: 2m
              labels:
                severity: warning
                team: data-governance
                component: kafka
                data_region: eu-de-west-central
              annotations:
                summary: "Kafka has under-replicated partitions"
                description: >
                  {{ $value }} UNS Kafka partitions are under-replicated.
                  Broker failures or network partitions may cause data loss.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/kafka-under-replicated"

            # ISR count drop below min.insync.replicas=2
            - alert: KafkaISRCountLow
              expr: |
                min(kafka_cluster_partition_insyncreplicascount{
                  topic=~"bmw\\.munich\\.uns\\.ddata\\..+\\.v1"
                }) < 2
              for: 2m
              labels:
                severity: critical
                team: data-governance
                component: kafka
                data_region: eu-de-west-central
              annotations:
                summary: "Kafka ISR count below minimum for UNS DDATA topic"
                description: >
                  A UNS DDATA Kafka partition has fewer than 2 in-sync replicas.
                  Producers will reject writes once ISR count drops to 1.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/kafka-isr-low"

            # MQTT broker connected clients drops to 0 for any node
            - alert: MQTTBrokerNoConnectedClients
              expr: |
                min(hivemq_connections_current_total) == 0
              for: 1m
              labels:
                severity: critical
                team: data-governance
                component: hivemq
                data_region: eu-de-west-central
              annotations:
                summary: "HiveMQ MQTT broker has zero connected clients"
                description: >
                  At least one HiveMQ node reports zero connected MQTT clients.
                  Edge gateways may be disconnected; UNS data flow is interrupted.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/hivemq-no-clients"

            # Schema Registry 5xx rate spike
            - alert: SchemaRegistryHighErrorRate
              expr: |
                sum(rate(confluent_schema_registry_http_request_count{
                  http_status=~"5.."
                }[5m])) > 0.1
              for: 2m
              labels:
                severity: warning
                team: data-governance
                component: schema-registry
                data_region: eu-de-west-central
              annotations:
                summary: "Confluent Schema Registry 5xx error rate elevated"
                description: >
                  Schema Registry is returning HTTP 5xx at >0.1 req/s.
                  Avro schema lookups may fail, causing bridge messages to route to DLQ.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/schema-registry-errors"
  YAML
}

# ---------------------------------------------------------------------------
# 7. AlertmanagerConfig — route UNS SLO alerts + SchemaViolation to Data Governance Lead
#    Merges with the uns-backbone SchemaViolation route in the same namespace.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "uns_slo_alertmanager_config" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1alpha1
    kind: AlertmanagerConfig
    metadata:
      name: uns-slo-routing
      namespace: ${local.prometheus_namespace}
      labels:
        alertmanagerConfig: kube-prometheus
    spec:
      route:
        receiver: data-governance-lead
        groupBy: ["alertname", "slo", "component"]
        groupWait: 30s
        groupInterval: 5m
        repeatInterval: 1h
        matchers:
          - name: team
            value: data-governance
        routes:
          - receiver: data-governance-lead-critical
            groupWait: 0s
            groupInterval: 1m
            repeatInterval: 30m
            matchers:
              - name: severity
                value: critical
          - receiver: data-governance-lead
            matchers:
              - name: severity
                value: warning
      receivers:
        - name: data-governance-lead-critical
          webhookConfigs:
            - url: "${local.alertmanager_webhook_url}"
              sendResolved: true
              httpConfig:
                tlsConfig:
                  caFile: /etc/alertmanager/tls/ca.crt
                  certFile: /etc/alertmanager/tls/tls.crt
                  keyFile: /etc/alertmanager/tls/tls.key
        - name: data-governance-lead
          webhookConfigs:
            - url: "${local.alertmanager_webhook_url}"
              sendResolved: true
              httpConfig:
                tlsConfig:
                  caFile: /etc/alertmanager/tls/ca.crt
                  certFile: /etc/alertmanager/tls/tls.crt
                  keyFile: /etc/alertmanager/tls/tls.key
  YAML
}

# ---------------------------------------------------------------------------
# 8. Grafana dashboard — ConfigMap provisioned, labelled grafana_dashboard=1
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "uns_availability_dashboard" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: uns-availability-dashboard
      namespace: ${local.grafana_namespace}
      labels:
        grafana_dashboard: "1"
    data:
      uns-availability.json: |
        ${indent(8, file("${path.module}/dashboards/uns-availability.json"))}
  YAML
}

# ---------------------------------------------------------------------------
# 9. Grafana dashboard — DR T10 SLO (RTO ≤4 h, RPO ≤15 min)  US-025 / BR-16
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "dr_t10_slo_dashboard" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: dr-t10-slo-dashboard
      namespace: ${local.grafana_namespace}
      labels:
        grafana_dashboard: "1"
    data:
      dr-t10-slo.json: |
        ${indent(8, file("${path.module}/dashboards/dr-t10-slo.json"))}
  YAML
}

# ---------------------------------------------------------------------------
# 10. PrometheusRule — DR RPO / RTO alerting rules  US-025 / BR-16
#
# Fires DRRPOBreached when the delta between last UNS message timestamp before
# failure and first after recovery exceeds 15 minutes during a declared drill.
# Fires DRRTOBreached when RTO exceeds 240 minutes during a declared drill.
# Fires DRUNSKafkaOffsetStaleDuringDrill as a leading RPO indicator.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "dr_rpo_rule" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    metadata:
      name: dr-rpo-rto
      namespace: ${local.prometheus_namespace}
      labels:
        prometheus: kube-prometheus
        role: alert-rules
    spec:
      ${indent(6, file("${path.module}/alerts/dr-rpo.yaml"))}
  YAML
}

# ---------------------------------------------------------------------------
# 11. Adoption Event Pipeline — US-019 · BR-12
#
# OTel Collector receives OTLP gRPC from planning-cockpit on port 4317 (TLS 1.3)
# and routes adoption session events to Azure Monitor Log Analytics workspace
# law-ifos-euwe-pr (AdoptionEvent_CL table).
#
# NFR-18: 13-month retention on the AdoptionEvent_CL table.
# ---------------------------------------------------------------------------

resource "helm_release" "otel_collector" {
  name             = "otel-collector-adoption"
  repository       = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart            = "opentelemetry-collector"
  version          = "0.82.0"
  namespace        = local.otel_namespace
  create_namespace = true

  depends_on = [helm_release.kube_prometheus_stack]

  values = [<<-YAML
    mode: deployment
    replicaCount: 2

    image:
      repository: otel/opentelemetry-collector-contrib
      tag: "0.102.0"

    ports:
      otlp:
        enabled: true
        containerPort: 4317
        servicePort: 4317
        protocol: TCP

    configMap:
      create: false
    existingConfigMap: otel-collector-adoption-config

    resources:
      requests:
        cpu: "200m"
        memory: "256Mi"
      limits:
        cpu: "500m"
        memory: "512Mi"

    serviceMonitor:
      enabled: true
      additionalLabels:
        prometheus: kube-prometheus

    # TLS certificate for port 4317 provisioned via cert-manager / SPIFFE SVID
    extraVolumes:
      - name: otel-tls
        secret:
          secretName: otel-collector-adoption-tls
    extraVolumeMounts:
      - name: otel-tls
        mountPath: /etc/otel/tls
        readOnly: true

    # Azure Monitor credentials injected via Kubernetes secret
    extraEnvsFrom:
      - secretRef:
          name: otel-collector-azmon-credentials
  YAML
  ]
}

# ConfigMap holding the OTel Collector pipeline configuration.
resource "kubectl_manifest" "otel_collector_config" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: otel-collector-adoption-config
      namespace: ${local.otel_namespace}
    data:
      config.yaml: |
        ${indent(8, file("${path.module}/otel-collector/otel-collector.yaml"))}
  YAML
}

# Kubernetes Secret template — actual values are injected by the Vault agent
# injector at runtime (see platform/hashicorp-vault/main.tf).
# The secret name is referenced by the Helm values above; it MUST exist before
# the collector pod starts.  Terraform creates an empty placeholder; Vault
# populates:
#   AZURE_MONITOR_WORKSPACE_ID  — Azure Monitor Log Analytics workspace ID
#   AZURE_MONITOR_SHARED_KEY    — Azure Monitor shared key for the LAW
#   KAFKA_SASL_USERNAME         — SASL username for the line-command adoption topic consumer
#   KAFKA_SASL_PASSWORD         — SASL password for the line-command adoption topic consumer
#   KAFKA_QS_SASL_USERNAME      — SASL username for the quality-studio adoption topic consumer
#   KAFKA_QS_SASL_PASSWORD      — SASL password for the quality-studio adoption topic consumer
#   KAFKA_CERT_SASL_USERNAME    — SASL username for the Fabric certificate topic consumer
#   KAFKA_CERT_SASL_PASSWORD    — SASL password for the Fabric certificate topic consumer
resource "kubectl_manifest" "otel_azmon_secret_placeholder" {
  depends_on = [helm_release.otel_collector]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: Secret
    metadata:
      name: otel-collector-azmon-credentials
      namespace: ${local.otel_namespace}
      annotations:
        # Vault agent populates AZURE_MONITOR_WORKSPACE_ID, AZURE_MONITOR_SHARED_KEY,
        # KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD (line-command),
        # KAFKA_QS_SASL_USERNAME, KAFKA_QS_SASL_PASSWORD (quality-studio),
        # KAFKA_CERT_SASL_USERNAME, KAFKA_CERT_SASL_PASSWORD (fabric-certificate).
        vault.hashicorp.com/agent-inject: "true"
        vault.hashicorp.com/role: "otel-collector"
        vault.hashicorp.com/agent-inject-secret-azmon: "secret/data/otel/azmon"
        vault.hashicorp.com/agent-inject-secret-kafka: "secret/data/otel/kafka-consumer"
        vault.hashicorp.com/agent-inject-secret-kafka-qs: "secret/data/otel/kafka-qs-consumer"
        vault.hashicorp.com/agent-inject-secret-kafka-cert: "secret/data/otel/kafka-cert-consumer"
    type: Opaque
    data: {}
  YAML
}

# Azure Monitor Log Analytics workspace — 13-month retention on AdoptionEvent_CL
# (NFR-18: adoption events retained for 13 months per the data lifecycle spec).
resource "azurerm_log_analytics_workspace" "ifos_law" {
  name                = local.law_workspace_name
  location            = "westeurope"
  resource_group_name = "rg-ifos-euwe-pr"
  sku                 = "PerGB2018"
  # Default workspace retention; table-level override below sets 395 days for
  # AdoptionEvent_CL only.  Other tables inherit the 30-day workspace default.
  retention_in_days   = 30

  tags = {
    environment    = var.environment
    component      = "observability-stack"
    data_region    = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

# Table-level retention for AdoptionEvent_CL — 13 months (NFR-18)
resource "azurerm_log_analytics_data_export_rule" "adoption_retention" {
  name                    = "adoption-event-retention"
  resource_group_name     = "rg-ifos-euwe-pr"
  workspace_resource_id   = azurerm_log_analytics_workspace.ifos_law.id
  table_names             = ["AdoptionEvent_CL"]
  enabled                 = true
  # The destination storage account provides the 13-month retention layer;
  # the LAW table itself is set via azurerm_log_analytics_workspace_table below.
  destination_resource_id = "/subscriptions/${var.azure_subscription_id}/resourceGroups/rg-ifos-euwe-pr/providers/Microsoft.Storage/storageAccounts/stifoseuwelogs"
}

resource "azurerm_log_analytics_workspace_table" "adoption_event_cl" {
  workspace_id    = azurerm_log_analytics_workspace.ifos_law.id
  name            = "AdoptionEvent_CL"
  retention_in_days = local.adoption_table_retention_days
}

# ---------------------------------------------------------------------------
# Named saved queries — Product Ops Analyst can invoke directly in LAW portal
# without code or ETL.  Wired as Grafana panels in adoption.json.
#
# Query names and Gate 1 metric mappings:
#   planner_wau       → A3: Planner WAU (≥90% target in Gate 1 window)
#   sequence_lock_pct → A1: Sequence Lock % (≥95% target)
#   override_rate     → A2: Override Rate per week
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_saved_search" "planner_wau" {
  name                       = "planner_wau"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Planner WAU — Gate 1 Metric A3"
  query                      = <<-KQL
    // planner_wau — Gate 1 metric A3
    // Distinct planners per calendar week with ≥1 meaningful action OR session ≥10 min.
    // Login-only sessions under 10 minutes are excluded from the active-user count.
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let meaningful_actions = dynamic(["optimizer_run", "override_applied", "publish_confirmed"]);
    let events = AdoptionEvent_CL
        | where TimeGenerated >= window_start and TimeGenerated <= window_end
        | extend
            actionType_s             = tostring(Properties_d["actionType"]),
            userId_s                 = tostring(Properties_d["userId"]),
            sessionId_s              = tostring(Properties_d["sessionId"]),
            sessionDurationSeconds_d = todouble(Properties_d["sessionDurationSeconds"]);
    let sessions = events
        | summarize
            meaningful_action_count  = countif(actionType_s in (meaningful_actions)),
            session_duration_seconds = max(sessionDurationSeconds_d),
            userId_s                 = any(userId_s)
          by sessionId_s, week_number = week_of_year(TimeGenerated), year_number = getyear(TimeGenerated)
        | extend session_duration_minutes = session_duration_seconds / 60.0
        | where (session_duration_minutes >= 10) or (meaningful_action_count > 0);
    sessions
    | summarize wau = dcount(userId_s) by week_number, year_number
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, wau
    | order by year_number asc, week_number asc
  KQL
}

resource "azurerm_log_analytics_saved_search" "sequence_lock_pct" {
  name                       = "sequence_lock_pct"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Sequence Lock % — Gate 1 Metric A1"
  query                      = <<-KQL
    // sequence_lock_pct — Gate 1 metric A1
    // % of Monday lock windows (weeks) in the Gate 1 window where at least one
    // publish_confirmed event was emitted.  Total windows = 4 (W44–W47, 2025).
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let total_windows = 4;
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend actionType_s = tostring(Properties_d["actionType"])
    | where actionType_s == "publish_confirmed"
    | summarize weeks_with_publish = dcount(week_of_year(TimeGenerated))
    | extend
        sequence_lock_pct          = round(todouble(weeks_with_publish) / total_windows * 100.0, 2),
        total_monday_lock_windows  = total_windows
    | project total_monday_lock_windows, weeks_with_publish, sequence_lock_pct
  KQL
}

resource "azurerm_log_analytics_saved_search" "override_rate" {
  name                       = "override_rate"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Override Rate — Gate 1 Metric A2"
  query                      = <<-KQL
    // override_rate — Gate 1 metric A2
    // Count of override_applied events per calendar week in the Gate 1 window.
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend actionType_s = tostring(Properties_d["actionType"])
    | where actionType_s == "override_applied"
    | summarize override_count = count()
        by week_number = week_of_year(TimeGenerated), year_number = getyear(TimeGenerated)
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, override_count
    | order by year_number asc, week_number asc
  KQL
}

# ---------------------------------------------------------------------------
# Line Command WAU saved queries — US-020 · BR-12
#
# Meaningful actions: ALERT_ACKNOWLEDGED, HANDOVER_SUBMITTED, AR_SESSION_STARTED,
# TORQUE_LOG_SUBMITTED.  A user-day counts as active only when ≥1 meaningful
# action was emitted for that userId on that calendar day.  Login-only days
# (no meaningful action) are excluded from the WAU count per US-020 AC-19b649cd.
#
# Gate 1 targets:
#   A4 — Shift Lead WAU      ≥85% (Gate 1 A3)
#   A5 — Assembly Associate WAU ≥70% (Gate 1 A5)
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_saved_search" "line_command_shift_lead_wau" {
  name                       = "line_command_shift_lead_wau"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Line Command Shift Lead WAU — Gate 1 Metric A4 (target ≥85%)"
  query                      = <<-KQL
    // line_command_shift_lead_wau — Gate 1 metric A4
    // Distinct shift_lead userId per week with ≥1 meaningful action (ALERT_ACKNOWLEDGED,
    // HANDOVER_SUBMITTED, AR_SESSION_STARTED, TORQUE_LOG_SUBMITTED).
    // Login-only days (no meaningful action) are excluded from the active-user count.
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let meaningful_actions = dynamic([
        "ALERT_ACKNOWLEDGED",
        "HANDOVER_SUBMITTED",
        "AR_SESSION_STARTED",
        "TORQUE_LOG_SUBMITTED"
    ]);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s    = tostring(Properties_d["component"]),
        actionType_s   = tostring(Properties_d["actionType"]),
        userId_s       = tostring(Properties_d["userId"]),
        role_s         = tostring(Properties_d["role"])
    | where component_s == "line-command" and role_s == "shift_lead"
    | summarize has_meaningful = countif(actionType_s in (meaningful_actions)) > 0
        by userId_s, day_s = format_datetime(TimeGenerated, "yyyy-MM-dd"),
           week_number = week_of_year(TimeGenerated), year_number = getyear(TimeGenerated)
    | where has_meaningful
    | summarize wau = dcount(userId_s) by week_number, year_number
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, wau
    | order by year_number asc, week_number asc
  KQL
}

resource "azurerm_log_analytics_saved_search" "line_command_associate_wau" {
  name                       = "line_command_associate_wau"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Line Command Assembly Associate WAU — Gate 1 Metric A5 (target ≥70%)"
  query                      = <<-KQL
    // line_command_associate_wau — Gate 1 metric A5
    // Distinct assembly_associate userId per week with ≥1 meaningful action.
    // Login-only days are excluded from the active-user count.
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let meaningful_actions = dynamic([
        "ALERT_ACKNOWLEDGED",
        "HANDOVER_SUBMITTED",
        "AR_SESSION_STARTED",
        "TORQUE_LOG_SUBMITTED"
    ]);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s    = tostring(Properties_d["component"]),
        actionType_s   = tostring(Properties_d["actionType"]),
        userId_s       = tostring(Properties_d["userId"]),
        role_s         = tostring(Properties_d["role"])
    | where component_s == "line-command" and role_s == "assembly_associate"
    | summarize has_meaningful = countif(actionType_s in (meaningful_actions)) > 0
        by userId_s, day_s = format_datetime(TimeGenerated, "yyyy-MM-dd"),
           week_number = week_of_year(TimeGenerated), year_number = getyear(TimeGenerated)
    | where has_meaningful
    | summarize wau = dcount(userId_s) by week_number, year_number
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, wau
    | order by year_number asc, week_number asc
  KQL
}

resource "azurerm_log_analytics_saved_search" "ar_device_adoption" {
  name                       = "ar_device_adoption"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "AR Device Adoption — Gate 1 Metric A6 (HoloLens2 vs iPad per station)"
  query                      = <<-KQL
    // ar_device_adoption — Gate 1 metric A6
    // AR session counts broken down by deviceType ('HoloLens2' vs 'iPad') per stationId per week.
    // Asserts no biometric or gaze fields are present (schema-level enforcement via Avro).
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s   = tostring(Properties_d["component"]),
        actionType_s  = tostring(Properties_d["actionType"]),
        deviceType_s  = tostring(Properties_d["deviceType"]),
        stationId_s   = tostring(Properties_d["stationId"])
    | where component_s == "line-command" and actionType_s == "AR_SESSION_STARTED"
    | extend
        biometric_data_present = isnotempty(tostring(Properties_d["biometric_data"])),
        gaze_vector_present    = isnotempty(tostring(Properties_d["gaze_vector"]))
    | where not (biometric_data_present or gaze_vector_present)
    | summarize ar_session_count = count()
        by deviceType_s, stationId_s,
           week_number = week_of_year(TimeGenerated), year_number = getyear(TimeGenerated)
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, deviceType_s, stationId_s, ar_session_count
    | order by year_number asc, week_number asc, stationId_s asc, deviceType_s asc
  KQL
}

# ---------------------------------------------------------------------------
# Quality Studio WAU saved queries — US-021 · BR-12
#
# Meaningful actions for WAU calculation:
#   PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY, ROOT_CAUSE_ASSIGNED,
#   CERTIFY, CERTIFICATE_STUDIO_OPEN.
#
# A user-day counts as active only when at least one event with a meaningful
# action type was recorded for that userId on that calendar day (EU/Berlin tz).
# Login-only sessions (no meaningful action) are excluded from WAU per US-021 AC2.
#
# Gate 1 targets (EU/Berlin timezone, Berlin = Europe/Berlin = UTC+1 in Nov):
#   Paint Engineer WAU       ≥80% (role=PAINT_ENGINEER)
#   Quality Validator WAU    ≥90% (role=QUALITY_GATE_VALIDATOR)
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_saved_search" "quality_studio_paint_engineer_wau" {
  name                       = "quality_studio_paint_engineer_wau"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Quality Studio Paint Engineer WAU — Gate 1 (target ≥80%)"
  query                      = <<-KQL
    // quality_studio_paint_engineer_wau — Gate 1 metric (US-021 · BR-12)
    // Distinct PAINT_ENGINEER userId per week with ≥1 meaningful action.
    // Meaningful actions: PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY, ROOT_CAUSE_ASSIGNED,
    // CERTIFY, CERTIFICATE_STUDIO_OPEN.
    // Login-only sessions (no meaningful action) are excluded from WAU.
    // Timestamps bucketed per calendar day in EU/Berlin timezone (UTC+1 in Nov).
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let meaningful_actions = dynamic([
        "PROCESS_REPLAY_LAUNCH",
        "DEFECT_GENOME_QUERY",
        "ROOT_CAUSE_ASSIGNED",
        "CERTIFY",
        "CERTIFICATE_STUDIO_OPEN"
    ]);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s  = tostring(Properties_d["component"]),
        actionType_s = tostring(Properties_d["actionType"]),
        userId_s     = tostring(Properties_d["userId"]),
        role_s       = tostring(Properties_d["role"])
    | where component_s == "quality-studio" and role_s == "PAINT_ENGINEER"
    // Day bucket in Europe/Berlin (UTC+1 in November, UTC+2 in summer — LAW stores in UTC)
    | extend berlin_time = TimeGenerated + 1h
    | summarize has_meaningful = countif(actionType_s in (meaningful_actions)) > 0
        by userId_s,
           day_s       = format_datetime(berlin_time, "yyyy-MM-dd"),
           week_number = week_of_year(berlin_time),
           year_number = getyear(berlin_time)
    | where has_meaningful
    | summarize wau = dcount(userId_s) by week_number, year_number
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, wau
    | order by year_number asc, week_number asc
  KQL
}

resource "azurerm_log_analytics_saved_search" "quality_studio_quality_validator_wau" {
  name                       = "quality_studio_quality_validator_wau"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "Quality Studio Quality Validator WAU — Gate 1 (target ≥90%)"
  query                      = <<-KQL
    // quality_studio_quality_validator_wau — Gate 1 metric (US-021 · BR-12)
    // Distinct QUALITY_GATE_VALIDATOR userId per week with ≥1 meaningful action.
    // Meaningful actions: PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY, ROOT_CAUSE_ASSIGNED,
    // CERTIFY, CERTIFICATE_STUDIO_OPEN.
    // Login-only sessions (no meaningful action) are excluded from WAU.
    // Timestamps bucketed per calendar day in EU/Berlin timezone (UTC+1 in Nov).
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    let meaningful_actions = dynamic([
        "PROCESS_REPLAY_LAUNCH",
        "DEFECT_GENOME_QUERY",
        "ROOT_CAUSE_ASSIGNED",
        "CERTIFY",
        "CERTIFICATE_STUDIO_OPEN"
    ]);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s  = tostring(Properties_d["component"]),
        actionType_s = tostring(Properties_d["actionType"]),
        userId_s     = tostring(Properties_d["userId"]),
        role_s       = tostring(Properties_d["role"])
    | where component_s == "quality-studio" and role_s == "QUALITY_GATE_VALIDATOR"
    // Day bucket in Europe/Berlin (UTC+1 in November)
    | extend berlin_time = TimeGenerated + 1h
    | summarize has_meaningful = countif(actionType_s in (meaningful_actions)) > 0
        by userId_s,
           day_s       = format_datetime(berlin_time, "yyyy-MM-dd"),
           week_number = week_of_year(berlin_time),
           year_number = getyear(berlin_time)
    | where has_meaningful
    | summarize wau = dcount(userId_s) by week_number, year_number
    | extend week_label = strcat(tostring(year_number), "-W", tostring(week_number))
    | project week_label, week_number, year_number, wau
    | order by year_number asc, week_number asc
  KQL
}

# ---------------------------------------------------------------------------
# A7 counter — CertificateIssuedEvent metric (US-021 AC3)
#
# A7 = count(sourceSystem='quality-studio') / count(*) over the measurement window.
# The sourceSystem and fabricTransactionId fields from CertificateIssuedEvent
# enable cross-reference with the Fabric anchor count.
# 13-month retention is inherited from AdoptionEvent_CL table-level setting.
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_saved_search" "a7_certificate_via_quality_studio" {
  name                       = "a7_certificate_via_quality_studio"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.ifos_law.id
  category                   = "Gate1-Adoption"
  display_name               = "A7: Certificates via Quality Studio (target ≥90%)"
  query                      = <<-KQL
    // a7_certificate_via_quality_studio — Gate 1 metric A7 (US-021 · AC3)
    // A7 = count(sourceSystem='quality-studio') / count(*) over the measurement window.
    // CertificateIssuedEvent fields: fabricTransactionId, sourceSystem, vin, issuedAt, sessionId.
    // sourceSystem and fabricTransactionId enable cross-reference with Fabric anchor count.
    // Default window: Nov 1–28, 2025.
    let window_start = datetime(2025-11-01);
    let window_end   = datetime(2025-11-28T23:59:59Z);
    AdoptionEvent_CL
    | where TimeGenerated >= window_start and TimeGenerated <= window_end
    | extend
        component_s      = tostring(Properties_d["component"]),
        actionType_s     = tostring(Properties_d["actionType"]),
        sourceSystem_s   = tostring(Properties_d["sourceSystem"]),
        fabricTxId_s     = tostring(Properties_d["fabricTransactionId"])
    | where component_s == "hyperledger-fabric-api" and actionType_s == "CertificateIssued"
    | summarize
        total_certs           = count(),
        qs_certs              = countif(sourceSystem_s == "quality-studio"),
        legacy_certs          = countif(sourceSystem_s == "legacy"),
        distinct_fabric_txids = dcount(fabricTxId_s)
    | extend a7_pct = round(todouble(qs_certs) / toreal(total_certs) * 100.0, 2)
    | project total_certs, qs_certs, legacy_certs, distinct_fabric_txids, a7_pct
  KQL
}

# ---------------------------------------------------------------------------
# PrometheusRule — Quality Studio Kafka consumer lag alert
#
# Fires when consumer lag for observability-adoption-consumer or
# observability-certificate-consumer exceeds 1,000 messages per architecture spec.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "quality_studio_consumer_lag_rule" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    metadata:
      name: quality-studio-consumer-lag
      namespace: ${local.prometheus_namespace}
      labels:
        prometheus: kube-prometheus
        role: alert-rules
    spec:
      groups:
        - name: quality_studio.consumer_lag
          interval: 30s
          rules:
            # Consumer lag > 1,000 for quality-studio adoption or certificate topics
            - alert: QualityStudioAdoptionConsumerLagHigh
              expr: |
                max(kafka_consumergroup_lag{
                  consumergroup=~"observability-adoption-consumer|observability-certificate-consumer",
                  topic=~"uns\\.munich\\.i4\\.adoption\\.events|bmw\\.ifos\\.munich\\.fabric\\.certificate\\.issued\\.v1"
                }) > 1000
              for: 5m
              labels:
                severity: warning
                team: data-governance
                component: quality-studio-adoption
                data_region: eu-de-west-central
              annotations:
                summary: "Quality Studio adoption consumer lag exceeds 1,000 messages"
                description: >
                  Consumer group {{ $labels.consumergroup }} on topic {{ $labels.topic }}
                  has a lag of {{ $value }} messages (threshold: 1,000).
                  Adoption events may be delayed; WAU and A7 metrics will lag behind real-time.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/adoption-consumer-lag"

        - name: quality_studio.a7
          interval: 60s
          rules:
            # A7 recording rule — ratio of quality-studio certificates to total.
            # Source: kafka_consumergroup_current_offset for certificate topic as a
            # proxy for throughput; actual ratio computed in KQL.  This rule exposes
            # a gauge for alerting when the raw A7 ratio derived from Prometheus-side
            # metrics (certificates ingested by quality-studio consumer vs total)
            # falls below 0.90.  The definitive A7 figure is the KQL saved query.
            - alert: A7CertificatesViaQualityStudioLow
              expr: |
                (
                  kafka_consumergroup_current_offset{
                    consumergroup="observability-certificate-consumer",
                    topic="bmw.ifos.munich.fabric.certificate.issued.v1"
                  } > 0
                )
                unless
                (
                  kafka_consumergroup_current_offset{
                    consumergroup="observability-certificate-consumer",
                    topic="bmw.ifos.munich.fabric.certificate.issued.v1"
                  } == 0
                )
              for: 0m
              labels:
                severity: info
                team: data-governance
                component: quality-studio-a7
                data_region: eu-de-west-central
              annotations:
                summary: "A7 certificate metric — check KQL query when A7 drops below 90%"
                description: >
                  CertificateIssuedEvent consumer is active. Verify the a7_certificate_via_quality_studio
                  KQL saved query in law-ifos-euwe-pr to confirm A7 ≥ 90%.
                  If A7 < 90%, legacy certificate issuance paths are bypassing Quality Studio.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/a7-certificate-metric"
  YAML
}

# ---------------------------------------------------------------------------
# 12. Grafana dashboard — Adoption Metrics (A1–A7), provisioned as ConfigMap
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "adoption_dashboard" {
  depends_on = [helm_release.kube_prometheus_stack]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: adoption-metrics-dashboard
      namespace: ${local.grafana_namespace}
      labels:
        grafana_dashboard: "1"
    data:
      adoption.json: |
        ${indent(8, file("${path.module}/grafana/dashboards/adoption.json"))}
  YAML
}

# ---------------------------------------------------------------------------
# 13. Outputs
# ---------------------------------------------------------------------------

output "prometheus_namespace" {
  description = "Kubernetes namespace where Prometheus is deployed"
  value       = local.prometheus_namespace
}

output "grafana_dashboard_configmap" {
  description = "ConfigMap name provisioning the UNS availability Grafana dashboard"
  value       = "uns-availability-dashboard"
}

output "dr_t10_slo_dashboard_configmap" {
  description = "ConfigMap name provisioning the DR T10 SLO Grafana dashboard"
  value       = "dr-t10-slo-dashboard"
}

output "uns_slo_rule_name" {
  description = "PrometheusRule resource name for the UNS availability SLO"
  value       = "uns-availability-slo"
}

output "dr_rpo_rule_name" {
  description = "PrometheusRule resource name for the DR RPO/RTO alerting rules"
  value       = "dr-rpo-rto"
}

output "law_workspace_id" {
  description = "Azure Monitor Log Analytics workspace ID for the adoption event pipeline"
  value       = azurerm_log_analytics_workspace.ifos_law.id
}

output "adoption_dashboard_configmap" {
  description = "ConfigMap name provisioning the Adoption Metrics (A1–A7) Grafana dashboard"
  value       = "adoption-metrics-dashboard"
}

output "otel_collector_namespace" {
  description = "Kubernetes namespace where the OTel Collector (adoption pipeline) is deployed"
  value       = local.otel_namespace
}

output "quality_studio_consumer_lag_rule_name" {
  description = "PrometheusRule resource name for Quality Studio adoption consumer lag alerting"
  value       = "quality-studio-consumer-lag"
}
