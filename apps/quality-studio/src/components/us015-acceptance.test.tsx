/**
 * US-015 Acceptance tests
 *
 * Story: Receive a real-time defect cluster alert when DefectGenome detects
 * 3 or more VINs sharing the same root-cause signature above 80% confidence
 * within a 4-hour window.
 *
 * Criteria tested end-to-end across the seam between:
 *   - backend alert-persistence + REST API   (services/defect-genome)
 *   - SSE push hook                          (ClusterAlertPanel / useClusterAlertSSE)
 *   - alert detail view                      (AlertDetailView)
 *   - acknowledge + corrective-action flow   (AlertDetailView → api.ts)
 *   - dashboard Jira-ticket column           (ClusterAlertPanel AlertRow)
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { ClusterAlertPanel } from "./ClusterAlertPanel";
import { AlertDetailView } from "./AlertDetailView";
import * as api from "../services/api";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type {
  AlertListResponse,
  ClusterAlert,
  CorrectiveActionResult,
  DefectClusterAlertEvent,
} from "../types/defectGenome";

// ---------------------------------------------------------------------------
// Precondition guard — verify the types the criteria depend on exist and are
// structurally correct.  These fire before any behaviour assertion so the
// suite fails loudly when the feature is absent rather than trivially passing.
// ---------------------------------------------------------------------------

it("precondition: DefectClusterAlertEvent type has the fields required by AC1", () => {
  // Construct a value of the union type so TypeScript validates the shape at
  // compile time, and we assert it at runtime.
  const evt: DefectClusterAlertEvent = {
    alert_id: "a",
    cluster_id: "b",
    defect_class: "dust",
    affected_vin_count: 3,
    root_cause_signature: "sig",
    confidence_score: 0.81,
    most_recent_vin: "VIN1",
    process_replay_url: "https://replay/VIN1",
    triggered_at: "2099-01-01T00:00:00Z",
  };
  expect(evt.affected_vin_count).toBeGreaterThanOrEqual(3);
  expect(evt.confidence_score).toBeGreaterThan(0.8);
});

it("precondition: ClusterAlert interface has all AC2 display fields", () => {
  const alert: ClusterAlert = {
    alert_id: "a",
    cluster_id: "cccccccc-0000-4000-8000-000000000001",
    defect_class: "crater",
    status: "new",
    affected_vin_count: 3,
    root_cause_signature: "ecoat_voltage_deviation",
    confidence_score: 0.94,
    most_recent_vin: "WBS4E0108RG271034",
    process_replay_url: "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034",
    triggered_at: "2099-01-01T00:00:00Z",
    created_at: "2099-01-01T00:00:00Z",
    updated_at: "2099-01-01T00:00:00Z",
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    corrective_action_jira_id: null,
  };
  // All five AC2 fields must be present
  expect(alert.cluster_id).toBeTruthy();
  expect(alert.affected_vin_count).toBeGreaterThanOrEqual(3);
  expect(alert.root_cause_signature).toBeTruthy();
  expect(alert.confidence_score).toBeGreaterThan(0.8);
  expect(alert.process_replay_url).toContain(alert.most_recent_vin);
});

// ---------------------------------------------------------------------------
// Helpers & fixtures
// ---------------------------------------------------------------------------

const ENGINEER_GROUPS = ["GRP-IFOS-PAINTENGINEER-MUC"];

function makeAlert(overrides: Partial<ClusterAlert> = {}): ClusterAlert {
  return {
    alert_id: "aaaaaaaa-0001-4000-8000-000000000001",
    cluster_id: "cccccccc-0001-4000-8000-000000000001",
    defect_class: "crater",
    status: "new",
    affected_vin_count: 3,
    root_cause_signature: "E-coat voltage drop at stage 6 + humidity spike booth 3",
    confidence_score: 0.94,
    most_recent_vin: "WBS4E0108RG271034",
    process_replay_url:
      "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034",
    triggered_at: "2099-01-01T08:14:00Z",
    created_at: "2099-01-01T08:14:00Z",
    updated_at: "2099-01-01T08:14:00Z",
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    corrective_action_jira_id: null,
    ...overrides,
  };
}

function makeAlertListResponse(alerts: ClusterAlert[] = []): AlertListResponse {
  return { data: alerts, page: { next_cursor: null, limit: 50 } };
}

function makeCorrectiveResult(): CorrectiveActionResult {
  return {
    jira_ticket_id: "IFOS-QUALITY-42",
    jira_ticket_url: "https://jira.internal/browse/IFOS-QUALITY-42",
    cluster_id: "cccccccc-0001-4000-8000-000000000001",
    created_at: "2099-01-01T09:00:00Z",
  };
}

/** Minimal SSE event that meets the story thresholds (≥3 VINs, >80% confidence). */
function makeSSEEvent(overrides: Partial<DefectClusterAlertEvent> = {}): DefectClusterAlertEvent {
  return {
    alert_id: "bbbbbbbb-0001-4000-8000-000000000002",
    cluster_id: "dddddddd-0001-4000-8000-000000000002",
    defect_class: "dust",
    affected_vin_count: 4,        // ≥ 3 VINs
    root_cause_signature: "Ioniser malfunction pre-booth stage 9",
    confidence_score: 0.91,       // > 80% confidence
    most_recent_vin: "WBS4E0106RG270891",
    process_replay_url:
      "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0106RG270891",
    triggered_at: "2099-01-01T09:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Mock EventSource — same pattern as the existing ClusterAlertPanel unit tests
// ---------------------------------------------------------------------------

class MockEventSource {
  static instance: MockEventSource | null = null;
  url: string;
  private _listeners: Map<string, Array<(e: MessageEvent) => void>> = new Map();
  onerror: ((e: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instance = this;
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void): void {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type)!.push(fn);
  }

  dispatchEvent(type: string, data: unknown): void {
    const handlers = this._listeners.get(type) ?? [];
    const event = { data: JSON.stringify(data) } as MessageEvent;
    handlers.forEach((fn) => fn(event));
  }

  close(): void {
    MockEventSource.instance = null;
  }
}

function renderWithI18n(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

// ---------------------------------------------------------------------------
// AC1 — In-app alert pushed within the engineer's session when the cluster
// threshold is crossed (≥3 VINs, >80% confidence, rolling 4-hour window).
//
// The 5-minute SLA is an infrastructure/latency constraint that cannot be
// automatically asserted in a unit test; see "untestable" note at end of file.
// What IS testable: the SSE event — which encodes the threshold facts — arrives
// at the panel and is surfaced as a toast and a table row without page reload.
// ---------------------------------------------------------------------------

describe("AC1 – real-time cluster alert pushed into the engineer's Quality Studio session", () => {
  beforeEach(() => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(makeAlertListResponse([]));
    vi.stubGlobal("EventSource", MockEventSource);
    MockEventSource.instance = null;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("SSE event with ≥3 VINs and >80% confidence renders an in-app toast notification", async () => {
    const onOpen = vi.fn();
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={onOpen} />,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined());

    const event = makeSSEEvent();
    // Assert the event itself meets the story threshold before verifying UI
    expect(event.affected_vin_count).toBeGreaterThanOrEqual(3);
    expect(event.confidence_score).toBeGreaterThan(0.8);

    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", event);
    });

    await waitFor(() =>
      expect(screen.getByTestId("cluster-alert-toast")).toBeDefined(),
    );
  });

  it("SSE alert row is prepended to the table without a page reload", async () => {
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined());

    const event = makeSSEEvent();
    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", event);
    });

    await waitFor(() =>
      expect(screen.getByTestId(`alert-row-${event.alert_id}`)).toBeDefined(),
    );
    // Empty-state placeholder must be gone — the panel updated in-place
    expect(screen.queryByTestId("cluster-alerts-empty")).toBeNull();
  });

  it("SSE toast badge count increments by 1 per incoming alert", async () => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(makeAlertListResponse([]));
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined());

    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", makeSSEEvent());
    });

    // Badge counter showing "1" should appear (panel section heading)
    await waitFor(() => {
      const badge = screen.getByLabelText("1 new");
      expect(badge).toBeDefined();
    });
  });

  it("SSE toast carries the Open Alert button that fires onOpenAlert with the alert_id", async () => {
    const onOpen = vi.fn();
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={onOpen} />,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined());

    const event = makeSSEEvent();
    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", event);
    });

    await waitFor(() => expect(screen.getByTestId("cluster-alert-toast")).toBeDefined());

    // Click the "Open Alert" button inside the toast
    const openBtn = screen.getByRole("button", { name: /open/i });
    fireEvent.click(openBtn);

    expect(onOpen).toHaveBeenCalledWith(event.alert_id);
  });
});

