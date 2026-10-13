import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { ClusterAlertPanel } from "./ClusterAlertPanel";
import { AlertDetailView } from "./AlertDetailView";
import * as api from "../services/api";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type { AlertListResponse, ClusterAlert, CorrectiveActionResult } from "../types/defectGenome";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeAlert(overrides: Partial<ClusterAlert> = {}): ClusterAlert {
  return {
    alert_id: "aaaaaaaa-0000-4000-8000-000000000001",
    cluster_id: "cccccccc-0000-4000-8000-000000000001",
    defect_class: "crater",
    status: "new",
    affected_vin_count: 3,
    root_cause_signature: "E-coat voltage drop at stage 6 + humidity spike booth 3",
    confidence_score: 0.94,
    most_recent_vin: "WBS4E0108RG271034",
    process_replay_url:
      "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0108RG271034",
    triggered_at: "2026-10-12T08:14:00Z",
    created_at: "2026-10-12T08:14:00Z",
    updated_at: "2026-10-12T08:14:00Z",
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    corrective_action_jira_id: null,
    ...overrides,
  };
}

function makeAlertListResponse(alerts: ClusterAlert[] = []): AlertListResponse {
  return {
    data: alerts,
    page: { next_cursor: null, limit: 50 },
  };
}

function makeCorrectiveResult(): CorrectiveActionResult {
  return {
    jira_ticket_id: "IFOS-42",
    jira_ticket_url: "https://jira.internal/browse/IFOS-42",
    cluster_id: "cccccccc-0000-4000-8000-000000000001",
    created_at: "2026-10-12T09:00:00Z",
  };
}

const ENGINEER_GROUPS = ["GRP-IFOS-PAINTENGINEER-MUC"];
const OTHER_GROUPS: string[] = [];

// ---------------------------------------------------------------------------
// Mock EventSource
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderWithI18n(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

// ---------------------------------------------------------------------------
// ClusterAlertPanel tests
// ---------------------------------------------------------------------------

describe("ClusterAlertPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([]),
    );
    // Replace global EventSource with mock
    vi.stubGlobal("EventSource", MockEventSource);
    MockEventSource.instance = null;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows loading skeleton while fetching", () => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockReturnValue(new Promise(() => {}));
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    expect(screen.getByTestId("cluster-alerts-loading")).toBeDefined();
  });

  it("shows empty state when no alerts", async () => {
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined(),
    );
  });

  it("shows error state with retry button on fetch failure", async () => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockRejectedValue(
      new Error("Network error"),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cluster-alerts-error")).toBeDefined(),
    );
    expect(screen.getByText("Retry")).toBeDefined();
  });

  it("renders loaded alerts with correct columns", async () => {
    const alert = makeAlert();
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([alert]),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId(`alert-row-${alert.alert_id}`)).toBeDefined(),
    );
    // Truncated cluster id and root cause visible
    expect(screen.getByText(/aaaaaaaa/)).toBeDefined();
    expect(screen.getByText(/E-coat voltage drop at stage 6/)).toBeDefined();
  });

  it("shows badge count for new alerts", async () => {
    const alert = makeAlert({ status: "new" });
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([alert]),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByText("1")).toBeDefined(),
    );
  });

  it("calls onOpenAlert when alert row is clicked", async () => {
    const onOpen = vi.fn();
    const alert = makeAlert();
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([alert]),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={onOpen} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId(`alert-row-${alert.alert_id}`)).toBeDefined(),
    );
    fireEvent.click(screen.getByTestId(`alert-row-${alert.alert_id}`));
    expect(onOpen).toHaveBeenCalledWith(alert.alert_id);
  });

  it("SSE push appends a toast and a new row without a page reload", async () => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([]),
    );
    const onOpen = vi.fn();
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={onOpen} />,
    );
    // Wait for empty state
    await waitFor(() =>
      expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined(),
    );

    const sseAlert = {
      alert_id: "bbbbbbbb-0000-4000-8000-000000000002",
      cluster_id: "dddddddd-0000-4000-8000-000000000002",
      defect_class: "dust",
      affected_vin_count: 4,
      root_cause_signature: "Ioniser malfunction pre-booth stage 9",
      confidence_score: 0.91,
      most_recent_vin: "WBS4E0106RG270891",
      process_replay_url:
        "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0106RG270891",
      triggered_at: "2026-10-12T09:00:00Z",
    };

    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", sseAlert);
    });

    // Toast appears
    await waitFor(() =>
      expect(screen.getByTestId("cluster-alert-toast")).toBeDefined(),
    );
    // Row added to table without page reload
    await waitFor(() =>
      expect(
        screen.getByTestId(`alert-row-${sseAlert.alert_id}`),
      ).toBeDefined(),
    );
  });

  it("dismissing a toast removes it from the screen", async () => {
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([]),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cluster-alerts-empty")).toBeDefined(),
    );

    act(() => {
      MockEventSource.instance!.dispatchEvent("cluster-alert", {
        alert_id: "eeeeeeee-0000-4000-8000-000000000003",
        cluster_id: "ffffffff-0000-4000-8000-000000000003",
        defect_class: "run",
        affected_vin_count: 3,
        root_cause_signature: "Flash-off temp variance",
        confidence_score: 0.85,
        most_recent_vin: "WBS4E0102RG271201",
        process_replay_url: "https://quality-studio.internal.bmw-factory-os.eu/process-replay/WBS4E0102RG271201",
        triggered_at: "2026-10-12T09:01:00Z",
      });
    });

    await waitFor(() =>
      expect(screen.getByTestId("cluster-alert-toast")).toBeDefined(),
    );

    fireEvent.click(screen.getByText("Dismiss"));

    await waitFor(() =>
      expect(screen.queryByTestId("cluster-alert-toast")).toBeNull(),
    );
  });

  it("renders Jira link when corrective_action_jira_id is set", async () => {
    const alert = makeAlert({ corrective_action_jira_id: "IFOS-99" });
    vi.spyOn(api, "getDefectGenomeAlerts").mockResolvedValue(
      makeAlertListResponse([alert]),
    );
    renderWithI18n(
      <ClusterAlertPanel userGroups={ENGINEER_GROUPS} onOpenAlert={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /Jira ticket IFOS-99/ })).toBeDefined(),
    );
  });
});

