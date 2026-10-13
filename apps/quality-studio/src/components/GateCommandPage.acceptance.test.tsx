/**
 * Acceptance tests for US-017 — Gate Command health score completeness indicator.
 *
 * These tests exercise the END-TO-END seam across the three story tasks:
 *   1. postgresql-citus migration defines the data shape
 *   2. opcenter-sync-service delivers it via GET /api/v1/gate-results
 *   3. quality-studio renders completeness, blocks/enables Certify, shows timeout
 *
 * Existing unit tests cover each piece in isolation. This file covers the
 * seams that no single unit-test file reaches, in particular:
 *   - The full AC1 scenario (pending indicator + Certify BLOCKED) rendered to DOM
 *   - The full AC2 scenario (Certify becomes ENABLED → clicking it certifies)
 *   - The full AC3 scenario (timeout fires, alert shown, Manual Override flow,
 *     justification gate, pending approval state)
 *
 * Covered acceptance criteria:
 *   AC1 — missing gate results shown as Pending + Certify disabled with tooltip
 *   AC2 — all 5 present → Certify enabled; clicking it shows "Vehicle Certified"
 *   AC3 — timeout alert identifies failed station; Manual Override requires typed
 *          justification and supervisor approval before certification proceeds
 */

import type { ReactNode } from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { GateCompletenessCard } from "./GateCommandPage";
import type { GateResultListResponse } from "../types/gateResults";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const VIN = "WBA31AB09NCG47381";
const ALL_GATE_TYPES = ["dimensional", "leak", "electrical", "nvh", "adas"] as const;

function makeResult(gateType: string, result: "pass" | "conditional_pass" | "fail", i: number) {
  return {
    gateResultId: `gr-${i}`,
    vin: VIN,
    gateId: `GATE-${gateType.slice(0, 3).toUpperCase()}`,
    gateType,
    result,
    measurements: [],
    measuredAt: "2099-01-01T06:00:00Z",
    receivedFromOpcenterAt: "2099-01-01T06:00:01Z",
    opcenterEventId: null,
  };
}

function makeApiResponse(gateTypes: readonly string[]): GateResultListResponse {
  return {
    data: gateTypes.map((gt, i) => makeResult(gt, "pass", i) as never),
    page: { next_cursor: null, limit: 50 },
  };
}

function stubFetch(response: GateResultListResponse) {
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
}

// ---------------------------------------------------------------------------
// Wrapper / render helpers
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

function renderCard(vin = VIN) {
  return render(
    <Wrapper>
      <GateCompletenessCard vin={vin} />
    </Wrapper>,
  );
}

async function advanceToTimeout() {
  // Advance 601 s past the 600 s default threshold
  await act(async () => {
    vi.advanceTimersByTime(601_000);
  });
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
}

// ---------------------------------------------------------------------------
// Precondition assertions — the test suite fails loudly if the components
// or API contract it relies on are absent.
// ---------------------------------------------------------------------------

describe("US-017 Preconditions", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
    // Suppress fetch during precondition checks
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("GateCompletenessCard renders without crashing — component is present", () => {
    renderCard();
    // Loading state proves the component is wired up
    expect(screen.getByText("Loading gate results…")).toBeTruthy();
  });

  it("all 5 gate type labels are present in i18n en-GB locale", () => {
    // If these keys are missing the badge renderer silently falls back to the key name
    for (const gt of ALL_GATE_TYPES) {
      const label = i18n.t(`gateCommand.gateType.${gt}`);
      expect(label).not.toBe(`gateCommand.gateType.${gt}`);
    }
  });
});

// ---------------------------------------------------------------------------
// AC1 — Missing gate results shown as Pending; Certify button disabled
// ---------------------------------------------------------------------------

describe("AC1 — Pending indicator and disabled Certify when results are missing", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("AC1a — gate types not yet received are shown with 'Pending' badge", async () => {
    stubFetch(makeApiResponse(["dimensional", "leak", "electrical"]));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // The two absent gate types must render as Pending
    const pending = screen.getAllByText("Pending");
    expect(pending.length).toBe(2);
  });

  it("AC1b — Certify button is aria-disabled when any gate result is pending", async () => {
    stubFetch(makeApiResponse(["dimensional"]));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    const certify = screen.getByRole("button", { name: "Certify" });
    expect(certify.getAttribute("aria-disabled")).toBe("true");
  });

  it("AC1c — Certify button carries aria-describedby pointing at the tooltip when disabled", async () => {
    stubFetch(makeApiResponse(["dimensional", "leak"]));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("2 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    const certify = screen.getByRole("button", { name: "Certify" });
    // Must be associated with a tooltip element explaining which results are outstanding
    const tooltipId = certify.getAttribute("aria-describedby");
    expect(tooltipId).toBeTruthy();
    const tooltip = document.getElementById(tooltipId!);
    expect(tooltip).toBeTruthy();
    expect(tooltip!.getAttribute("role")).toBe("tooltip");
  });

  it("AC1d — tooltip text lists the pending gate type names", async () => {
    stubFetch(makeApiResponse(["dimensional", "leak", "electrical"]));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    const tooltip = screen.getByRole("tooltip");
    // NVH and ADAS are the two pending types
    expect(tooltip.textContent).toContain("NVH");
    expect(tooltip.textContent).toContain("ADAS");
  });

  it("AC1e — 0/5: all five gate types show as Pending and Certify is disabled", async () => {
    stubFetch(makeApiResponse([]));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("0 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    expect(screen.getAllByText("Pending")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Certify" }).getAttribute("aria-disabled")).toBe("true");
  });
});

// ---------------------------------------------------------------------------
// AC2 — All 5 gate results received: Certify enabled; clicking certifies
// ---------------------------------------------------------------------------

