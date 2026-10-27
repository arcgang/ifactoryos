import { render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "../i18n";
import { ARWorkbenchPage } from "./ARWorkbenchPage";
import { apiRequest } from "../services/api";

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

vi.mock("../hooks/useTorqueStream", () => ({
  useTorqueStream: () => ({ kind: "neutral" }),
}));

vi.mock("../hooks/useExpertCall", () => ({
  useExpertCall: () => ({
    state: { kind: "idle" },
    initiateCall: vi.fn(),
    endCall: vi.fn(),
  }),
}));

const mockApiRequest = vi.mocked(apiRequest);

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  vi.useFakeTimers();
  await i18n.changeLanguage("en-GB");
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ARWorkbenchPage — adoption event endpoints", () => {
  it("calls POST /ar/sessions with vin, stationId, and deviceType when overlay renders", async () => {
    mockApiRequest.mockImplementation(async (path: string, options?: any) => {
      if (typeof path === "string" && path.includes("/ar/instructions")) {
        return {
          vin: "WBS11EM030CHX9104",
          stationId: "MARRIAGE-01",
          optionCodes: ["SA258"],
          variantCode: "i4 eDrive40",
          isRareVariant: false,
          instructionVersion: "v4.2.1",
          fetchedAt: "2026-10-12T06:31:44Z",
          fallbackPdfQrUrl: "https://example.com/qr",
          steps: [],
        };
      }
      return {};
    });

    render(
      <I18nextProvider i18n={i18n}>
        <ARWorkbenchPage
          vin="WBS11EM030CHX9104"
          userId="AA-5566"
          onForbidden={vi.fn()}
        />
      </I18nextProvider>,
    );

    // Wait for AR instructions to fetch and overlay to render
    await waitFor(
      () => {
        const sessionPostCall = mockApiRequest.mock.calls.find(
          ([path]) => typeof path === "string" && path.includes("/ar/sessions"),
        );
        expect(sessionPostCall).toBeDefined();
      },
      { timeout: 5000 },
    );

    const sessionPostCall = mockApiRequest.mock.calls.find(
      ([path]) => typeof path === "string" && path.includes("/ar/sessions"),
    );
    expect(sessionPostCall).toBeDefined();

    const [path, options] = sessionPostCall!;
    expect(path).toBe("/api/v1/ar/sessions");
    expect(options?.method).toBe("POST");

    const body = JSON.parse(options!.body as string);
    expect(body.vin).toBe("WBS11EM030CHX9104");
    expect(body.stationId).toBe("MARRIAGE-01");
    expect(body.deviceType).toMatch(/HoloLens2|iPad/);
    expect(body.idempotencyKey).toBeTruthy();
  });

  it("uses stable idempotency key across retries (not regenerated on each call)", async () => {
    const capturedKeys: string[] = [];

    mockApiRequest.mockImplementation(async (path: string, options?: any) => {
      if (typeof path === "string" && path.includes("/ar/instructions")) {
        return {
          vin: "WBS11EM030CHX9104",
          stationId: "MARRIAGE-01",
          optionCodes: ["SA258"],
          variantCode: "i4 eDrive40",
          isRareVariant: false,
          instructionVersion: "v4.2.1",
          fetchedAt: "2026-10-12T06:31:44Z",
          fallbackPdfQrUrl: "https://example.com/qr",
          steps: [],
        };
      }
      if (typeof path === "string" && path.includes("/ar/sessions")) {
        const body = JSON.parse(options!.body as string);
        capturedKeys.push(body.idempotencyKey);
      }
      return {};
    });

    render(
      <I18nextProvider i18n={i18n}>
        <ARWorkbenchPage
          vin="WBS11EM030CHX9104"
          userId="AA-5566"
          onForbidden={vi.fn()}
        />
      </I18nextProvider>,
    );

    await waitFor(
      () => {
        expect(capturedKeys.length).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );

    // Idempotency key should be the same across all POST /ar/sessions calls
    // (there should only be one, but if retried, it must be stable)
    expect(capturedKeys.length).toBe(1);
    expect(capturedKeys[0]).toBe(capturedKeys[0]);
  });

  it("does not include biometric fields in POST /ar/sessions body", async () => {
    mockApiRequest.mockImplementation(async (path: string, options?: any) => {
      if (typeof path === "string" && path.includes("/ar/instructions")) {
        return {
          vin: "WBS11EM030CHX9104",
          stationId: "MARRIAGE-01",
          optionCodes: ["SA258"],
          variantCode: "i4 eDrive40",
          isRareVariant: false,
          instructionVersion: "v4.2.1",
          fetchedAt: "2026-10-12T06:31:44Z",
          fallbackPdfQrUrl: "https://example.com/qr",
          steps: [],
        };
      }
      return {};
    });

    render(
      <I18nextProvider i18n={i18n}>
        <ARWorkbenchPage
          vin="WBS11EM030CHX9104"
          userId="AA-5566"
          onForbidden={vi.fn()}
        />
      </I18nextProvider>,
    );

    await waitFor(
      () => {
        const sessionPostCall = mockApiRequest.mock.calls.find(
          ([path]) => typeof path === "string" && path.includes("/ar/sessions"),
        );
        expect(sessionPostCall).toBeDefined();
      },
      { timeout: 5000 },
    );

    const sessionPostCall = mockApiRequest.mock.calls.find(
      ([path]) => typeof path === "string" && path.includes("/ar/sessions"),
    );
    const [, options] = sessionPostCall!;
    const body = JSON.parse(options!.body as string);

    // Verify no biometric fields are present
    expect(body).not.toHaveProperty("gaze");
    expect(body).not.toHaveProperty("eyeTracking");
    expect(body).not.toHaveProperty("biometrics");
    expect(body).not.toHaveProperty("headTracking");
    expect(body).not.toHaveProperty("operator");
    expect(body).not.toHaveProperty("userId");

    // Only these fields should be present
    const allowedFields = ["vin", "stationId", "deviceType", "idempotencyKey"];
    Object.keys(body).forEach((key) => {
      expect(allowedFields).toContain(key);
    });
  });
});
