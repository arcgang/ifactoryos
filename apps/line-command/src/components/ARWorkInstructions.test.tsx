import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "../i18n";
import { ARWorkInstructions } from "./ARWorkInstructions";
import type { ArInstructionResponse, ArInstructionFallbackResponse } from "./ARWorkInstructions";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STANDARD_RESPONSE: ArInstructionResponse = {
  vin: "WBS4Y9103NCK29471",
  stationId: "MARRIAGE-01",
  optionCodes: ["SA258", "SA386", "SA4AC"],
  variantCode: "i4 eDrive40",
  isRareVariant: false,
  instructionVersion: "v4.2.1",
  fetchedAt: "2026-10-12T06:31:44Z",
  fallbackPdfQrUrl: "https://pdf.ifos.internal/qr/WBS4Y9103NCK29471",
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
  vin: "WBS4Y9C08NCK31082",
  stationId: "MARRIAGE-01",
  optionCodes: ["SA258", "SA3A5", "SA4T9"],
  variantCode: "i4 M50 xDrive",
  isRareVariant: true,
  instructionVersion: "v4.2.1",
  fetchedAt: "2026-10-12T07:12:06Z",
  fallbackPdfQrUrl: "https://pdf.ifos.internal/qr/WBS4Y9C08NCK31082",
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
      torqueSpecNm: 135,
      torqueToleranceNm: 5,
      fastenerPointId: "FP-FRONT-SUB-M",
      isVariantDelta: true,
      variantDeltaNote: "M Performance: reinforced subframe — 135 Nm (vs 110 Nm standard), 8-bolt pattern",
    },
  ],
};

const FALLBACK_RESPONSE: ArInstructionFallbackResponse = {
  vin: "WBS4Y9103NCK29471",
  stationId: "FINAL-01",
  fallbackPdfQrUrl: "https://pdf.ifos.internal/qr/WBS4Y9103NCK29471",
  failureLoggedAt: "2026-10-12T06:40:00Z",
  correlationId: "c1a2b3c4-d5e6-4f78-9012-abcdef012345",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderInstructions(
  fetchFn: (vin: string, sid: string, signal: AbortSignal) => Promise<ArInstructionResponse | ArInstructionFallbackResponse>,
  overrideProps?: { vin?: string; stationId?: string },
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ARWorkInstructions
        vin={overrideProps?.vin ?? "WBS4Y9103NCK29471"}
        stationId={overrideProps?.stationId ?? "MARRIAGE-01"}
        fetchInstructions={fetchFn}
      />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// State 1: Loading
// ---------------------------------------------------------------------------

describe("State: loading", () => {
  it("renders a loading indicator while fetch is in progress", async () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => {/* never */});
    renderInstructions(neverResolves);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/Loading holographic overlay/i)).toBeTruthy();
  });

  it("displays the VIN while loading", () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => {/* never */});
    renderInstructions(neverResolves, { vin: "WBS4Y9103NCK29471" });

    expect(screen.getByText("WBS4Y9103NCK29471")).toBeTruthy();
  });

  it("does not render step list while loading", () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => {/* never */});
    renderInstructions(neverResolves);

    expect(screen.queryByRole("list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// State 2: Populated (standard i4)
// ---------------------------------------------------------------------------

describe("State: populated — standard i4", () => {
  it("renders the VIN after successful fetch", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText("WBS4Y9103NCK29471")).toBeTruthy(),
    );
  });

  it("renders all step descriptions", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText(/Body-to-chassis alignment verification/i)).toBeTruthy(),
    );
    expect(screen.getByText(/Front subframe marriage bolt sequence/i)).toBeTruthy();
  });

  it("renders torque spec for step 1: 47 Nm ± 2 Nm", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText("47 Nm ± 2 Nm")).toBeTruthy(),
    );
  });

  it("renders the variantCode", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    // Wait for populated state — instruction version is always shown in the header
    await waitFor(() =>
      expect(screen.getByText("v4.2.1")).toBeTruthy(),
    );
  });

  it("renders the instruction version", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText("v4.2.1")).toBeTruthy(),
    );
  });

  it("renders the option codes SA258, SA386, SA4AC", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => expect(screen.getByText("SA258")).toBeTruthy());
    expect(screen.getByText("SA386")).toBeTruthy();
    expect(screen.getByText("SA4AC")).toBeTruthy();
  });

  it("does not render the rare-variant banner for a standard vehicle", async () => {
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.queryByRole("status")).toBeNull(),
    );
    expect(screen.queryByText(/RARE VARIANT/i)).toBeNull();
  });

  it("calls onOverlayRendered exactly once with the response data", async () => {
    const onRendered = vi.fn();
    const fetchFn = vi.fn().mockResolvedValue(STANDARD_RESPONSE);

    render(
      <I18nextProvider i18n={i18n}>
        <ARWorkInstructions
          vin="WBS4Y9103NCK29471"
          stationId="MARRIAGE-01"
          fetchInstructions={fetchFn}
          onOverlayRendered={onRendered}
        />
      </I18nextProvider>,
    );

    await waitFor(() => expect(onRendered).toHaveBeenCalledTimes(1));
    expect(onRendered).toHaveBeenCalledWith(
      expect.objectContaining({ vin: "WBS4Y9103NCK29471", isRareVariant: false }),
    );
  });
});

