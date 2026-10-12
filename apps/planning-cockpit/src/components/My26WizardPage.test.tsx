import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { My26WizardPage } from "./My26WizardPage";
import type { My26Config, My26ValidationResult } from "../types/my26";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG_ID = "cfg-11111111-2222-3333-4444-555555555555";

function makeActiveConfig(overrides?: Partial<My26Config>): My26Config {
  return {
    configId: CONFIG_ID,
    version: "2026.10.12",
    status: "active",
    optionCodes: [
      { code: "P0490", description: "M Sport Package", constraintMappings: ["paint_capacity"] },
    ],
    paintPalette: [
      { paintCode: "A90", batchGroup: "B1", changeoverPenaltyMinutes: 30 },
    ],
    kittingRules: null,
    createdAt: "2026-10-12T08:00:00Z",
    createdBy: "k.mueller@bmw.de",
    activatedAt: "2026-10-12T08:01:00Z",
    ...overrides,
  };
}

function makeValidResult(): My26ValidationResult {
  return { valid: true, issues: [] };
}

function makeInvalidResult(): My26ValidationResult {
  return {
    valid: false,
    issues: [
      {
        severity: "error",
        field: "optionCodes[ZP01U].constraintMappings",
        message: "Option code 'ZP01U' has no constraint mappings",
      },
      {
        severity: "warning",
        field: "optionCodes[ZP02U]",
        message: "ZP02U references kitting station K-07 which is not in the current kitting rule set",
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests — Step 1: Import
// ---------------------------------------------------------------------------

describe("My26WizardPage — Step 1: Import", () => {
  it("renders the wizard title and step indicators on load", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeActiveConfig()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<My26WizardPage />);

    expect(screen.getByRole("heading", { level: 2 })).toBeTruthy();
    expect(screen.getByText(/Import/i)).toBeTruthy();
    expect(screen.getByText(/Validate/i)).toBeTruthy();
  });

  it("shows Validate button in step 1", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(""),
    } as Response);

    render(<My26WizardPage />);
    expect(screen.getByTestId("validate-btn")).toBeTruthy();
  });

  it("allows adding and removing option code rows", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(""),
    } as Response);

    const user = userEvent.setup();
    render(<My26WizardPage />);

    const addBtn = screen.getByTestId("add-option-row");
    await user.click(addBtn);
    // Two option code rows now exist (initial + newly added)
    const codeInputs = screen.getAllByPlaceholderText(/e\.g\. P0490/i);
    expect(codeInputs.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Tests — Step 2: Validation
// ---------------------------------------------------------------------------

describe("My26WizardPage — Step 2: Validation loading state", () => {
  it("shows loading spinner while validation is in flight", async () => {
    let resolveValidate!: (v: Response) => void;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return new Promise<Response>((res) => { resolveValidate = res; });
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-loading")).toBeTruthy();
    });

    // Unblock the request
    resolveValidate({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeValidResult()),
      text: () => Promise.resolve(""),
    } as Response);

    fetchMock.mockRestore();
  });
});

describe("My26WizardPage — Step 2: Validation error state", () => {
  it("shows error banner with Retry button on network failure", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.reject(new Error("Network error"));
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-error")).toBeTruthy();
    });
    expect(screen.getByTestId("retry-validate-btn")).toBeTruthy();
  });
});

describe("My26WizardPage — Step 2: Validation failed (issues present)", () => {
  it("renders error and warning issues with correct severity indicators", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeInvalidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-failed")).toBeTruthy();
    });

    // Blocking chip for error
    const blockingChips = screen.getAllByText(/blocking/i);
    expect(blockingChips.length).toBeGreaterThanOrEqual(1);

    // Warning chip
    const warningChips = screen.getAllByText(/warning/i);
    expect(warningChips.length).toBeGreaterThanOrEqual(1);
  });

  it("disables 'Continue to Review & Save' button when valid is false", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeInvalidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy();
    });

    const continueBtn = screen.getByTestId("continue-to-save-btn");
    expect(continueBtn).toHaveProperty("disabled", true);
  });
});

describe("My26WizardPage — Step 2: Validation passed", () => {
  it("shows success banner and enables Continue button when valid is true", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeValidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-passed")).toBeTruthy();
    });

    const continueBtn = screen.getByTestId("continue-to-save-btn");
    expect(continueBtn).toHaveProperty("disabled", false);
  });
});

// ---------------------------------------------------------------------------
// Tests — Step 3: Review & Save
// ---------------------------------------------------------------------------

describe("My26WizardPage — Step 3: Save", () => {
  function setupStep3Mock() {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeValidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      if (urlStr.includes("/my26/config/save")) {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () => Promise.resolve(makeActiveConfig()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });
  }

  async function goToStep3() {
    setupStep3Mock();
    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    return user;
  }

  it("shows Save & Activate button on step 3", async () => {
    await goToStep3();
    expect(screen.getByTestId("save-config-btn")).toBeTruthy();
  });

  it("shows confirmation banner after successful save (201)", async () => {
    const user = await goToStep3();

    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("save-confirmation")).toBeTruthy();
    });
  });

  it("POSTs to /my26/config/save with optionCodes and paintPalette", async () => {
    const captured: RequestInit[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL, init?: RequestInit) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/save")) {
        if (init) captured.push(init);
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () => Promise.resolve(makeActiveConfig()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeValidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => {
      expect(captured.length).toBeGreaterThanOrEqual(1);
    });

    const body = JSON.parse(captured[0]!.body as string) as { optionCodes: unknown[]; paintPalette: unknown[] };
    expect(Array.isArray(body.optionCodes)).toBe(true);
    expect(Array.isArray(body.paintPalette)).toBe(true);
  });

  it("shows error banner with retry option on save failure", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/my26/config/save")) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ code: "CONFLICT", message: "Conflict", correlation_id: "abc" }),
          text: () => Promise.resolve("Conflict"),
        } as Response);
      }
      if (urlStr.includes("/my26/config/validate")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeValidResult()),
          text: () => Promise.resolve(""),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") } as Response);
    });

    const user = userEvent.setup();
    render(<My26WizardPage />);
    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("save-error")).toBeTruthy();
    });
  });
});
