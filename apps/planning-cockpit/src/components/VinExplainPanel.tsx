import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { explainVinPosition } from "../services/api";
import type { ConstraintFactor, VinExplanation } from "../types/overrides";

interface Props {
  runId: string;
  vin: string;
  position: number;
  onClose: () => void;
}

type PanelState =
  | { phase: "loading" }
  | { phase: "done"; data: VinExplanation }
  | { phase: "error"; message: string };

export function VinExplainPanel({ runId, vin, position, onClose }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<PanelState>({ phase: "loading" });
  const [retryCount, setRetryCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ phase: "loading" });

    explainVinPosition(runId, vin)
      .then((data) => {
        if (!controller.signal.aborted) {
          setState({ phase: "done", data });
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setState({
            phase: "error",
            message: err instanceof Error ? err.message : t("errors.requestFailed"),
          });
        }
      });

    return () => {
      controller.abort();
    };
  }, [runId, vin, t, retryCount]);

  return (
    <aside
      className="explain-panel"
      role="complementary"
      aria-label={t("explain.panelLabel", { vin })}
    >
      <div className="explain-panel-header">
        <h3 className="explain-panel-title">
          {t("explain.panelTitle")}
        </h3>
        <div className="explain-panel-meta">
          <span className="vin-mono" aria-label={t("explain.vinLabel")}>{vin}</span>
          <span className="explain-pos-badge" aria-label={t("sequenceBuilder.positionLabel", { pos: position })}>
            {t("explain.posLabel", { pos: position })}
          </span>
        </div>
        <button
          type="button"
          className="btn btn-secondary explain-close-btn"
          onClick={onClose}
          aria-label={t("explain.close")}
        >
          ✕
        </button>
      </div>

      <div className="explain-panel-body">
        {state.phase === "loading" && (
          <div className="explain-skeleton" role="status" aria-live="polite" aria-label={t("explain.loading")} data-testid="explain-loading">
            {[1, 2, 3].map((i) => (
              <div key={i} className="explain-skeleton-row" aria-hidden="true">
                <div className="explain-skeleton-bar explain-skeleton-bar--name" />
                <div className="explain-skeleton-bar explain-skeleton-bar--score" />
                <div className="explain-skeleton-bar explain-skeleton-bar--source" />
              </div>
            ))}
          </div>
        )}

        {state.phase === "error" && (
          <div className="explain-error" role="alert" data-testid="explain-error">
            <span className="explain-error-icon" aria-hidden="true">!</span>
            <div>
              <strong>{t("explain.errorTitle")}</strong>
              <p className="explain-error-msg">{state.message}</p>
              <button
                type="button"
                className="btn btn-secondary wiz-btn-sm"
                onClick={() => setRetryCount((c) => c + 1)}
              >
                {t("errors.retry")}
              </button>
            </div>
          </div>
        )}

        {state.phase === "done" && (
          <table className="explain-table" aria-label={t("explain.tableLabel")}>
            <thead>
              <tr>
                <th scope="col">{t("explain.colFactor")}</th>
                <th scope="col">{t("explain.colWeightedScore")}</th>
                <th scope="col">{t("explain.colDataSource")}</th>
              </tr>
            </thead>
            <tbody>
              {state.data.topFactors.map((factor) => (
                <FactorRow key={factor.factorName} factor={factor} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </aside>
  );
}

function FactorRow({ factor }: { factor: ConstraintFactor }) {
  const { t } = useTranslation();
  const factorLabel = t(`explain.factors.${factor.factorName}`);
  const scoreClass =
    factor.weightedScore >= 0.7
      ? "explain-score--high"
      : factor.weightedScore >= 0.4
        ? "explain-score--mid"
        : "explain-score--low";

  return (
    <tr data-testid={`explain-factor-${factor.factorName}`}>
      <td>
        <span className="explain-factor-name">{factorLabel}</span>
      </td>
      <td>
        <span className={`explain-score ${scoreClass}`} aria-label={`${factorLabel} ${t("explain.weightedScoreLabel")}: ${factor.weightedScore.toFixed(2)}`}>
          {factor.weightedScore.toFixed(2)}
        </span>
      </td>
      <td>
        <span className="explain-source" title={factor.dataSource}>{factor.dataSource}</span>
      </td>
    </tr>
  );
}