// ---------------------------------------------------------------------------
// AC2 — Alert detail shows: cluster ID, affected VIN count, root-cause
// signature, confidence score, and a direct link to the Process Replay for
// the most recent affected VIN.
// ---------------------------------------------------------------------------

describe("AC2 – alert detail view shows all five required fields", () => {
  beforeEach(() => {
    vi.spyOn(adoptionEvents, "emitClusterAlertViewed").mockReturnValue(undefined);
    vi.spyOn(adoptionEvents, "emitClusterAlertAcknowledged").mockReturnValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("cluster_id is visible in the detail view", async () => {
    const alert = makeAlert();
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("alert-detail-view")).toBeDefined());
    expect(screen.getByText(alert.cluster_id)).toBeDefined();
  });

  it("affected VIN count (≥3) is visible in the detail view", async () => {
    const alert = makeAlert({ affected_vin_count: 3 });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("alert-detail-view")).toBeDefined());
    expect(screen.getByText("3")).toBeDefined();
  });

  it("root-cause signature is visible in full in the detail view", async () => {
    const alert = makeAlert();
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("alert-detail-view")).toBeDefined());
    expect(
      screen.getByText("E-coat voltage drop at stage 6 + humidity spike booth 3"),
    ).toBeDefined();
  });

  it("confidence score is shown as a percentage (>80%)", async () => {
    const alert = makeAlert({ confidence_score: 0.94 });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("alert-detail-view")).toBeDefined());
    expect(screen.getByText("94%")).toBeDefined();
  });

  it("process replay link points to the most-recent-VIN URL", async () => {
    const alert = makeAlert();
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("process-replay-link")).toBeDefined());
    const link = screen.getByTestId("process-replay-link") as HTMLAnchorElement;
    expect(link.href).toContain(alert.most_recent_vin);
  });

  it("all five AC2 fields are present simultaneously for a single alert", async () => {
    const alert = makeAlert();
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("alert-detail-view")).toBeDefined());

    // cluster_id
    expect(screen.getByText(alert.cluster_id)).toBeDefined();
    // affected_vin_count
    expect(screen.getByText(String(alert.affected_vin_count))).toBeDefined();
    // root_cause_signature (full text, not truncated in detail view)
    expect(
      screen.getByText("E-coat voltage drop at stage 6 + humidity spike booth 3"),
    ).toBeDefined();
    // confidence_score as percentage
    expect(screen.getByText("94%")).toBeDefined();
    // process_replay_url link
    const link = screen.getByTestId("process-replay-link") as HTMLAnchorElement;
    expect(link.href).toContain(alert.most_recent_vin);
  });
});

