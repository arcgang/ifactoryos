# iFactoryOS UNS Backbone — Disaster Recovery Runbook

**Component:** `cmp:uns-backbone`
**Realises:** US-025 · BR-16
**RTO target:** ≤ 4 hours from failure declaration to service confirmation
**RPO target:** ≤ 15 minutes (last recoverable data point no older than 15 min before failure)
**DR region:** Azure Germany North (`germanyNorth`) — cluster `glbl-pr-ifos-euno-rgp`
**Primary region:** Azure Germany West Central (`germanyWestCentral`)

This runbook codifies the 8 steps from §8.5.4 of the iFactoryOS DR Plan. Each step lists
the responsible role, expected duration, the command(s) to run, the expected output, and a
rollback note. Do not skip steps; each has a gate check.

---

## Pre-requisites (verify before declaring incident)

- Access to the DR cluster kubeconfig (`~/.kube/config-dr` or set `KUBECONFIG`)
- Vault token with read access to `secret/ifos/dr/*`
- `kubectl`, `argocd` CLI, `az` CLI authenticated to the BMW Group Azure tenant
- Phone / Teams contact list for Plant Manager, Production Planner, and Shift Lead on hand

---

## Step 1 — Declare Incident and Open War Room

**Responsible:** On-Call Engineer (Platform Team)
**Expected duration:** 5 minutes
**Gate:** Incident declared in ServiceNow, war-room channel created, stakeholders paged

1. Open a P1 incident in ServiceNow. Note the exact UTC timestamp — this is **T₀**.
2. Create a Microsoft Teams channel `#ifos-dr-<date>` and add Platform Lead, Plant Manager,
   Production Planner, and Shift Lead.
3. Post in the channel:
   ```
   DR DRILL / INCIDENT DECLARED at <T₀> UTC
   RTO target: service restored by <T₀ + 4h> UTC
   RPO target: last data point ≤ <T₀ - 15min> UTC
   ```
4. Assign a timekeeper to track elapsed time against the 4-hour RTO.

**Rollback:** No infrastructure has changed. Incident can be closed if root cause is resolved
in primary before Step 2 begins.

---

## Step 2 — Verify DR Kafka Consumer Group Offsets

**Responsible:** Platform Engineer
**Expected duration:** 15 minutes
**Gate:** MirrorMaker 2 checkpoint offsets are current (lag < 30 s), confirming RPO ≤ 15 min

MirrorMaker 2 checkpoints consumer group offsets every 10 s. The most recent checkpoint
represents the RPO watermark. Run the following against the DR Kafka cluster:

```bash
# Set DR Kafka credentials from Vault
export DR_MQ_BOOTSTRAP=$(vault kv get -field=bootstrap secret/ifos/dr/kafka)
export DR_MQ_SMOKE_USERNAME=$(vault kv get -field=username secret/ifos/dr/kafka-smoke)
export DR_MQ_SMOKE_PASSWORD=$(vault kv get -field=password secret/ifos/dr/kafka-smoke)

# List checkpoint topics — one per consumer group
kafka-topics.sh \
  --bootstrap-server "${DR_MQ_BOOTSTRAP}" \
  --command-config /etc/kafka/dr-client.properties \
  --list | grep checkpoints

# Show latest offset for a key UNS topic on DR cluster
kafka-console-consumer.sh \
  --bootstrap-server "${DR_MQ_BOOTSTRAP}" \
  --consumer.config /etc/kafka/dr-client.properties \
  --topic bmw.munich.uns.ddata.assembly.v1 \
  --from-beginning \
  --max-messages 1 \
  --property print.timestamp=true
```

**Expected output:** Checkpoint topics listed; latest message timestamp ≤ (T₀ − 15 min).

**Verify RPO:** Compare the last message timestamp on the DR cluster against T₀.
If `(T₀ - last_timestamp) > 15 min`, MirrorMaker 2 lag is too high — escalate to Step 2b.

