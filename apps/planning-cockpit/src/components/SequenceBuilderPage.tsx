import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { postOptimize, getOptimizeRun } from "../services/api";
import type {
  OptimizeResponse,
  RankedVin,
  ResultType,
  VinInput,
  ConstraintSnapshot,
} from "../types/optimizer";

const POLL_INTERVAL_MS = 2_000;
const POLL_TIMEOUT_MS = 45_000;
const MONTE_CARLO_RUNS = 50;

type RunState =
  | { phase: "idle" }
  | { phase: "running"; runId: string; elapsedMs: number }
  | { phase: "done"; result: OptimizeResponse }
  | { phase: "error"; message: string };

function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function emitOptimizerRunEvent(userId: string, sessionId: string): void {
  const event = {
    action: "optimizer_run",
    userId,
    sessionId,
    timestamp: new Date().toISOString(),
  };
  if (
    typeof window !== "undefined" &&
    typeof (
      window as Window & { otelClient?: { track: (e: unknown) => void } }
    ).otelClient?.track === "function"
  ) {
    (
      window as Window & { otelClient: { track: (e: unknown) => void } }
    ).otelClient.track(event);
  }
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
}

function makeConstraintSnapshot(): ConstraintSnapshot {
  return {
    snapshotTimestamp: new Date().toISOString(),
    paintCapacityUtilisation: 94,
    bodyWipQueueDepth: 78,
    kittingReadinessPct: 97,
  };
}

function makeSampleVins(): VinInput[] {
  return [
    { vin: "WBY31CF080NCH00001", vipPriority: true, regulatoryHold: false },
    { vin: "WBY31CF080NCH00002", vipPriority: false, regulatoryHold: false },
    { vin: "WBY31CF080NCH00003", vipPriority: false, regulatoryHold: true },
  ];
}

