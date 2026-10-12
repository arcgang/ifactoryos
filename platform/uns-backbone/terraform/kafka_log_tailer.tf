# uns-backbone — Kafka authentication-failure log tailer
#
# Realises US-027 acceptance criterion:
#   "any unauthenticated connection attempt is rejected and logged in the audit trail"
#
# A sidecar container in the uns-kafka StatefulSet tails the Kafka broker's
# authentication-failure log file and POSTs each rejection event within 1 second
# to the audit-forwarder service at:
#   http://audit-forwarder.uns-backbone.svc.cluster.local/rejections/kafka
#
# Rejection event schema (shared contract with Child 0 and Child 2):
# {
#   "timestamp":        "<ISO-8601 UTC>",
#   "source_ip":        "<string>",
#   "gateway_id":       "<SPIFFE SVID from client TLS cert, empty string if none>",
#   "rejection_reason": "expired_cert | unknown_ca | no_cert_presented",
#   "correlation_id":   "<UUID v4>"
# }

# ---------------------------------------------------------------------------
# Namespace for the audit forwarder receiver
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "uns_backbone_namespace" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: Namespace
    metadata:
      name: uns-backbone
      labels:
        data_residency: "eu-de-west-central"
        classification: "confidential"
        iec62443-sl: "SL2"
        app.kubernetes.io/managed-by: terraform
  YAML
}

# ---------------------------------------------------------------------------
# ConfigMap — log tailer script
#
# The tailer is a Python 3 script bundled in a ConfigMap and run as a sidecar
# on the uns-kafka pods. It:
#   1. Tail-follows /var/log/kafka/kafka-authorizer.log (Kafka auth failures).
#   2. Parses each AUTHENTICATION_FAILED line.
#   3. Extracts source_ip, gateway_id (SPIFFE URI SAN from the client cert DN),
#      and rejection_reason (mapped from the Kafka error code).
#   4. POSTs the rejection event to audit-forwarder within 1 second.
#   5. Generates a UUID v4 correlation_id per event.
# ---------------------------------------------------------------------------

resource "kubernetes_config_map" "kafka_log_tailer_script" {
  metadata {
    name      = "kafka-log-tailer-script"
    namespace = "confluent"

    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      data_residency                 = "eu-de-west-central"
      iec62443_sl                    = "SL2"
    }
  }

  data = {
    "tailer.py" = <<-PYTHON
      #!/usr/bin/env python3
      """
      Kafka authentication-failure log tailer.
      Tails /var/log/kafka/kafka-authorizer.log and POSTs each rejection to
      http://audit-forwarder.uns-backbone.svc.cluster.local/rejections/kafka
      within 1 second of the log line appearing.

      Rejection reasons mapped from Kafka SSL exception messages:
        - "expired_cert"       : certificate has passed its notAfter date
        - "unknown_ca"         : signing CA not in the trust store (not spiffe://munich.bmw)
        - "no_cert_presented"  : client TLS handshake sent no certificate

      SPIFFE gateway_id extracted from the client cert's Subject CN field when the
      log line includes a principal like: "User:spiffe://munich.bmw/edge-gateways/..."
      Empty string when no cert was presented.
      """
      import json
      import re
      import time
      import uuid
      from datetime import datetime, timezone
      from urllib import request, error

      LOG_PATH = "/var/log/kafka/kafka-authorizer.log"
      AUDIT_ENDPOINT = (
          "http://audit-forwarder.uns-backbone.svc.cluster.local/rejections/kafka"
      )
      POST_TIMEOUT_SECONDS = 1

      # Kafka SSL error fragments → normalised rejection_reason
      _REASON_MAP = {
          "certificate_expired": "expired_cert",
          "certificate_unknown": "unknown_ca",
          "bad_certificate": "unknown_ca",
          "no certificate": "no_cert_presented",
          "empty certificate": "no_cert_presented",
      }

      _AUTH_FAILURE_RE = re.compile(
          r"AUTHENTICATION_FAILED.*?client=(?P<source_ip>[\d.a-fA-F:]+)"
          r".*?(?P<error_fragment>certificate_expired|certificate_unknown"
          r"|bad_certificate|no certificate|empty certificate)",
          re.IGNORECASE,
      )
      _PRINCIPAL_RE = re.compile(
          r"principal=User:(?P<principal>spiffe://[^\s,]+)", re.IGNORECASE
      )


      def _map_reason(fragment: str) -> str:
          return _REASON_MAP.get(fragment.lower(), "unknown_ca")


      def _extract_gateway_id(line: str) -> str:
          m = _PRINCIPAL_RE.search(line)
          return m.group("principal") if m else ""


      def _post_rejection(event: dict) -> None:
          payload = json.dumps(event).encode()
          req = request.Request(
              AUDIT_ENDPOINT,
              data=payload,
              headers={"Content-Type": "application/json"},
              method="POST",
          )
          try:
              with request.urlopen(req, timeout=POST_TIMEOUT_SECONDS):
                  pass
          except error.URLError:
              # Audit forwarder temporarily unreachable; emit to stderr so the
              # broker's log aggregator captures the event for retry.
              import sys
              print(
                  f"[kafka-log-tailer] WARN: audit-forwarder unavailable; "
                  f"rejection event: {json.dumps(event)}",
                  file=sys.stderr,
              )


      def _tail(path: str):
          with open(path, "r") as fh:
              fh.seek(0, 2)  # seek to end
              while True:
                  line = fh.readline()
                  if not line:
                      time.sleep(0.05)
                      continue
                  yield line


      def main() -> None:
          for line in _tail(LOG_PATH):
              m = _AUTH_FAILURE_RE.search(line)
              if not m:
                  continue
              event = {
                  "timestamp": datetime.now(timezone.utc).isoformat(),
                  "source_ip": m.group("source_ip"),
                  "gateway_id": _extract_gateway_id(line),
                  "rejection_reason": _map_reason(m.group("error_fragment")),
                  "correlation_id": str(uuid.uuid4()),
              }
              _post_rejection(event)


      if __name__ == "__main__":
          main()
    PYTHON
  }
}

