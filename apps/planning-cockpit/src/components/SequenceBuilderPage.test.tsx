import "../i18n";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import type { SequencePublishResponse } from "../types/sequence";
import * as api from "../services/api";

vi.mock("../services/api", () => ({
  publishSequence: vi.fn(),
  getSyncJob: vi.fn(),
  listSyncJobs: vi.fn(),
  listConstraintThresholds: vi.fn(),
  upsertConstraintThreshold: vi.fn(),
  getConstraintStreamUrl: vi.fn(() => ""),
}));

const PUBLISHED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-1",
  opcenterSequenceId: "OPC-SEQ-2026-042-A-88142",
  status: "published",
  source: "planning-cockpit",
  publishedAt: "2026-10-13T06:17:38Z",
  auditEventId: "aud-001",
  retryScheduledAt: null,
};

const QUEUED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-2",
  opcenterSequenceId: null,
  status: "queued",
  source: "planning-cockpit",
  publishedAt: null,
  retryScheduledAt: "2026-10-13T06:18:08Z",
};

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.mocked(api.listSyncJobs).mockResolvedValue({
    data: [],
    page: { next_cursor: null, limit: 50 },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  localStorage.clear();
});

describe("SequenceBuilderPage", () => {
  it("renders Publish to OpCenter button in ready state", () => {
    render(<SequenceBuilderPage />);
    expect(screen.getByRole("button", { name: /publish to opcenter/i })).toBeTruthy();
  });

  it("shows confirm panel when Publish button is clicked", () => {
    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    expect(
      screen.getByRole("heading", { name: /confirm publish to siemens opcenter/i }),
    ).toBeTruthy();
  });

  it("cancels publish and hides confirm panel when Cancel is clicked", () => {
    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(
      screen.queryByRole("heading", { name: /confirm publish to siemens opcenter/i }),
    ).toBeNull();
  });

  it("shows success banner with opcenterSequenceId and publishedAt after published response", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);

    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
    });

    expect(screen.getByText("OPC-SEQ-2026-042-A-88142")).toBeTruthy();
    expect(screen.getByText("2026-10-13T06:17:38Z")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: /successfully published to siemens opcenter/i }),
    ).toBeTruthy();
  });

  it("shows Pending Publish badge with retry time when response is queued", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);

    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }),
    );

    await waitFor(() => {
      expect(screen.getByText(/pending publish/i)).toBeTruthy();
    });

    expect(screen.getByText(/next retry at/i)).toBeTruthy();
  });

  it("shows error banner when publishSequence throws", async () => {
    vi.mocked(api.publishSequence).mockRejectedValue(
      new Error("OPCENTER_REJECTED: Invalid VIN"),
    );

    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });

    expect(screen.getByText(/publish failed/i)).toBeTruthy();
  });

  it("dismiss button removes the success banner", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);

    render(<SequenceBuilderPage />);
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(screen.queryByText("OPC-SEQ-2026-042-A-88142")).toBeNull();
  });
});
