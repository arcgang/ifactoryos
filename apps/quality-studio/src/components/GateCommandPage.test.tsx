import type { ReactNode } from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { GateCompletenessCard } from "./GateCommandPage";
import type { GateResultListResponse } from "../types/gateResults";

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function makeGateResult(
  gateType: string,
  result: "pass" | "conditional_pass" | "fail",
  index: number,
): object {
  return {
    gateResultId: `gr-${index}`,
    vin: "WBA31AB09NCG47381",
    gateId: `GATE-${gateType.toUpperCase().slice(0, 3)}`,
    gateType,
    result,
    measurements: [],
    measuredAt: "2026-10-12T14:21:05Z",
    receivedFromOpcenterAt: "2026-10-12T14:21:10Z",
    opcenterEventId: null,
  };
}

function makeResponse(gateTypes: string[]): GateResultListResponse {
  return {
    data: gateTypes.map((gt, i) => makeGateResult(gt, "pass", i) as never),
    page: { next_cursor: null, limit: 50 },
  };
}

const VIN = "WBA31AB09NCG47381";

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      {children}
    </I18nextProvider>
  );
}

function renderCard(vin = VIN) {
  return render(
    <Wrapper>
      <GateCompletenessCard vin={vin} />
    </Wrapper>,
  );
}

// ---------------------------------------------------------------------------
// Tests: completeness aggregation
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — completeness aggregation", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows loading state initially", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})), // never resolves
    );
    renderCard();
    expect(screen.getByText("Loading gate results…")).toBeTruthy();
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("Network error"))),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Error loading gate results. Please retry.")).toBeTruthy(),
    );
  });

  it("0/5 — all five gate types show as Pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse([])),
        }),
      ),
    );
    renderCard();
    await waitFor(() => expect(screen.getAllByText("Pending")).toHaveLength(5));
    expect(screen.getByText("0 of 5 gate results received from OpCenter")).toBeTruthy();
  });

  it("3/5 — received types show their result, absent types show Pending", async () => {
    const response = makeResponse(["dimensional", "leak", "electrical"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(response),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    expect(screen.getAllByText("Pass")).toHaveLength(3);
    expect(screen.getAllByText("Pending")).toHaveLength(2);
  });

  it("5/5 — shows 'Health score complete' and no Pending badges", async () => {
    const response = makeResponse([
      "dimensional",
      "leak",
      "electrical",
      "nvh",
      "adas",
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(response),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );
    expect(screen.queryAllByText("Pending")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Tests: Certify button disabled / enabled
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — Certify button", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("Certify button has aria-disabled when gate types are absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse(["dimensional", "leak"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("2 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    const btn = screen.getByRole("button", { name: "Certify" });
    expect(btn.getAttribute("aria-disabled")).toBe("true");
  });

  it("Certify button is not aria-disabled when all 5 gate types are present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeResponse(["dimensional", "leak", "electrical", "nvh", "adas"]),
            ),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );
    const btn = screen.getByRole("button", { name: "Certify" });
    expect(btn.getAttribute("aria-disabled")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: tooltip content lists absent gate names
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — tooltip", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("tooltip lists the absent gate type names", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(makeResponse(["dimensional", "leak", "electrical"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    // Tooltip element should contain the pending gate names
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("NVH");
    expect(tooltip.textContent).toContain("ADAS");
  });
});

// ---------------------------------------------------------------------------
// Tests: timeout countdown state machine
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — timeout countdown", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows timeout alert after the timeout threshold elapses with pending gates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse(["dimensional"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // Advance 10 minutes + 1 second past the 600-second default timeout
    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Gate station timeout");
  });

  it("polling stops after timeout (fetch count does not increase after timeout)", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeResponse([])),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("0 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // Trigger timeout — polls fire every 10s up to ~600s, then stop
    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());

    // Capture call count AFTER timeout
    const callsAtTimeout = fetchMock.mock.calls.length;

    // Advance another 30s — no extra polls should happen after timeout
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    expect(fetchMock.mock.calls.length).toBe(callsAtTimeout);
  });
});

// ---------------------------------------------------------------------------
// Tests: Manual Override modal
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — Manual Override modal", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function makeUser() {
    return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  }

  async function renderWithTimeout() {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (typeof url === "string" && url.includes("gate-results")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeResponse(["dimensional"])),
          });
        }
        // supervisor approval endpoint — not yet deployed
        return Promise.reject(new Error("Not found"));
      }),
    );
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  }

  it("Manual Override button appears after timeout", async () => {
    await renderWithTimeout();
    expect(screen.getByRole("button", { name: "Manual Override" })).toBeTruthy();
  });

  it("clicking Manual Override opens dialog with correct role and label", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(
      screen.getByRole("heading", {
        name: "Manual Override — Gate Certification",
      }),
    ).toBeTruthy();
  });

  it("justification textarea is present with required label", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    expect(textarea).toBeTruthy();
    expect(textarea.getAttribute("aria-required")).toBe("true");
  });

  it("shows validation error when justification is fewer than 20 characters", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    await user.type(textarea, "Too short");
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));
    expect(
      screen.getByText("Justification must be at least 20 characters."),
    ).toBeTruthy();
  });

  it("shows pending state after Request Supervisor Approval with valid justification", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    await user.type(textarea, "This is a long enough justification text to pass validation.");
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));
    // Button is now disabled — check it's present and has aria-disabled
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Pending approval/i });
      expect(btn).toBeTruthy();
      expect(btn.getAttribute("aria-disabled")).toBe("true");
    });
  });

  it("closes modal when Cancel is clicked", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