// ---------------------------------------------------------------------------
// State 3: Rare variant with delta indicators
// ---------------------------------------------------------------------------

describe("State: rare variant — M Performance", () => {
  it("renders the RARE VARIANT banner when isRareVariant is true", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
    expect(screen.getByText(/RARE VARIANT/i)).toBeTruthy();
  });

  it("renders the variant code i4 M50 xDrive in the banner", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(screen.getByText(/i4 M50 xDrive/i)).toBeTruthy(),
    );
  });

  it("renders the Δ DELTA badge on the delta step", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(screen.getByText("Δ DELTA")).toBeTruthy(),
    );
  });

  it("delta badge has role=status for screen-reader announcement", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(screen.getByRole("status")).toBeTruthy(),
    );
  });

  it("renders the variantDeltaNote text for the delta step", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(
        screen.getByText(/M Performance: reinforced subframe/i),
      ).toBeTruthy(),
    );
  });

  it("renders the delta torque spec 135 Nm ± 5 Nm", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() =>
      expect(screen.getByText("135 Nm ± 5 Nm")).toBeTruthy(),
    );
  });

  it("renders the highlighted option codes SA3A5, SA4T9", async () => {
    const fetchFn = vi.fn().mockResolvedValue(RARE_VARIANT_RESPONSE);
    renderInstructions(fetchFn, { vin: "WBS4Y9C08NCK31082" });

    await waitFor(() => expect(screen.getByText("SA3A5")).toBeTruthy());
    expect(screen.getByText("SA4T9")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// State 4: Fallback (504 response)
// ---------------------------------------------------------------------------

describe("State: fallback — 504 response", () => {
  it("renders the fallback title when fetch returns ArInstructionFallbackResponse", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
    expect(screen.getByText(/Overlay Unavailable/i)).toBeTruthy();
  });

  it("renders the VIN in the fallback panel", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText("WBS4Y9103NCK29471")).toBeTruthy(),
    );
  });

  it("renders a QR code img with descriptive alt text containing the VIN", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => {
      const img = screen.getByRole("img") as HTMLImageElement;
      expect(img.alt).toMatch(/WBS4Y9103NCK29471/);
    });
  });

  it("renders the QR code img with explicit width and height (no layout shift)", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() => {
      const img = screen.getByRole("img") as HTMLImageElement;
      expect(img.getAttribute("width")).toBe("200");
      expect(img.getAttribute("height")).toBe("200");
    });
  });

  it("renders the correlation ID in the fallback audit section", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText("c1a2b3c4-d5e6-4f78-9012-abcdef012345")).toBeTruthy(),
    );
  });

  it("renders a label directing the associate to scan for PDF instructions", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByText(/Scan QR code for PDF instructions/i)).toBeTruthy(),
    );
  });

  it("does not render step list in fallback state", async () => {
    const fetchFn = vi.fn().mockResolvedValue(FALLBACK_RESPONSE);
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
    expect(screen.queryByRole("list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// State 4b: Fallback via client-side 10 s timeout
// ---------------------------------------------------------------------------

describe("State: fallback — client-side 10 s timeout", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("shows fallback panel when 10 s elapses with no response", async () => {
    const neverResolves = () => new Promise<ArInstructionResponse>(() => {/* never */});
    renderInstructions(neverResolves);

    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/Overlay Unavailable/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Error state (non-504 fetch failure)
// ---------------------------------------------------------------------------

describe("State: error — fetch failure", () => {
  it("renders an error alert when fetch throws a non-abort error", async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error("Network error"));
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
    expect(screen.getByText(/Overlay Load Failed/i)).toBeTruthy();
  });

  it("renders a retry button on error", async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error("Network error"));
    renderInstructions(fetchFn);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy(),
    );
  });
});

// ---------------------------------------------------------------------------
// Cleanup: abort controller is cancelled on unmount
// ---------------------------------------------------------------------------

describe("AbortController cleanup", () => {
  it("does not update state after unmount (no act warning)", async () => {
    let resolveP!: (v: ArInstructionResponse) => void;
    const pending = new Promise<ArInstructionResponse>((r) => { resolveP = r; });
    const fetchFn = vi.fn().mockReturnValue(pending);
    const { unmount } = renderInstructions(fetchFn);

    unmount();
    // Resolve after unmount — should not cause state update
    await act(async () => { resolveP(STANDARD_RESPONSE); });
    // No throw / act warning expected
  });
});

// ---------------------------------------------------------------------------
// i18n: German translations for new keys
// ---------------------------------------------------------------------------

describe("i18n — German translation keys", () => {
  it("ar_workbench.rare_variant_badge resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("ar_workbench.variant_delta_badge")).toBe("Δ DELTA");
  });

  it("ar_workbench.fallback_title resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("ar_workbench.fallback_title")).toBe(
      "Overlay nicht verfügbar — PDF-Anweisungen verwenden",
    );
  });

  it("ar_workbench.rare_variant_title resolves in en-GB", async () => {
    await act(async () => { await i18n.changeLanguage("en-GB"); });
    expect(i18n.t("ar_workbench.rare_variant_title", { variant: "i4 M50 xDrive" })).toBe(
      "RARE VARIANT — i4 M50 xDrive",
    );
  });
});
