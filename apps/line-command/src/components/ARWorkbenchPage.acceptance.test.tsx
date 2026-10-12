/**
 * Acceptance tests — US-010: AR Workbench RFID-triggered overlay (frontend seam).
 *
 * Tests the behaviour produced by ARWorkbenchPage, which composes:
 *   - fetchArInstructions (API helper wired from the real fetchFn via useCallback)
 *   - ARWorkInstructions (render logic, 10-s client-side timeout, fallback panel)
 *
 * Precondition: ARWorkbenchPage mounts and passes a fetchInstructions prop down
 * to ARWorkInstructions.  If the component fails to render at all, every
 * `waitFor` block will time out and the test will report failure.
 *
 * AC1: Overlay renders option codes, torque specs, and step sequence.
 * AC2: Rare variant — visual delta indicator (Δ DELTA badge + RARE VARIANT banner).
 * AC3: Client-side 10-s timeout → fallback screen with VIN + QR code.
 */

import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import i18n from "../i18n";
import type { ArInstructionResponse, ArInstructionFallbackResponse } from "./ARWorkInstructions";
import { ARWorkInstructions } from "./ARWorkInstructions";
import { ARWorkbenchPage } from "./ARWorkbenchPage";

// ---------------------------------------------------------------------------
// Precondition: globalThis.crypto must be available for generateIdempotencyKey
// ---------------------------------------------------------------------------
beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STANDARD_VIN = "WBS4Y9103NCK29471";
const RARE_VIN = "WBS4Y9C08NCK31082";

const STANDARD_RESPONSE: ArInstructionResponse = {
  vin: STANDARD_VIN,
  stationId: "MARRIAGE-01",
  optionCodes: ["SA258", "SA386", "SA4AC"],
  variantCode: "G26",
  isRareVariant: false,
  instructionVersion: "v4.2.1",
  fetchedAt: "2099-01-01T06:00:00Z",
  steps: [
    {
      stepNumber: 1,
      description: "Body-to-chassis alignment verification",
      torqueSpecNm: 47,
      torqueToleranceNm: 2,
      fastenerPointId: "FP-DATUM-M12",
      isVariantDelta: false,
    },
    {
      stepNumber: 2,
      description: "Front subframe marriage bolt sequence",
      torqueSpecNm: 110,
      torqueToleranceNm: 5,
      fastenerPointId: "FP-FRONT-SUB",
      isVariantDelta: false,
    },
  ],
};

const RARE_VARIANT_RESPONSE: ArInstructionResponse = {
  vin: RARE_VIN,
  stationId: "MARRIAGE-01",
  optionCodes: ["SA258", "SA3A5", "SA4T9"],
  variantCode: "i4 M50 xDrive",
  isRareVariant: true,
  instructionVersion: "v4.2.4",
  fetchedAt: "2099-01-01T06:00:00Z",
  steps: [
    {
      stepNumber: 1,
      description: "Body-to-chassis alignment verification",
      torqueSpecNm: 47,
      torqueToleranceNm: 2,
      fastenerPointId: "FP-DATUM-M12",
      isVariantDelta: false,
    },
    {
      stepNumber: 2,
      description: "Front subframe marriage bolt sequence (M Performance)",
      torqueSpecNm: 135,
      torqueToleranceNm: 5,
      fastenerPointId: "FP-FRONT-SUB-M",
      isVariantDelta: true,
      variantDeltaNote:
        "M Performance: reinforced subframe — 135 Nm (vs 110 Nm standard), 8-bolt pattern",
    },
  ],
};

const FALLBACK_RESPONSE: ArInstructionFallbackResponse = {
  vin: STANDARD_VIN,
  stationId: "MARRIAGE-01",
  fallbackPdfQrUrl: `https://docs.internal.ifos.example/instructions/${STANDARD_VIN}.pdf`,
  failureLoggedAt: "2099-01-01T06:10:00Z",
  correlationId: "c1a2b3c4-d5e6-4f78-9012-abcdef012345",
};

// ---------------------------------------------------------------------------
// Helper: render ARWorkInstructions directly (unit-level contract)
// ---------------------------------------------------------------------------

