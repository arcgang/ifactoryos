import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  getMy26Config,
  validateMy26Config,
  saveMy26Config,
  postAuditEvent,
} from "../services/api";
import type { My26Config, My26ConfigDraft, My26ValidationIssue, My26ValidationResult, OptionCodeEntry, PaintPaletteEntry } from "../types/my26";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = 1 | 2 | 3;

type ValidationState =
  | { phase: "idle" }
  | { phase: "pending" }
  | { phase: "done"; result: My26ValidationResult }
  | { phase: "error"; message: string };

type SaveState =
  | { phase: "idle" }
  | { phase: "pending" }
  | { phase: "done"; config: My26Config }
  | { phase: "error"; message: string };

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StepIndicator({
  step,
  currentStep,
  label,
}: {
  step: WizardStep;
  currentStep: WizardStep;
  label: string;
}) {
  const done = step < currentStep;
  const active = step === currentStep;
  const cls = done ? "wiz-step wiz-step--done" : active ? "wiz-step wiz-step--active" : "wiz-step";
  return (
    <div className={cls} aria-current={active ? "step" : undefined}>
      <span className="wiz-step-num" aria-hidden="true">
        {done ? "✓" : step}
      </span>
      <span>{label}</span>
    </div>
  );
}

function ConfigStatusBadge({ status }: { status: string }) {
  const cls =
    status === "active"
      ? "wiz-badge wiz-badge--active"
      : status === "validated"
        ? "wiz-badge wiz-badge--validated"
        : status === "superseded"
          ? "wiz-badge wiz-badge--superseded"
          : "wiz-badge wiz-badge--draft";
  return <span className={cls}>{status.toUpperCase()}</span>;
}

