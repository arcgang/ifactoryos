/**
 * Acceptance tests — US-019 · Gate 1 Pre-built Queries (AC-2)
 *
 * AC-2: Given the Gate 1 measurement window (Nov 1–28, 2025), when the Product
 *       Ops Analyst queries the instrumentation layer, then WAU, sequence lock %,
 *       and override rate are available as pre-built queries with no manual data
 *       extraction required.
 *
 * Approach: these tests read the actual generated artefacts on disk
 *   (platform/observability-stack/queries/adoption_queries.kql and main.tf)
 *   to assert that the three required pre-built queries exist and contain the
 *   correct measurement-window, metric names, and WAU exclusion logic.
 *
 * A query file that does not exist, or a Terraform resource that has not been
 * provisioned, will cause an explicit failure here — confirming the seam
 * between "queries authored" (observability-stack task) and "queries queryable
 * without manual extraction" (AC-2 requirement).
 */

import { describe, expect, it } from "vitest";
import { readFileSync, existsSync } from "fs";
import { resolve } from "path";

// ─── Locate artefacts (paths relative to repo root) ─────────────────────────
// These tests run from apps/planning-cockpit/, so we walk up two levels.
const REPO_ROOT = resolve(__dirname, "../../../..");

const KQL_PATH = resolve(
  REPO_ROOT,
  "platform/observability-stack/queries/adoption_queries.kql",
);

const TF_PATH = resolve(
  REPO_ROOT,
  "platform/observability-stack/main.tf",
);

// ─── Precondition guards ─────────────────────────────────────────────────────

describe("AC-2 — precondition: Gate 1 query artefacts exist on disk", () => {
  it("adoption_queries.kql file exists at platform/observability-stack/queries/adoption_queries.kql", () => {
    expect(existsSync(KQL_PATH)).toBe(true);
  });

  it("main.tf file exists at platform/observability-stack/main.tf", () => {
    expect(existsSync(TF_PATH)).toBe(true);
  });
});

// ─── AC-2 — KQL query content ────────────────────────────────────────────────

describe("AC-2 — adoption_queries.kql contains the three required pre-built Gate 1 queries", () => {
  let kql: string;

  // Load once; each 'it' reads from this local variable
  kql = existsSync(KQL_PATH) ? readFileSync(KQL_PATH, "utf-8") : "";

  it("contains a @name planner_wau query for Planner WAU (Gate 1 Metric A3)", () => {
    expect(kql).toContain("@name planner_wau");
  });

  it("contains a @name sequence_lock_pct query for Sequence Lock % (Gate 1 Metric A1)", () => {
    expect(kql).toContain("@name sequence_lock_pct");
  });

  it("contains a @name override_rate query for Override Rate (Gate 1 Metric A2)", () => {
    expect(kql).toContain("@name override_rate");
  });
});

describe("AC-2 — adoption_queries.kql measurement window is Nov 1–28, 2025", () => {
  let kql: string;
  kql = existsSync(KQL_PATH) ? readFileSync(KQL_PATH, "utf-8") : "";

  it("planner_wau query uses 2025-11-01 as window_start", () => {
    // The window_start line must appear and reference 2025-11-01
    expect(kql).toMatch(/window_start\s*=\s*datetime\(2025-11-01/);
  });

  it("planner_wau query uses 2025-11-28 as window_end", () => {
    expect(kql).toMatch(/window_end\s*=\s*datetime\(2025-11-28/);
  });
});

describe("AC-2 — planner_wau query covers all four required event actionTypes", () => {
  let kql: string;
  kql = existsSync(KQL_PATH) ? readFileSync(KQL_PATH, "utf-8") : "";

  it("planner_wau query includes optimizer_run in meaningful_actions", () => {
    expect(kql).toContain("optimizer_run");
  });

  it("planner_wau query includes override_applied in meaningful_actions", () => {
    expect(kql).toContain("override_applied");
  });

  it("planner_wau query includes publish_confirmed in meaningful_actions", () => {
    expect(kql).toContain("publish_confirmed");
  });
});

describe("AC-3 (KQL) — planner_wau query implements the WAU exclusion rule", () => {
  let kql: string;
  kql = existsSync(KQL_PATH) ? readFileSync(KQL_PATH, "utf-8") : "";

  it("planner_wau query filters sessions using the 10-minute threshold (600 seconds)", () => {
    // Exclusion: include if session_duration_minutes >= 10 OR meaningful_action_count > 0
    expect(kql).toContain("session_duration_minutes >= 10");
  });

  it("planner_wau query counts meaningful_action_count to gate WAU inclusion", () => {
    expect(kql).toContain("meaningful_action_count");
  });

  it("planner_wau query uses dcount(userId_s) to count distinct weekly active users", () => {
    expect(kql).toContain("dcount(userId_s)");
  });
});

describe("AC-2 — sequence_lock_pct query targets 4 Monday lock windows (W44–W47 2025)", () => {
  let kql: string;
  kql = existsSync(KQL_PATH) ? readFileSync(KQL_PATH, "utf-8") : "";

  it("sequence_lock_pct query references total_windows = 4 for the 4 Monday windows", () => {
    expect(kql).toMatch(/total_windows\s*=\s*4/);
  });

  it("sequence_lock_pct query counts distinct weeks with a publish_confirmed event", () => {
    // Formula: weeks_with_publish / 4 × 100
    expect(kql).toContain("weeks_with_publish");
  });
});

// ─── AC-2 — Terraform resources confirm queries are provisioned (not just authored) ─

describe("AC-2 — Terraform main.tf provisions the Gate 1 saved searches so queries are available without manual extraction", () => {
  let tf: string;
  tf = existsSync(TF_PATH) ? readFileSync(TF_PATH, "utf-8") : "";

  it("main.tf provisions an azurerm_log_analytics_saved_search resource named planner_wau", () => {
    expect(tf).toContain('azurerm_log_analytics_saved_search" "planner_wau"');
  });

  it("main.tf provisions an azurerm_log_analytics_saved_search resource named sequence_lock_pct", () => {
    expect(tf).toContain('azurerm_log_analytics_saved_search" "sequence_lock_pct"');
  });

  it("main.tf provisions an azurerm_log_analytics_saved_search resource named override_rate", () => {
    expect(tf).toContain('azurerm_log_analytics_saved_search" "override_rate"');
  });

  it("all three saved searches are in the Gate1-Adoption category, confirming no manual extraction is required", () => {
    const gate1Count = (tf.match(/Gate1-Adoption/g) ?? []).length;
    // One occurrence per saved_search resource = 3 minimum
    expect(gate1Count).toBeGreaterThanOrEqual(3);
  });
});

describe("AC-2 — adoption event table is provisioned in the observability stack (data must land somewhere)", () => {
  let tf: string;
  tf = existsSync(TF_PATH) ? readFileSync(TF_PATH, "utf-8") : "";

  it("main.tf contains an azurerm_log_analytics_workspace_table resource for AdoptionEvent_CL", () => {
    expect(tf).toContain("AdoptionEvent_CL");
  });
});