function renderInstructions(
  fetchFn: (
    vin: string,
    sid: string,
    signal: AbortSignal,
  ) => Promise<ArInstructionResponse | ArInstructionFallbackResponse>,
  vin = STANDARD_VIN,
  stationId = "MARRIAGE-01",
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ARWorkInstructions
        vin={vin}
        stationId={stationId}
        fetchInstructions={fetchFn}
      />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Precondition: ARWorkInstructions renders without crashing
// ---------------------------------------------------------------------------

describe("Precondition: component mounts", () => {
  it("ARWorkInstructions renders a loading state immediately on mount", () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => { /* never */ });
    renderInstructions(neverResolves);
    // Any rendered output confirms the component is mounted; loading text is the observable
    expect(screen.getByText(/Loading holographic overlay/i)).toBeTruthy();
  });

  it("ARWorkbenchPage renders the AR Workbench heading", async () => {
    // ARWorkbenchPage uses import.meta.env and crypto — verify it at least mounts
    const onForbidden = vi.fn();
    const neverResolves = () => new Promise<never>(() => { /* never */ });

    // We mock globalThis.fetch so apiRequest (called via fetchArInstructions) returns a pending promise
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockReturnValue(
      new Promise<Response>(() => { /* never */ }),
    );

    render(
      <I18nextProvider i18n={i18n}>
        <ARWorkbenchPage
          vin={STANDARD_VIN}
          userId="user-123"
          onForbidden={onForbidden}
        />
      </I18nextProvider>,
    );

    expect(screen.getByText(/AR Workbench/i)).toBeTruthy();
    fetchSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// AC1: Overlay renders correct option codes, torque specs, and step sequence
// ---------------------------------------------------------------------------

describe("AC1: overlay renders vehicle-specific option codes, torque specs, and steps", () => {
  it("AC1 — renders all three option codes after a successful fetch", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => expect(screen.getByText("SA258")).toBeTruthy());
    expect(screen.getByText("SA386")).toBeTruthy();
    expect(screen.getByText("SA4AC")).toBeTruthy();
  });

  it("AC1 — renders torque spec '47 Nm ± 2 Nm' for step 1", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => expect(screen.getByText("47 Nm ± 2 Nm")).toBeTruthy());
  });

  it("AC1 — renders torque spec '110 Nm ± 5 Nm' for step 2", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => expect(screen.getByText("110 Nm ± 5 Nm")).toBeTruthy());
  });

  it("AC1 — renders step descriptions in sequence order", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText(/Body-to-chassis alignment verification/i)).toBeTruthy(),
    );
    expect(screen.getByText(/Front subframe marriage bolt sequence/i)).toBeTruthy();
  });

  it("AC1 — renders the instruction version", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => expect(screen.getByText("v4.2.1")).toBeTruthy());
  });

  it("AC1 — renders the VIN in the overlay header", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() =>
      expect(screen.getAllByText(STANDARD_VIN).length).toBeGreaterThan(0),
    );
  });
});

// ---------------------------------------------------------------------------
// AC2: Rare variant — visual delta indicator
// ---------------------------------------------------------------------------

describe("AC2: rare variant shows RARE VARIANT banner and Δ DELTA badge on delta steps", () => {
  it("AC2 — renders RARE VARIANT banner when isRareVariant is true", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/RARE VARIANT/i)).toBeTruthy();
  });

  it("AC2 — RARE VARIANT banner includes the variant code 'i4 M50 xDrive'", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() =>
      expect(screen.getByText(/i4 M50 xDrive/i)).toBeTruthy(),
    );
  });

  it("AC2 — Δ DELTA badge rendered on the delta step", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() => expect(screen.getByText("Δ DELTA")).toBeTruthy());
  });

  it("AC2 — Δ DELTA badge has role=status for screen-reader announcement", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() =>
      expect(screen.getByRole("status")).toBeTruthy(),
    );
  });

  it("AC2 — variantDeltaNote text is visible for the delta step", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() =>
      expect(
        screen.getByText(/M Performance: reinforced subframe/i),
      ).toBeTruthy(),
    );
  });

  it("AC2 — delta torque spec '135 Nm ± 5 Nm' is rendered (differs from standard 110 Nm)", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, RARE_VIN);

    await waitFor(() => expect(screen.getByText("135 Nm ± 5 Nm")).toBeTruthy());
  });

  it("AC2 — standard vehicle does NOT show RARE VARIANT banner", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    // Wait for overlay to finish loading
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.queryByText(/RARE VARIANT/i)).toBeNull();
  });

  it("AC2 — standard vehicle steps do NOT carry Δ DELTA badge", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.queryByText("Δ DELTA")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC3: Client-side 10-second timeout → fallback screen with VIN + QR code
// ---------------------------------------------------------------------------

describe("AC3: 10-second timeout triggers fallback screen with VIN and QR code", () => {
  it("AC3 — fallback panel appears exactly when 10 s elapses with no response", async () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => { /* never */ });
    renderInstructions(neverResolves, STANDARD_VIN);

    // Before timeout — still loading
    expect(screen.queryByRole("alert")).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/Overlay Unavailable/i)).toBeTruthy();
  });

  it("AC3 — fallback panel does NOT appear before 10 s (9.9 s still shows loading)", async () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => { /* never */ });
    renderInstructions(neverResolves, STANDARD_VIN);

    await act(async () => {
      vi.advanceTimersByTime(9_900);
    });

    // Loading spinner should still be visible; fallback should not
    expect(screen.queryByText(/Overlay Unavailable/i)).toBeNull();
    expect(screen.getByText(/Loading holographic overlay/i)).toBeTruthy();
  });

  it("AC3 — fallback panel after 504 response shows the VIN", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(STANDARD_VIN)).toBeTruthy();
  });

  it("AC3 — fallback panel after 504 response renders a QR code img with the VIN in alt text", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() => {
      const img = screen.getByRole("img") as HTMLImageElement;
      expect(img.alt).toMatch(new RegExp(STANDARD_VIN));
    });
  });

  it("AC3 — fallback panel after 504 response shows scan-QR instruction label", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() =>
      expect(screen.getByText(/Scan QR code for PDF instructions/i)).toBeTruthy(),
    );
  });

  it("AC3 — fallback panel after 504 response shows the correlationId for the associate's audit record", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() =>
      expect(screen.getByText("c1a2b3c4-d5e6-4f78-9012-abcdef012345")).toBeTruthy(),
    );
  });

  it("AC3 — fallback panel does not render a step list", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn, STANDARD_VIN);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByRole("list")).toBeNull();
  });
});
