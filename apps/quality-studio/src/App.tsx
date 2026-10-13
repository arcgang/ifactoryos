import { Suspense, useState } from "react";
import { I18nextProvider, useTranslation } from "react-i18next";
import i18n, {
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "./i18n";
import { useTranslatedAlert } from "./useTranslatedAlert";
import { DefectDashboardPage, type DefectRow } from "./components/DefectDashboardPage";
import { ProcessReplayPage } from "./components/ProcessReplayPage";
import { ClusterAlertPanel } from "./components/ClusterAlertPanel";
import { AlertDetailView } from "./components/AlertDetailView";
import { DefectGenomePage } from "./components/DefectGenomePage";

type Route =
  | { page: "dashboard" }
  | { page: "replay"; vin: string }
  | { page: "alertDetail"; alertId: string };

// Mock OIDC context — in production this comes from the BMW Entra ID provider
const MOCK_USER_ID = "kb-entra-001";
const MOCK_USER_GROUPS = ["GRP-IFOS-PAINTENGINEER-MUC"];
  | { page: "defectGenome" };

function LanguageSwitcher() {
  const { t, i18n: i18nInstance } = useTranslation();
  const current = i18nInstance.language as SupportedLanguage;

  function handleChange(lang: SupportedLanguage) {
    void i18nInstance.changeLanguage(lang);
  }

  return (
    <div role="group" aria-label={t("settings.languageSelect")}>
      {SUPPORTED_LANGUAGES.map((lang) => (
        <button
          key={lang}
          type="button"
          aria-pressed={current === lang}
          onClick={() => handleChange(lang)}
          data-testid={`lang-btn-${lang}`}
        >
          {t(`language.${lang}`)}
        </button>
      ))}
    </div>
  );
}

function DefectClusterAlertDemo() {
  const { t } = useTranslation();
  const title = useTranslatedAlert("alerts.defectCluster.title");
  const body = useTranslatedAlert("alerts.defectCluster.body", {
    clusterId: "DG-0042",
    count: 4,
    rootCause: "Ecoat voltage deviation",
    confidence: 87,
  });

  if (!title.ready || !body.ready) {
    return (
      <div role="alert" aria-live="polite" data-testid="alert-translation-pending">
        {t("alerts.translationPending")}
      </div>
    );
  }

  return (
    <div role="alert" aria-live="assertive" data-testid="alert-defect-cluster">
      <strong>{title.text}</strong>
      <p>{body.text}</p>
    </div>
  );
}

// Sample rows for the dashboard — in production these come from the defect API.
const SAMPLE_ROWS: DefectRow[] = [
  {
    vin: "WBS4E0108RG271034",
    defectType: "Crater",
    aiConfidence: 73,
    suspectStage: "Stage 6 – E-coat",
    flaggedAt: "08:47:12",
    dataCompleteness: 91,
    status: "in_review",
  },
  {
    vin: "WBS4E0106RG270891",
    defectType: "Dust",
    aiConfidence: 58,
    suspectStage: "Stage 9 – Sealer",
    flaggedAt: "07:12:55",
    dataCompleteness: 100,
    status: "awaiting_root_cause",
  },
  {
    vin: "WBS4E0102RG271201",
    defectType: "Orange peel",
    aiConfidence: 81,
    suspectStage: "Booth 3 – Basecoat",
    flaggedAt: "06:34:08",
    dataCompleteness: 100,
    status: "awaiting_root_cause",
  },
];

function QualityStudioShell() {
  const { t } = useTranslation();
  const [route, setRoute] = useState<Route>({ page: "dashboard" });

  function navigate(next: Route) {
    setRoute(next);
  }

  return (
    <main>
      <h1>{t("app.title")}</h1>

      <nav aria-label={t("nav.manufacturing")}>
        <ul>
          <li>{t("nav.arWorkbench")}</li>
          <li>
            <button
              type="button"
              onClick={() => navigate({ page: "dashboard" })}
              aria-current={route.page === "dashboard" ? "page" : undefined}
            >
              {t("nav.defectDashboard")}
            </button>
          </li>
          <li>
            <button
              type="button"
              onClick={() => navigate({ page: "defectGenome" })}
              aria-current={route.page === "defectGenome" ? "page" : undefined}
            >
              {t("defectGenome.breadcrumbParent")}
            </button>
          </li>
          <li>
            <button
              type="button"
              onClick={() =>
                route.page === "replay"
                  ? undefined
                  : navigate({ page: "dashboard" })
              }
              aria-current={route.page === "replay" ? "page" : undefined}
            >
              {t("nav.processReplay")}
            </button>
          </li>
          <li>{t("nav.gateCommand")}</li>
          <li>{t("nav.certificateStudio")}</li>
        </ul>
      </nav>

      <section aria-labelledby="settings-heading">
        <h2 id="settings-heading">{t("settings.title")}</h2>
        <label htmlFor="lang-switcher">{t("settings.language")}</label>
        <div id="lang-switcher">
          <LanguageSwitcher />
        </div>
      </section>

      <section aria-labelledby="alerts-heading">
        <h2 id="alerts-heading">{t("nav.defectDashboard")}</h2>
        <DefectClusterAlertDemo />
      </section>

      {route.page === "dashboard" && (
        <>
          <ClusterAlertPanel
            userGroups={MOCK_USER_GROUPS}
            onOpenAlert={(alertId) => navigate({ page: "alertDetail", alertId })}
          />
          <DefectDashboardPage
            rows={SAMPLE_ROWS}
            onLaunchReplay={(vin) => navigate({ page: "replay", vin })}
          />
        </>
      )}

      {route.page === "alertDetail" && (
        <AlertDetailView
          alertId={route.alertId}
          userId={MOCK_USER_ID}
          userGroups={MOCK_USER_GROUPS}
          onBack={() => navigate({ page: "dashboard" })}
        />
      )}

      {route.page === "replay" && (
        <ProcessReplayPage
          vin={route.vin}
          onBack={() => navigate({ page: "dashboard" })}
        />
      )}

      {route.page === "defectGenome" && (
        <DefectGenomePage />
      )}
    </main>
  );
}

export function App() {
  return (
    <I18nextProvider i18n={i18n}>
      <Suspense fallback={null}>
        <QualityStudioShell />
      </Suspense>
    </I18nextProvider>
  );
}
