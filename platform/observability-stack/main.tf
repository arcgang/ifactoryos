# observability-stack — Prometheus, Grafana, OpenTelemetry, IBM Instana
# LLD component cmp:observability-stack. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}
