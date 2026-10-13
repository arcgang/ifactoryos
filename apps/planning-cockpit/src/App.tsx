import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { ConstraintRadarPage } from "./components/ConstraintRadarPage";
import { SequenceBuilderPage } from "./components/SequenceBuilderPage";
import { My26WizardPage } from "./components/My26WizardPage";
import { OverrideLogPage } from "./components/OverrideLogPage";
import {
  emitLoginEvent,
  emitSessionEndEvent,
  initSession,
} from "./instrumentation/sessionEvents";

type ActivePage = "sequence-builder" | "constraint-radar" | "my26-wizard" | "override-log";

// Resolve the authenticated user's ID from the stored JWT sub claim.
function resolveUserId(): string {
  try {
    const token = localStorage.getItem("ifos_planning_cockpit_token");
    if (!token) return "anonymous";
    const payload = token.split(".")[1];
    if (!payload) return "anonymous";
    const decoded = JSON.parse(atob(payload)) as { sub?: string };
    return decoded.sub ?? "anonymous";
  } catch {
    return "anonymous";
  }
}

export function App() {
  const { t } = useTranslation();
  const [activePage, setActivePage] = useState<ActivePage>("sequence-builder");

  useEffect(() => {
    const userId = resolveUserId();
    initSession(userId);
    emitLoginEvent(userId);

    const handleUnload = () => emitSessionEndEvent();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") emitSessionEndEvent();
    };

    window.addEventListener("beforeunload", handleUnload);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.removeEventListener("beforeunload", handleUnload);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

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
          <a
            href="#override-log"
            aria-current={activePage === "override-log" ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); setActivePage("override-log"); }}
            data-testid="override-log-nav-link"
          >
            {t("nav.overrideLog")}
          </a>
          <a
            href="#my26-wizard"
            aria-current={activePage === "my26-wizard" ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); setActivePage("my26-wizard"); }}
          >
            {t("nav.my26ConfigWizard")}
          </a>
        </nav>
      </header>
      {activePage === "sequence-builder" && <SequenceBuilderPage />}
      {activePage === "constraint-radar" && <ConstraintRadarPage />}
      {activePage === "override-log" && <OverrideLogPage />}
      {activePage === "my26-wizard" && <My26WizardPage />}
    </div>
  );
}
