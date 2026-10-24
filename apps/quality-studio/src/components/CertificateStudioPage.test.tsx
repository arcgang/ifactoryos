import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import {
  TamperEvidencePanel,
  ExportShiftSummaryButton,
  CertificateStudioPage,
} from "./CertificateStudioPage";
import * as api from "../services/api";
import * as adoption from "../services/adoption";
import type { CertificateRecord, CertificateVerifyResponse } from "../services/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCertificateRecord(
  overrides: Partial<CertificateRecord> = {},
): CertificateRecord {
  return {
    certificateId: "cert-abc-001",
    vin: "WBY8P4107PCE12345",
    gateResultSnapshot: {
      gateId: "GATE-3",
      stationResults: [
        { stationCode: "DIM", result: "PASS" },
        { stationCode: "LEAK", result: "PASS" },
        { stationCode: "ELEC", result: "PASS" },
        { stationCode: "NVH", result: "CONDITIONAL" },
        { stationCode: "ADAS", result: "PASS" },
      ],
    },
    healthScore: 94.7,
    validatorId: "validator-001",
    issuedAt: "2026-10-13T07:41:00Z",
    anchoredAt: "2026-10-13T07:41:45Z",
    certificateHash: "a".repeat(64),
    fabricTransactionId: "tx-fabric-001",
    fabricBlockNumber: 42,
    sourceSystem: "quality-studio",
    shiftId: "shift-shift-001",
    ...overrides,
  };
}

function makeVerifyResponse(
  overrides: Partial<CertificateVerifyResponse> = {},
): CertificateVerifyResponse {
  return {
    certificateId: "cert-abc-001",
    verified: true,
    storedHash: "a".repeat(64),
    fabricTransactionId: "tx-fabric-001",
    fabricHash: "a".repeat(64),
    verifiedAt: "2026-10-13T09:00:00Z",
    ...overrides,
  };
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  vi.spyOn(adoption, "emitAdoptionEvent").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// TamperEvidencePanel — verified
// ---------------------------------------------------------------------------

describe("TamperEvidencePanel — verified", () => {
  it("shows loading state initially", () => {
    vi.spyOn(api, "verifyCertificate").mockReturnValue(new Promise(() => undefined));

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    expect(screen.getByTestId("verify-panel-loading")).toBeTruthy();
  });

  it("shows VERIFIED badge when verified=true", async () => {
    vi.spyOn(api, "verifyCertificate").mockResolvedValue(makeVerifyResponse({ verified: true }));

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("verify-panel-ok")).toBeTruthy(),
    );

    const panel = screen.getByTestId("verify-panel-ok");
    expect(panel.textContent).toContain("VERIFIED");
    expect(panel.textContent).toContain("✓");
    expect(screen.getByTestId("verify-stored-hash").textContent).toBe("a".repeat(64));
    expect(screen.getByTestId("verify-fabric-hash").textContent).toBe("a".repeat(64));
  });

  it("VERIFIED badge uses aria-label not only colour", async () => {
    vi.spyOn(api, "verifyCertificate").mockResolvedValue(makeVerifyResponse({ verified: true }));

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("verify-panel-ok")).toBeTruthy());

    const badge = screen
      .getByTestId("verify-panel-ok")
      .querySelector("[aria-label]");
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("aria-label")).toContain("tamper-evident");
  });
});

// ---------------------------------------------------------------------------
// TamperEvidencePanel — tampered
// ---------------------------------------------------------------------------

describe("TamperEvidencePanel — tampered", () => {
  it("shows TAMPER DETECTED when verified=false", async () => {
    vi.spyOn(api, "verifyCertificate").mockResolvedValue(
      makeVerifyResponse({
        verified: false,
        mismatchReason: "Hash mismatch: stored differs from ledger",
      }),
    );

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("verify-panel-tampered")).toBeTruthy(),
    );

    const panel = screen.getByTestId("verify-panel-tampered");
    expect(panel.textContent).toContain("TAMPER");
    expect(panel.textContent).toContain("⚠");
    expect(screen.getByTestId("verify-mismatch-reason").textContent).toContain(
      "Hash mismatch",
    );
  });

  it("TAMPER badge uses aria-label not only colour", async () => {
    vi.spyOn(api, "verifyCertificate").mockResolvedValue(
      makeVerifyResponse({ verified: false }),
    );

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("verify-panel-tampered")).toBeTruthy());

    const badge = screen
      .getByTestId("verify-panel-tampered")
      .querySelector("[aria-label]");
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("aria-label")).toContain("potential tampering");
  });
});

// ---------------------------------------------------------------------------
// TamperEvidencePanel — error
// ---------------------------------------------------------------------------