// ---------------------------------------------------------------------------
// AlertDetailView tests
// ---------------------------------------------------------------------------

describe("AlertDetailView", () => {
  beforeEach(() => {
    vi.spyOn(adoptionEvents, "emitClusterAlertViewed").mockReturnValue(undefined);
    vi.spyOn(adoptionEvents, "emitClusterAlertAcknowledged").mockReturnValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeleton while fetching", () => {
    vi.spyOn(api, "getClusterAlert").mockReturnValue(new Promise(() => {}));
    renderWithI18n(
      <AlertDetailView
        alertId="aaaaaaaa-0000-4000-8000-000000000001"
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByTestId("alert-detail-loading")).toBeDefined();
  });

  it("shows error state with retry on fetch failure", async () => {
    vi.spyOn(api, "getClusterAlert").mockRejectedValue(new Error("Network error"));
    renderWithI18n(
      <AlertDetailView
        alertId="aaaaaaaa-0000-4000-8000-000000000001"
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("alert-detail-error")).toBeDefined(),
    );
    expect(screen.getByTestId("alert-detail-retry")).toBeDefined();
  });

  it("renders all five AC2 fields on loaded alert", async () => {
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
    await waitFor(() =>
      expect(screen.getByTestId("alert-detail-view")).toBeDefined(),
    );
    // cluster_id
    expect(screen.getByText(alert.cluster_id)).toBeDefined();
    // affected_vin_count
    expect(screen.getByText(String(alert.affected_vin_count))).toBeDefined();
    // root_cause_signature (full text)
    expect(
      screen.getByText("E-coat voltage drop at stage 6 + humidity spike booth 3"),
    ).toBeDefined();
    // confidence_score as percentage
    expect(screen.getByText("94%")).toBeDefined();
    // process_replay_url link
    expect(screen.getByTestId("process-replay-link")).toBeDefined();
  });

  it("emits CLUSTER_ALERT_VIEWED adoption event on load", async () => {
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
    await waitFor(() =>
      expect(adoptionEvents.emitClusterAlertViewed).toHaveBeenCalledWith(
        alert.alert_id,
        alert.cluster_id,
      ),
    );
  });

  it("acknowledge 200 optimistically updates status badge and emits CLUSTER_ALERT_ACKNOWLEDGED", async () => {
    const alert = makeAlert({ status: "new" });
    const acknowledged = makeAlert({
      status: "in_progress",
      acknowledged_at: "2026-10-12T08:20:00Z",
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
    await waitFor(() =>
      expect(screen.getByTestId("acknowledge-button")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("acknowledge-button"));

    await waitFor(() =>
      expect(screen.getByTestId("acknowledge-success")).toBeDefined(),
    );
    expect(adoptionEvents.emitClusterAlertAcknowledged).toHaveBeenCalledWith(
      alert.alert_id,
      alert.cluster_id,
    );
  });

  it("acknowledge 409 shows already-acknowledged notice without error toast", async () => {
    const alert = makeAlert({ status: "new" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "acknowledgeClusterAlert").mockRejectedValue(
      new api.ApiError(409, "Conflict"),
    );

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={ENGINEER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("acknowledge-button")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("acknowledge-button"));

    await waitFor(() =>
      expect(screen.getByTestId("acknowledge-already")).toBeDefined(),
    );
    // No generic error toast
    expect(screen.queryByTestId("alert-detail-error")).toBeNull();
  });

  it("corrective-action 201 renders the Jira link", async () => {
    const alert = makeAlert({ status: "acknowledged" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "createCorrectiveAction").mockResolvedValue(
      makeCorrectiveResult(),
    );

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
    expect(
      screen.getByTestId("corrective-action-jira-link"),
    ).toBeDefined();
  });

  it("corrective-action 409 renders the existing ticket ID", async () => {
    const alert = makeAlert({ status: "acknowledged" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);
    vi.spyOn(api, "createCorrectiveAction").mockRejectedValue(
      new api.ApiError(409, JSON.stringify({ details: [{ jira_ticket_id: "IFOS-55" }] })),
    );

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
      expect(screen.getByTestId("corrective-action-error")).toBeDefined(),
    );
    expect(screen.getByText(/IFOS-55/)).toBeDefined();
  });

  it("acknowledge and corrective-action buttons are disabled when user is not in engineer group", async () => {
    const alert = makeAlert({ status: "new" });
    vi.spyOn(api, "getClusterAlert").mockResolvedValue(alert);

    renderWithI18n(
      <AlertDetailView
        alertId={alert.alert_id}
        userId="kb-001"
        userGroups={OTHER_GROUPS}
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("acknowledge-button")).toBeDefined(),
    );

    const ackBtn = screen.getByTestId("acknowledge-button");
    expect((ackBtn as HTMLButtonElement).disabled).toBe(true);

    const caBtn = screen.getByTestId("assign-corrective-action-button");
    expect((caBtn as HTMLButtonElement).disabled).toBe(true);
  });
});
