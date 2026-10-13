import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { DefectGenomePage } from "./DefectGenomePage";
import * as api from "../services/api";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type { ClusterQueryResult, CorrectiveActionResult, DefectCluster } from "../types/defectGenome";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeClusterQueryResult(overrides: Partial<ClusterQueryResult> = {}): ClusterQueryResult {
  const base: ClusterQueryResult = {
    query_id: "11111111-1111-1111-1111-111111111111",
    cluster_id: "22222222-2222-2222-2222-222222222222",
    queried_at: "2026-10-12T09:35:02Z",
    defect_class: "dust",
    model_confidence: 0.78,
    low_confidence: false,
    top_factors: [
      {
        rank: 1,
        factor_name: "E-coat voltage drop — Stage 6",
        factor_category: "prep_stage",
        probability_score: 0.73,
        supporting_vin_count: 42,
      },
      {
        rank: 2,
        factor_name: "Booth humidity spike",
        factor_category: "booth_parameter",
        probability_score: 0.61,
        supporting_vin_count: 38,
      },
      {
        rank: 3,
        factor_name: "Ambient temperature deviation",
        factor_category: "climate_condition",
        probability_score: 0.45,
        supporting_vin_count: 30,
      },
      {
        rank: 4,
        factor_name: "Phosphate concentration low",
        factor_category: "prep_stage",
        probability_score: 0.38,
        supporting_vin_count: 22,
      },
      {
        rank: 5,
        factor_name: "Stage 3 dwell time exceeded",
        factor_category: "prep_stage",
        probability_score: 0.21,
        supporting_vin_count: 11,
      },
    ],
    affected_vin_count: 42,
    affected_vins: ["WBA3A5C5XDF123456", "WBA3A5C5XDF123457"],
    model_version: "2.4.1",
    advisor_v1_fallback: null,
  };
  return { ...base, ...overrides };
}

function makeLowConfidenceResult(): ClusterQueryResult {
  return makeClusterQueryResult({
    model_confidence: 0.52,
    low_confidence: true,
    advisor_v1_fallback: {
      rule_id: "RULE-017",
      rule_description: "Dust contamination from prep stage 6 e-coat transition",
      matched_conditions: ["ecoat_voltage_drop > 15%", "stage_6_duration_s > 480"],
      recommended_action: "Inspect and recalibrate rectifier unit on Stage 6. Check for voltage fluctuations during batch transition.",
    },
  });
}

function makeCorrectiveActionResult(): CorrectiveActionResult {
  return {
    jira_ticket_id: "IFOS-QA-1042",
    jira_ticket_url: "https://jira.bmw.internal/browse/IFOS-QA-1042",
    cluster_id: "22222222-2222-2222-2222-222222222222",
    created_at: "2026-10-12T09:40:00Z",
    prepopulated_summary: "Dust cluster: E-coat voltage drop — Stage 6",
  };
}

function makeCluster(): DefectCluster {
  return {
    cluster_id: "22222222-2222-2222-2222-222222222222",
    defect_class: "dust",
    status: "open",
    root_cause_signature: "ecoat_voltage_drop:stage_6",
    confidence_score: 0.78,
    affected_vin_count: 42,
    most_recent_vin: "WBA3A5C5XDF123456",
    detected_at: "2026-10-12T09:00:00Z",
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:35:00Z",
  };
}

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function renderAndRunQuery(
  queryResult: ClusterQueryResult,
  defectClassValue = "dust",
) {
  vi.spyOn(api, "getConfidenceThreshold").mockResolvedValue({
    low_confidence_threshold: 0.6,
    cluster_alert_threshold: 0.8,
    cluster_alert_min_vins: 3,
    cluster_alert_window_hours: 4,
  });
  vi.spyOn(api, "listClusters").mockResolvedValue({
    data: [makeCluster()],
    page: { next_cursor: null, limit: 3 },
  });
  vi.spyOn(api, "runClusterQuery").mockResolvedValue(queryResult);

  render(
    <Wrapper>
      <DefectGenomePage userId="katrin.schwarz" />
    </Wrapper>,
  );

  await i18n.changeLanguage("en-GB");

  // Select defect class
  const radio = screen.getByTestId(`radio-${defectClassValue}`);
  fireEvent.click(radio);

  // Submit
  const btn = screen.getByTestId("run-defect-genome-btn");
  fireEvent.click(btn);

  // Wait for results
  await waitFor(() => expect(screen.getByTestId("factor-list")).toBeTruthy(), {
    timeout: 3000,
  });
}