# ---------------------------------------------------------------------------
# Sidecar injection — PodPatch that adds the tailer container to uns-kafka pods
#
# Confluent for Kubernetes supports a podTemplate.containers patch on the Kafka
# CRD. We express this as a separate kubectl_manifest that patches the existing
# uns-kafka resource rather than rewriting the full kafka_cluster definition in
# main.tf, keeping the two concerns orthogonal.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_log_tailer_patch" {
  depends_on = [
    kubernetes_config_map.kafka_log_tailer_script,
  ]

  force_conflicts = true

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: Kafka
    metadata:
      name: uns-kafka
      namespace: confluent
    spec:
      podTemplate:
        containers:
          - name: kafka-log-tailer
            image: python:3.12-slim
            command:
              - python3
              - /opt/tailer/tailer.py
            resources:
              requests:
                cpu: "50m"
                memory: "64Mi"
              limits:
                cpu: "100m"
                memory: "128Mi"
            securityContext:
              runAsNonRoot: true
              runAsUser: 1000
              readOnlyRootFilesystem: true
              allowPrivilegeEscalation: false
            volumeMounts:
              - name: kafka-logs
                mountPath: /var/log/kafka
                readOnly: true
              - name: tailer-script
                mountPath: /opt/tailer
                readOnly: true
        volumes:
          - name: tailer-script
            configMap:
              name: kafka-log-tailer-script
              defaultMode: 0555
  YAML
}

# ---------------------------------------------------------------------------
# audit-forwarder Deployment — receives rejection POSTs and writes to immutable-audit-log
#
# This lightweight service accepts the rejection event from the log tailer and
# forwards it to the immutable-audit-log (Azure Blob WORM) via the audit-log
# client library already provisioned under platform/immutable-audit-log/.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "audit_forwarder_deployment" {
  depends_on = [kubectl_manifest.uns_backbone_namespace]

  yaml_body = <<-YAML
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: audit-forwarder
      namespace: uns-backbone
      labels:
        app: audit-forwarder
        app.kubernetes.io/managed-by: terraform
        data_residency: "eu-de-west-central"
        iec62443-sl: "SL2"
    spec:
      replicas: 2
      selector:
        matchLabels:
          app: audit-forwarder
      template:
        metadata:
          labels:
            app: audit-forwarder
            data_residency: "eu-de-west-central"
            iec62443-sl: "SL2"
        spec:
          serviceAccountName: audit-forwarder
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            fsGroup: 1000
          containers:
            - name: audit-forwarder
              image: ifos/audit-forwarder:1.0.0
              ports:
                - containerPort: 8080
                  name: http
              env:
                - name: AUDIT_LOG_ENDPOINT
                  value: "https://immutable-audit-log.ifos.munich.internal/api/v1/audit/event"
                - name: AUDIT_LOG_API_KEY
                  valueFrom:
                    secretKeyRef:
                      name: audit-forwarder-credentials
                      key: api-key
              resources:
                requests:
                  cpu: "100m"
                  memory: "128Mi"
                limits:
                  cpu: "200m"
                  memory: "256Mi"
              livenessProbe:
                httpGet:
                  path: /healthz
                  port: 8080
                initialDelaySeconds: 5
                periodSeconds: 10
              readinessProbe:
                httpGet:
                  path: /readyz
                  port: 8080
                initialDelaySeconds: 3
                periodSeconds: 5
  YAML
}

resource "kubectl_manifest" "audit_forwarder_service" {
  depends_on = [kubectl_manifest.audit_forwarder_deployment]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: Service
    metadata:
      name: audit-forwarder
      namespace: uns-backbone
      labels:
        app: audit-forwarder
        app.kubernetes.io/managed-by: terraform
    spec:
      selector:
        app: audit-forwarder
      ports:
        - name: http
          port: 80
          targetPort: 8080
          protocol: TCP
      type: ClusterIP
  YAML
}

resource "kubectl_manifest" "audit_forwarder_service_account" {
  depends_on = [kubectl_manifest.uns_backbone_namespace]

  yaml_body = <<-YAML
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: audit-forwarder
      namespace: uns-backbone
      labels:
        app.kubernetes.io/managed-by: terraform
        data_residency: "eu-de-west-central"
  YAML
}
