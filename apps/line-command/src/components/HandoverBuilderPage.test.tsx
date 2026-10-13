import { render, screen, act, cleanup, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { apiRequest, ForbiddenError } from "../services/api";
import { HandoverBuilderPage } from "./HandoverBuilderPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../services/api", () => ({
  apiRequest: vi.fn(),
  ForbiddenError: class ForbiddenError extends Error {
    resource: string;
    userId: string;
    constructor(resource: string, userId: string) {
      super(`403 Forbidden: ${resource}`);
      this.resource = resource;
      this.userId = userId;
    }
  },
}));

const mockApiRequest = vi.mocked(apiRequest);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const OPEN_ALERTS = [
  {
    alertId: "00000000-0000-0000-0000-000000000001",
    stationId: "WELD-CELL-07",
    lineId: "WL-02",
    alertType: "predictive",
    predictedFailureType: "electrode_wear",
    minutesToPredictedFailure: 16,
    recommendedAction: "Replace electrode cap immediately.",
    status: "open",
    detectedAt: new Date().toISOString(),
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
  {
    alertId: "00000000-0000-0000-0000-000000000002",
    stationId: "WELD-CELL-03",
    lineId: "WL-01",
    alertType: "predictive",
    predictedFailureType: "press_die_overrun",
    minutesToPredictedFailure: 22,
    recommendedAction: "Monitor die tonnage at shift start.",
    status: "open",
    detectedAt: new Date().toISOString(),
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupAlertsMock(alerts = OPEN_ALERTS) {
  mockApiRequest.mockImplementation(async (path: string) => {
    if (typeof path === "string" && path.includes("/andon/alerts")) {
      return { data: alerts, page: { next_cursor: null, limit: 200 } };
    }
    return { data: [] };
  });
}

function renderPage() {
  return render(
    <I18nextProvider i18n={i18n}>
      <HandoverBuilderPage userId="test-user" />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  vi.useFakeTimers();
  await i18n.changeLanguage("en-GB");
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// AC: Loading state shown while alerts are fetching
// ---------------------------------------------------------------------------

describe("Loading state", () => {
  it("shows loading state while alerts are in flight", () => {
    mockApiRequest.mockImplementation(() => new Promise(() => {})); // never resolves
    renderPage();

    const loadingEl = document.querySelector("[aria-busy='true']");
    expect(loadingEl).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC: Open alerts pre-populated as required line items
// ---------------------------------------------------------------------------

describe("Alert dispositions", () => {
  it("renders one disposition row per open alert", async () => {
    setupAlertsMock();
    renderPage();

    await act(async () => { await Promise.resolve(); });

    // Two selects — one per alert
    const selects = screen.getAllByRole("combobox");
    expect(selects.length).toBe(2);
  });

  it("each alert row shows the stationId", async () => {
    setupAlertsMock();
    renderPage();

    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("WELD-CELL-07")).toBeTruthy();
    expect(screen.getByText("WELD-CELL-03")).toBeTruthy();
  });

  it("each alert row shows the predictedFailureType", async () => {
    setupAlertsMock();
    renderPage();

    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("electrode_wear")).toBeTruthy();
    expect(screen.getByText("press_die_overrun")).toBeTruthy();
  });

  it("submit button is disabled while any disposition is unset", async () => {
    setupAlertsMock();
    renderPage();

    await act(async () => { await Promise.resolve(); });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn).toBeTruthy();
    expect(submitBtn.getAttribute("aria-disabled")).toBe("true");
  });

  it("submit button is enabled once all dispositions are set", async () => {
    setupAlertsMock();
    renderPage();

    await act(async () => { await Promise.resolve(); });

    const selects = screen.getAllByRole("combobox");

    await act(async () => {
      for (const sel of selects) {
        fireEvent.change(sel, { target: { value: "resolved" } });
      }
    });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn.getAttribute("aria-disabled")).toBe("false");
    expect(submitBtn).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// AC: Empty alerts state
// ---------------------------------------------------------------------------

describe("Empty alerts", () => {
  it("shows empty-alerts message and enables submit when no open alerts", async () => {
    setupAlertsMock([]);
    renderPage();

    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("No open alerts for this shift")).toBeTruthy();

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn.getAttribute("aria-disabled")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// AC: VIN validation
// ---------------------------------------------------------------------------

describe("Held VINs", () => {
  it("rejects an invalid VIN", async () => {
    setupAlertsMock([]);
    renderPage();

    await act(async () => { await Promise.resolve(); });

    const vinInput = screen.getByRole("textbox", { name: /add a vin/i });
    const addBtn = screen.getByRole("button", { name: /\+ add vin/i });

    await act(async () => {
      fireEvent.change(vinInput, { target: { value: "INVALID" } });
      addBtn.click();
    });

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/17 characters/i)).toBeTruthy();
  });

  it("accepts a valid VIN and shows it as a tag", async () => {
    setupAlertsMock([]);
    renderPage();

    await act(async () => { await Promise.resolve(); });

    const vinInput = screen.getByRole("textbox", { name: /add a vin/i });
    const addBtn = screen.getByRole("button", { name: /\+ add vin/i });

    await act(async () => {
      fireEvent.change(vinInput, { target: { value: "WBS1J71080P123456" } });
      addBtn.click();
    });

    expect(screen.getByText("WBS1J71080P123456")).toBeTruthy();
  });

  it("removes a VIN when × is clicked", async () => {
    setupAlertsMock([]);
    renderPage();

    await act(async () => { await Promise.resolve(); });

    const vinInput = screen.getByRole("textbox", { name: /add a vin/i });
    const addBtn = screen.getByRole("button", { name: /\+ add vin/i });

    await act(async () => {
      fireEvent.change(vinInput, { target: { value: "WBS1J71080P123456" } });
      addBtn.click();
    });

    expect(screen.getByText("WBS1J71080P123456")).toBeTruthy();

    const removeBtn = screen.getByRole("button", { name: /remove vin wbs1j71080p123456/i });
    await act(async () => { removeBtn.click(); });

    expect(screen.queryByText("WBS1J71080P123456")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC: Submission — success path
// ---------------------------------------------------------------------------

describe("Submission", () => {
  it("calls POST /api/v1/handover and shows success state on 201", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path === "/api/v1/handover") {
        return {
          handoverId: "aabbccdd-0000-0000-0000-000000000001",
          submittedAt: new Date().toISOString(),
          submittedByUserId: "test-user",
        };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    await act(async () => {
      submitBtn.click();
      await Promise.resolve();
    });

    const successRegion = screen.getByRole("status");
    expect(successRegion.textContent).toContain("Handover submitted successfully");
  });

  it("shows conflict error on 409", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path === "/api/v1/handover") {
        throw new Error("Request failed: 409 Conflict");
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    await act(async () => {
      submitBtn.click();
      await Promise.resolve();
    });

    const errorBanner = screen.getByRole("alert");
    expect(errorBanner.textContent).toContain("already submitted for this shift");
  });

  it("shows forbidden error on 403", async () => {
    const { ForbiddenError: FE } = await import("../services/api");

    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path === "/api/v1/handover") {
        throw new FE("/api/v1/handover", "test-user");
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    await act(async () => {
      submitBtn.click();
      await Promise.resolve();
    });

    const errorBanner = screen.getByRole("alert");
    expect(errorBanner.textContent).toContain("Shift Lead role required");
  });
});

// ---------------------------------------------------------------------------
// AC: i18n
// ---------------------------------------------------------------------------

describe("i18n", () => {
  it("renders German headings when locale is de-DE", async () => {
    setupAlertsMock([]);
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("OEE-Delta vs. 30-Tage-Durchschnitt")).toBeTruthy();
  });

  it("renders English headings when locale is en-GB", async () => {
    setupAlertsMock([]);
    await act(async () => { await i18n.changeLanguage("en-GB"); });
    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("OEE Delta vs 30-Day Average")).toBeTruthy();
  });
});
