import { useState, type FormEvent } from "react";
import type { ConstraintThreshold, ConstraintType } from "../types/constraints";
import { upsertConstraintThreshold } from "../services/api";

interface Props {
  thresholds: ConstraintThreshold[];
  onSaved: (updated: ConstraintThreshold) => void;
}

const CONSTRAINT_LABELS: Record<ConstraintType, string> = {
  paint_capacity: "Paint Capacity",
  body_wip_queue: "Body WIP Queue",
  kitting_readiness: "Kitting Readiness",
  maintenance_window: "Maintenance Window",
  agv_delay: "AGV Delay",
  press_die_overrun: "Press Die Overrun",
};

export function ThresholdConfigPanel({ thresholds, onSaved }: Props) {
  const [saving, setSaving] = useState<string | null>(null);
  const [errors, setErrors] = useState<Map<string, string>>(new Map());

  async function handleSave(threshold: ConstraintThreshold) {
    setSaving(threshold.thresholdId);
    setErrors((prev) => {
      const next = new Map(prev);
      next.delete(threshold.thresholdId);
      return next;
    });
    try {
      const updated = await upsertConstraintThreshold(threshold);
      onSaved(updated);
    } catch (err) {
      setErrors((prev) => {
        const next = new Map(prev);
        next.set(
          threshold.thresholdId,
          err instanceof Error ? err.message : "Save failed",
        );
        return next;
      });
    } finally {
      setSaving(null);
    }
  }

  return (
    <section className="threshold-config" aria-labelledby="threshold-config-heading">
      <div className="threshold-config-header">
        <h3 id="threshold-config-heading">Constraint Warning Thresholds</h3>
      </div>
      <div className="config-table-head" aria-hidden="true">
        <span>Constraint</span>
        <span>Warning level</span>
        <span>Critical level</span>
        <span>Unit</span>
        <span>Action</span>
      </div>
      {thresholds.map((t) => (
        <ThresholdRow
          key={t.thresholdId}
          threshold={t}
          label={CONSTRAINT_LABELS[t.constraintType] ?? t.constraintType}
          isSaving={saving === t.thresholdId}
          error={errors.get(t.thresholdId)}
          onSave={handleSave}
        />
      ))}
    </section>
  );
}

interface RowProps {
  threshold: ConstraintThreshold;
  label: string;
  isSaving: boolean;
  error: string | undefined;
  onSave: (t: ConstraintThreshold) => void;
}

function ThresholdRow({ threshold, label, isSaving, error, onSave }: RowProps) {
  const [warningLevel, setWarningLevel] = useState(String(threshold.warningLevel));
  const [criticalLevel, setCriticalLevel] = useState(String(threshold.criticalLevel));

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const wl = parseFloat(warningLevel);
    const cl = parseFloat(criticalLevel);
    if (Number.isNaN(wl) || Number.isNaN(cl)) return;
    onSave({ ...threshold, warningLevel: wl, criticalLevel: cl });
  }

  return (
    <form
      className="config-table-row"
      onSubmit={handleSubmit}
      aria-label={`Threshold configuration for ${label}`}
    >
      <div>
        <div className="config-constraint-name">{label}</div>
        <div className="config-constraint-id">{threshold.constraintType}</div>
      </div>
      <div className="config-field">
        <label htmlFor={`warn-${threshold.thresholdId}`} className="sr-only">
          Warning level for {label}
        </label>
        <input
          id={`warn-${threshold.thresholdId}`}
          className="config-input"
          type="number"
          step="any"
          value={warningLevel}
          onChange={(e) => setWarningLevel(e.target.value)}
          aria-describedby={error ? `err-${threshold.thresholdId}` : undefined}
        />
        <span className="config-unit-label">{threshold.unit}</span>
      </div>
      <div className="config-field">
        <label htmlFor={`crit-${threshold.thresholdId}`} className="sr-only">
          Critical level for {label}
        </label>
        <input
          id={`crit-${threshold.thresholdId}`}
          className="config-input"
          type="number"
          step="any"
          value={criticalLevel}
          onChange={(e) => setCriticalLevel(e.target.value)}
        />
        <span className="config-unit-label">{threshold.unit}</span>
      </div>
      <div>{threshold.unit}</div>
      <div>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={isSaving}
          aria-busy={isSaving}
        >
          {isSaving ? "Saving…" : "Save"}
        </button>
        {error && (
          <span id={`err-${threshold.thresholdId}`} role="alert" className="field-error">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}
