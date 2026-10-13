export type My26ConfigStatus = "draft" | "validated" | "active" | "superseded";

export interface OptionCodeEntry {
  code: string;
  description: string;
  constraintMappings?: string[] | null;
}

export interface PaintPaletteEntry {
  paintCode: string;
  batchGroup: string;
  changeoverPenaltyMinutes?: number | null;
}

export interface My26Config {
  configId: string;
  version: string;
  status: My26ConfigStatus;
  optionCodes: OptionCodeEntry[];
  paintPalette: PaintPaletteEntry[];
  kittingRules?: Record<string, unknown>[] | null;
  createdAt: string;
  createdBy: string;
  activatedAt?: string | null;
}

export interface My26ConfigDraft {
  optionCodes: OptionCodeEntry[];
  paintPalette: PaintPaletteEntry[];
  kittingRules?: Record<string, unknown>[] | null;
}

export type My26ValidationSeverity = "error" | "warning";

export interface My26ValidationIssue {
  severity: My26ValidationSeverity;
  field: string;
  message: string;
}

export interface My26ValidationResult {
  valid: boolean;
  issues: My26ValidationIssue[];
}