// ---------------------------------------------------------------------------
// Tests: ranked factor list
// ---------------------------------------------------------------------------

describe("DefectGenomePage — factor list renders from ClusterQueryResult", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders all 5 factors from top_factors in the result", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    expect(screen.getByTestId("factor-row-1")).toBeTruthy();
    expect(screen.getByTestId("factor-row-2")).toBeTruthy();
    expect(screen.getByTestId("factor-row-3")).toBeTruthy();
    expect(screen.getByTestId("factor-row-4")).toBeTruthy();
    expect(screen.getByTestId("factor-row-5")).toBeTruthy();
  });

  it("shows factor_name for rank 1 in its row", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const row1 = screen.getByTestId("factor-row-1");
    expect(row1.textContent).toContain("E-coat voltage drop — Stage 6");
  });

  it("shows probability score as percentage for rank 1 (73%)", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const row1 = screen.getByTestId("factor-row-1");
    expect(row1.textContent).toContain("73%");
  });

  it("shows supporting_vin_count for rank 1 (42 VINs)", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const row1 = screen.getByTestId("factor-row-1");
    expect(row1.textContent).toContain("42");
  });

  it("shows probability score and VIN count for rank 2 (61%, 38 VINs)", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const row2 = screen.getByTestId("factor-row-2");
    expect(row2.textContent).toContain("61%");
    expect(row2.textContent).toContain("38");
  });
});

// ---------------------------------------------------------------------------
// Tests: Low Confidence badge
// ---------------------------------------------------------------------------