**Step 2b (escalation — lag > 15 min):** MirrorMaker 2 may have fallen behind. Check connector
status and restart if needed:

```bash
kubectl -n confluent get kafkamirrormaker2 uns-mirrormaker2-dr -o yaml | grep -A5 conditions
kubectl -n confluent rollout restart deployment uns-mirrormaker2-dr-connect
```

Wait 5 minutes and re-run the offset check.

**Rollback:** No traffic has been switched. Revert by resolving the primary region issue.

---

## Step 3 — Scale DR ARO Cluster to Full Replicas

**Responsible:** Platform Engineer
**Expected duration:** 20 minutes
**Gate:** All pods in `Healthy` / `Running` state at full replica count across all DR namespaces

The DR cluster runs at 50% replica count at rest. Scale to full capacity before switching traffic.

```bash
export KUBECONFIG=~/.kube/config-dr

# Scale all deployments in iFactoryOS namespaces to 2× their current replicas
for ns in ifos-planning-cockpit ifos-line-command ifos-quality-studio ifos-ai-serving confluent hivemq; do
  kubectl -n "${ns}" get deployments -o name | while read -r dep; do
    current=$(kubectl -n "${ns}" get "${dep}" -o jsonpath='{.spec.replicas}')
    kubectl -n "${ns}" scale "${dep}" --replicas=$(( current * 2 ))
  done
done

# Confirm all pods reach Running state
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded \
  --watch --timeout=15m
```

**Expected output:** Zero pods in `Pending`, `CrashLoopBackOff`, or `Error` after scaling.

**ArgoCD gate:** After scaling, verify ArgoCD Applications are `Synced` and `Healthy`:

```bash
argocd --kubeconfig ~/.kube/config-dr app list | grep -E 'dr$'
```

**Rollback:** Scale deployments back to 50% (`current / 2`) and investigate pod failures.

---

## Step 4 — Switch Azure Traffic Manager to DR Endpoint

**Responsible:** Platform Lead (requires approval from Plant Manager)
**Expected duration:** 10 minutes (including DNS propagation)
**Gate:** Traffic Manager health probe confirms DR endpoint healthy; DNS resolves to Germany North

> ⚠️ This step is **irreversible** during the drill window. Confirm with Plant Manager before
> proceeding. If DR smoke tests (Step 5) have not passed, do NOT switch.

```bash
# Retrieve Traffic Manager profile resource ID
TM_PROFILE=$(az network traffic-manager profile show \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --name "atm-ifos-prod" \
  --query id -o tsv)

# Disable primary endpoint (West Europe)
az network traffic-manager endpoint update \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --profile-name "atm-ifos-prod" \
  --name "primary-west-europe" \
  --type externalEndpoints \
  --endpoint-status Disabled

# Verify Traffic Manager now routes to DR endpoint
az network traffic-manager endpoint show \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --profile-name "atm-ifos-prod" \
  --name "secondary-germany-north" \
  --type externalEndpoints \
  --query endpointMonitorStatus
```

**Expected output:** `secondary-germany-north` endpoint status changes to `Online`.
DNS propagation completes within ~5 minutes (TTL = 60 s).

```bash
# Confirm DNS resolves to DR endpoint
dig +short ifos-prod.trafficmanager.net
nslookup ifos-prod.trafficmanager.net
```

**Rollback:**
```bash
az network traffic-manager endpoint update \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --profile-name "atm-ifos-prod" \
  --name "primary-west-europe" \
  --type externalEndpoints \
  --endpoint-status Enabled
```

---

## Step 5 — Verify SPIRE SVID Issuance

**Responsible:** Platform Engineer
**Expected duration:** 10 minutes
**Gate:** All DR-region workloads hold valid SVIDs; trust bundle exchange with primary confirmed

