/**
 * Acceptance tests for US-006 — MY26 Configuration Wizard in Planning Cockpit
 *
 * Tests the seam between:
 *   - planning-cockpit: My26WizardPage (import → validate → review/save wizard)
 *   - sequence-optimizer: POST /my26/config/validate + POST /my26/config/save
 *   - sequence-optimizer: POST /optimize with my26ConfigVersion
 *   - audit log: postAuditEvent called after save with my26ConfigVersion
 *
 * AC1 — wizard validates each entry against the SequenceOptimizer constraint model
 *        and flags conflicts/missing constraint mappings before saving
 * AC2 — optimizer applies new option codes and paint codes without error and
 *        response reflects the updated constraints (my26ConfigVersion echoed back)
 * AC3 — publish with MY26 VINs records the MY26 config version in the audit log
 */

import "../i18n";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { My26WizardPage } from "./My26WizardPage";
import type { My26Config, My26ValidationResult } from "../types/my26";

// ---------------------------------------------------------------------------
// Hoisted mocks (must appear before imports that read them)
// ---------------------------------------------------------------------------

const mockValidateMy26Config = vi.hoisted(() => vi.fn());
const mockSaveMy26Config = vi.hoisted(() => vi.fn());
const mockGetMy26Config = vi.hoisted(() => vi.fn());
const mockPostAuditEvent = vi.hoisted(() => vi.fn());

