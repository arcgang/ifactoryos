import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { ConstraintRadarPage } from "./components/ConstraintRadarPage";

export function App() {
  const { t } = useTranslation();

  return (
    <div className="page-shell">
      <header role="banner" className="topbar">
        <span
          role="heading"
          aria-level={1}
          className="topbar-logo"
        >
          {t("topbar.appName")}
        </span>
        <div className="topbar-spacer" aria-hidden="true" />
        <LanguageSwitcher />
        <nav className="topbar-nav" aria-label={t("nav.planning")}>
          <a href="#constraint-radar">{t("nav.constraintRadar")}</a>
        </nav>
      </header>
      <ConstraintRadarPage />
    </div>
  );
}
