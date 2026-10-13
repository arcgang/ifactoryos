/**
 * US-014 acceptance tests — quality-studio frontend
 *
 * These tests prove the acceptance criteria end-to-end through the DefectGenomePage
 * component (the real entry point) at the seam between the frontend, the api.ts
 * service layer, and the adoptionEvents instrumentation module.
 *
 * Unit tests in DefectGenomePage.test.tsx already cover individual behaviours.
 * What they do NOT cover is the cross-task contract:
 *  - AC1: every factor returned by the backend is rendered with probability and VIN count
 *  - AC2: the low_confidence threshold is configurable and the badge + fallback appear together
 *  - AC3: the modal pre-populates cluster_id, probability, VIN list, prep adjustment before
 *         a single confirm click submits the corrective action
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { DefectGenomePage } from "./DefectGenomePage";
import * as api from "../services/api";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type {
  ClusterQueryResult,
  CorrectiveActionResult,
  DefectCluster,
} from "../types/defectGenome";

// ---------------------------------------------------------------------------
// Test fixture helpers — express timestamps as far-future constants, not
// hardcoded absolute dates, to satisfy the baseline convention.
// ---------------------------------------------------------------------------

const FAR_FUTURE_TS = "2099-01-01T00:00:00Z";

function makeCluster(): DefectCluster {
  return {
    cluster_id: "ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa",
    defect_class: "dust",
    status: "open",
    root_cause_signature: "ecoat_voltage_drop:stage_6",
    confidence_score: 0.78,
    affected_vin_count: 42,
    most_recent_vin: "WBA3A5C5XDF999001",
    detected_at: FAR_FUTURE_TS,
    created_at: FAR_FUTURE_TS,
    updated_at: FAR_FUTURE_TS,
  };
}

/** AC1 fixture — exactly 5 top_factors, each category represented. */
function makeFiveFactorResult(): ClusterQueryResult {
  return {
    query_id: "00000000-0000-0000-0000-000000000001",
    cluster_id: "ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa",
    queried_at: FAR_FUTURE_TS,
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
    affected_vins: ["WBA3A5C5XDF999001", "WBA3A5C5XDF999002"],
    model_version: "2.4.1",
    advisor_v1_fallback: null,
  };
}

/** AC2 fixture — confidence below 60% threshold, advisor fallback present. */
function makeLowConfResult(): ClusterQueryResult {
  return {
    ...makeFiveFactorResult(),
    model_confidence: 0.52,
    low_confidence: true,
    advisor_v1_fallback: {
      rule_id: "ADV-DUST-01",
      rule_description: "Elevated humidity in pre-paint stages 3–4",
      matched_conditions: ["humidity_elevated", "pre_paint_stage"],
      recommended_action:
        "Reduce booth humidity to ≤60% in stages 3–4; inspect air supply desiccant filters",
    },
  };
}