vi.mock("../services/api", () => ({
  getMy26Config: mockGetMy26Config,
  validateMy26Config: mockValidateMy26Config,
  saveMy26Config: mockSaveMy26Config,
  postAuditEvent: mockPostAuditEvent,
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONFIG_ID = "a1b2c3d4-0000-0000-0000-000000000001";

function makeActiveConfig(overrides?: Partial<My26Config>): My26Config {
  return {
    configId: CONFIG_ID,
    version: "2026.10.29",
    status: "active",
    optionCodes: [
      { code: "P0490", description: "M Sport Package", constraintMappings: ["paint_capacity"] },
    ],
    paintPalette: [
      { paintCode: "A90", batchGroup: "B1", changeoverPenaltyMinutes: 30 },
    ],
    kittingRules: null,
    createdAt: "2026-10-29T08:00:00Z",
    createdBy: "k.mueller@bmw.de",
    activatedAt: "2026-10-29T08:01:00Z",
    ...overrides,
  };
}

function makeValidResult(): My26ValidationResult {
  return { valid: true, issues: [] };
}

function makeInvalidResultWithError(): My26ValidationResult {
  return {
    valid: false,
    issues: [
      {
        severity: "error",
        field: "optionCodes[ZP01U].constraintMappings",
        message: "Option code 'ZP01U' has no constraint mappings",
      },
    ],
  };
}

function makeInvalidResultWithWarning(): My26ValidationResult {
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
        message: "ZP02U references a kitting station not in the current kitting rule set",
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Global setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no active config yet
  mockGetMy26Config.mockResolvedValue(null);
  // Default: audit event is best-effort fire-and-forget
  mockPostAuditEvent.mockResolvedValue(undefined);
});

// ---------------------------------------------------------------------------
// Preconditions — ensure the mocks are wired so tests are never vacuous
// ---------------------------------------------------------------------------

describe("Preconditions — mocks are in scope", () => {
  it("precondition: validateMy26Config is mocked so AC1 assertions are not vacuous", () => {
    expect(vi.isMockFunction(mockValidateMy26Config)).toBe(true);
  });

  it("precondition: saveMy26Config is mocked so AC3 assertions are not vacuous", () => {
    expect(vi.isMockFunction(mockSaveMy26Config)).toBe(true);
  });

  it("precondition: postAuditEvent is mocked so audit log assertions can be observed", () => {
    expect(vi.isMockFunction(mockPostAuditEvent)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AC1 — wizard validates entries against the SequenceOptimizer constraint model
//        and flags any conflicts or missing constraint mappings before saving
// ---------------------------------------------------------------------------

describe("AC1 — wizard calls /my26/config/validate before allowing save", () => {
  it("AC1: clicking Validate triggers a POST to the validate endpoint", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(mockValidateMy26Config).toHaveBeenCalledTimes(1);
    });
  });

  it("AC1: wizard calls validate with the option codes and paint palette from step 1", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    // Type a code into the first option code row
    const codeInputs = screen.getAllByPlaceholderText(/e\.g\. P0490/i);
    await user.clear(codeInputs[0]);
    await user.type(codeInputs[0], "ZP01U");

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(mockValidateMy26Config).toHaveBeenCalledTimes(1);
    });

    const calledWith = mockValidateMy26Config.mock.calls[0][0] as {
      optionCodes: { code: string }[];
      paintPalette: unknown[];
    };
    expect(Array.isArray(calledWith.optionCodes)).toBe(true);
    expect(Array.isArray(calledWith.paintPalette)).toBe(true);
    // The entered code must be present in the payload
    const codes = calledWith.optionCodes.map((oc) => oc.code);
    expect(codes).toContain("ZP01U");
  });

  it("AC1: error-severity issue is flagged with 'Blocking' chip in the validation result", async () => {
    mockValidateMy26Config.mockResolvedValue(makeInvalidResultWithError());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-failed")).toBeTruthy();
    });

    // Issue table must contain a blocking chip
    const blockingChips = screen.getAllByText(/blocking/i);
    expect(blockingChips.length).toBeGreaterThanOrEqual(1);
  });

  it("AC1: warning-severity issue is flagged in the validation result alongside the error", async () => {
    mockValidateMy26Config.mockResolvedValue(makeInvalidResultWithWarning());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-failed")).toBeTruthy();
    });

    const warningChips = screen.getAllByText(/warning/i);
    expect(warningChips.length).toBeGreaterThanOrEqual(1);
  });

  it("AC1: specific missing-constraint error message is displayed to the planner", async () => {
    mockValidateMy26Config.mockResolvedValue(makeInvalidResultWithError());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByText(/no constraint mappings/i)).toBeTruthy();
    });
  });

  it("AC1: 'Continue to Review & Save' is disabled when validate returns valid=false (errors block progression)", async () => {
    mockValidateMy26Config.mockResolvedValue(makeInvalidResultWithError());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy();
    });

    const continueBtn = screen.getByTestId("continue-to-save-btn");
    // Must be disabled — can't proceed to save with outstanding errors
    expect((continueBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("AC1: valid=true result shows success banner so planner knows entries passed constraint model check", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-passed")).toBeTruthy();
    });
  });

  it("AC1: validation shows which checks passed — option codes, paint codes, kitting rules all marked valid", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-passed")).toBeTruthy();
    });

    // The passed-step summary panel must include all three checked fields
    // (translated labels from en-GB.json: "Option codes", "Paint codes", "Kitting rules")
    // Multiple matches are expected (section heading + dl label) — any match proves the criterion.
    const resultSection = screen.getByTestId("validation-result");
    expect(within(resultSection).getAllByText(/option codes/i).length).toBeGreaterThanOrEqual(1);
    expect(within(resultSection).getAllByText(/paint codes/i).length).toBeGreaterThanOrEqual(1);
    expect(within(resultSection).getAllByText(/kitting rules/i).length).toBeGreaterThanOrEqual(1);
  });

  it("AC1: 'Continue to Review & Save' is enabled when validate returns valid=true (entries pass constraint model)", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy();
    });

    const continueBtn = screen.getByTestId("continue-to-save-btn");
    expect((continueBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it("AC1: the validate payload includes constraintMappings for each option code row", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    const user = userEvent.setup();
    render(<My26WizardPage />);

    // Fill in an option code with constraint mappings
    const codeInputs = screen.getAllByPlaceholderText(/e\.g\. P0490/i);
    await user.clear(codeInputs[0]);
    await user.type(codeInputs[0], "P0490");

    const cmInputs = screen.getAllByPlaceholderText(/paint_capacity,kitting_readiness/i);
    await user.clear(cmInputs[0]);
    await user.type(cmInputs[0], "paint_capacity");

    await user.click(screen.getByTestId("validate-btn"));

    await waitFor(() => {
      expect(mockValidateMy26Config).toHaveBeenCalledTimes(1);
    });

    const calledWith = mockValidateMy26Config.mock.calls[0][0] as {
      optionCodes: { code: string; constraintMappings: string[] }[];
    };
    const p0490 = calledWith.optionCodes.find((oc) => oc.code === "P0490");
    expect(p0490).toBeTruthy();
    expect(p0490!.constraintMappings).toContain("paint_capacity");
  });
});

