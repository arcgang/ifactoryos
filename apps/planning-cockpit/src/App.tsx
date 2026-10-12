import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { ConstraintRadarPage } from "./components/ConstraintRadarPage";
import { SequenceBuilderPage } from "./components/SequenceBuilderPage";

type ActivePage = "sequence-builder" | "constraint-radar";

export function App() {
  const { t } = useTranslation();
  const [activePage, setActivePage] = useState<ActivePage>("sequence-builder");

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
          <a
            href="#sequence-builder"
            aria-current={activePage === "sequence-builder" ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); setActivePage("sequence-builder"); }}
          >
            {t("nav.sequenceBuilder")}
          </a>
          <a
            href="#constraint-radar"
            aria-current={activePage === "constraint-radar" ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); setActivePage("constraint-radar"); }}
          >
            {t("nav.constraintRadar")}
          </a>
        </nav>
      </header>
      {activePage === "sequence-builder" && <SequenceBuilderPage />}
      {activePage === "constraint-radar" && <ConstraintRadarPage />}
    </div>
  );
}