```bash
export KUBECONFIG=~/.kube/config-dr

# Confirm SPIRE server is running
kubectl -n spire get pods -l app=spire-server

# Confirm SPIRE agents are DaemonSet-healthy on all nodes
kubectl -n spire get pods -l app=spire-agent

# Fetch SVID for planning-cockpit workload to verify issuance
kubectl -n ifos-planning-cockpit exec -it \
  "$(kubectl -n ifos-planning-cockpit get pod -l app=planning-cockpit -o name | head -1)" \
  -- /bin/sh -c \
  "spire-agent api fetch x509 -socketPath /run/spire/sockets/agent.sock | head -20"
```

**Expected output:** X.509 SVID with SPIFFE ID
`spiffe://ifos.bmwgroup.net/ns/ifos-planning-cockpit/sa/planning-cockpit` issued, expiry ≥ 1 h.

**Verify federation with primary:**
```bash
kubectl -n spire exec -it deploy/spire-server -- \
  spire-server bundle show -format spiffe | grep "trustDomain"
```

**Rollback:** If SPIRE agents are not attested, check the k8s-psat nodeAttestor logs:
```bash
kubectl -n spire logs -l app=spire-agent --tail=50
```
Restart the agent DaemonSet: `kubectl -n spire rollout restart daemonset spire-agent`

---

## Step 6 — Execute DR Smoke Tests

**Responsible:** Platform Engineer
**Expected duration:** 15 minutes
**Gate:** All 7 smoke-test checks pass; `test_dr_smoke.py` exits 0

```bash
# Set credentials from Vault
export DR_BASE_URL=$(vault kv get -field=base_url secret/ifos/dr/endpoints)
export DR_SMOKE_TOKEN=$(vault kv get -field=token secret/ifos/dr/smoke-test)
export DR_CA_CERT=/etc/ssl/certs/ifos-dr-ca.crt
export DR_KAFKA_REST="${DR_BASE_URL}/kafka-rest"
export DR_SCHEMA_REGISTRY_URL="${DR_BASE_URL}/schema-registry"
export DR_AI_SERVING_URL="${DR_BASE_URL}/ai-serving"
export DR_MQ_SMOKE_USERNAME=$(vault kv get -field=username secret/ifos/dr/kafka-smoke)
export DR_MQ_SMOKE_PASSWORD=$(vault kv get -field=password secret/ifos/dr/kafka-smoke)

# Run the automated smoke-test suite (E2E-010)
pytest platform/uns-backbone/tests/test_dr_smoke.py -v --timeout=120

# Also run the shell smoke-test for rapid CLI verification
bash platform/uns-backbone/scripts/dr-smoke-test.sh
```

**Expected output:** All tests pass. Example:
```
PASSED platform/uns-backbone/tests/test_dr_smoke.py::TestPlanningCockpitDR::test_health_returns_200
PASSED platform/uns-backbone/tests/test_dr_smoke.py::TestLineCommandDR::test_oee_endpoint_returns_200
PASSED platform/uns-backbone/tests/test_dr_smoke.py::TestQualityStudioDR::test_process_replay_health_returns_200
...
[RESULT] PASS — all DR smoke checks passed
```

**If any test fails:**
1. Check pod logs: `kubectl -n <namespace> logs -l app=<service> --tail=100`
2. Check ArgoCD sync status: `argocd app get <app-name>-dr`
3. Do not proceed to Step 7 until all smoke tests pass.
4. If smoke tests cannot be fixed within 30 minutes, initiate rollback per Step 4.

---

## Step 7 — Notify Plant Manager, Production Planner, and Shift Lead

**Responsible:** Platform Lead
**Expected duration:** 5 minutes
**Gate:** All three stakeholders confirmed via Teams or phone

Post in `#ifos-dr-<date>` and call each stakeholder directly:

```
DR DRILL / SERVICE RESTORED at <T_restored> UTC

Summary:
  - All 3 consoles operational on Germany North DR cluster
  - UNS backbone (Kafka + Schema Registry + HiveMQ) confirmed healthy
  - AI model serving (SequenceOptimizer, DefectGenome) responding on DR
  - MirrorMaker 2 replication lag at time of switch: <lag>s (RPO ≤ 15 min ✓)
  - Elapsed from T₀ to service confirmation: <elapsed>h <elapsed_m>m (RTO ≤ 4h ✓)

Next step: Production Planner to confirm shift schedule unaffected.
```

Contacts:
- **Plant Manager:** escalate via ServiceNow CMDB (role: `plant-manager`) or Teams
- **Production Planner:** escalate via ServiceNow CMDB (role: `production-planner`)
- **Shift Lead:** contact directly via plant radio or Teams

---

## Step 8 — Document Actual RTO and RPO

**Responsible:** Platform Lead
**Expected duration:** 15 minutes
**Gate:** ServiceNow incident updated with measured RTO and RPO; drill evidence attached

1. Record the following in the ServiceNow incident:
   - **T₀**: failure declaration timestamp (UTC)
   - **T_restored**: service confirmation timestamp (UTC)
   - **Measured RTO**: T_restored − T₀ (must be ≤ 4h)
   - **Last primary message timestamp** (from Step 2 Kafka check)
   - **Measured RPO**: T₀ − last_primary_message_timestamp (must be ≤ 15 min)
   - **MirrorMaker 2 lag at failover**: seconds (from Step 2)
   - **Smoke test output**: attach `pytest` output from Step 6

2. Export the `dr-smoke-test.sh` output log and attach it to the incident.

3. Update the DR drill register in Confluence (space: `IFOS-Platform`):

   | Drill date | T₀ (UTC) | T_restored (UTC) | RTO (min) | RPO (min) | Outcome |
   |---|---|---|---|---|---|
   | 2025-09-30 | TBD | TBD | TBD | TBD | TBD |

4. If RTO > 4h or RPO > 15 min, open a follow-up P2 incident for remediation before Gate 1.

---

## Rollback — Restore Primary Region

If DR failover must be abandoned (e.g., DR cluster unstable), restore primary:

```bash
# 1. Re-enable primary Traffic Manager endpoint
az network traffic-manager endpoint update \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --profile-name "atm-ifos-prod" \
  --name "primary-west-europe" \
  --type externalEndpoints \
  --endpoint-status Enabled

# 2. Disable DR endpoint
az network traffic-manager endpoint update \
  --resource-group glbl-pr-ifos-eude-rgp-backup \
  --profile-name "atm-ifos-prod" \
  --name "secondary-germany-north" \
  --type externalEndpoints \
  --endpoint-status Disabled

# 3. Scale DR cluster back to 50% warm-standby replicas
export KUBECONFIG=~/.kube/config-dr
for ns in ifos-planning-cockpit ifos-line-command ifos-quality-studio ifos-ai-serving confluent hivemq; do
  kubectl -n "${ns}" get deployments -o name | while read -r dep; do
    current=$(kubectl -n "${ns}" get "${dep}" -o jsonpath='{.spec.replicas}')
    kubectl -n "${ns}" scale "${dep}" --replicas=$(( current / 2 ))
  done
done
```

---

## Contact Directory

| Role | Contact method | ServiceNow group |
|---|---|---|
| Platform Lead | Teams / mobile | `IFOS-Platform-Lead` |
| Platform Engineer (on-call) | PagerDuty | `IFOS-Platform-OnCall` |
| Plant Manager | Teams / plant phone | `BMW-Munich-Plant-Manager` |
| Production Planner | Teams | `BMW-Munich-Production-Planner` |
| Shift Lead | Plant radio / Teams | `BMW-Munich-Shift-Lead` |
| Security (SPIFFE/SPIRE) | Teams | `IFOS-Security-SPIRE` |
| Data Governance Lead | Teams | `IFOS-Data-Governance` |

---

*This runbook is version-controlled in `platform/uns-backbone/docs/dr-runbook.md`.
Update after every drill with measured RTO/RPO and any procedural improvements.*