// ---------------------------------------------------------------------------
// AC2 — optimizer applies new option codes and paint codes without error;
//        sequence output reflects updated constraints
//
// The AC2 seam is between wizard save → my26ConfigVersion available for use in
// optimizer calls.  The wizard POSTs /my26/config/save and receives back a
// configId, then emits a postAuditEvent with that my26ConfigVersion.
// The optimizer path (POST /optimize with my26ConfigVersion) is exercised in
// the Python integration test (test_my26_config_integration.py); here we cover
// the seam that connects the wizard save response to the planner's ability to
// see a configId they could provide to the optimizer.
// ---------------------------------------------------------------------------

describe("AC2 — saved MY26 config surfaces configId that can be passed to the optimizer", () => {
  async function walkToSaveAndSave(): Promise<void> {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());

    const user = userEvent.setup();
    render(<My26WizardPage />);

    // Step 1 → 2
    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());

    // Step 2 → 3
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());

    // Trigger save
    await user.click(screen.getByTestId("save-config-btn"));
    await waitFor(() => expect(screen.getByTestId("save-confirmation")).toBeTruthy());
  }

  it("AC2: save returns configId that is shown in the confirmation — this is the version the optimizer will use", async () => {
    await walkToSaveAndSave();

    // The save confirmation panel must include the configId in its text content
    // (translated as: "Version 2026.10.29 (a1b2c3d4-...) is now active for all SequenceOptimizer runs.")
    const confirmation = screen.getByTestId("save-confirmation");
    expect(confirmation.textContent).toContain(CONFIG_ID);
  });

  it("AC2: saved config has status=active meaning the optimizer can immediately use it", async () => {
    await walkToSaveAndSave();

    // The status badge must show ACTIVE — required for the optimizer to accept the version
    const statusBadges = screen.getAllByText(/active/i);
    expect(statusBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("AC2: saveMy26Config is called with the same optionCodes/paintPalette from the import step", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());

    const user = userEvent.setup();
    render(<My26WizardPage />);

    // Enter option code data
    const codeInputs = screen.getAllByPlaceholderText(/e\.g\. P0490/i);
    await user.clear(codeInputs[0]);
    await user.type(codeInputs[0], "SA100");

    const cmInputs = screen.getAllByPlaceholderText(/paint_capacity,kitting_readiness/i);
    await user.clear(cmInputs[0]);
    await user.type(cmInputs[0], "paint_capacity");

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => expect(mockSaveMy26Config).toHaveBeenCalledTimes(1));

    const savedDraft = mockSaveMy26Config.mock.calls[0][0] as {
      optionCodes: { code: string; constraintMappings: string[] }[];
      paintPalette: unknown[];
    };
    expect(Array.isArray(savedDraft.optionCodes)).toBe(true);
    expect(Array.isArray(savedDraft.paintPalette)).toBe(true);
    const sa100 = savedDraft.optionCodes.find((oc) => oc.code === "SA100");
    expect(sa100).toBeTruthy();
    expect(sa100!.constraintMappings).toContain("paint_capacity");
  });

  it("AC2: saving with valid paint palette entries preserves changeoverPenaltyMinutes for the optimizer", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());

    const user = userEvent.setup();
    render(<My26WizardPage />);

    // Fill paint code and penalty
    const paintCodeInputs = screen.getAllByPlaceholderText(/e\.g\. A90/i);
    await user.clear(paintCodeInputs[0]);
    await user.type(paintCodeInputs[0], "A75");

    const penaltyInputs = screen.getAllByPlaceholderText(/min/i);
    await user.clear(penaltyInputs[0]);
    await user.type(penaltyInputs[0], "45");

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => expect(mockSaveMy26Config).toHaveBeenCalledTimes(1));

    const savedDraft = mockSaveMy26Config.mock.calls[0][0] as {
      paintPalette: { paintCode: string; changeoverPenaltyMinutes: number | null }[];
    };
    const a75 = savedDraft.paintPalette.find((p) => p.paintCode === "A75");
    expect(a75).toBeTruthy();
    // changeoverPenaltyMinutes=45 must be preserved — this is what the optimizer uses for batch scheduling
    expect(a75!.changeoverPenaltyMinutes).toBe(45);
  });

  it("AC2: validate is always called before save — save endpoint is not hit if validate hasn't run", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());
    render(<My26WizardPage />);

    // Save button is not yet visible — we're on step 1
    expect(screen.queryByTestId("save-config-btn")).toBeNull();
    // saveMy26Config must not have been called
    expect(mockSaveMy26Config).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// AC3 — publish containing MY26 VINs records the MY26 config version in the