function ConstraintScoreBar({ score }: { score: number }) {
  const pct = Math.min(100, Math.max(0, score * 100));
  const colorClass =
    pct >= 80 ? "score-bar-fill--high" : pct >= 60 ? "score-bar-fill--mid" : "score-bar-fill--low";
  return (
    <div className="score-bar-wrap">
      <div
        className="score-bar-track"
        role="presentation"
        aria-hidden="true"
      >
        <div className={`score-bar-fill ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`score-bar-num ${colorClass}`}>{score.toFixed(2)}</span>
    </div>
  );
}

function RankedSequenceTable({ rows }: { rows: RankedVin[] }) {
  const { t } = useTranslation();
  const sorted = [...rows].sort((a, b) => a.position - b.position);
  return (
    <div className="seq-table-wrap">
      <table className="seq-table" aria-label={t("sequenceBuilder.rankedSequenceLabel")}>
        <thead>
          <tr>
            <th scope="col">{t("sequence.columns.position")}</th>
            <th scope="col">{t("sequence.columns.vin")}</th>
            <th scope="col">{t("sequence.columns.constraintScore")}</th>
            <th scope="col">{t("sequenceBuilder.overrideFlagsCol")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.vin}>
              <td>
                <span
                  className={`rank-badge${row.position <= 2 ? " rank-badge--top" : ""}`}
                  aria-label={t("sequenceBuilder.positionLabel", { pos: row.position })}
                >
                  {row.position}
                </span>
              </td>
              <td>
                <span className="vin-mono">{row.vin}</span>
              </td>
              <td>
                <ConstraintScoreBar score={row.constraintScore} />
              </td>
              <td>
                {row.vipPriority && (
                  <span className="flag-badge flag-badge--vip" aria-label={t("sequenceBuilder.vipFlag")}>
                    {t("sequenceBuilder.vipFlag")}
                  </span>
                )}
                {row.regulatoryHold && (
                  <span
                    className="flag-badge flag-badge--reg"
                    aria-label={t("sequenceBuilder.regHoldFlag")}
                  >
                    {t("sequenceBuilder.regHoldFlag")}
                  </span>
                )}
                {!row.vipPriority && !row.regulatoryHold && (
                  <span className="flag-none" aria-hidden="true">
                    —
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeuristicBanner() {
  const { t } = useTranslation();
  return (
    <div
      className="heuristic-banner"
      role="alert"
      aria-live="assertive"
      data-testid="heuristic-banner"
    >
      <span className="heuristic-banner-icon" aria-hidden="true">⚠</span>
      <div className="heuristic-banner-body">
        <strong>{t("sequenceBuilder.heuristicBannerTitle")}</strong>
        <span>{t("sequenceBuilder.heuristicBannerDesc")}</span>
      </div>
    </div>
  );
}

function RunningIndicator({ elapsedMs }: { elapsedMs: number }) {
  const { t } = useTranslation();
  const elapsedS = (elapsedMs / 1000).toFixed(1);
  const timeoutS = POLL_TIMEOUT_MS / 1000;
  const pct = Math.min(100, (elapsedMs / POLL_TIMEOUT_MS) * 100);

  return (
    <div
      className="running-indicator"
      role="status"
      aria-live="polite"
      aria-label={t("sequenceBuilder.runningLabel")}
      data-testid="running-indicator"
    >
      <div className="running-spinner" aria-hidden="true">
        <div className="running-spinner-ring">
          <div
            className="running-spinner-fill"
            style={{ background: `conic-gradient(var(--accent-blue) ${pct}%, var(--border) 0%)` }}
          />
          <div className="running-spinner-center">
            {Math.round(pct)}%
          </div>
        </div>
      </div>
      <p className="running-title">{t("sequenceBuilder.runningTitle")}</p>
      <p className="running-sub">{t("sequenceBuilder.runningSubtitle")}</p>
      <div className="running-timer-row" aria-live="polite">
        <span className="running-timer">{elapsedS} s</span>
        <span className="running-timer-label">
          {t("sequenceBuilder.elapsedOf", { total: timeoutS })}
        </span>
      </div>
      <div
        className="running-progress-track"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t("sequenceBuilder.progressLabel")}
      >
        <div className="running-progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function SequenceBuilderPage() {
  const { t } = useTranslation();
  const [runState, setRunState] = useState<RunState>({ phase: "idle" });
  const idempotencyKeyRef = useRef<string>(generateIdempotencyKey());
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startMsRef = useRef<number>(0);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (elapsedTimerRef.current !== null) {
      clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const handleRunOptimizer = useCallback(async () => {
    stopPolling();
    idempotencyKeyRef.current = generateIdempotencyKey();

    emitOptimizerRunEvent("anonymous", "session-" + idempotencyKeyRef.current.slice(0, 8));

    const vins = makeSampleVins();
    const constraintSnapshot = makeConstraintSnapshot();
    const weekIso = "2026-W42";

    let runId: string;
    try {
      const initial = await postOptimize({
        weekIso,
        vins,
        constraintSnapshot,
        monteCarloRuns: MONTE_CARLO_RUNS,
        idempotencyKey: idempotencyKeyRef.current,
      });

      runId = initial.runId;

      // If POST returned a completed result (idempotent hit or synchronous response)
      if (initial.rankedSequence && initial.rankedSequence.length > 0) {
        setRunState({ phase: "done", result: initial });
        return;
      }
    } catch (err) {
      setRunState({
        phase: "error",
        message: err instanceof Error ? err.message : t("errors.optimizerRunFailed"),
      });
      return;
    }

    startMsRef.current = Date.now();
    setRunState({ phase: "running", runId, elapsedMs: 0 });

    elapsedTimerRef.current = setInterval(() => {
      setRunState((prev) => {
        if (prev.phase !== "running") return prev;
        return { ...prev, elapsedMs: Date.now() - startMsRef.current };
      });
    }, 100);

    pollTimerRef.current = setInterval(async () => {
      const elapsed = Date.now() - startMsRef.current;
      if (elapsed >= POLL_TIMEOUT_MS) {
        stopPolling();
        setRunState((prev) => {
          if (prev.phase !== "running") return prev;
          return {
            phase: "error",
            message: t("errors.optimizerRunFailed"),
          };
        });
        return;
      }

      try {
        const resp = await getOptimizeRun(runId);
        if (resp.rankedSequence && resp.rankedSequence.length > 0) {
          stopPolling();
          setRunState({ phase: "done", result: resp });
        }
      } catch {
        // transient poll failure — keep polling until timeout
      }
    }, POLL_INTERVAL_MS);
  }, [stopPolling, t]);

  const handleCancel = useCallback(() => {
    stopPolling();
    setRunState({ phase: "idle" });
  }, [stopPolling]);

  const handleRetry = useCallback(() => {
    setRunState({ phase: "idle" });
  }, []);

  const isRunnable = runState.phase === "idle" || runState.phase === "done" || runState.phase === "error";

  return (
    <main className="main-content" id="sequence-builder">
      <div className="page-header">
        <div className="page-header-left">
          <h2>{t("nav.sequenceBuilder")}</h2>
          <p className="subtitle">{t("sequenceBuilder.subtitle")}</p>
        </div>
      </div>

      {/* Run control card */}
      <section className="seq-card" aria-label={t("sequenceBuilder.runCardLabel")}>
        <div className="seq-card-header">
          <span className="seq-card-title">{t("sequenceBuilder.runCardTitle")}</span>
          <span className="seq-card-sub">{t("sequenceBuilder.runCardSub")}</span>
        </div>
        <div className="seq-card-body">
          <dl className="seq-config-grid">
            <div className="seq-config-item">
              <dt className="seq-config-label">weekIso</dt>
              <dd className="seq-config-value">2026-W42</dd>
            </div>
            <div className="seq-config-item">
              <dt className="seq-config-label">monteCarloRuns</dt>
              <dd className="seq-config-value">{MONTE_CARLO_RUNS}</dd>
            </div>
            <div className="seq-config-item">
              <dt className="seq-config-label">constraintSnapshot</dt>
              <dd className="seq-config-value">{t("sequenceBuilder.snapshotLive")}</dd>
            </div>
            <div className="seq-config-item">
              <dt className="seq-config-label">idempotencyKey</dt>
              <dd className="seq-config-value seq-config-value--mono">{idempotencyKeyRef.current.slice(0, 18)}…</dd>
            </div>
          </dl>
        </div>
        <div className="seq-run-zone">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleRunOptimizer}
            disabled={!isRunnable}
            aria-label={t("sequenceBuilder.runButtonLabel")}
            data-testid="run-optimizer-btn"
          >
            {t("sequenceBuilder.runButton")}
          </button>
          <span className="seq-run-hint">{t("sequenceBuilder.runHint")}</span>
        </div>
      </section>

      {/* In-progress state */}
      {runState.phase === "running" && (
        <section className="seq-card seq-card--running" aria-label={t("sequenceBuilder.runningLabel")}>
          <RunningIndicator elapsedMs={runState.elapsedMs} />
          <div className="seq-cancel-row">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleCancel}
              data-testid="cancel-run-btn"
            >
              {t("sequenceBuilder.cancelRun")}
            </button>
          </div>
        </section>
      )}

      {/* Error state */}
      {runState.phase === "error" && (
        <div
          className="seq-error-wrap"
          role="alert"
          data-testid="error-state"
        >
          <div className="seq-error-icon" aria-hidden="true">!</div>
          <h3 className="seq-error-title">{t("sequenceBuilder.errorTitle")}</h3>
          <p className="seq-error-sub">{runState.message}</p>
          <div className="seq-error-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleRunOptimizer()}
              data-testid="retry-btn"
            >
              {t("errors.retry")}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleRetry}
            >
              {t("sequenceBuilder.cancelRun")}
            </button>
          </div>
        </div>
      )}

      {/* Result state */}
      {runState.phase === "done" && (
        <section className="seq-card" aria-label={t("sequenceBuilder.resultsLabel")} data-testid="results-section">
          {(runState.result.resultType as ResultType) === "heuristic_fallback" && (
            <HeuristicBanner />
          )}
          <div
            className={`seq-result-banner${runState.result.resultType === "monte_carlo" ? " seq-result-banner--mc" : " seq-result-banner--hf"}`}
            role="status"
          >
            <span
              className={`result-type-tag${runState.result.resultType === "monte_carlo" ? " result-type-tag--mc" : " result-type-tag--hf"}`}
            >
              {runState.result.resultType === "monte_carlo"
                ? t("sequenceBuilder.resultTypeMonteCarlo")
                : t("sequenceBuilder.resultTypeHeuristic")}
            </span>
            <div className="result-meta">
              <strong>{runState.result.rankedSequence.length}</strong>
              {" "}{t("sequenceBuilder.vinsRanked")}
              {" · "}{(runState.result.durationMs / 1000).toFixed(1)}{" s"}
              {" · "}{runState.result.weekIso}
            </div>
            <div className="result-confidence">
              <span
                className={`confidence-score${runState.result.resultType === "monte_carlo" ? " confidence-score--mc" : " confidence-score--hf"}`}
                aria-label={t("sequenceBuilder.confidenceLabel", { score: runState.result.confidenceScore.toFixed(2) })}
              >
                {runState.result.confidenceScore.toFixed(2)}
              </span>
              <span className="confidence-label">{t("sequenceBuilder.confidenceScore")}</span>
            </div>
          </div>

          {runState.result.rankedSequence.length === 0 ? (
            <div className="seq-empty" role="status">
              {t("sequenceBuilder.emptySequence")}
            </div>
          ) : (
            <RankedSequenceTable rows={runState.result.rankedSequence} />
          )}

          <div className="seq-run-meta">
            <span><strong>runId:</strong> {runState.result.runId}</span>
            <span><strong>completedAt:</strong> {runState.result.completedAt}</span>
            <span><strong>confidenceScore:</strong> {runState.result.confidenceScore.toFixed(2)}</span>
          </div>
        </section>
      )}

      {/* Idle — empty state */}
      {runState.phase === "idle" && (
        <section className="seq-card" aria-label={t("sequenceBuilder.emptyStateLabel")}>
          <div className="seq-card-header">
            <span className="seq-card-title">{t("sequenceBuilder.rankedSequenceTitle")}</span>
            <span className="seq-card-sub">{t("sequenceBuilder.noRunYet")}</span>
          </div>
          <div className="seq-empty-state" role="status">
            <div className="seq-empty-icon" aria-hidden="true">⟳</div>
            <h3 className="seq-empty-title">{t("sequenceBuilder.noSequenceTitle")}</h3>
            <p className="seq-empty-desc">{t("sequenceBuilder.noSequenceDesc")}</p>
          </div>
        </section>
      )}
    </main>
  );
}
