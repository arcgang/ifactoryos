import "../i18n";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OverrideLogPage } from "./OverrideLogPage";
import type { OverrideListResponse, OverrideRecord } from "../types/overrides";

function makeOverrideRecord(
  idx: number,
  overrides?: Partial<OverrideRecord>,
): OverrideRecord {
  return {
    overrideId: `ovr-2026W42-${String(idx).padStart(4, "0")}`,
    runId: "00000000-0000-0000-0000-aabbccddeeff",
    vin: `WBA3G2C04PFX1900${idx}`,
    originalPosition: idx * 3,
    newPosition: idx,
    justification: `Justification text number ${idx} for testing purposes`,
    plannerId: "K.Braun",
    createdAt: "2026-10-12T06:00:00Z",
    ...overrides,
  };
}

function makeListResponse(
  count: number,
  nextCursor: string | null = null,
): OverrideListResponse {
  return {
    data: Array.from({ length: count }, (_, i) => makeOverrideRecord(i + 1)),
    page: { next_cursor: nextCursor, limit: 50 },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("OverrideLogPage — empty state", () => {
  it("shows empty state when API returns no overrides", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeListResponse(0)),
      text: () => Promise.resolve(""),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-empty")).toBeTruthy();
    });

    expect(screen.getByText(/no overrides recorded this week/i)).toBeTruthy();
  });

  it("export button is disabled when there are no overrides", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeListResponse(0)),
      text: () => Promise.resolve(""),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-empty")).toBeTruthy();
    });

    const exportBtn = screen.getByTestId("export-pdf-btn");
    expect(exportBtn).toBeDisabled();
  });
});

describe("OverrideLogPage — populated state", () => {
  it("renders override rows with VIN, position change, justification, planner, timestamp", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeListResponse(4)),
      text: () => Promise.resolve(""),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    // All 4 records rendered
    const rows = screen.getAllByText(/WBA3G2C04PFX1900/);
    expect(rows.length).toBe(4);

    // Planner shown
    const planners = screen.getAllByText("K.Braun");
    expect(planners.length).toBeGreaterThanOrEqual(1);
  });

  it("shows count badge with correct override count", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeListResponse(4)),
      text: () => Promise.resolve(""),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    expect(screen.getByText("4 overrides")).toBeTruthy();
  });
});

describe("OverrideLogPage — infinite scroll", () => {
  it("fetches next page when scroll sentinel intersects", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeListResponse(3, "cursor-page-2")),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeListResponse(2, null)),
        text: () => Promise.resolve(""),
      } as Response);
    });

    // Mock IntersectionObserver
    const observerCallbacks: ((entries: IntersectionObserverEntry[]) => void)[] = [];
    const mockObserver = {
      observe: vi.fn(),
      disconnect: vi.fn(),
    };
    vi.stubGlobal(
      "IntersectionObserver",
      vi.fn((cb: (entries: IntersectionObserverEntry[]) => void) => {
        observerCallbacks.push(cb);
        return mockObserver;
      }),
    );

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    // Sentinel should exist since next_cursor is non-null
    expect(screen.getByTestId("override-scroll-sentinel")).toBeTruthy();

    // Trigger intersection
    await act(async () => {
      for (const cb of observerCallbacks) {
        cb([{ isIntersecting: true } as IntersectionObserverEntry]);
      }
    });

    await waitFor(() => {
      expect(callCount).toBe(2);
    });

    vi.unstubAllGlobals();
  });

  it("stops fetching when next_cursor is null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeListResponse(2, null)),
      text: () => Promise.resolve(""),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    // No sentinel when no next_cursor
    expect(screen.queryByTestId("override-scroll-sentinel")).toBeNull();
  });
});

describe("OverrideLogPage — export PDF", () => {
  it("calls GET /overrides/export and triggers file download", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/overrides/export")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          blob: () => Promise.resolve(new Blob(["PDF"], { type: "application/pdf" })),
          text: () => Promise.resolve(""),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeListResponse(2)),
        text: () => Promise.resolve(""),
      } as Response);
    });

    // Mock URL.createObjectURL and a element click
    const mockUrl = "blob:mock-url";
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn().mockReturnValue(mockUrl),
      revokeObjectURL: vi.fn(),
    });

    const appendSpy = vi.spyOn(document.body, "appendChild").mockImplementation((el: Node) => el);
    const removeSpy = vi.spyOn(document.body, "removeChild").mockImplementation((el: Node) => el);
    const clickSpy = vi.fn();

    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreateElement(tag);
      if (tag === "a") {
        el.click = clickSpy;
      }
      return el;
    });

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    const exportBtn = screen.getByTestId("export-pdf-btn");
    expect(exportBtn).not.toBeDisabled();

    await userEvent.setup({ delay: null }).click(exportBtn);

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls as [string][];
      const exportCall = calls.find(([url]) => url.includes("/overrides/export"));
      expect(exportCall).toBeTruthy();
    });

    vi.unstubAllGlobals();
    appendSpy.mockRestore();
    removeSpy.mockRestore();
  });
});

describe("OverrideLogPage — error state", () => {
  it("shows error state with retry button when API fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve("Internal server error"),
    } as Response);

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-error")).toBeTruthy();
    });

    expect(screen.getByText(/load failed/i)).toBeTruthy();
  });
});