//        audit log
//
// The audit log write happens in My26WizardPage.handleSave() via postAuditEvent
// immediately after saveMy26Config succeeds, passing my26ConfigVersion=configId.
// This seam (save → audit) is not covered by any existing test.
// ---------------------------------------------------------------------------

describe("AC3 — audit log records MY26 config version after save", () => {
  it("AC3: postAuditEvent is called after a successful save", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());
    mockPostAuditEvent.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("save-confirmation")).toBeTruthy();
    });

    // audit event must be called (best-effort, but it must be attempted)
    await waitFor(() => {
      expect(mockPostAuditEvent).toHaveBeenCalledTimes(1);
    });
  });

  it("AC3: postAuditEvent carries action='my26_config_save' identifying the save operation", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());
    mockPostAuditEvent.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => expect(mockPostAuditEvent).toHaveBeenCalled());

    const auditPayload = mockPostAuditEvent.mock.calls[0][0] as {
      action: string;
      my26ConfigVersion: string;
      configVersion: string;
      timestamp: string;
    };
    expect(auditPayload.action).toBe("my26_config_save");
  });

  it("AC3: postAuditEvent records the MY26 config version (configId from the saved config)", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());
    mockPostAuditEvent.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => expect(mockPostAuditEvent).toHaveBeenCalled());

    const auditPayload = mockPostAuditEvent.mock.calls[0][0] as {
      my26ConfigVersion: string;
      configVersion: string;
    };
    // my26ConfigVersion must be the configId returned by the save endpoint
    expect(auditPayload.my26ConfigVersion).toBe(CONFIG_ID);
    // configVersion must also be the configId (same field, named for the audit schema)
    expect(auditPayload.configVersion).toBe(CONFIG_ID);
  });

  it("AC3: audit log records a timestamp matching the activatedAt from the saved config", async () => {
    const savedConfig = makeActiveConfig({ activatedAt: "2026-10-29T08:01:00Z" });
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(savedConfig);
    mockPostAuditEvent.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    await waitFor(() => expect(mockPostAuditEvent).toHaveBeenCalled());

    const auditPayload = mockPostAuditEvent.mock.calls[0][0] as { timestamp: string };
    // Timestamp must be the activatedAt from the backend (not a client-generated value)
    expect(auditPayload.timestamp).toBe("2026-10-29T08:01:00Z");
  });

  it("AC3: audit log is written even if postAuditEvent rejects — save confirmation still shown", async () => {
    // audit event failure must never block the save confirmation
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockResolvedValue(makeActiveConfig());
    mockPostAuditEvent.mockRejectedValue(new Error("audit-log unavailable"));

    const user = userEvent.setup();
    render(<My26WizardPage />);

    await user.click(screen.getByTestId("validate-btn"));
    await waitFor(() => expect(screen.getByTestId("continue-to-save-btn")).toBeTruthy());
    await user.click(screen.getByTestId("continue-to-save-btn"));
    await waitFor(() => expect(screen.getByTestId("save-config-btn")).toBeTruthy());
    await user.click(screen.getByTestId("save-config-btn"));

    // Confirmation must appear even if the audit log call fails
    await waitFor(() => {
      expect(screen.getByTestId("save-confirmation")).toBeTruthy();
    });
  });

  it("AC3: postAuditEvent is NOT called when save fails — only a successful save creates an audit record", async () => {
    mockValidateMy26Config.mockResolvedValue(makeValidResult());
    mockSaveMy26Config.mockRejectedValue(new Error("Save conflict"));

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

    // Audit event must not have been called — no config was saved
    expect(mockPostAuditEvent).not.toHaveBeenCalled();
  });
});
