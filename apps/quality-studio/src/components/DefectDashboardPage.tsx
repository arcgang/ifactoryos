"use client";

import { useTranslation } from "react-i18next";
import { emitProcessReplayLaunch } from "../instrumentation/adoptionEvents";

export interface DefectRow {
  vin: string;
  defectType: string;
  aiConfidence: number;
  suspectStage: string;
  flaggedAt: string;
  dataCompleteness: number;
  status: "awaiting_root_cause" | "in_review" | "resolved";
}

interface Props {
  rows: DefectRow[];
  onLaunchReplay: (vin: string) => void;
}

function completenessVariant(pct: number): string {
  if (pct >= 95) return "badge-green";
  if (pct >= 80) return "badge-amber";
  return "badge-red";
}

function statusBadge(status: DefectRow["status"], t: (k: string) => string): React.ReactNode {
  if (status === "in_review") {
    return (
      <span className="badge badge-amber" data-testid="status-badge">
        <span className="dot dot-amber" aria-hidden="true" />
        {t("defectDashboard.statusInReview")}
      </span>
    );
  }
  if (status === "awaiting_root_cause") {
    return (
      <span className="badge badge-red" data-testid="status-badge">
        <span className="dot dot-red" aria-hidden="true" />
        {t("defectDashboard.statusAwaitingRootCause")}
      </span>
    );
  }
  return (
    <span className="badge badge-grey" data-testid="status-badge">
      {t("defectDashboard.statusResolved")}
    </span>
  );
}

function confidenceColor(pct: number): string {
  if (pct >= 70) return "var(--red)";
  if (pct >= 50) return "#d97706";
  return "var(--grey)";
}

export function DefectDashboardPage({ rows, onLaunchReplay }: Props) {
  const { t } = useTranslation();

  function handleLaunch(vin: string) {
    emitProcessReplayLaunch(vin);
    onLaunchReplay(vin);
  }

  return (
    <section aria-labelledby="defect-dashboard-heading">
      <div className="page-header">
        <div style={{ flex: 1 }}>
          <div className="breadcrumb">
            Quality Studio / <span>{t("nav.defectDashboard")}</span>
          </div>
          <h1 id="defect-dashboard-heading">{t("defectDashboard.title")}</h1>
        </div>
      </div>

      {rows.length === 0 ? (
        <p role="status" style={{ padding: "16px 20px", color: "var(--grey)" }}>
          {t("defectDashboard.empty")}
        </p>
      ) : (
        <div className="content">
          <div className="card">
            <table aria-label={t("defectDashboard.tableLabel")}>
              <thead>
                <tr>
                  <th scope="col">{t("defectDashboard.colVin")}</th>
                  <th scope="col">{t("defectDashboard.colDefectType")}</th>
                  <th scope="col">{t("defectDashboard.colAiConfidence")}</th>
                  <th scope="col">{t("defectDashboard.colSuspectStage")}</th>
                  <th scope="col">{t("defectDashboard.colFlaggedAt")}</th>
                  <th scope="col">{t("defectDashboard.colDataCompleteness")}</th>
                  <th scope="col">{t("defectDashboard.colStatus")}</th>
                  <th scope="col">{t("defectDashboard.colAction")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.vin}>
                    <td>
                      <strong
                        style={{ fontFamily: "monospace", fontSize: "12px" }}
                        aria-label={`VIN ${row.vin}`}
                      >
                        {row.vin}
                      </strong>
                    </td>
                    <td>
                      <span className="defect-type">{row.defectType}</span>
                    </td>
                    <td>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}
                        aria-label={`${row.aiConfidence}% ${t("defectDashboard.colAiConfidence")}`}
                      >
                        <div
                          style={{
                            flex: 1,
                            height: "6px",
                            background: "var(--border)",
                            borderRadius: "3px",
                            overflow: "hidden",
                            minWidth: "60px",
                          }}
                          role="presentation"
                        >
                          <div
                            style={{
                              height: "100%",
                              width: `${row.aiConfidence}%`,
                              background: confidenceColor(row.aiConfidence),
                              borderRadius: "3px",
                            }}
                          />
                        </div>
                        <span
                          style={{
                            fontSize: "12px",
                            fontWeight: 700,
                            color: confidenceColor(row.aiConfidence),
                          }}
                        >
                          {row.aiConfidence}%
                        </span>
                      </div>
                    </td>
                    <td>{row.suspectStage}</td>
                    <td>
                      <time>{row.flaggedAt}</time>
                    </td>
                    <td>
                      <span
                        className={`badge ${completenessVariant(row.dataCompleteness)}`}
                        aria-label={`${row.dataCompleteness}% ${t("processReplay.completeness")}`}
                      >
                        {row.dataCompleteness}%
                      </span>
                    </td>
                    <td>{statusBadge(row.status, t)}</td>
                    <td>
                      {row.status === "resolved" ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => handleLaunch(row.vin)}
                          aria-label={`${t("defectDashboard.viewReplay")} ${row.vin}`}
                        >
                          {t("defectDashboard.viewReplay")}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => handleLaunch(row.vin)}
                          aria-label={`${t("processReplay.launch")} ${row.vin}`}
                          data-testid={`launch-replay-${row.vin}`}
                        >
                          {t("processReplay.launch")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