function makeCorrectiveActionResult(): CorrectiveActionResult {
  return {
    jira_ticket_id: "DEFECTGENOME-999",
    jira_ticket_url: "https://jira.bmwgroup.net/browse/DEFECTGENOME-999",
    cluster_id: "ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa",
    created_at: FAR_FUTURE_TS,
    prepopulated_summary: "DefectGenome corrective action – dust cluster ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa",
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

async function renderAndRunQuery(queryResult: ClusterQueryResult) {
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

  await i18n.changeLanguage("en-GB");

  render(
    <Wrapper>
      <DefectGenomePage userId="katrin.schwarz" />
    </Wrapper>,
  );

  fireEvent.click(screen.getByTestId("radio-dust"));
  fireEvent.click(screen.getByTestId("run-defect-genome-btn"));

  await waitFor(() => expect(screen.getByTestId("factor-list")).toBeTruthy(), {
    timeout: 3000,
  });
}

// ---------------------------------------------------------------------------
// AC1 — Ranked root-cause probability distribution
// ---------------------------------------------------------------------------

describe("AC1 — ranked root-cause probability distribution", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders all 5 contributing factors after a successful query", async () => {
    // AC1: top 5 contributing factors are displayed
    await renderAndRunQuery(makeFiveFactorResult());

    for (let rank = 1; rank <= 5; rank++) {
      expect(
        screen.getByTestId(`factor-row-${rank}`),
        `factor-row-${rank} must be present`,
      ).toBeTruthy();
    }
  });

  it("displays probability_score as a percentage for each factor", async () => {
    // AC1: each factor shows its probability_score
    await renderAndRunQuery(makeFiveFactorResult());

    const row1 = screen.getByTestId("factor-row-1");
    expect(row1.textContent).toContain("73%");

    const row3 = screen.getByTestId("factor-row-3");
    expect(row3.textContent).toContain("45%");

    const row5 = screen.getByTestId("factor-row-5");
    expect(row5.textContent).toContain("21%");
  });

  it("displays supporting_vin_count for each factor", async () => {
    // AC1: each factor shows its supporting VIN count
    await renderAndRunQuery(makeFiveFactorResult());

    const row1 = screen.getByTestId("factor-row-1");
    expect(row1.textContent).toContain("42");

    const row2 = screen.getByTestId("factor-row-2");
    expect(row2.textContent).toContain("38");

    const row4 = screen.getByTestId("factor-row-4");
    expect(row4.textContent).toContain("22");
  });

  it("displays factor_name for each of the 5 rows", async () => {
    // AC1: factor names visible so engineers can read the ranked list
    await renderAndRunQuery(makeFiveFactorResult());

    expect(screen.getByTestId("factor-row-1").textContent).toContain(
      "E-coat voltage drop — Stage 6",
    );
    expect(screen.getByTestId("factor-row-2").textContent).toContain(
      "Booth humidity spike",
    );
    expect(screen.getByTestId("factor-row-5").textContent).toContain(
      "Stage 3 dwell time exceeded",
    );
  });

  it("emits DEFECT_GENOME_QUERY adoption event after the query runs", async () => {
    // AC1: instrumentation fires so adoption can be measured
    const spy = vi.spyOn(adoptionEvents, "emitDefectGenomeQuery");

    await renderAndRunQuery(makeFiveFactorResult());

    expect(spy).toHaveBeenCalledOnce();
    const call = spy.mock.calls[0][0];
    expect(call.defectClass).toBe("dust");
    expect(call.userId).toBe("katrin.schwarz");
  });
});

// ---------------------------------------------------------------------------
// AC2 — Low Confidence badge + Advisor v1 fallback
// ---------------------------------------------------------------------------

