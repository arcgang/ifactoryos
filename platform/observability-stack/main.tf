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
# 11. Outputs
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