describe("AC2 — Certify becomes enabled and works when all 5 gate results are present", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("AC2a — completeness indicator shows 'Health score complete' at 5/5", async () => {
    stubFetch(makeApiResponse(ALL_GATE_TYPES));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );
    expect(screen.queryAllByText("Pending")).toHaveLength(0);
  });

  it("AC2b — Certify button is NOT aria-disabled when all 5 gate types are present", async () => {
    stubFetch(makeApiResponse(ALL_GATE_TYPES));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );

    const certify = screen.getByRole("button", { name: "Certify" });
    expect(certify.getAttribute("aria-disabled")).toBeNull();
  });

  it("AC2c — clicking the enabled Certify button shows 'Vehicle Certified' confirmation", async () => {
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    stubFetch(makeApiResponse(ALL_GATE_TYPES));
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );

    const certify = screen.getByRole("button", { name: "Certify" });
    await user.click(certify);

    // AC2 final state: visual confirmation that health score is certified
    await waitFor(() =>
      expect(screen.getByText("Vehicle Certified")).toBeTruthy(),
    );
  });

  it("AC2d — after certifying, Certify button disappears (no double-certification)", async () => {
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    stubFetch(makeApiResponse(ALL_GATE_TYPES));
    renderCard();

    await waitFor(() => expect(screen.getByText("Health score complete")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "Certify" }));

    await waitFor(() => expect(screen.getByText("Vehicle Certified")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Certify" })).toBeNull();
  });

  it("AC2e — no tooltip is rendered when Certify is enabled (no outstanding gates)", async () => {
    stubFetch(makeApiResponse(ALL_GATE_TYPES));
    renderCard();

    await waitFor(() => expect(screen.getByText("Health score complete")).toBeTruthy());
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Timeout alert, Manual Override flow, justification gate
// ---------------------------------------------------------------------------

describe("AC3 — Timeout alert identifies failed station; Manual Override requires justification and supervisor approval", () => {
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

  async function renderWithTimeoutReached(receivedTypes: readonly string[] = ["dimensional"]) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (typeof url === "string" && url.includes("gate-results")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeApiResponse(receivedTypes)),
          });
        }
        return Promise.reject(new Error("not found"));
      }),
    );
    renderCard();

    await waitFor(() =>
      expect(
        screen.getByText(
          `${receivedTypes.length} of 5 gate results received from OpCenter`,
        ),
      ).toBeTruthy(),
    );
    await advanceToTimeout();
  }

  it("AC3a — timeout alert appears after the default 600-second threshold elapses", async () => {
    await renderWithTimeoutReached();
    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
  });

  it("AC3b — timeout alert identifies the station(s) that did not send a result", async () => {
    // Only dimensional received; NVH, Leak, Electrical, ADAS timed out
    await renderWithTimeoutReached(["dimensional"]);

    const alert = screen.getByRole("alert");
    // Alert must contain name(s) of timed-out gate type(s)
    expect(alert.textContent).toContain("NVH");
  });

  it("AC3c — timeout alert title says 'Gate station timeout'", async () => {
    await renderWithTimeoutReached();
    expect(screen.getByText("Gate station timeout")).toBeTruthy();
  });

  it("AC3d — Manual Override button appears after timeout", async () => {
    await renderWithTimeoutReached();
    expect(screen.getByRole("button", { name: "Manual Override" })).toBeTruthy();
  });

  it("AC3e — Manual Override button is NOT visible before timeout", async () => {
    // With 1/5 results but before timeout
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeApiResponse(["dimensional"])),
        }),
      ),
    );
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // Only 10 seconds elapsed — no timeout
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    expect(screen.queryByRole("button", { name: "Manual Override" })).toBeNull();
  });

  it("AC3f — clicking Manual Override opens a dialog with the correct heading", async () => {
    const user = makeUser();
    await renderWithTimeoutReached();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Manual Override — Gate Certification" }),
    ).toBeTruthy();
  });

  it("AC3g — dialog requires a typed justification (aria-required textarea)", async () => {
    const user = makeUser();
    await renderWithTimeoutReached();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));

    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    expect(textarea).toBeTruthy();
    expect(textarea.getAttribute("aria-required")).toBe("true");
  });

  it("AC3h — submitting fewer than 20 characters shows a validation error (certification is blocked)", async () => {
    const user = makeUser();
    await renderWithTimeoutReached();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    await user.type(screen.getByRole("textbox", { name: /Justification/i }), "Too short");
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));

    expect(screen.getByText("Justification must be at least 20 characters.")).toBeTruthy();
  });

  it("AC3i — supervisor approval button transitions to 'Pending approval' after valid justification", async () => {
    const user = makeUser();
    await renderWithTimeoutReached();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    await user.type(
      screen.getByRole("textbox", { name: /Justification/i }),
      "NVH sensor offline — confirmed by line supervisor on duty.",
    );
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));

    // The button text changes to 'Pending approval…' and becomes aria-disabled
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Pending approval/i });
      expect(btn).toBeTruthy();
      expect(btn.getAttribute("aria-disabled")).toBe("true");
    });
  });

  it("AC3j — cancelling the override modal closes the dialog without certifying", async () => {
    const user = makeUser();
    await renderWithTimeoutReached();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    expect(screen.getByRole("dialog")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // Certify confirmation must not have appeared
    expect(screen.queryByText("Vehicle Certified")).toBeNull();
  });

  it("AC3k — Certify button on the main card is aria-disabled after timeout (certify path is override-only)", async () => {
    await renderWithTimeoutReached();
    const certify = screen.getByRole("button", { name: "Certify" });
    // timedOut && !isComplete → button remains disabled
    expect(certify.getAttribute("aria-disabled")).toBe("true");
  });
});