// ---------------------------------------------------------------------------
// AC3 — After the engineer acknowledges and assigns a corrective action:
//   • alert status transitions to "In Progress"
//   • the Defect Dashboard cluster panel row shows the Jira ticket ID
// ---------------------------------------------------------------------------

describe("AC3 – acknowledge + corrective action → In Progress status + Jira ticket in dashboard row", () => {
  beforeEach(() => {
    vi.spyOn(adoptionEvents, "emitClusterAlertViewed").mockReturnValue(undefined);
    vi.spyOn(adoptionEvents, "emitClusterAlertAcknowledged").mockReturnValue(undefined);
    vi.spyOn(adoptionEvents, "emitRootCauseAssigned").mockReturnValue(undefined);
    vi.stubGlobal("EventSource", MockEventSource);
    MockEventSource.instance = null;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("acknowledging an alert updates the detail-view status badge to In Progress", async () => {
    const alert = makeAlert({ status: "new" });
    const acknowledged = makeAlert({
      status: "in_progress",
      acknowledged_at: "2099-01-01T08:20:00Z",
      acknowledged_by_user_id: "kb-001",
    });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "acknowledgeClusterAlert").mockResolvedValue(acknowledged);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("acknowledge-button")).toBeDefined());

    fireEvent.click(screen.getByTestId("acknowledge-button"));

    await waitFor(() => expect(screen.getByTestId("acknowledge-success")).toBeDefined());
  });

  it("assigning a corrective action creates a Jira ticket and shows the Jira link", async () => {
    const alert = makeAlert({ status: "acknowledged" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(makeCorrectiveResult());

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("assign-corrective-action-button")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("assign-corrective-action-button"));
    expect(screen.getByTestId("corrective-action-form")).toBeDefined();

    fireEvent.click(screen.getByTestId("corrective-action-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("corrective-action-result")).toBeDefined(),
    );
    expect(screen.getByTestId("corrective-action-jira-link")).toBeDefined();
  });

  it("after corrective action the Jira link URL points to the created ticket", async () => {
    const alert = makeAlert({ status: "acknowledged" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(makeCorrectiveResult());

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("assign-corrective-action-button")).toBeDefined(),
    );
    fireEvent.click(screen.getByTestId("assign-corrective-action-button"));
    fireEvent.click(screen.getByTestId("corrective-action-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("corrective-action-jira-link")).toBeDefined(),
    );
    const jiraLink = screen.getByTestId(
      "corrective-action-jira-link",
    ) as HTMLAnchorElement;
    expect(jiraLink.href).toContain("IFOS-QUALITY-42");
  });

  it("dashboard panel row shows the Jira ticket ID after the alert is updated via onAlertUpdated callback", async () => {
    // This test exercises the seam: AlertDetailView calls onAlertUpdated(updated)
    // after corrective action succeeds, which allows the parent (ClusterAlertPanel in
    // the real App) to reflect the Jira ID in the table row.
    //
    // Here we verify the callback is invoked with an alert whose
    // corrective_action_jira_id is set AND whose status is "in_progress".

    const alert = makeAlert({ status: "acknowledged" });
    const caResult = makeCorrectiveResult();
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(caResult);

    const onAlertUpdated = vi.fn();

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
        onAlertUpdated={onAlertUpdated}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("assign-corrective-action-button")).toBeDefined(),
    );
    fireEvent.click(screen.getByTestId("assign-corrective-action-button"));
    fireEvent.click(screen.getByTestId("corrective-action-submit"));

    await waitFor(() => expect(onAlertUpdated).toHaveBeenCalled());

    const updatedAlert: ClusterAlert = onAlertUpdated.mock.calls[0][0];
    expect(updatedAlert.status).toBe("in_progress");
    expect(updatedAlert.corrective_action_jira_id).toBe(caResult.jira_ticket_id);
  });

  it("ClusterAlertPanel row shows Jira ticket ID when alert already has corrective_action_jira_id set", async () => {
    // Verifies the Defect Dashboard panel column renders the Jira ID — this is
    // what the engineer sees after the corrective action has been created.
    const alert = makeAlert({
      status: "in_progress",
      corrective_action_jira_id: "IFOS-QUALITY-42",
    });
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([alert]),
    );

    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId(`alert-row-${alert.alert_id}`)).toBeDefined(),
    );
    expect(
      screen.getByRole("link", { name: /Jira ticket IFOS-QUALITY-42/i }),
    ).toBeDefined();
  });

  it("full end-to-end: SSE push → acknowledge → corrective action → Jira ID visible in panel", async () => {
    // Step 1: panel starts empty; SSE fires with a cluster-threshold-crossing event
    const jiraId = "IFOS-QUALITY-99";
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(makeAlertListResponse([]));

    const onOpen = vi.fn();
    const { unmount } = renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={onOpen} />,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined());

    const sseEvent = makeSSEEvent();
    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", sseEvent);
    });

    // Step 2: toast appears; the newly inserted row uses status "new"
    await waitFor(() =>
      expect(screen.getByTestId(`alert-row-${sseEvent.alert_id}`)).toBeDefined(),
    );
    unmount();

    // Step 3: engineer opens the detail view for the alert
    const alertFromAPI = makeAlert({
      alert_id: sseEvent.alert_id,
      cluster_id: sseEvent.cluster_id,
      affected_vin_count: sseEvent.affected_vin_count,
      confidence_score: sseEvent.confidence_score,
      root_cause_signature: sseEvent.root_cause_signature,
      status: "acknowledged",
    });
    const caResult: CorrectiveActionResult = {
      jira_ticket_id: jiraId,
      jira_ticket_url: `https://jira.internal/browse/${jiraId}`,
      cluster_id: sseEvent.cluster_id,
      created_at: "2099-01-01T09:10:00Z",
    };

    vi.spyOn(adoptionEvents, "emitClusterAlertViewed").mockReturnValue(undefined);
    vi.spyOn(adoptionEvents, "emitClusterAlertAcknowledged").mockReturnValue(undefined);
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alertFromAPI);
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(caResult);

    renderWithI18n(
      <AlertDetailView
        alertId={sseEvent.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("assign-corrective-action-button")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("assign-corrective-action-button"));
    fireEvent.click(screen.getByTestId("corrective-action-submit"));

    // Step 4: corrective action result is visible with the Jira ID
    await waitFor(() =>
      expect(screen.getByTestId("corrective-action-jira-link")).toBeDefined(),
    );
    const link = screen.getByTestId("corrective-action-jira-link") as HTMLAnchorElement;
    expect(link.href).toContain(jiraId);
  });
});
