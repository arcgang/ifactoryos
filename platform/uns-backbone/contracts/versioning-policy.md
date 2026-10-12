# AsyncAPI Contract Versioning Policy

## Topic naming and version increments

Topic names carry an explicit version suffix (`.v1`, `.v2`, …).

A **new incompatible schema version** — any change that breaks existing consumers
(field removal, type change, required field addition, enum narrowing) — **requires a
new topic name** with an incremented suffix (e.g. `bmw.munich.i4.sequence.optimized.v2`).
Backward-compatible additions (new optional fields) may be applied in-place without
a version bump, provided the schema is validated and published to the Confluent Schema
Registry with `BACKWARD_TRANSITIVE` compatibility.

## Minimum support window

The `.v1` topic **must remain active and consumed for a minimum of 6 months** after the
deprecation notice for `.v2` is published.

## Issuing a deprecation notice

A deprecation notice is issued by adding the following annotations to the **channel
block** in the `.v1` AsyncAPI file:

```yaml
channels:
  bmw.munich.i4.sequence.optimized.v1:
    x-deprecated: true
    x-sunset: "2026-06-01T00:00:00Z"   # ISO-8601 date ≥ 6 months from publish date
    description: >
      DEPRECATED as of 2025-12-01. Migrate to bmw.munich.i4.sequence.optimized.v2
      before 2026-06-01. See <link-to-migration-guide>.
```

Both `x-deprecated: true` and `x-sunset` must be set together; either alone is not
sufficient to trigger the sunset countdown in the IBM Event Endpoint Management portal.

## Developer portal surfacing

The IBM Event Endpoint Management portal reads `x-deprecated` and `x-sunset` from
every registered AsyncAPI file and:

- Displays a deprecation banner on the topic's portal page.
- Surfaces the sunset date in the catalogue metadata.
- Emits a Prometheus metric (`eem_topic_deprecated_days_remaining`) that Grafana
  watches for SLO alerting.

Horizon 2 teams are expected to subscribe to deprecation notifications via the
developer portal and migrate before the sunset date.

## Semantic versioning of the AsyncAPI spec itself

Each AsyncAPI YAML carries `info.version` in semver (`1.0.0`). Changes to the spec
file that do **not** alter the Kafka schema (documentation, binding metadata,
`x-deprecation-policy` text) increment the patch version.  Schema-compatible
additions increment the minor version.  Breaking schema changes are prohibited in-place
and must result in a new topic name as described above.

## Governance enforcement

- The CI pipeline runs `asyncapi validate` against every YAML in `contracts/asyncapi/`
  on every pull request.  A validation error blocks merge.
- The Confluent Schema Registry enforces `BACKWARD_TRANSITIVE` compatibility on all
  versioned topics.  A registration that violates compatibility is rejected.
- IBM Event Endpoint Management registration is gated on a valid AsyncAPI spec; the
  Terraform `ibm_eem_topic_subscription` resource fails `plan` if the YAML path is
  invalid or the file fails validation.
