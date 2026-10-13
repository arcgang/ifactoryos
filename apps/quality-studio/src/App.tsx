import { Suspense, useState } from "react";
import { I18nextProvider, useTranslation } from "react-i18next";
import i18n, {
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "./i18n";
import { useTranslatedAlert } from "./useTranslatedAlert";
import { GateCommandPage } from "./components/GateCommandPage";

type Screen = "shell" | "gate-command";

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

function QualityStudioShell() {
  const { t } = useTranslation();
  const [screen, setScreen] = useState<Screen>("shell");

  if (screen === "gate-command") {
    return (
      <main>
        <h1>{t("app.title")}</h1>
        <button
          type="button"
          onClick={() => setScreen("shell")}
          data-testid="back-to-shell"
        >
          ← {t("nav.gateCommand")}
        </button>
        <Suspense fallback={<p>{t("gateCommand.loading")}</p>}>
          <GateCommandPage vin="WBY73CF080NCH14427" />
        </Suspense>
      </main>
    );
  }

  return (
    <main>
      <h1>{t("app.title")}</h1>

      <nav aria-label={t("nav.manufacturing")}>
        <ul>
          <li>{t("nav.arWorkbench")}</li>
          <li>{t("nav.defectDashboard")}</li>
          <li>{t("nav.processReplay")}</li>
          <li>
            <button
              type="button"
              onClick={() => setScreen("gate-command")}
              data-testid="nav-gate-command"
            >
              {t("nav.gateCommand")}
            </button>
          </li>
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