describe("TamperEvidencePanel — error", () => {
  it("shows error state when verifyCertificate throws", async () => {
    vi.spyOn(api, "verifyCertificate").mockRejectedValue(
      Object.assign(new Error("Fabric unavailable"), { status: 502 }),
    );

    render(
      <Wrapper>
        <TamperEvidencePanel certificateId="cert-abc-001" />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("verify-panel-error")).toBeTruthy(),
    );

    expect(screen.getByTestId("verify-panel-error").textContent).toContain(
      "Fabric unavailable",
    );
  });
});

// ---------------------------------------------------------------------------
// ExportShiftSummaryButton — empty state
// ---------------------------------------------------------------------------

describe("ExportShiftSummaryButton — empty state", () => {
  it("shows empty state message when no certificates in shift", async () => {
    vi.spyOn(api, "listCertificates").mockResolvedValue({
      data: [],
      page: { next_cursor: null, limit: 200 },
    });

    const user = userEvent.setup();

    render(
      <Wrapper>
        <ExportShiftSummaryButton shiftId="SH-2026-10-12-A" />
      </Wrapper>,
    );

    await user.click(screen.getByTestId("export-shift-button"));

    await waitFor(() =>
      expect(screen.getByTestId("export-empty-state")).toBeTruthy(),
    );

    expect(screen.getByTestId("export-empty-state").textContent).toContain(
      "No certificates issued",
    );
  });
});

// ---------------------------------------------------------------------------
// ExportShiftSummaryButton — accessible aria-label
// ---------------------------------------------------------------------------

describe("ExportShiftSummaryButton — accessibility", () => {
  it("export button has descriptive aria-label", () => {
    render(
      <Wrapper>
        <ExportShiftSummaryButton shiftId="SH-2026-10-12-A" />
      </Wrapper>,
    );

    const btn = screen.getByTestId("export-shift-button");
    expect(btn.getAttribute("aria-label")).toContain("SH-2026-10-12-A");
  });
});

// ---------------------------------------------------------------------------
// CertificateStudioPage — full page states
// ---------------------------------------------------------------------------

describe("CertificateStudioPage — loaded state", () => {
  it("renders certificate record when getCertificate succeeds", async () => {
    vi.spyOn(api, "getCertificate").mockResolvedValue(makeCertificateRecord());
    vi.spyOn(api, "verifyCertificate").mockReturnValue(new Promise(() => undefined));

    render(
      <Wrapper>
        <CertificateStudioPage
          certificateId="cert-abc-001"
          onBack={() => undefined}
        />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("cert-studio-page")).toBeTruthy(),
    );

    expect(screen.getByTestId("cs-vin").textContent).toBe("WBY8P4107PCE12345");
    expect(screen.getByTestId("cs-cert-id").textContent).toBe("cert-abc-001");
    expect(screen.getByTestId("cs-fabric-tx").textContent).toBe("tx-fabric-001");
  });

  it("shows loading state initially", () => {
    vi.spyOn(api, "getCertificate").mockReturnValue(new Promise(() => undefined));

    render(
      <Wrapper>
        <CertificateStudioPage
          certificateId="cert-abc-001"
          onBack={() => undefined}
        />
      </Wrapper>,
    );

    expect(screen.getByTestId("cert-studio-loading")).toBeTruthy();
  });

  it("shows not-found state when getCertificate returns 404", async () => {
    vi.spyOn(api, "getCertificate").mockRejectedValue(
      Object.assign(new Error("Not found"), { status: 404 }),
    );

    render(
      <Wrapper>
        <CertificateStudioPage
          certificateId="cert-abc-001"
          onBack={() => undefined}
        />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("cert-studio-not-found")).toBeTruthy(),
    );
  });

  it("shows error state when getCertificate throws non-404", async () => {
    vi.spyOn(api, "getCertificate").mockRejectedValue(
      Object.assign(new Error("Fabric query failed"), { status: 502 }),
    );

    render(
      <Wrapper>
        <CertificateStudioPage
          certificateId="cert-abc-001"
          onBack={() => undefined}
        />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("cert-studio-error")).toBeTruthy(),
    );

    expect(screen.getByTestId("cert-studio-error").textContent).toContain(
      "Fabric query failed",
    );
  });
});

// ---------------------------------------------------------------------------
// CertificateStudioPage — adoption event
// ---------------------------------------------------------------------------

describe("CertificateStudioPage — adoption events", () => {
  it("emits CERTIFICATE_STUDIO_OPEN on mount", async () => {
    vi.spyOn(api, "getCertificate").mockReturnValue(new Promise(() => undefined));
    const emitSpy = vi.spyOn(adoption, "emitAdoptionEvent").mockResolvedValue(undefined);

    render(
      <Wrapper>
        <CertificateStudioPage
          certificateId="cert-abc-001"
          onBack={() => undefined}
        />
      </Wrapper>,
    );

    await waitFor(() =>
      expect(emitSpy).toHaveBeenCalledWith(
        expect.objectContaining({ action: "CERTIFICATE_STUDIO_OPEN" }),
      ),
    );
  });
});