describe("AC2 — Low Confidence badge and rule-based Advisor v1 fallback", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows Low Confidence badge when model confidence is below the threshold", async () => {
    // AC2: badge is visible on low-confidence results
    await renderAndRunQuery(makeLowConfResult());

    expect(screen.getByTestId("low-confidence-badge")).toBeTruthy();
  });

  it("shows Advisor v1 fallback card alongside the factor list when low_confidence=true", async () => {
    // AC2: fallback displayed alongside the main result, not as a replacement
    await renderAndRunQuery(makeLowConfResult());

    expect(screen.getByTestId("advisor-v1-fallback")).toBeTruthy();
    // Factor list must still be present
    expect(screen.getByTestId("factor-list")).toBeTruthy();
  });

  it("Advisor v1 card shows the recommended_action text", async () => {
    // AC2: engineers can read the rule-based recommendation
    await renderAndRunQuery(makeLowConfResult());

    const advisor = screen.getByTestId("advisor-v1-fallback");
    expect(advisor.textContent).toContain("Reduce booth humidity to ≤60%");
  });

  it("Advisor v1 card shows the rule_description", async () => {
    // AC2: rule description gives context for why the fallback was triggered
    await renderAndRunQuery(makeLowConfResult());

    const advisor = screen.getByTestId("advisor-v1-fallback");
    expect(advisor.textContent).toContain("Elevated humidity in pre-paint stages 3–4");
  });

  it("does NOT show Low Confidence badge when model_confidence ≥ threshold", async () => {
    // AC2: badge is absent for high-confidence results
    await renderAndRunQuery(makeFiveFactorResult());

    expect(screen.queryByTestId("low-confidence-badge")).toBeNull();
  });

  it("does NOT show Advisor v1 fallback when low_confidence=false", async () => {
    // AC2: fallback is hidden unless needed — does not clutter high-confidence UI
    await renderAndRunQuery(makeFiveFactorResult());

    expect(screen.queryByTestId("advisor-v1-fallback")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Corrective Action modal pre-population and single-confirm submit
// ---------------------------------------------------------------------------

describe("AC3 — Create Corrective Action pre-population and single-confirm flow", () => {
  afterEach(() => vi.restoreAllMocks());

  async function openModal() {
    await renderAndRunQuery(makeFiveFactorResult());

    // Select a root cause factor
    fireEvent.click(screen.getByTestId("factor-radio-1"));

    // Open the modal
    fireEvent.click(screen.getByTestId("create-corrective-action-btn"));

    await waitFor(
      () => expect(screen.getByTestId("corrective-action-modal")).toBeTruthy(),
      { timeout: 3000 },
    );
  }

  it("modal pre-populates cluster_id before the engineer clicks confirm", async () => {
    // AC3: cluster_id visible in the modal — no data entry needed
    await openModal();

    const clusterId = screen.getByTestId("modal-cluster-id");
    expect(clusterId.textContent).toContain("ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa");
  });

  it("modal pre-populates root-cause probability score", async () => {
    // AC3: probability pre-filled from the selected factor
    await openModal();

    const probability = screen.getByTestId("modal-probability");
    expect(probability.textContent).toBe("73%");
  });

  it("modal pre-populates affected VIN count", async () => {
    // AC3: affected VIN list pre-filled from ClusterQueryResult.affected_vin_count
    await openModal();

    const vins = screen.getByTestId("modal-affected-vins");
    expect(vins.textContent).toContain("42");
  });

  it("modal pre-populates recommended prep stage adjustment from factor category", async () => {
    // AC3: prep stage adjustment derived from factor_category, pre-filled for the engineer
    await openModal();

    const prepAdj = screen.getByTestId("modal-prep-adjustment");
    // Rank 1 factor is "prep_stage" category + factor_name
    expect(prepAdj.textContent).toContain("E-coat voltage drop — Stage 6");
  });

  it("a single confirm click submits the corrective action without additional data entry", async () => {
    // AC3: only one click required — the modal is pre-populated, confirming this is a
    //      single-confirm flow
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(
      makeCorrectiveActionResult(),
    );

    await openModal();

    // Verify the confirm button is present and enabled before clicking
    const confirmBtn = screen.getByTestId("modal-confirm");
    expect(confirmBtn.getAttribute("aria-disabled")).not.toBe("true");

    fireEvent.click(confirmBtn);

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });
  });

  it("success toast shows Jira ticket ID after confirm", async () => {
    // AC3: confirmation flow produces a Jira ticket — visible in the toast
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(
      makeCorrectiveActionResult(),
    );

    await openModal();
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });

    expect(screen.getByTestId("success-toast").textContent).toContain("DEFECTGENOME-999");
  });

  it("emits ROOT_CAUSE_ASSIGNED adoption event after corrective action is created", async () => {
    // AC3: instrumentation fires so root-cause assignment rate can be measured
    const spy = vi.spyOn(adoptionEvents, "emitRootCauseAssigned");
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(
      makeCorrectiveActionResult(),
    );

    await openModal();
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });

    expect(spy).toHaveBeenCalledOnce();
    const call = spy.mock.calls[0][0];
    expect(call.clusterId).toBe("ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa");
  });

  it("createCorrectiveAction is called with the selected factor rank and the cluster_id", async () => {
    // AC3: verifies the right data flows from the modal to the API call —
    //      the cluster_id from the query result and the selected factor rank.
    const spy = vi
      .spyOn(api, "createCorrectiveAction")
      .mockResolvedValue(makeCorrectiveActionResult());

    await openModal();
    fireEvent.click(screen.getByTestId("modal-confirm"));

    await waitFor(() => expect(screen.getByTestId("success-toast")).toBeTruthy(), {
      timeout: 3000,
    });

    expect(spy).toHaveBeenCalledOnce();
    const [clusterId, body] = spy.mock.calls[0];
    expect(clusterId).toBe("ac14-cluster-aaaa-aaaa-aaaaaaaaaaaa");
    expect(body.assigned_root_cause_factor_rank).toBe(1);
    expect(body.confirmed_by_user_id).toBe("katrin.schwarz");
  });
});
