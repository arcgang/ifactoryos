import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCertificate,
  listCertificates,
  verifyCertificate,
} from "../services/api";
import type {
  CertificateRecord,
  CertificateVerifyResponse,
} from "../services/api";
import { emitAdoptionEvent } from "../services/adoption";
import { useAuth } from "../hooks/useAuth";

// ---------------------------------------------------------------------------
// TamperEvidencePanel
// ---------------------------------------------------------------------------

type VerifyPhase = "idle" | "loading" | "verified" | "tampered" | "error";

interface TamperEvidencePanelProps {
  certificateId: string;
}

export function TamperEvidencePanel({ certificateId }: TamperEvidencePanelProps) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<VerifyPhase>("idle");
  const [verifyResult, setVerifyResult] = useState<CertificateVerifyResponse | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setVerifyError(null);

    verifyCertificate(certificateId)
      .then((result) => {
        if (cancelled) return;
        setVerifyResult(result);
        setPhase(result.verified ? "verified" : "tampered");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setVerifyError(err instanceof Error ? err.message : t("errors.serverError"));
        setPhase("error");
      });

    return () => {
      cancelled = true;
    };
  }, [certificateId, t]);

  if (phase === "loading") {
    return (
      <div
        className="qs-verify-panel qs-verify-panel--loading"
        role="status"
        aria-live="polite"
        data-testid="verify-panel-loading"
      >
        <span className="gate-spinner" aria-hidden="true" />
        <p>{t("certificateStudio.verifying")}</p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div
        className="qs-verify-panel qs-verify-panel--error"
        role="alert"
        data-testid="verify-panel-error"
      >
        <span aria-hidden="true" className="qs-verify-error-icon">⚠</span>
        <p>{verifyError ?? t("certificateStudio.verifyError")}</p>
      </div>
    );
  }

  if (phase === "verified" && verifyResult) {
    return (
      <section
        className="qs-verify-panel qs-verify-panel--ok"
        aria-labelledby="verify-panel-heading"
        data-testid="verify-panel-ok"
      >
        <header className="qs-verify-panel-header">
          <span className="qs-verify-icon-ok" aria-hidden="true">✓</span>
          <h3 id="verify-panel-heading" className="qs-verify-title">
            {t("certificateStudio.tamperEvidenceVerified")}
          </h3>
          <span className="qs-verify-badge qs-verify-badge--ok" aria-label={t("certificateStudio.verifiedAriaLabel")}>
            {t("certificateStudio.verified")}
          </span>
        </header>
        <dl className="qs-verify-fields">
          <div className="qs-verify-field">
            <dt>{t("certificateStudio.storedHash")}</dt>
            <dd className="qs-verify-hash" data-testid="verify-stored-hash">
              {verifyResult.storedHash}
            </dd>
          </div>
          {verifyResult.fabricHash && (
            <div className="qs-verify-field">
              <dt>{t("certificateStudio.fabricHash")}</dt>
              <dd className="qs-verify-hash" data-testid="verify-fabric-hash">
                {verifyResult.fabricHash}
              </dd>
            </div>
          )}
          <div className="qs-verify-field">
            <dt>{t("certificateStudio.fabricTransactionId")}</dt>
            <dd data-testid="verify-tx-id">{verifyResult.fabricTransactionId}</dd>
          </div>
          {verifyResult.verifiedAt && (
            <div className="qs-verify-field">
              <dt>{t("certificateStudio.verifiedAt")}</dt>
              <dd data-testid="verify-verified-at">
                {new Date(verifyResult.verifiedAt).toISOString()}
              </dd>
            </div>
          )}
        </dl>
      </section>
    );
  }

  if (phase === "tampered" && verifyResult) {
    return (
      <section
        className="qs-verify-panel qs-verify-panel--tampered"
        aria-labelledby="verify-panel-tamper-heading"
        data-testid="verify-panel-tampered"
      >
        <header className="qs-verify-panel-header">
          <span className="qs-verify-icon-tamper" aria-hidden="true">⚠</span>
          <h3 id="verify-panel-tamper-heading" className="qs-verify-title qs-verify-title--tampered">
            {t("certificateStudio.tamperDetected")}
          </h3>
          <span className="qs-verify-badge qs-verify-badge--tampered" aria-label={t("certificateStudio.tamperDetectedAriaLabel")}>
            {t("certificateStudio.tamperAlert")}
          </span>
        </header>
        {verifyResult.mismatchReason && (
          <p className="qs-verify-mismatch" data-testid="verify-mismatch-reason">
            {verifyResult.mismatchReason}
          </p>
        )}
        <dl className="qs-verify-fields">
          <div className="qs-verify-field">
            <dt>{t("certificateStudio.storedHash")}</dt>
            <dd className="qs-verify-hash qs-verify-hash--mismatch" data-testid="verify-stored-hash">
              {verifyResult.storedHash}
            </dd>
          </div>
          {verifyResult.fabricHash && (
            <div className="qs-verify-field">
              <dt>{t("certificateStudio.fabricHash")}</dt>
              <dd className="qs-verify-hash qs-verify-hash--mismatch" data-testid="verify-fabric-hash">
                {verifyResult.fabricHash}
              </dd>
            </div>
          )}
        </dl>
      </section>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// Shift export helpers
// ---------------------------------------------------------------------------

async function fetchAllShiftCertificates(
  shiftId: string,
  signal: AbortSignal,
): Promise<CertificateRecord[]> {
  const all: CertificateRecord[] = [];
  let cursor: string | null = null;

  do {
    const page = await listCertificates({
      shiftId,
      limit: 200,
      cursor: cursor ?? undefined,
    });
    all.push(...page.data);
    cursor = page.page.next_cursor;

    if (signal.aborted) break;
  } while (cursor !== null);

  return all;
}

async function buildShiftSummaryPdf(
  records: CertificateRecord[],
  shiftId: string,
): Promise<Blob> {
  // pdfmake exposes pdfMake on window in UMD builds; we bridge to a Promise.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pdfMake = (window as any).pdfMake as {
    createPdf: (docDef: unknown) => { getBlob: (cb: (blob: Blob) => void) => void };
  } | undefined;

  if (!pdfMake) {
    throw new Error("pdfMake not loaded");
  }

  const rows: [string, string, string][] = records.map((r) => [
    r.vin,
    new Date(r.issuedAt).toISOString(),
    r.fabricTransactionId,
  ]);

  const docDefinition = {
    content: [
      { text: `Shift Certificate Summary — ${shiftId}`, style: "header" },
      {
        table: {
          headerRows: 1,
          widths: ["auto", "auto", "*"],
          body: [
            [
              { text: "VIN", bold: true },
              { text: "Issued At (ISO 8601)", bold: true },
              { text: "Fabric Transaction ID", bold: true },
            ],
            ...rows,
          ],
        },
      },
    ],
    styles: {
      header: { fontSize: 14, bold: true, marginBottom: 12 },
    },
  };

  return new Promise<Blob>((resolve) => {
    pdfMake.createPdf(docDefinition).getBlob(resolve);
  });
}

// ---------------------------------------------------------------------------
// ExportShiftSummaryButton
// ---------------------------------------------------------------------------

type ExportPhase = "idle" | "loading" | "done" | "empty" | "error";

interface ExportShiftSummaryButtonProps {
  shiftId: string;
}

export function ExportShiftSummaryButton({ shiftId }: ExportShiftSummaryButtonProps) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<ExportPhase>("idle");
  const [exportError, setExportError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stopTimer, [stopTimer]);

  const handleExport = useCallback(async () => {
    setPhase("loading");
    setExportError(null);
    setElapsedSeconds(0);

    timerRef.current = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const records = await fetchAllShiftCertificates(shiftId, controller.signal);

      if (controller.signal.aborted) return;

      if (records.length === 0) {
        stopTimer();
        setPhase("empty");
        return;
      }

      const blob = await buildShiftSummaryPdf(records, shiftId);
      stopTimer();

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `shift-summary-${shiftId}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);

      setPhase("done");
    } catch (err: unknown) {
      stopTimer();
      if (!controller.signal.aborted) {
        setExportError(err instanceof Error ? err.message : t("errors.serverError"));
        setPhase("error");
      }
    }
  }, [shiftId, t, stopTimer]);

  return (
    <div className="qs-export-shift-wrap" data-testid="export-shift-wrap">
      {phase === "loading" ? (
        <div
          className="qs-export-shift-progress"
          role="status"
          aria-live="polite"
          data-testid="export-progress"
        >
          <span className="gate-spinner" aria-hidden="true" />
          <span className="qs-export-elapsed">
            {t("certificateStudio.exportProgress", { seconds: elapsedSeconds })}
          </span>
        </div>
      ) : (
        <button
          type="button"
          className="qs-btn qs-btn--export"
          onClick={() => {
            void handleExport();
          }}
          aria-label={t("certificateStudio.exportShiftAriaLabel", { shiftId })}
          data-testid="export-shift-button"
        >
          {t("certificateStudio.exportShiftSummary")}
        </button>
      )}

      {phase === "empty" && (
        <p
          className="qs-export-empty"
          role="status"
          data-testid="export-empty-state"
        >
          {t("certificateStudio.exportEmpty")}
        </p>
      )}

      {phase === "done" && (
        <p className="qs-export-done" role="status" data-testid="export-done">
          {t("certificateStudio.exportDone")}
        </p>
      )}

      {phase === "error" && exportError && (
        <p
          role="alert"
          className="qs-error-inline"
          data-testid="export-error"
        >
          {exportError}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CertificateStudioPage — full page view
// ---------------------------------------------------------------------------

type PagePhase = "loading" | "loaded" | "error" | "not-found";

interface CertificateStudioPageProps {
  certificateId: string;
  onBack: () => void;
}

export function CertificateStudioPage({
  certificateId,
  onBack,
}: CertificateStudioPageProps) {
  const { t } = useTranslation();
  const { userId } = useAuth();
  const [phase, setPhase] = useState<PagePhase>("loading");
  const [record, setRecord] = useState<CertificateRecord | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  // Emit CERTIFICATE_STUDIO_OPEN on mount
  useEffect(() => {
    void emitAdoptionEvent({
      userId: userId || "",
      role: "QUALITY_GATE_VALIDATOR",
      action: "CERTIFICATE_STUDIO_OPEN",
      vin: "",
      timestamp: new Date().toISOString(),
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // emit once on page mount

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setLoadError(null);

    getCertificate(certificateId)
      .then((r) => {
        if (cancelled) return;
        setRecord(r);
        setPhase("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = (err as { status?: number }).status;
        if (status === 404) {
          setPhase("not-found");
        } else {
          setLoadError(err instanceof Error ? err.message : t("errors.serverError"));
          setPhase("error");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [certificateId, t, retryCount]);

  const reload = useCallback(() => {
    setRetryCount((c) => c + 1);
  }, []);

  if (phase === "loading") {
    return (
      <div
        className="qs-cert-studio-page"
        role="status"
        aria-live="polite"
        data-testid="cert-studio-loading"
      >
        <span className="gate-spinner" aria-hidden="true" />
        <p>{t("certificateStudio.loading")}</p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div
        className="qs-cert-studio-page qs-cert-studio-page--error"
        role="alert"
        data-testid="cert-studio-error"
      >
        <span className="qs-error-icon" aria-hidden="true">⚠</span>
        <h2>{t("certificateStudio.error")}</h2>
        <p>{loadError}</p>
        <button
          type="button"
          className="qs-btn qs-btn--retry"
          onClick={reload}
        >
          {t("defectDashboard.retry")}
        </button>
      </div>
    );
  }

  if (phase === "not-found" || !record) {
    return (
      <div
        className="qs-cert-studio-page qs-cert-studio-page--empty"
        role="status"
        data-testid="cert-studio-not-found"
      >
        <p>{t("certificateStudio.empty")}</p>
        <button
          type="button"
          className="qs-btn qs-btn--outline"
          onClick={onBack}
        >
          {t("processReplay.backToDashboard")}
        </button>
      </div>
    );
  }

  return (
    <div
      className="qs-cert-studio-page"
      data-testid="cert-studio-page"
    >
      <header className="qs-cert-studio-header">
        <button
          type="button"
          className="qs-btn qs-btn--outline"
          onClick={onBack}
          data-testid="cert-studio-back"
        >
          ← {t("nav.certificateStudio")}
        </button>
        <h1 className="qs-cert-studio-title">{t("certificateStudio.title")}</h1>
      </header>

      <section
        className="qs-cert-studio-record"
        aria-labelledby="cert-studio-record-heading"
      >
        <h2 id="cert-studio-record-heading">
          {t("certificateStudio.digitalBirthCertificate")}
        </h2>

        <dl className="qs-cert-detail-fields">
          <div className="qs-cert-detail-field">
            <dt>{t("gateCommand.vinLabel")}</dt>
            <dd className="qs-cert-detail-mono" data-testid="cs-vin">
              {record.vin}
            </dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("certificateStudio.certificateId")}</dt>
            <dd className="qs-cert-detail-mono" data-testid="cs-cert-id">
              {record.certificateId}
            </dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("certificateStudio.issuedAt")}</dt>
            <dd data-testid="cs-issued-at">
              {new Date(record.issuedAt).toISOString()}
            </dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("certificateStudio.anchoredAt")}</dt>
            <dd data-testid="cs-anchored-at">
              {new Date(record.anchoredAt).toISOString()}
            </dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("gateCommand.validatorId")}</dt>
            <dd data-testid="cs-validator">{record.validatorId}</dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("gateCommand.healthScore")}</dt>
            <dd data-testid="cs-health-score">{record.healthScore}</dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("certificateStudio.fabricTransactionId")}</dt>
            <dd className="qs-cert-detail-mono" data-testid="cs-fabric-tx">
              {record.fabricTransactionId}
            </dd>
          </div>
          <div className="qs-cert-detail-field">
            <dt>{t("certificateStudio.certificateHash")}</dt>
            <dd className="qs-cert-detail-mono" data-testid="cs-hash">
              {record.certificateHash}
            </dd>
          </div>
        </dl>
      </section>

      <section
        className="qs-cert-studio-verify"
        aria-labelledby="cert-studio-verify-heading"
      >
        <h2 id="cert-studio-verify-heading">
          {t("certificateStudio.tamperEvidence")}
        </h2>
        <TamperEvidencePanel certificateId={certificateId} />
      </section>

      {record.shiftId && (
        <section
          className="qs-cert-studio-export"
          aria-labelledby="cert-studio-export-heading"
        >
          <h2 id="cert-studio-export-heading">
            {t("certificateStudio.shiftExport")}
          </h2>
          <ExportShiftSummaryButton shiftId={record.shiftId} />
        </section>
      )}
    </div>
  );
}
