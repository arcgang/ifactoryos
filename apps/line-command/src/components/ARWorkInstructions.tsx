import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useBilingualContent, type BilingualStrings } from "../hooks/useBilingualContent";

export interface WorkInstructionStep {
  id: string;
  stepNumber: number;
  title: BilingualStrings;
  detail: BilingualStrings;
  torqueNm?: number | null;
}

interface Props {
  vin: string;
  fetchInstructions: (vin: string, signal: AbortSignal) => Promise<WorkInstructionStep[]>;
}

function InstructionStep({ step }: { step: WorkInstructionStep }) {
  const { t } = useTranslation();

  const title = useBilingualContent(step.title);
  const detail = useBilingualContent(step.detail);

  if (title === null || detail === null) {
    return (
      <div className="wi-step" role="listitem">
        <div className="wi-num" aria-hidden="true">{step.stepNumber}</div>
        <div className="wi-body">
          <div className="wi-title">{t("ar_workbench.translation_pending")}</div>
          <div className="wi-detail" aria-label={t("ar_workbench.step_label", { number: step.stepNumber })}>
            {t("ar_workbench.translation_pending")}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wi-step" role="listitem">
      <div className="wi-num" aria-hidden="true">{step.stepNumber}</div>
      <div className="wi-body">
        <div className="wi-title">{title}</div>
        <div className="wi-detail">{detail}</div>
      </div>
      {step.torqueNm != null && (
        <div className="wi-meta" aria-label={`${step.torqueNm} ${t("ar_workbench.torque_unit")}`}>
          <div className="wi-torque">{step.torqueNm}</div>
          <div className="wi-unit">{t("ar_workbench.torque_unit")}</div>
        </div>
      )}
    </div>
  );
}

export function ARWorkInstructions({ vin, fetchInstructions }: Props) {
  const { t, i18n } = useTranslation();
  const [steps, setSteps] = useState<WorkInstructionStep[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetchInstructions(vin, controller.signal)
      .then((data) => {
        setSteps(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        const message = err instanceof Error ? err.message : t("ar_workbench.error");
        console.warn("[line-command] AR instructions fetch failed:", message);
        setError(t("ar_workbench.error"));
        setLoading(false);
      });

    return () => controller.abort();
  }, [vin, fetchInstructions, i18n.language, t]);

  if (loading) {
    return (
      <div className="ar-instructions ar-instructions--loading" aria-busy="true">
        <p>{t("ar_workbench.loading")}</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ar-instructions ar-instructions--error" role="alert">
        <p>{error}</p>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => {
            setLoading(true);
            setError(null);
            setSteps(null);
          }}
        >
          {t("ar_workbench.retry")}
        </button>
      </div>
    );
  }

  if (!steps || steps.length === 0) {
    return (
      <div className="ar-instructions ar-instructions--empty">
        <p>{t("ar_workbench.empty")}</p>
      </div>
    );
  }

  return (
    <div className="ar-instructions" role="list" aria-label={t("ar_workbench.title")}>
      {steps.map((step) => (
        <InstructionStep key={step.id} step={step} />
      ))}
    </div>
  );
}
