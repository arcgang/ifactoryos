import type { ReactNode } from "react";
import type { ConstraintThreshold, ConstraintTileState } from "../types/constraints";

interface Props {
  state: ConstraintTileState;
  threshold: ConstraintThreshold | undefined;
  label: string;
  sublabel: string;
  children?: ReactNode;
}

function formatTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

export function ConstraintTile({ state, threshold, label, sublabel, children }: Props) {
  const { currentValue, unit, measuredAt, isAmber, isStale, lastKnownGoodAt } = state;

  const tileClass = isStale ? "tile stale" : isAmber ? "tile amber" : "tile green";
  const badgeClass = isStale
    ? "tile-status-badge badge-stale"
    : isAmber
      ? "tile-status-badge badge-warn"
      : "tile-status-badge badge-ok";
  const badgeText = isStale ? "STALE" : isAmber ? "WARN" : "OK";
  const valueClass = isStale
    ? "tile-value stale"
    : isAmber
      ? "tile-value amber"
      : "tile-value green";

  const gaugeMax = threshold
    ? Math.max(threshold.criticalLevel * 1.1, (currentValue ?? 0) * 1.1, 1)
    : 100;
  const gaugePct =
    currentValue !== null
      ? Math.min(100, Math.round((currentValue / gaugeMax) * 100))
      : 0;
  const warnPct = threshold
    ? Math.min(100, Math.round((threshold.warningLevel / gaugeMax) * 100))
    : null;

  return (
    <article
      className={tileClass}
      aria-label={`${label}: ${currentValue ?? "—"} ${unit}`}
    >
      <div className="tile-header">
        <div>
          <div className="tile-label">{label}</div>
          <div className="tile-sublabel">{sublabel}</div>
        </div>
        <span className={badgeClass}>{badgeText}</span>
      </div>

      {currentValue !== null ? (
        <div className={valueClass}>
          {currentValue}
          <span className="tile-value-unit"> {unit}</span>
        </div>
      ) : (
        <div className={valueClass} aria-label="Loading">—</div>
      )}

      <div className="tile-unit">{children}</div>

      <div className="gauge-track" role="presentation" aria-hidden="true">
        <div
          className={`gauge-fill ${isStale ? "stale" : isAmber ? "amber" : "green"}`}
          style={{ width: `${gaugePct}%` }}
        />
        {warnPct !== null && (
          <div
            className="gauge-threshold-marker"
            style={{ left: `${warnPct}%` }}
            data-label={`WARN ${threshold!.warningLevel}`}
          />
        )}
      </div>

      {isAmber && threshold && currentValue !== null && (
        <div className="threshold-row" role="status">
          <div className="threshold-col">
            <span className="threshold-col-label">Current</span>
            <span className="threshold-col-value">{currentValue}</span>
          </div>
          <div className="threshold-divider" aria-hidden="true" />
          <span className="threshold-arrow" aria-hidden="true">{">"}</span>
          <div className="threshold-divider" aria-hidden="true" />
          <div className="threshold-col">
            <span className="threshold-col-label">Warning</span>
            <span className="threshold-col-value">{threshold.warningLevel}</span>
          </div>
        </div>
      )}

      {isStale && (
        <div className="staleness-row" role="status">
          <span className="staleness-icon" aria-hidden="true">⚠</span>
          <div>
            <span className="staleness-label">UNS data stale</span>
            <span className="staleness-ts">
              Last good: {formatTs(lastKnownGoodAt)}
            </span>
          </div>
        </div>
      )}

      <div className="tile-meta">
        <span />
        <span className="tile-meta-ts">{formatTs(measuredAt)}</span>
      </div>
    </article>
  );
}