function IssueRow({ issue }: { issue: My26ValidationIssue }) {
  const isError = issue.severity === "error";
  return (
    <tr>
      <td>
        <span
          className={isError ? "wiz-chip wiz-chip--err" : "wiz-chip wiz-chip--warn"}
          aria-label={isError ? "Blocking error" : "Warning"}
        >
          <span className={isError ? "wiz-dot wiz-dot--err" : "wiz-dot wiz-dot--warn"} aria-hidden="true" />
          {isError ? "Blocking" : "Warning"}
        </span>
      </td>
      <td>
        <code className="wiz-code">{issue.field}</code>
      </td>
      <td>{issue.message}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Import
// ---------------------------------------------------------------------------

interface OptionCodeRow {
  id: string;
  code: string;
  description: string;
  constraintMappings: string;
}

interface PaintRow {
  id: string;
  paintCode: string;
  batchGroup: string;
  changeoverPenaltyMinutes: string;
}

function generateRowId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return Math.random().toString(36).slice(2);
}

function ImportStep({
  optionRows,
  paintRows,
  kittingJson,
  onOptionRowsChange,
  onPaintRowsChange,
  onKittingJsonChange,
  onNext,
}: {
  optionRows: OptionCodeRow[];
  paintRows: PaintRow[];
  kittingJson: string;
  onOptionRowsChange: (rows: OptionCodeRow[]) => void;
  onPaintRowsChange: (rows: PaintRow[]) => void;
  onKittingJsonChange: (v: string) => void;
  onNext: () => void;
}) {
  const { t } = useTranslation();

  const addOptionRow = useCallback(() => {
    onOptionRowsChange([
      ...optionRows,
      { id: generateRowId(), code: "", description: "", constraintMappings: "" },
    ]);
  }, [optionRows, onOptionRowsChange]);

  const updateOptionRow = useCallback(
    (id: string, field: keyof Omit<OptionCodeRow, "id">, value: string) => {
      onOptionRowsChange(
        optionRows.map((r) => (r.id === id ? { ...r, [field]: value } : r)),
      );
    },
    [optionRows, onOptionRowsChange],
  );

  const removeOptionRow = useCallback(
    (id: string) => {
      onOptionRowsChange(optionRows.filter((r) => r.id !== id));
    },
    [optionRows, onOptionRowsChange],
  );

  const addPaintRow = useCallback(() => {
    onPaintRowsChange([
      ...paintRows,
      { id: generateRowId(), paintCode: "", batchGroup: "", changeoverPenaltyMinutes: "" },
    ]);
  }, [paintRows, onPaintRowsChange]);

  const updatePaintRow = useCallback(
    (id: string, field: keyof Omit<PaintRow, "id">, value: string) => {
      onPaintRowsChange(
        paintRows.map((r) => (r.id === id ? { ...r, [field]: value } : r)),
      );
    },
    [paintRows, onPaintRowsChange],
  );

  const removePaintRow = useCallback(
    (id: string) => {
      onPaintRowsChange(paintRows.filter((r) => r.id !== id));
    },
    [paintRows, onPaintRowsChange],
  );

  return (
    <div className="wiz-body" role="form" aria-label={t("my26.importFormLabel")}>
      <section className="wiz-card" aria-labelledby="option-codes-heading">
        <div className="wiz-card-head">
          <h3 id="option-codes-heading">{t("my26.optionCodesLabel")}</h3>
          <code className="wiz-api-field">optionCodes*</code>
        </div>
        <div className="wiz-card-body">
          <table className="wiz-table" aria-label={t("my26.optionCodesTableLabel")}>
            <thead>
              <tr>
                <th scope="col">{t("my26.colCode")}</th>
                <th scope="col">{t("my26.colDescription")}</th>
                <th scope="col">{t("my26.colConstraintMappings")}</th>
                <th scope="col">
                  <span className="sr-only">{t("my26.colActions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {optionRows.map((row, idx) => (
                <tr key={row.id}>
                  <td>
                    <label htmlFor={`opt-code-${row.id}`} className="sr-only">
                      {t("my26.colCode")} {idx + 1}
                    </label>
                    <input
                      id={`opt-code-${row.id}`}
                      type="text"
                      className="wiz-input"
                      value={row.code}
                      onChange={(e) => updateOptionRow(row.id, "code", e.target.value)}
                      placeholder="e.g. P0490"
                      aria-label={`${t("my26.colCode")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <label htmlFor={`opt-desc-${row.id}`} className="sr-only">
                      {t("my26.colDescription")} {idx + 1}
                    </label>
                    <input
                      id={`opt-desc-${row.id}`}
                      type="text"
                      className="wiz-input"
                      value={row.description}
                      onChange={(e) => updateOptionRow(row.id, "description", e.target.value)}
                      placeholder="e.g. M Sport Package"
                      aria-label={`${t("my26.colDescription")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <label htmlFor={`opt-cm-${row.id}`} className="sr-only">
                      {t("my26.colConstraintMappings")} {idx + 1}
                    </label>
                    <input
                      id={`opt-cm-${row.id}`}
                      type="text"
                      className="wiz-input"
                      value={row.constraintMappings}
                      onChange={(e) =>
                        updateOptionRow(row.id, "constraintMappings", e.target.value)
                      }
                      placeholder="paint_capacity,kitting_readiness"
                      aria-label={`${t("my26.colConstraintMappings")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-secondary wiz-btn-sm"
                      onClick={() => removeOptionRow(row.id)}
                      aria-label={t("my26.removeRow", { index: idx + 1 })}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            className="btn btn-secondary wiz-add-row-btn"
            onClick={addOptionRow}
            data-testid="add-option-row"
          >
            + {t("my26.addOptionCode")}
          </button>
        </div>
      </section>

      <section className="wiz-card" aria-labelledby="paint-palette-heading">
        <div className="wiz-card-head">
          <h3 id="paint-palette-heading">{t("my26.paintPaletteLabel")}</h3>
          <code className="wiz-api-field">paintPalette*</code>
        </div>
        <div className="wiz-card-body">
          <table className="wiz-table" aria-label={t("my26.paintPaletteTableLabel")}>
            <thead>
              <tr>
                <th scope="col">{t("my26.colPaintCode")}</th>
                <th scope="col">{t("my26.colBatchGroup")}</th>
                <th scope="col">{t("my26.colChangeoverPenalty")}</th>
                <th scope="col">
                  <span className="sr-only">{t("my26.colActions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {paintRows.map((row, idx) => (
                <tr key={row.id}>
                  <td>
                    <label htmlFor={`paint-code-${row.id}`} className="sr-only">
                      {t("my26.colPaintCode")} {idx + 1}
                    </label>
                    <input
                      id={`paint-code-${row.id}`}
                      type="text"
                      className="wiz-input"
                      value={row.paintCode}
                      onChange={(e) => updatePaintRow(row.id, "paintCode", e.target.value)}
                      placeholder="e.g. A90"
                      aria-label={`${t("my26.colPaintCode")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <label htmlFor={`paint-bg-${row.id}`} className="sr-only">
                      {t("my26.colBatchGroup")} {idx + 1}
                    </label>
                    <input
                      id={`paint-bg-${row.id}`}
                      type="text"
                      className="wiz-input"
                      value={row.batchGroup}
                      onChange={(e) => updatePaintRow(row.id, "batchGroup", e.target.value)}
                      placeholder="e.g. B1"
                      aria-label={`${t("my26.colBatchGroup")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <label htmlFor={`paint-pen-${row.id}`} className="sr-only">
                      {t("my26.colChangeoverPenalty")} {idx + 1}
                    </label>
                    <input
                      id={`paint-pen-${row.id}`}
                      type="number"
                      className="wiz-input wiz-input--narrow"
                      value={row.changeoverPenaltyMinutes}
                      min={0}
                      onChange={(e) =>
                        updatePaintRow(row.id, "changeoverPenaltyMinutes", e.target.value)
                      }
                      placeholder="min"
                      aria-label={`${t("my26.colChangeoverPenalty")} ${idx + 1}`}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-secondary wiz-btn-sm"
                      onClick={() => removePaintRow(row.id)}
                      aria-label={t("my26.removeRow", { index: idx + 1 })}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            className="btn btn-secondary wiz-add-row-btn"
            onClick={addPaintRow}
            data-testid="add-paint-row"
          >
            + {t("my26.addPaintEntry")}
          </button>
        </div>
      </section>

      <section className="wiz-card" aria-labelledby="kitting-rules-heading">
        <div className="wiz-card-head">
          <h3 id="kitting-rules-heading">{t("my26.kittingRulesLabel")}</h3>
          <span className="wiz-optional-note">{t("my26.kittingRulesOptional")}</span>
        </div>
        <div className="wiz-card-body">
          <label htmlFor="kitting-rules-json" className="wiz-label">
            {t("my26.kittingRulesJsonLabel")}
            <code className="wiz-api-field"> kittingRules[]</code>
          </label>
          <textarea
            id="kitting-rules-json"
            className="wiz-textarea"
            value={kittingJson}
            onChange={(e) => onKittingJsonChange(e.target.value)}
            rows={4}
            placeholder='[{"ruleId":"KR-MY26-01","description":"M Sport (P0490) requires carbon brake option code CB01","optionCode":"P0490","requiredCode":"CB01"}]'
            aria-describedby="kitting-rules-hint"
          />
          <p id="kitting-rules-hint" className="wiz-field-hint">
            {t("my26.kittingRulesHint")}
          </p>
        </div>
      </section>

      <div className="wiz-btn-group">
        <span className="wiz-step-info">
          {t("my26.stepOf", { step: 1, total: 3 })} · POST /my26/config/validate
        </span>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onNext}
          data-testid="validate-btn"
        >
          {t("my26.validateButton")} →
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Validation results
// ---------------------------------------------------------------------------

function ValidationStep({
  validationState,
  onBack,
  onRetry,
  onNext,
}: {
  validationState: ValidationState;
  onBack: () => void;
  onRetry: () => void;
  onNext: () => void;
}) {
  const { t } = useTranslation();

  if (validationState.phase === "pending") {
    return (
      <div className="wiz-body" role="form" aria-label={t("my26.validateFormLabel")}>
        <section
          className="wiz-card"
          aria-labelledby="validating-heading"
          data-testid="validation-loading"
        >
          <div className="wiz-card-head">
            <h3 id="validating-heading">{t("my26.validatingTitle")}</h3>
          </div>
          <div className="wiz-card-body wiz-card-body--center">
            <div className="wiz-spinner-row" aria-live="polite" role="status">
              <div className="wiz-spinner" aria-hidden="true" />
              <span>{t("my26.validatingSubtitle")}</span>
            </div>
            <div
              className="wiz-progress-bar"
              role="progressbar"
              aria-label={t("my26.validationProgress")}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="wiz-progress-fill wiz-progress-fill--indeterminate" />
            </div>
          </div>
        </section>
      </div>
    );
  }

  if (validationState.phase === "error") {
    return (
      <div className="wiz-body" role="form" aria-label={t("my26.validateFormLabel")}>
        <div
          className="wiz-alert wiz-alert--err"
          role="alert"
          data-testid="validation-error"
        >
          <span className="wiz-alert-icon" aria-hidden="true">✕</span>
          <div>
            <strong>{t("my26.validationRequestFailed")}</strong>{" "}
            {validationState.message}
          </div>
        </div>
        <div className="wiz-btn-group">
          <button type="button" className="btn btn-secondary" onClick={onBack}>
            ← {t("my26.back")}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onRetry}
            data-testid="retry-validate-btn"
          >
            {t("my26.retryValidation")}
          </button>
        </div>
      </div>
    );
  }

  if (validationState.phase === "done") {
    const { result } = validationState;
    const errors = result.issues.filter((i) => i.severity === "error");
    const warnings = result.issues.filter((i) => i.severity === "warning");

    return (
      <div
        className="wiz-body"
        role="form"
        aria-label={t("my26.validateFormLabel")}
        aria-live="polite"
        data-testid="validation-result"
      >
        {result.valid ? (
          <div className="wiz-alert wiz-alert--ok" role="status" data-testid="validation-passed">
            <span className="wiz-alert-icon" aria-hidden="true">✓</span>
            <div>
              <strong>{t("my26.validationPassedTitle")}</strong>{" "}
              {t("my26.validationPassedDesc")}
            </div>
          </div>
        ) : (
          <div className="wiz-alert wiz-alert--err" role="alert" data-testid="validation-failed">
            <span className="wiz-alert-icon" aria-hidden="true">⚠</span>
            <div>
              <strong>
                {t("my26.validationFailedTitle", {
                  errorCount: errors.length,
                  warningCount: warnings.length,
                })}
              </strong>{" "}
              {t("my26.validationFailedDesc")}
            </div>
          </div>
        )}

        {result.issues.length > 0 && (
          <section className="wiz-card" aria-labelledby="issues-heading">
            <div className="wiz-card-head">
              <h3 id="issues-heading">{t("my26.issuesLabel")}</h3>
              <span
                className={errors.length > 0 ? "wiz-chip wiz-chip--err" : "wiz-chip wiz-chip--warn"}
              >
                {errors.length > 0
                  ? t("my26.blockingCount", { count: errors.length })
                  : t("my26.warningCount", { count: warnings.length })}
              </span>
            </div>
            <div className="wiz-card-body wiz-card-body--no-pad">
              <table className="wiz-table" aria-label={t("my26.issuesTableLabel")}>
                <thead>
                  <tr>
                    <th scope="col">{t("my26.colSeverity")}</th>
                    <th scope="col">{t("my26.colField")}</th>
                    <th scope="col">{t("my26.colDetail")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.issues.map((issue, idx) => (
                    <IssueRow key={`${issue.field}-${idx}`} issue={issue} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {result.valid && (
          <section className="wiz-card" aria-labelledby="passed-heading">
            <div className="wiz-card-head">
              <h3 id="passed-heading">{t("my26.validationSummaryLabel")}</h3>
            </div>
            <div className="wiz-card-body">
              <dl className="wiz-kv-grid">
                <div className="wiz-kv">
                  <dt className="wiz-kv-label">{t("my26.optionCodesChecked")}</dt>
                  <dd className="wiz-kv-val">✓</dd>
                </div>
                <div className="wiz-kv">
                  <dt className="wiz-kv-label">{t("my26.paintCodesChecked")}</dt>
                  <dd className="wiz-kv-val">✓</dd>
                </div>
                <div className="wiz-kv">
                  <dt className="wiz-kv-label">{t("my26.kittingRulesChecked")}</dt>
                  <dd className="wiz-kv-val">✓</dd>
                </div>
              </dl>
            </div>
          </section>
        )}

        <div className="wiz-btn-group">
          <button type="button" className="btn btn-secondary" onClick={onBack}>
            ← {t("my26.back")}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onNext}
            disabled={!result.valid}
            aria-disabled={!result.valid}
            data-testid="continue-to-save-btn"
          >
            {t("my26.continueToReview")} →
          </button>
        </div>
      </div>
    );
  }

  // idle (shouldn't happen but guard)
  return null;
}

// ---------------------------------------------------------------------------
// Step 3: Review & Save
// ---------------------------------------------------------------------------

function ReviewStep({
  draft,
  activeConfig,
  saveState,
  onBack,
  onSave,
}: {
  draft: My26ConfigDraft;
  activeConfig: My26Config | null;
  saveState: SaveState;
  onBack: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();

  if (saveState.phase === "done") {
    const { config } = saveState;
    return (
      <div className="wiz-body">
        <div
          className="wiz-alert wiz-alert--ok"
          role="status"
          data-testid="save-confirmation"
        >
          <span className="wiz-alert-icon" aria-hidden="true">✓</span>
          <div>
            <strong>{t("my26.saveConfirmedTitle")}</strong>{" "}
            {t("my26.saveConfirmedDesc", { version: config.version, configId: config.configId })}
          </div>
        </div>
        <section className="wiz-card" aria-labelledby="saved-config-heading">
          <div className="wiz-card-head">
            <h3 id="saved-config-heading">{t("my26.savedConfigLabel")}</h3>
          </div>
          <div className="wiz-card-body">
            <dl className="wiz-kv-grid">
              <div className="wiz-kv">
                <dt className="wiz-kv-label">configId</dt>
                <dd className="wiz-kv-val wiz-mono">{String(config.configId)}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">version</dt>
                <dd className="wiz-kv-val wiz-mono">{config.version}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">status</dt>
                <dd className="wiz-kv-val">
                  <ConfigStatusBadge status={config.status} />
                </dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">activatedAt</dt>
                <dd className="wiz-kv-val wiz-mono">{config.activatedAt ?? "—"}</dd>
              </div>
            </dl>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="wiz-body" role="form" aria-label={t("my26.reviewFormLabel")}>
      <div className="wiz-alert wiz-alert--info" role="note">
        <span className="wiz-alert-icon" aria-hidden="true">ℹ</span>
        <div>{t("my26.reviewInfoBanner")}</div>
      </div>

      {saveState.phase === "error" && (
        <div className="wiz-alert wiz-alert--err" role="alert" data-testid="save-error">
          <span className="wiz-alert-icon" aria-hidden="true">✕</span>
          <div>
            <strong>{t("my26.saveFailed")}</strong> {saveState.message}
          </div>
        </div>
      )}

      <div className="wiz-two-col">
        <section className="wiz-card" aria-labelledby="config-meta-heading">
          <div className="wiz-card-head">
            <h3 id="config-meta-heading">{t("my26.configMetaLabel")}</h3>
            <code className="wiz-api-field">GET /my26/config</code>
          </div>
          <div className="wiz-card-body">
            <dl className="wiz-kv-list">
              <div className="wiz-kv">
                <dt className="wiz-kv-label">status</dt>
                <dd className="wiz-kv-val">
                  <ConfigStatusBadge status={activeConfig?.status ?? "draft"} />
                </dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">version</dt>
                <dd className="wiz-kv-val wiz-mono">{activeConfig?.version ?? t("my26.pending")}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">configId</dt>
                <dd className="wiz-kv-val wiz-mono">{activeConfig ? String(activeConfig.configId) : t("my26.pending")}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">createdBy</dt>
                <dd className="wiz-kv-val">{activeConfig?.createdBy ?? "—"}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">activatedAt</dt>
                <dd className="wiz-kv-val wiz-mono">{activeConfig?.activatedAt ?? t("my26.notYetSaved")}</dd>
              </div>
            </dl>
          </div>
        </section>

        <section className="wiz-card" aria-labelledby="summary-counts-heading">
          <div className="wiz-card-head">
            <h3 id="summary-counts-heading">{t("my26.summaryCounts")}</h3>
          </div>
          <div className="wiz-card-body">
            <dl className="wiz-kv-list">
              <div className="wiz-kv">
                <dt className="wiz-kv-label">optionCodes</dt>
                <dd className="wiz-kv-val">{draft.optionCodes.length}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">paintPalette</dt>
                <dd className="wiz-kv-val">{draft.paintPalette.length}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">kittingRules</dt>
                <dd className="wiz-kv-val">{draft.kittingRules?.length ?? 0}</dd>
              </div>
              <div className="wiz-kv">
                <dt className="wiz-kv-label">{t("my26.validationStatus")}</dt>
                <dd className="wiz-kv-val">
                  <span className="wiz-chip wiz-chip--ok">
                    valid: true · 0 issues
                  </span>
                </dd>
              </div>
            </dl>
          </div>
        </section>
      </div>

      <section className="wiz-card" aria-labelledby="review-options-heading">
        <div className="wiz-card-head">
          <h3 id="review-options-heading">
            {t("my26.optionCodesLabel")} — {t("my26.fullList")}
          </h3>
        </div>
        <div className="wiz-card-body wiz-card-body--no-pad">
          <table className="wiz-table" aria-label={t("my26.optionCodesTableLabel")}>
            <thead>
              <tr>
                <th scope="col">{t("my26.colCode")}</th>
                <th scope="col">{t("my26.colDescription")}</th>
                <th scope="col">{t("my26.colConstraintMappings")}</th>
              </tr>
            </thead>
            <tbody>
              {draft.optionCodes.map((oc) => (
                <tr key={oc.code}>
                  <td><code className="wiz-code">{oc.code}</code></td>
                  <td>{oc.description}</td>
                  <td>
                    {oc.constraintMappings && oc.constraintMappings.length > 0 ? (
                      <span className="wiz-chip wiz-chip--ok">{t("my26.mapped")}</span>
                    ) : (
                      <span className="wiz-chip wiz-chip--warn">{t("my26.notMapped")}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="wiz-btn-group">
        <button type="button" className="btn btn-secondary" onClick={onBack}>
          ← {t("my26.back")}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onSave}
          disabled={saveState.phase === "pending"}
          aria-disabled={saveState.phase === "pending"}
          data-testid="save-config-btn"
        >
          {saveState.phase === "pending" ? t("my26.saving") : t("my26.saveAndActivate")}
        </button>
        <span className="wiz-step-info">POST /my26/config/save</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard component
// ---------------------------------------------------------------------------

function rowsToOptionCodes(rows: OptionCodeRow[]): OptionCodeEntry[] {
  return rows
    .filter((r) => r.code.trim() !== "")
    .map((r) => ({
      code: r.code.trim(),
      description: r.description.trim(),
      constraintMappings: r.constraintMappings
        ? r.constraintMappings.split(",").map((s) => s.trim()).filter(Boolean)
        : [],
    }));
}

function rowsToPaintPalette(rows: PaintRow[]): PaintPaletteEntry[] {
  return rows
    .filter((r) => r.paintCode.trim() !== "")
    .map((r) => ({
      paintCode: r.paintCode.trim(),
      batchGroup: r.batchGroup.trim(),
      changeoverPenaltyMinutes:
        r.changeoverPenaltyMinutes !== "" ? parseInt(r.changeoverPenaltyMinutes, 10) : null,
    }));
}

function parseKittingRules(json: string): Record<string, unknown>[] | null {
  const trimmed = json.trim();
  if (!trimmed) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return Array.isArray(parsed) ? (parsed as Record<string, unknown>[]) : null;
  } catch {
    return null;
  }
}

export function My26WizardPage() {
  const { t } = useTranslation();
  const [step, setStep] = useState<WizardStep>(1);
  const [activeConfig, setActiveConfig] = useState<My26Config | null | "loading">("loading");

  const [optionRows, setOptionRows] = useState<OptionCodeRow[]>([
    { id: generateRowId(), code: "", description: "", constraintMappings: "" },
  ]);
  const [paintRows, setPaintRows] = useState<PaintRow[]>([
    { id: generateRowId(), paintCode: "", batchGroup: "", changeoverPenaltyMinutes: "" },
  ]);
  const [kittingJson, setKittingJson] = useState("");

  const [validationState, setValidationState] = useState<ValidationState>({ phase: "idle" });
  const [saveState, setSaveState] = useState<SaveState>({ phase: "idle" });

  const abortRef = useRef<AbortController | null>(null);

  // Load active config on mount
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const config = await getMy26Config();
        if (!cancelled) setActiveConfig(config);
      } catch {
        if (!cancelled) setActiveConfig(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const buildDraft = useCallback((): My26ConfigDraft => ({
    optionCodes: rowsToOptionCodes(optionRows),
    paintPalette: rowsToPaintPalette(paintRows),
    kittingRules: parseKittingRules(kittingJson),
  }), [optionRows, paintRows, kittingJson]);

  const handleValidate = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setValidationState({ phase: "pending" });
    setStep(2);
    try {
      const draft = buildDraft();
      const result = await validateMy26Config(draft);
      setValidationState({ phase: "done", result });
    } catch (err) {
      setValidationState({
        phase: "error",
        message: err instanceof Error ? err.message : t("errors.requestFailed"),
      });
    }
  }, [buildDraft, t]);

  const handleRetryValidate = useCallback(async () => {
    await handleValidate();
  }, [handleValidate]);

  const handleSave = useCallback(async () => {
    setSaveState({ phase: "pending" });
    try {
      const draft = buildDraft();
      const saved = await saveMy26Config(draft);
      // Emit audit event — best-effort, never blocks save confirmation
      void postAuditEvent({
        userId: "anonymous",
        action: "my26_config_save",
        configVersion: String(saved.configId),
        my26ConfigVersion: String(saved.configId),
        timestamp: saved.activatedAt ?? new Date().toISOString(),
      }).catch(() => undefined);
      setSaveState({ phase: "done", config: saved });
      setActiveConfig(saved);
    } catch (err) {
      setSaveState({
        phase: "error",
        message: err instanceof Error ? err.message : t("errors.requestFailed"),
      });
    }
  }, [buildDraft, t]);

  const configForDisplay =
    activeConfig === "loading" ? null : activeConfig;

  const configStatus =
    activeConfig === "loading"
      ? null
      : activeConfig?.status ?? null;

  const configVersion =
    activeConfig === "loading"
      ? null
      : activeConfig?.version ?? null;

  const draft = buildDraft();

  return (
    <main className="main-content" id="my26-wizard">
      {/* Page header */}
      <div className="wiz-header">
        <div className="page-header-left">
          <h2>{t("my26.wizardTitle")}</h2>
          <p className="subtitle">
            {t("my26.wizardSubtitle")}
            {configVersion && (
              <>
                {" · "}
                <span className="wiz-config-version">{configVersion}</span>
                {configStatus && <ConfigStatusBadge status={configStatus} />}
              </>
            )}
            {activeConfig === null && (
              <span className="wiz-no-config">{t("my26.noConfigYet")}</span>
            )}
          </p>
        </div>
      </div>

      {/* Step indicators */}
      <nav className="wiz-steps" aria-label={t("my26.stepsLabel")}>
        <StepIndicator step={1} currentStep={step} label={t("my26.stepImport")} />
        <StepIndicator step={2} currentStep={step} label={t("my26.stepValidate")} />
        <StepIndicator step={3} currentStep={step} label={t("my26.stepReviewSave")} />
      </nav>

      {/* Step content */}
      {step === 1 && (
        <ImportStep
          optionRows={optionRows}
          paintRows={paintRows}
          kittingJson={kittingJson}
          onOptionRowsChange={setOptionRows}
          onPaintRowsChange={setPaintRows}
          onKittingJsonChange={setKittingJson}
          onNext={() => void handleValidate()}
        />
      )}

      {step === 2 && (
        <ValidationStep
          validationState={validationState}
          onBack={() => setStep(1)}
          onRetry={() => void handleRetryValidate()}
          onNext={() => setStep(3)}
        />
      )}

      {step === 3 && (
        <ReviewStep
          draft={draft}
          activeConfig={configForDisplay}
          saveState={saveState}
          onBack={() => setStep(2)}
          onSave={() => void handleSave()}
        />
      )}
    </main>
  );
}

// Export for convenience in tests / sidebar reference
export type { OptionCodeRow, PaintRow };
