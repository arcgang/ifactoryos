import { useTranslation } from "react-i18next";
import { LanguageToggle } from "./components/LanguageToggle";
import { AndonAlert, type AndonAlertContent } from "./components/AndonAlert";

const ELECTRODE_WEAR_ALERT: AndonAlertContent = {
  title: {
    "de-DE": "Prädikativer Andon — Elektrodenverschleiß Schweißzelle 7",
    "en-GB": "Predictive Andon — Electrode Wear Welding Cell 7",
  },
  body: {
    "de-DE":
      "Schweißstrom-Kurve zeigt Abweichung von −8 % gegenüber Basis. Prognostizierter Ausfall in 14 Minuten. Sofort Wartung beauftragen.",
    "en-GB":
      "Welding current curve shows deviation of −8% from baseline. Predicted failure in 14 minutes. Dispatch maintenance immediately.",
  },
  source: {
    "de-DE": "Heute · 06:02 Uhr · Quelle: Beckhoff PLC — Schweißlinie 3",
    "en-GB": "Today · 06:02 · Source: Beckhoff PLC — Welding Line 3",
  },
};

const OEE_ALERT: AndonAlertContent = {
  title: {
    "de-DE": "OEE unter Tagesziel — Schweißlinie 2",
    "en-GB": "OEE Below Daily Target — Welding Line 2",
  },
  body: {
    "de-DE":
      "Aktueller OEE 84,1 % · Ziel 87,0 %. Tendenz letzte 30 Min. fallend. Mögliche Ursache: Elektrodenreinigungszyklus überfällig.",
    "en-GB":
      "Current OEE 84.1% · Target 87.0%. Trend last 30 min declining. Possible cause: electrode cleaning cycle overdue.",
  },
  source: {
    "de-DE": "Heute · 05:55 Uhr",
    "en-GB": "Today · 05:55",
  },
};

export function App() {
  const { t } = useTranslation();

  return (
    <div className="shell">
      <header className="topbar" role="banner">
        <div className="topbar-logo">{t("app.name")}</div>
        <div className="topbar-app">{t("app.console")}</div>
        <div className="topbar-spacer" />
        <LanguageToggle />
      </header>

      <div className="app-body">
        <nav className="sidebar" aria-label={t("nav.section_line")}>
          <div className="nav-section">{t("nav.section_line")}</div>
          <div className="nav-item active" aria-current="page">
            <span className="icon" aria-hidden="true">◈</span>
            {t("nav.command_center")}
          </div>
          <div className="nav-item">
            <span className="icon" aria-hidden="true">◉</span>
            {t("nav.station_detail")}
          </div>
          <div className="nav-item">
            <span className="icon" aria-hidden="true">⬡</span>
            {t("nav.andon_log")}
          </div>
          <div className="nav-section">{t("nav.section_shift")}</div>
          <div className="nav-item">
            <span className="icon" aria-hidden="true">↗</span>
            {t("nav.handover_builder")}
          </div>
          <div className="nav-item">
            <span className="icon" aria-hidden="true">⚙</span>
            {t("nav.settings")}
          </div>
        </nav>

        <main className="main-content" aria-label={t("command_center.title")}>
          <AndonAlert
            content={ELECTRODE_WEAR_ALERT}
            variant="error"
            countdown={t("andon.minutes_until_failure", { count: "14" })}
            onDispatchMaintenance={() => undefined}
            onAcknowledge={() => undefined}
          />

          <AndonAlert
            content={OEE_ALERT}
            variant="warning"
            onAcknowledge={() => undefined}
          />
        </main>
      </div>
    </div>
  );
}
