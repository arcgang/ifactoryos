# API Versioning Policy — iFactoryOS API Gateway

## Scope

This policy governs all REST APIs registered in the IBM API Connect catalogue under
the iFactoryOS developer portal. It implements the **6-month deprecation window**
required by US-029.

## Rules

### 1. Semver + URI path versioning

Every REST API contract **must** carry a `Major.Minor.Patch` version in:

- The `info.version` field of the OpenAPI 3.1 contract (e.g. `1.0.0`).
- The URI path prefix: `/v{Major}/` (e.g. `/v1/sequence`).

A contract missing a valid `info.version` or a versioned path prefix is rejected by
the API gateway at registration time.

### 2. Breaking-change classification

The following changes require a **new major URI prefix** (e.g. `/v2/`):

- Removing a field from a response schema.
- Changing the type of an existing field.
- Removing an endpoint.
- Changing the meaning of an existing status code in a breaking way.
- Removing or narrowing a required request field.

Adding optional fields, adding new endpoints, or loosening validation are
non-breaking and may be delivered as a Minor or Patch increment without a URI
change.

### 3. Deprecation notice

When a new major version is published to the catalogue:

1. The API gateway **automatically** sets a `Deprecation` timestamp on all responses
   from the previous major version (RFC 8594 `Deprecation` header, IMF-fixdate
   format).
2. The gateway **automatically** sets a `Sunset` date on all responses from the
   previous major version — exactly **180 calendar days** after the `Deprecation`
   timestamp.
3. A deprecation notice entry is created in the internal developer portal, visible
   to all subscribers.
4. Prometheus metric `api_version_deprecated_days_remaining{api,version}` begins
   counting down, visible on the Grafana SLO dashboard.

### 4. Minimum support window

The previous major version **remains active and traffic-routed** for a minimum of
**180 calendar days** (6 months) from the `Deprecation` timestamp.

After 180 days:

- The gateway **blocks new subscriptions** to the deprecated version.
- Existing subscribers enter a **30-day grace period** during which traffic is still
  served but each response includes a `Warning: 299 "Deprecated API version"` header.
- After the 30-day grace period the gateway decommissions the deprecated version.

### 5. Emergency exceptions

A security-critical breaking change (e.g. removing an endpoint that leaks data) may
be applied with less than 180 days notice. Such exceptions require:

- Written approval from the Platform Architect.
- Direct notification to all active subscribers before the change takes effect.
- A retrospective ADR entry per IBM-METH-001.

### 6. Developer portal

All registered APIs are visible in the IBM API Connect Developer Portal at
`https://developer.ifactoryos.bmw.de/`. Each API listing exposes:

- The current and any deprecated major versions.
- The `openapi-generator` download action to generate a client stub directly from the
  portal (no direct communication with the Horizon 1 engineering team required).
- The `Sunset` date for any deprecated version.

## Enforcement

The IBM API Connect gateway enforces rules 1–4 automatically. Rules 5–6 are process
controls verified during the IBM Well-Architected review (IBM-METH-001) before each
major-version production go-live.