describe("DefectGenomePage — Low Confidence badge", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders Low Confidence badge when low_confidence=true", async () => {
    const result = makeLowConfidenceResult();
    await renderAndRunQuery(result);

    expect(screen.getByTestId("low-confidence-badge")).toBeTruthy();
  });

  it("does NOT render Low Confidence badge when low_confidence=false", async () => {
    const result = makeClusterQueryResult({ low_confidence: false });
    await renderAndRunQuery(result);

    expect(screen.queryByTestId("low-confidence-badge")).toBeNull();
  });

  it("renders High Confidence badge when low_confidence=false", async () => {
    const result = makeClusterQueryResult({ low_confidence: false });
    await renderAndRunQuery(result);

    expect(screen.getByTestId("high-confidence-badge")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Tests: Advisor v1 fallback card
// ---------------------------------------------------------------------------

describe("DefectGenomePage — Advisor v1 fallback", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders Advisor v1 card when advisor_v1_fallback is present and low_confidence=true", async () => {
    const result = makeLowConfidenceResult();
    await renderAndRunQuery(result);

    expect(screen.getByTestId("advisor-v1-fallback")).toBeTruthy();
  });

  it("shows rule_description in the advisor card", async () => {
    const result = makeLowConfidenceResult();
    await renderAndRunQuery(result);

    const advisor = screen.getByTestId("advisor-v1-fallback");
    expect(advisor.textContent).toContain("Dust contamination from prep stage 6 e-coat transition");
  });

  it("shows recommended_action in the advisor card", async () => {
    const result = makeLowConfidenceResult();
    await renderAndRunQuery(result);

    const action = screen.getByTestId("advisor-recommended-action");
    expect(action.textContent).toContain("Inspect and recalibrate rectifier unit");
  });

  it("does NOT render Advisor v1 card when advisor_v1_fallback is null", async () => {
    const result = makeClusterQueryResult({ low_confidence: false, advisor_v1_fallback: null });
    await renderAndRunQuery(result);

    expect(screen.queryByTestId("advisor-v1-fallback")).toBeNull();
  });

  it("does NOT render Advisor v1 card when low_confidence=false even if fallback present", async () => {
    const result = makeClusterQueryResult({
      low_confidence: false,
      advisor_v1_fallback: {
        rule_id: "RULE-001",
        rule_description: "Should not show",
        recommended_action: "Do nothing",
      },
    });
    await renderAndRunQuery(result);

    expect(screen.queryByTestId("advisor-v1-fallback")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Create Corrective Action button disabled until factor selected
// ---------------------------------------------------------------------------

describe("DefectGenomePage — Create Corrective Action button", () => {
  afterEach(() => vi.restoreAllMocks());

  it("button is disabled before any factor is selected", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const btn = screen.getByTestId("create-corrective-action-btn");
    expect(btn.getAttribute("aria-disabled")).toBe("true");
  });

  it("button becomes enabled after selecting a factor", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    const radio = screen.getByTestId("factor-radio-1");
    fireEvent.click(radio);

    const btn = screen.getByTestId("create-corrective-action-btn");
    expect(btn.getAttribute("aria-disabled")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// Tests: Modal pre-population
// ---------------------------------------------------------------------------

describe("DefectGenomePage — confirmation modal pre-population", () => {
  afterEach(() => vi.restoreAllMocks());

  it("modal shows cluster_id", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    const clusterId = screen.getByTestId("modal-cluster-id");
    expect(clusterId.textContent).toContain("22222222-2222-2222-2222-222222222222");
  });

  it("modal shows selected factor name", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    const rootCause = screen.getByTestId("modal-root-cause");
    expect(rootCause.textContent).toContain("E-coat voltage drop — Stage 6");
  });

  it("modal shows probability score as percentage", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    const probability = screen.getByTestId("modal-probability");
    expect(probability.textContent).toBe("73%");
  });

  it("modal shows affected VIN count", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    const vins = screen.getByTestId("modal-affected-vins");
    expect(vins.textContent).toContain("42");
  });

  it("modal cancel button closes the modal", async () => {
    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    fireEvent.click(screen.getByTestId("modal-cancel"));
    expect(screen.queryByTestId("corrective-action-modal")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Success toast on 201
// ---------------------------------------------------------------------------

describe("DefectGenomePage — success toast on 201", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows success toast with Jira ticket ID after successful corrective action creation", async () => {
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(makeCorrectiveActionResult());

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());

    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });

    expect(screen.getByTestId("success-toast").textContent).toContain("IFOS-QA-1042");
  });

  it("toast Jira link points to the correct URL", async () => {
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(makeCorrectiveActionResult());

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("toast-jira-link")).toBeTruthy(), {
      timeout: 3000,
    });

    const link = screen.getByTestId("toast-jira-link") as HTMLAnchorElement;
    expect(link.href).toBe("https://jira.bmw.internal/browse/IFOS-QA-1042");
  });
});

// ---------------------------------------------------------------------------
// Tests: 409 duplicate informational message
// ---------------------------------------------------------------------------

describe("DefectGenomePage — 409 duplicate corrective action", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows informational duplicate message (not error) when API returns 409", async () => {
    vi.spyOn(api, "createCorrectiveAction").mockRejectedValue(
      new api.ApiError(409, JSON.stringify({
        code: "CORRECTIVE_ACTION_DUPLICATE",
        message: "A corrective action already exists for this cluster.",
        correlation_id: "corr-409-abc",
        details: [{ corrective_action_jira_id: "IFOS-QA-0999" }],
      })),
    );

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("duplicate-info")).toBeTruthy(), {
      timeout: 3000,
    });
    // Modal should close
    expect(screen.queryByTestId("corrective-action-modal")).toBeNull();
    expect(screen.getByTestId("duplicate-info").textContent).toContain("IFOS-QA-0999");
  });
});

// ---------------------------------------------------------------------------
// Tests: Error banner on other errors from corrective action
// ---------------------------------------------------------------------------

