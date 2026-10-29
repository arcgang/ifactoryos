"""
US-027 Acceptance Tests — cross-task seam verification

Acceptance criteria:
  AC1: Given any data flow crosses the OT-to-IT zone boundary, when the flow is
       initiated, then mutual TLS authentication is enforced using SPIFFE-issued
       certificates and any unauthenticated connection attempt is rejected and
       logged in the audit trail.

  AC2: Given the IEC 62443 SL2 external audit is conducted on October 1, 2025,
       when the auditor reviews the zone-and-conduit model, network diagrams, and
       penetration test results, then the audit report records zero critical findings.

  AC3: Given a container image or dependency is scanned in the CI/CD pipeline,
       when the scan completes, then any critical or high CVE causes the pipeline
       to fail and the image is blocked from deployment until the vulnerability is
       remediated.

These tests verify the SEAM between tasks — contracts that no existing unit test
covers because each unit test exercises only one task in isolation:
  - test_forwarder.py     → forwarder logic only (does not read webhook/tailer config)
  - test_mtls_spiffe.py   → edge-gateway TLS stubs only (does not import forwarder)
  The critical gap: is the payload the webhook/tailer produces COMPATIBLE with the
  forwarder's RejectionEvent model?  Does the trust domain stay consistent across
  spiffe-spire, mqtt-acl-rules.xml, and zone-conduit-model.md?  Does the CI/CD
  pipeline actually block on CVEs?  These tests answer those questions.

Run from platform/uns-backbone/forwarder/:
    pytest test_us027_acceptance.py -v
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blob_writer import MockBlobWriter
from main import app, override_writer
from models import RejectionEvent, RejectionReason

# ---------------------------------------------------------------------------
# Path constants — relative to this file so tests remain portable.
# ---------------------------------------------------------------------------

_FORWARDER_DIR = Path(__file__).parent
_UNS_BACKBONE_DIR = _FORWARDER_DIR.parent
_PLATFORM_DIR = _UNS_BACKBONE_DIR.parent
_REPO_ROOT = _PLATFORM_DIR.parent

_HIVEMQ_REJECTION_WEBHOOK_XML = (
    _UNS_BACKBONE_DIR / "helm" / "hivemq-rejection-webhook-extension.xml"
)
_HIVEMQ_VALUES_YAML = _UNS_BACKBONE_DIR / "helm" / "hivemq-values.yaml"
_MQTT_ACL_RULES_XML = _UNS_BACKBONE_DIR / "helm" / "mqtt-acl-rules.xml"
_KAFKA_ACLS_TF = _UNS_BACKBONE_DIR / "terraform" / "kafka_acls.tf"
_KAFKA_LOG_TAILER_TF = _UNS_BACKBONE_DIR / "terraform" / "kafka_log_tailer.tf"
_KAFKA_LISTENERS_TF = _UNS_BACKBONE_DIR / "terraform" / "kafka_listeners.tf"
_ZONE_CONDUIT_DOC = _PLATFORM_DIR / "edge-gateways" / "docs" / "zone-conduit-model.md"
_SPIFFE_SPIRE_MAIN_TF = _PLATFORM_DIR / "spiffe-spire" / "main.tf"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

_EXPECTED_TRUST_DOMAIN = "munich.bmw"

# Fields declared on RejectionEvent — must all appear in the webhook payload template
_REJECTION_EVENT_FIELDS = frozenset(
    {"timestamp", "source_ip", "gateway_id", "rejection_reason", "correlation_id"}
)

# All RejectionReason enum values that must be handled end-to-end
_ALL_REJECTION_REASONS = sorted(r.value for r in RejectionReason)

# Reusable valid payload; correlation_id is a valid UUID v4
_VALID_PAYLOAD_BASE: dict = {
    "timestamp": "2099-12-31T00:00:00Z",
    "source_ip": "10.200.1.1",
    "gateway_id": "spiffe://munich.bmw/edge-gateways/body/01",
    "rejection_reason": "no_cert_presented",
    "correlation_id": "a1b2c3d4-e5f6-4789-8abc-def012345678",
}

# Dockerfiles added by the ifactoryos task (AC3 precondition)
_EXPECTED_DOCKERFILES = [
    _REPO_ROOT / "services" / "defect-genome" / "Dockerfile",
    _REPO_ROOT / "services" / "hyperledger-fabric-api" / "Dockerfile",
    _REPO_ROOT / "services" / "line-command-api" / "Dockerfile",
    _REPO_ROOT / "services" / "sequence-optimizer" / "Dockerfile",
    _REPO_ROOT / "services" / "vin-digital-thread-api" / "Dockerfile",
    _REPO_ROOT / "apps" / "line-command" / "Dockerfile",
    _REPO_ROOT / "apps" / "planning-cockpit" / "Dockerfile",
    _REPO_ROOT / "apps" / "quality-studio" / "Dockerfile",
]


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """TestClient backed by a fresh MockBlobWriter; no Azure connection."""
    writer = MockBlobWriter()
    override_writer(writer)
    return TestClient(app)


# ---------------------------------------------------------------------------
# AC1 — mTLS enforcement + audit trail seam
#
# Verifies that the contracts BETWEEN the three components that implement AC1
# are consistent:
#   (i)  hivemq-rejection-webhook-extension.xml → audit-forwarder /rejections/mqtt
#   (ii) kafka_log_tailer.tf → audit-forwarder /rejections/kafka
#   (iii) HiveMQ requireClientCert=true is active
# ---------------------------------------------------------------------------


class TestAC1MtlsEnforcementAuditTrailSeam:
    """
    AC1: mTLS enforced on all OT-to-IT data flows; unauthenticated connections
    rejected and logged in the audit trail.

    Seams tested (not covered by existing unit tests):
      - Webhook config URL matches forwarder MQTT endpoint.
      - Webhook payload field names match RejectionEvent model field names.
      - Webhook trigger names match RejectionReason enum values.
      - Kafka log tailer POSTs to forwarder Kafka endpoint.
      - Kafka log tailer _REASON_MAP covers all RejectionReason enum values.
      - Forwarder accepts all 3 rejection reasons for both sources end-to-end.
    """

    def test_hivemq_requires_client_cert_is_enabled(self) -> None:
        """
        AC1 precondition: requireClientCert: true must be set in hivemq-values.yaml.
        Without this HiveMQ accepts clients with no certificate, making mTLS
        enforcement illusory.
        """
        assert _HIVEMQ_VALUES_YAML.exists(), (
            f"hivemq-values.yaml not found at {_HIVEMQ_VALUES_YAML}; "
            "HiveMQ Helm values file must exist"
        )
        content = _HIVEMQ_VALUES_YAML.read_text(encoding="utf-8")
        assert "requireClientCert: true" in content, (
            "hivemq-values.yaml does not set requireClientCert: true; "
            "without this flag the broker accepts unauthenticated MQTT clients, "
            "violating AC1 mTLS enforcement"
        )

    def test_hivemq_tls_listener_port_is_8883(self) -> None:
        """
        AC1 precondition: HiveMQ TLS listener must be on port 8883, matching
        conduit CM-013 in the zone-conduit model.
        """
        assert _HIVEMQ_VALUES_YAML.exists(), (
            f"hivemq-values.yaml not found at {_HIVEMQ_VALUES_YAML}"
        )
        content = _HIVEMQ_VALUES_YAML.read_text(encoding="utf-8")
        assert "port: 8883" in content, (
            "hivemq-values.yaml must configure port: 8883 under tls; "
            "conduit CM-013 specifies TCP/8883 as the OT-to-IT MQTT boundary port"
        )

    def test_rejection_webhook_url_targets_forwarder_mqtt_endpoint(self) -> None:
        """
        AC1 seam (webhook → forwarder): the <webhook-url> in
        hivemq-rejection-webhook-extension.xml must end with /rejections/mqtt —
        the exact path the forwarder exposes for HiveMQ rejection events.
        """
        assert _HIVEMQ_REJECTION_WEBHOOK_XML.exists(), (
            f"hivemq-rejection-webhook-extension.xml not found at "
            f"{_HIVEMQ_REJECTION_WEBHOOK_XML}; this file wires the HiveMQ "
            "rejection event to the audit-forwarder"
        )
        tree = ET.parse(_HIVEMQ_REJECTION_WEBHOOK_XML)
        root = tree.getroot()
        url_elem = root.find("webhook-url")
        assert url_elem is not None and url_elem.text, (
            "<webhook-url> element missing or empty in hivemq-rejection-webhook-extension.xml"
        )
        url = url_elem.text.strip()
        assert url.endswith("/rejections/mqtt"), (
            f"Webhook URL {url!r} does not end with /rejections/mqtt; "
            "the forwarder only accepts HiveMQ rejection events at POST /rejections/mqtt"
        )

    def test_rejection_webhook_payload_template_contains_all_rejection_event_fields(
        self,
    ) -> None:
        """
        AC1 seam (webhook → forwarder): every field of RejectionEvent must appear
        as a quoted key in the webhook payload template.  A missing field causes
        the forwarder to return 400 and the rejection is lost from the audit trail.
        """
        assert _HIVEMQ_REJECTION_WEBHOOK_XML.exists(), (
            f"hivemq-rejection-webhook-extension.xml not found at "
            f"{_HIVEMQ_REJECTION_WEBHOOK_XML}"
        )
        content = _HIVEMQ_REJECTION_WEBHOOK_XML.read_text(encoding="utf-8")
        for field in _REJECTION_EVENT_FIELDS:
            assert f'"{field}"' in content, (
                f"Field {field!r} is missing from the webhook payload template in "
                f"hivemq-rejection-webhook-extension.xml; "
                f"the forwarder's RejectionEvent model requires all five fields: "
                f"{sorted(_REJECTION_EVENT_FIELDS)}"
            )

    def test_rejection_webhook_triggers_cover_all_rejection_reason_enum_values(
        self,
    ) -> None:
        """
        AC1 seam (webhook → forwarder): every RejectionReason enum value must be
        listed as a <trigger> in the webhook config.  An unlisted reason means that
        rejection type never reaches the audit trail.
        """
        assert _HIVEMQ_REJECTION_WEBHOOK_XML.exists(), (
            f"hivemq-rejection-webhook-extension.xml not found at "
            f"{_HIVEMQ_REJECTION_WEBHOOK_XML}"
        )
        tree = ET.parse(_HIVEMQ_REJECTION_WEBHOOK_XML)
        root = tree.getroot()
        triggers_elem = root.find("triggers")
        assert triggers_elem is not None, (
            "<triggers> element missing from hivemq-rejection-webhook-extension.xml"
        )
        trigger_values = {
            t.text.strip()
            for t in triggers_elem.findall("trigger")
            if t.text
        }
        for reason in _ALL_REJECTION_REASONS:
            assert reason in trigger_values, (
                f"RejectionReason value {reason!r} is not listed as a <trigger> in "
                f"hivemq-rejection-webhook-extension.xml; "
                f"rejections of type {reason!r} will never be forwarded to the audit trail"
            )

    def test_kafka_log_tailer_posts_to_forwarder_kafka_endpoint(self) -> None:
        """
        AC1 seam (log tailer → forwarder): the Kafka log tailer sidecar must POST
        to /rejections/kafka — the path the forwarder exposes for Kafka auth failures.
        """
        assert _KAFKA_LOG_TAILER_TF.exists(), (
            f"kafka_log_tailer.tf not found at {_KAFKA_LOG_TAILER_TF}"
        )
        content = _KAFKA_LOG_TAILER_TF.read_text(encoding="utf-8")
        assert "/rejections/kafka" in content, (
            "kafka_log_tailer.tf does not reference /rejections/kafka; "
            "the Kafka log tailer must POST to the forwarder's Kafka endpoint, "
            "not the MQTT endpoint"
        )

    def test_kafka_log_tailer_reason_map_covers_all_rejection_reason_enum_values(
        self,
    ) -> None:
        """
        AC1 seam (log tailer → forwarder): every RejectionReason enum value must
        appear as a value in the log tailer's _REASON_MAP so every Kafka auth failure
        class reaches the audit trail with the correct reason label.
        """
        assert _KAFKA_LOG_TAILER_TF.exists(), (
            f"kafka_log_tailer.tf not found at {_KAFKA_LOG_TAILER_TF}"
        )
        content = _KAFKA_LOG_TAILER_TF.read_text(encoding="utf-8")
        for reason in _ALL_REJECTION_REASONS:
            assert f'"{reason}"' in content, (
                f"RejectionReason value {reason!r} not found in kafka_log_tailer.tf; "
                f"the Kafka log tailer _REASON_MAP must produce all three reason labels "
                f"so every Kafka auth failure type is forwarded correctly"
            )

    @pytest.mark.parametrize("rejection_reason", _ALL_REJECTION_REASONS)
    def test_forwarder_mqtt_endpoint_accepts_all_rejection_reasons(
        self, client: TestClient, rejection_reason: str
    ) -> None:
        """
        AC1 end-to-end: POST /rejections/mqtt with each RejectionReason value
        must return 204.  Ensures the forwarder does not silently reject any
        event type that the HiveMQ webhook produces.
        """
        payload = {**_VALID_PAYLOAD_BASE, "rejection_reason": rejection_reason}
        resp = client.post("/rejections/mqtt", json=payload)
        assert resp.status_code == 204, (
            f"POST /rejections/mqtt with rejection_reason={rejection_reason!r} "
            f"returned HTTP {resp.status_code}; expected 204. "
            f"Every HiveMQ webhook event type must be accepted by the forwarder."
        )

    @pytest.mark.parametrize("rejection_reason", _ALL_REJECTION_REASONS)
    def test_forwarder_kafka_endpoint_accepts_all_rejection_reasons(
        self, client: TestClient, rejection_reason: str
    ) -> None:
        """
        AC1 end-to-end: POST /rejections/kafka with each RejectionReason value
        must return 204.  Ensures every Kafka auth failure type is persisted to WORM.
        """
        payload = {**_VALID_PAYLOAD_BASE, "rejection_reason": rejection_reason}
        resp = client.post("/rejections/kafka", json=payload)
        assert resp.status_code == 204, (
            f"POST /rejections/kafka with rejection_reason={rejection_reason!r} "
            f"returned HTTP {resp.status_code}; expected 204. "
            f"Every Kafka log tailer event type must be accepted by the forwarder."
        )

    def test_both_sources_write_independent_blobs_to_audit_trail(self) -> None:
        """
        AC1 end-to-end: one MQTT rejection + one Kafka rejection → exactly 2 blobs
        written to WORM storage, one per source.  Both OT-to-IT crossing types
        must reach the audit trail independently.
        """
        writer = MockBlobWriter()
        override_writer(writer)
        tc = TestClient(app)

        # Two distinct correlation IDs — both valid UUID v4
        mqtt_id = "a1b2c3d4-e5f6-4789-8abc-def012345678"
        kafka_id = "b2c3d4e5-f6a7-4890-9bcd-ef0123456789"

        r1 = tc.post("/rejections/mqtt", json={**_VALID_PAYLOAD_BASE, "correlation_id": mqtt_id})
        r2 = tc.post("/rejections/kafka", json={**_VALID_PAYLOAD_BASE, "correlation_id": kafka_id})
        assert r1.status_code == 204, f"MQTT rejection POST failed: {r1.status_code}"
        assert r2.status_code == 204, f"Kafka rejection POST failed: {r2.status_code}"

        assert len(writer.writes) == 2, (
            f"Expected 2 WORM blob writes (mqtt + kafka); got {len(writer.writes)}. "
            "Both rejection sources must independently reach the audit trail."
        )
        sources = {w[0] for w in writer.writes}
        assert sources == {"mqtt", "kafka"}, (
            f"Expected blob sources {{mqtt, kafka}}; got {sources!r}"
        )


# ---------------------------------------------------------------------------
# AC2 — IEC 62443 SL2 audit readiness seam
#
# Verifies trust domain consistency across all tasks and the completeness
# of the Kafka ACL matrix — gaps would constitute critical audit findings.
# ---------------------------------------------------------------------------


class TestAC2IEC62443SL2AuditReadinessSeam:
    """
    AC2: IEC 62443 SL2 audit records zero critical findings.

    Seams tested:
      - spiffe-spire trust domain == mqtt-acl-rules.xml SPIFFE ID domain == zone-conduit doc.
        Any inconsistency means some identity operates outside the controlled CA.
      - Kafka SC-002 ACL matrix has exactly 20 entries (10 service + 10 edge gateway).
        Fewer entries means a principal has uncontrolled access to Kafka topics.
      - Kafka EXTERNAL listener enforces ssl.client.auth=required.
    """

    def test_spiffe_spire_declares_munich_bmw_trust_domain(self) -> None:
        """
        AC2 precondition: spiffe-spire/main.tf must declare trust_domain = munich.bmw,
        which is the root CA anchor for all SPIFFE SVIDs in the system.
        """
        assert _SPIFFE_SPIRE_MAIN_TF.exists(), (
            f"spiffe-spire/main.tf not found at {_SPIFFE_SPIRE_MAIN_TF}"
        )
        content = _SPIFFE_SPIRE_MAIN_TF.read_text(encoding="utf-8")
        assert re.search(
            rf'trust_domain\s*=\s*"{re.escape(_EXPECTED_TRUST_DOMAIN)}"', content
        ), (
            f"spiffe-spire/main.tf does not declare "
            f'trust_domain = "{_EXPECTED_TRUST_DOMAIN}"; '
            "all cross-zone SPIFFE SVIDs must be rooted in this single trust domain"
        )

    def test_mqtt_acl_rules_reference_only_munich_bmw_trust_domain(self) -> None:
        """
        AC2 seam (spiffe-spire → uns-backbone MQTT ACLs): every SPIFFE URI in
        mqtt-acl-rules.xml must be in the spiffe://munich.bmw trust domain.
        A foreign trust domain in the ACL would allow an uncontrolled identity
        to publish to the MQTT broker — a critical IEC 62443 finding.
        """
        assert _MQTT_ACL_RULES_XML.exists(), (
            f"mqtt-acl-rules.xml not found at {_MQTT_ACL_RULES_XML}"
        )
        content = _MQTT_ACL_RULES_XML.read_text(encoding="utf-8")
        spiffe_domains = re.findall(r"spiffe://([^/\"<\s]+)", content)
        assert spiffe_domains, (
            "No SPIFFE IDs found in mqtt-acl-rules.xml; the ACL rules must reference "
            "gateway SPIFFE IDs for per-gateway authorisation"
        )
        for domain in spiffe_domains:
            assert domain == _EXPECTED_TRUST_DOMAIN, (
                f"SPIFFE domain {domain!r} in mqtt-acl-rules.xml is not "
                f"{_EXPECTED_TRUST_DOMAIN!r}; all ACL identities must be under the "
                "single approved trust domain provisioned by spiffe-spire"
            )

    def test_zone_conduit_document_trust_domain_matches_spire(self) -> None:
        """
        AC2 seam (spiffe-spire → edge-gateways zone-conduit doc): the zone-conduit
        model must name the same trust domain as spiffe-spire/main.tf.
        A stale or divergent document would misrepresent the actual security posture
        to the auditor — a critical finding.
        """
        assert _ZONE_CONDUIT_DOC.exists(), (
            f"zone-conduit-model.md not found at {_ZONE_CONDUIT_DOC}"
        )
        content = _ZONE_CONDUIT_DOC.read_text(encoding="utf-8")
        assert _EXPECTED_TRUST_DOMAIN in content, (
            f"Trust domain {_EXPECTED_TRUST_DOMAIN!r} not found in zone-conduit-model.md; "
            "the audit document must name the same trust domain as the SPIRE server"
        )

    def test_zone_conduit_document_declares_no_additional_trust_roots(self) -> None:
        """
        AC2: zone-conduit-model.md must explicitly state that no additional trust roots
        beyond munich.bmw are introduced.  IEC 62443 SL2 requires a single controlled
        trust anchor; an undocumented second root is a critical finding.
        """
        assert _ZONE_CONDUIT_DOC.exists(), (
            f"zone-conduit-model.md not found at {_ZONE_CONDUIT_DOC}"
        )
        content = _ZONE_CONDUIT_DOC.read_text(encoding="utf-8")
        assert "No additional trust roots" in content, (
            "zone-conduit-model.md must explicitly state that no additional trust roots "
            "are introduced; the auditor requires this as SL2 evidence"
        )

    def test_kafka_sc002_acl_matrix_has_ten_service_principal_entries(self) -> None:
        """
        AC2 seam (uns-backbone Kafka ACLs): kafka_acls.tf must declare exactly
        10 service-principal ACL entries in the sc002_acls local.
        Missing entries mean a service principal has uncontrolled Kafka access.
        """
        assert _KAFKA_ACLS_TF.exists(), (
            f"kafka_acls.tf not found at {_KAFKA_ACLS_TF}"
        )
        content = _KAFKA_ACLS_TF.read_text(encoding="utf-8")
        # Each sc002_acl entry begins with `{ key = "..."` followed by `principal`
        entries = re.findall(r'\{\s*key\s*=\s*"[^"]+"\s*principal', content)
        assert len(entries) == 10, (
            f"Expected 10 service ACL entries in sc002_acls local in kafka_acls.tf; "
            f"found {len(entries)}. "
            "The SC-002 matrix must cover all 10 service-principal rows."
        )

    def test_kafka_sc002_acl_matrix_covers_all_five_edge_gateway_shops(self) -> None:
        """
        AC2 seam (uns-backbone Kafka ACLs): kafka_acls.tf must list all 5 shops in
        edge_shops so all 10 edge-gateway principals (2 per shop) get Kafka ACLs.
        A missing shop means its gateways can write to any topic — implicit DENY is
        insufficient when the schema is incomplete.
        """
        assert _KAFKA_ACLS_TF.exists(), (
            f"kafka_acls.tf not found at {_KAFKA_ACLS_TF}"
        )
        content = _KAFKA_ACLS_TF.read_text(encoding="utf-8")
        match = re.search(
            r'edge_shops\s*=\s*\[([^\]]+)\]', content, re.DOTALL
        )
        assert match is not None, (
            "edge_shops local not found in kafka_acls.tf; "
            "all five shops must be listed for edge-gateway ACL generation"
        )
        shops = re.findall(r'"([^"]+)"', match.group(1))
        expected_shops = {"inbound", "body", "paint", "assembly", "quality"}
        assert set(shops) == expected_shops, (
            f"edge_shops in kafka_acls.tf is {set(shops)!r}; "
            f"expected {expected_shops!r} — all five shops must have Kafka ACLs"
        )

    def test_kafka_external_listener_requires_ssl_client_auth(self) -> None:
        """
        AC2 seam (uns-backbone Kafka listener): the EXTERNAL listener must set
        ssl.client.auth=required so any Kafka client without a SPIFFE SVID is
        rejected at the TLS handshake — not just denied by ACL.
        """
        assert _KAFKA_LISTENERS_TF.exists(), (
            f"kafka_listeners.tf not found at {_KAFKA_LISTENERS_TF}"
        )
        content = _KAFKA_LISTENERS_TF.read_text(encoding="utf-8")
        assert "ssl.client.auth=required" in content, (
            "kafka_listeners.tf must set ssl.client.auth=required on the EXTERNAL "
            "listener; without this Kafka accepts unauthenticated connections which "
            "the log tailer cannot detect in time to satisfy the audit trail SLA"
        )


# ---------------------------------------------------------------------------
# AC3 — CVE scanning CI/CD gate
#
# Verifies that Dockerfiles exist for all deployed services (precondition: images
# to scan) AND that the GitHub Actions workflows include a blocking Trivy or Snyk
# scan step configured to fail on CRITICAL or HIGH severity CVEs.
# ---------------------------------------------------------------------------


class TestAC3CVEScanningCICDGate:
    """
    AC3: Critical or high CVE causes pipeline to fail; image blocked from deployment.

    Seams tested:
      - A Dockerfile exists for each service/app (ifactoryos task output).
      - GitHub Actions workflow includes a Trivy or Snyk scan step (ifactoryos task).
      - The scan step is configured to exit non-zero on CRITICAL or HIGH CVEs.
    """

    @pytest.mark.parametrize(
        "dockerfile_path",
        _EXPECTED_DOCKERFILES,
        ids=[f"{p.parent.parent.name}/{p.parent.name}" for p in _EXPECTED_DOCKERFILES],
    )
    def test_dockerfile_exists_for_each_deployed_service(
        self, dockerfile_path: Path
    ) -> None:
        """
        AC3 precondition: a Dockerfile must exist for every service and app so the
        CVE scanner has a build target.  The ifactoryos task must add these files.
        """
        assert dockerfile_path.exists(), (
            f"Dockerfile not found at {dockerfile_path}; "
            "the ifactoryos task must add a Dockerfile for every deployed service "
            "so the CI/CD CVE scanning gate has an image to scan"
        )

    def test_ci_pipeline_includes_trivy_or_snyk_container_scan_step(self) -> None:
        """
        AC3: at least one GitHub Actions workflow must include a Trivy or Snyk
        container-scanning step.  Without a scan step, CVEs are never detected
        and the gate cannot block deployment.
        """
        assert _WORKFLOWS_DIR.exists(), (
            f".github/workflows/ directory not found at {_WORKFLOWS_DIR}; "
            "CI/CD workflows must exist for the CVE scanning gate to run"
        )
        workflow_files = (
            list(_WORKFLOWS_DIR.glob("*.yml"))
            + list(_WORKFLOWS_DIR.glob("*.yaml"))
        )
        assert workflow_files, (
            "No workflow YAML files found in .github/workflows/; "
            "the CI/CD pipeline cannot scan for CVEs without a workflow definition"
        )

        combined = "\n".join(f.read_text(encoding="utf-8") for f in workflow_files)

        has_trivy = (
            "aquasecurity/trivy-action" in combined
            or "trivy image" in combined.lower()
        )
        has_snyk = "snyk" in combined.lower()

        assert has_trivy or has_snyk, (
            f"No Trivy or Snyk scanning step found across "
            f"{[wf.name for wf in workflow_files]}. "
            "AC3 requires the CI/CD pipeline to include a container image scan — "
            "use aquasecurity/trivy-action or 'snyk container test'."
        )

    def test_ci_pipeline_scan_is_configured_to_fail_on_critical_or_high_cve(
        self,
    ) -> None:
        """
        AC3: the CVE scanning step must be configured to exit non-zero on CRITICAL
        or HIGH severity vulnerabilities.  A scan that only reports without failing
        does not block deployment and does not satisfy AC3.
        """
        assert _WORKFLOWS_DIR.exists(), (
            f".github/workflows/ directory not found at {_WORKFLOWS_DIR}"
        )
        workflow_files = (
            list(_WORKFLOWS_DIR.glob("*.yml"))
            + list(_WORKFLOWS_DIR.glob("*.yaml"))
        )
        assert workflow_files, "No workflow YAML files found"

        combined = "\n".join(f.read_text(encoding="utf-8") for f in workflow_files)

        # Trivy blocking: exit-code: '1' + CRITICAL (and optionally HIGH) listed
        trivy_blocking = "exit-code: '1'" in combined and "CRITICAL" in combined

        # Snyk blocking: 'snyk container test' fails by default on high/critical;
        # explicit flag also acceptable
        snyk_blocking = "snyk container test" in combined or (
            "snyk test" in combined and "--severity-threshold" in combined
        )

        assert trivy_blocking or snyk_blocking, (
            "No blocking CVE scan configuration found in .github/workflows/. "
            "Trivy requires exit-code: '1' with severity: 'CRITICAL,HIGH'; "
            "Snyk requires 'snyk container test' (fails by default) or "
            "'--severity-threshold=high'. "
            "A scan step that does not exit non-zero on CRITICAL/HIGH CVEs cannot "
            "block deployment and therefore does not satisfy AC3."
        )