describe("DefectGenomePage — modal error on non-409 failure", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows error banner inside modal on 500", async () => {
    vi.spyOn(api, "createCorrectiveAction").mockRejectedValue(
      new api.ApiError(500, JSON.stringify({
        code: "INTERNAL_ERROR",
        message: "Internal server error",
        correlation_id: "corr-500-xyz",
      })),
    );

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("modal-error-banner")).toBeTruthy(), {
      timeout: 3000,
    });
    // Modal remains open
    expect(screen.getByTestId("corrective-action-modal")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Tests: adoption events
// ---------------------------------------------------------------------------

describe("DefectGenomePage — adoption event emission", () => {
  afterEach(() => vi.restoreAllMocks());

  it("emits DEFECT_GENOME_QUERY event after successful query", async () => {
    const emitSpy = vi.spyOn(adoptionEvents, "emitDefectGenomeQuery");

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    expect(emitSpy).toHaveBeenCalledOnce();
    const call = emitSpy.mock.calls[0][0];
    expect(call.defectClass).toBe("dust");
    expect(call.userId).toBe("katrin.schwarz");
  });

  it("emits ROOT_CAUSE_ASSIGNED event after successful corrective action creation", async () => {
    const assignSpy = vi.spyOn(adoptionEvents, "emitRootCauseAssigned");
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(makeCorrectiveActionResult());

    const result = makeClusterQueryResult();
    await renderAndRunQuery(result);

    fireEvent.click(screen.getByTestId("factor-radio-1"));
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(() => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy());
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });

    expect(assignSpy).toHaveBeenCalledOnce();
    const call = assignSpy.mock.calls[0][0];
    expect(call.clusterId).toBe("22222222-2222-2222-2222-222222222222");
  });
});

// ---------------------------------------------------------------------------
// Tests: query error state shows correlation_id
// ---------------------------------------------------------------------------

describe("DefectGenomePage — query error state", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows correlation_id from error response in error banner", async () => {
    vi.spyOn(api, "getConfidenceThreshold").mockResolvedValue({
      low_confidence_threshold: 0.6,
      cluster_alert_threshold: 0.8,
      cluster_alert_min_vins: 3,
      cluster_alert_window_hours: 4,
    });
    vi.spyOn(api, "listClusters").mockResolvedValue({
      data: [],
      page: { next_cursor: null, limit: 3 },
    });
    vi.spyOn(api, "runClusterQuery").mockRejectedValue(
      new api.ApiError(503, JSON.stringify({
        code: "SERVICE_UNAVAILABLE",
        message: "503 Service Unavailable — defect-genome-worker pool exhausted.",
        correlation_id: "corr-503-test",
      })),
    );

    await i18n.changeLanguage("en-GB");

    render(
      <Wrapper>
        <DefectGenomePage userId="katrin.schwarz" />
      </Wrapper>,
    );

    const radio = screen.getByTestId("radio-dust");
    fireEvent.click(radio);
    fireEvent.click(screen.getByTestId("run-defect-genome-btn"));

    await waitFor(() => expect(screen.getByTestId("query-error-banner")).toBeTruthy(), {
      timeout: 3000,
    });
    expect(screen.getByTestId("query-error-banner").textContent).toContain("corr-503-test");
  });
});

// ---------------------------------------------------------------------------
// Tests: loading state while query in-flight
// ---------------------------------------------------------------------------

describe("DefectGenomePage — loading state", () => {
  beforeEach(() => {
    vi.spyOn(api, "getConfidenceThreshold").mockResolvedValue({
      low_confidence_threshold: 0.6,
      cluster_alert_threshold: 0.8,
      cluster_alert_min_vins: 3,
      cluster_alert_window_hours: 4,
    });
    vi.spyOn(api, "listClusters").mockResolvedValue({
      data: [],
      page: { next_cursor: null, limit: 3 },
    });
    vi.spyOn(api, "runClusterQuery").mockReturnValue(new Promise(() => {}));
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows loading indicator while query is in-flight", async () => {
    await i18n.changeLanguage("en-GB");

    render(
      <Wrapper>
        <DefectGenomePage userId="katrin.schwarz" />
      </Wrapper>,
    );

    const radio = screen.getByTestId("radio-dust");
    fireEvent.click(radio);
    fireEvent.click(screen.getByTestId("run-defect-genome-btn"));

    await waitFor(() => expect(screen.getByTestId("query-loading")).toBeTruthy());
  });

  it("disables Run button while query is in-flight", async () => {
    await i18n.changeLanguage("en-GB");

    render(
      <Wrapper>
        <DefectGenomePage userId="katrin.schwarz" />
      </Wrapper>,
    );

    const radio = screen.getByTestId("radio-dust");
    fireEvent.click(radio);

    const btn = screen.getByTestId("run-defect-genome-btn");
    fireEvent.click(btn);

    await waitFor(() => expect(btn.getAttribute("aria-disabled")).toBe("true"));
  });
});
