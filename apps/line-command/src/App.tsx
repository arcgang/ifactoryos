import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { LanguageToggle } from "./components/LanguageToggle";
import { ForbiddenScreen } from "./components/ForbiddenScreen";
import { AccessRestrictedToast } from "./components/AccessRestrictedToast";
import { ARWorkbenchPage } from "./components/ARWorkbenchPage";
import { CommandCenterPage } from "./components/CommandCenterPage";
import { TorqueOutOfToleranceToast, type TorqueOotPayload } from "./components/TorqueOutOfToleranceToast";
import { useRbac } from "./hooks/useRbac";

// ---------------------------------------------------------------------------
// Route definitions
// ---------------------------------------------------------------------------

type Route =
  | "command-center"
  | "station-detail"
  | "andon-log"
  | "handover-builder"
  | "ar-workbench"
  | "settings";

/** Routes that Assembly Associates may not access. */
const ASSOCIATE_BLOCKED_ROUTES: Route[] = [
  "command-center",
  "station-detail",
  "handover-builder",
];

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export function App() {
  const { t } = useTranslation();
  const { role, userId, loading } = useRbac();

  const [currentRoute, setCurrentRoute] = useState<Route>("command-center");
  const [showRestrictedToast, setShowRestrictedToast] = useState(false);
  const [forbiddenResource, setForbiddenResource] = useState<string | null>(null);
  const [torqueOotAlerts, setTorqueOotAlerts] = useState<TorqueOotPayload[]>([]);

  // When role resolves to assembly_associate, force redirect away from blocked routes.
  // Uses functional updater so currentRoute is read from the setter, not the closure,
  // keeping it out of the dep array and avoiding a re-run loop.
  useEffect(() => {
    if (loading) return;
    if (role !== "assembly_associate") return;
    setCurrentRoute((prev) => {
      if (ASSOCIATE_BLOCKED_ROUTES.includes(prev)) {
        setShowRestrictedToast(true);
        return "ar-workbench";
      }
      return prev;
    });
  }, [role, loading]);

  const navigate = useCallback(
    (route: Route) => {
      if (role === "assembly_associate" && ASSOCIATE_BLOCKED_ROUTES.includes(route)) {
        setCurrentRoute("ar-workbench");
        setShowRestrictedToast(true);
        return;
      }
      setCurrentRoute(route);
    },
    [role],
  );

  const handleForbiddenFromApi = useCallback((resource: string) => {
    setForbiddenResource(resource);
  }, []);

  const handleReturnFromForbidden = useCallback(() => {
    setForbiddenResource(null);
  }, []);

  const handleOutOfTolerance = useCallback((payload: TorqueOotPayload) => {
    setTorqueOotAlerts((prev) => {
      // Deduplicate by alertId — edge reconnect may replay the same event
      if (prev.some((a) => a.alertId === payload.alertId)) return prev;
      return [payload, ...prev];
    });
  }, []);

  const dismissOotAlert = useCallback((alertId: string) => {
    setTorqueOotAlerts((prev) => prev.filter((a) => a.alertId !== alertId));
  }, []);

  // Loading skeleton
  if (loading) {
    return (
      <div className="shell" aria-busy="true">
        <div className="rbac-loading" role="status" aria-live="polite">
          <p>{t("rbac.loading")}</p>
        </div>
      </div>
    );
  }

  // Full-page 403 for roles with no Line Command access
  if (role === "forbidden" || forbiddenResource !== null) {
    return (
      <div className="shell">
        <ForbiddenScreen
          resource={forbiddenResource ?? "/line-command"}
          userPrincipal={userId || undefined}
          requiredScope="factory_os:console:line-command"
          onReturn={forbiddenResource !== null ? handleReturnFromForbidden : undefined}
        />
      </div>
    );
  }

  // Determine which nav items are visible for this role
  const isAssociate = role === "assembly_associate";

  return (
    <div className="shell">
      <header className="topbar" role="banner">
        <div className="topbar-logo">{t("app.name")}</div>
        <div className="topbar-app">{t("app.console")}</div>
        <div className="topbar-spacer" />
        <LanguageToggle />
      </header>

      {showRestrictedToast && (
        <AccessRestrictedToast onDismiss={() => setShowRestrictedToast(false)} />
      )}

      <div className="app-body">
        <nav className="sidebar" aria-label={t("nav.section_line")}>
          <div className="nav-section">{t("nav.section_line")}</div>

          {isAssociate ? (
            // Assembly Associate: only AR Workbench is navigable
            <>
              <button
                type="button"
                className={`nav-item${currentRoute === "ar-workbench" ? " active" : ""}`}
                aria-current={currentRoute === "ar-workbench" ? "page" : undefined}
                onClick={() => navigate("ar-workbench")}
              >
                <span className="icon" aria-hidden="true">⬡</span>
                {t("rbac.nav_ar_workbench")}
              </button>
              <div className="nav-item nav-item--locked" aria-disabled="true">
                <span className="icon" aria-hidden="true">◈</span>
                <span className="nav-item-label">{t("rbac.nav_command_center_locked")}</span>
              </div>
              <div className="nav-section">{t("nav.section_shift")}</div>
              <div className="nav-item nav-item--locked" aria-disabled="true">
                <span className="icon" aria-hidden="true">↗</span>
                <span className="nav-item-label">{t("rbac.nav_handover_locked")}</span>
              </div>
            </>
          ) : (
            // Shift Lead: full navigation
            <>
              <button
                type="button"
                className={`nav-item${currentRoute === "command-center" ? " active" : ""}`}
                aria-current={currentRoute === "command-center" ? "page" : undefined}
                onClick={() => navigate("command-center")}
              >
                <span className="icon" aria-hidden="true">◈</span>
                {t("nav.command_center")}
              </button>
              <button
                type="button"
                className={`nav-item${currentRoute === "station-detail" ? " active" : ""}`}
                aria-current={currentRoute === "station-detail" ? "page" : undefined}
                onClick={() => navigate("station-detail")}
              >
                <span className="icon" aria-hidden="true">◉</span>
                {t("nav.station_detail")}
              </button>
              <button
                type="button"
                className={`nav-item${currentRoute === "andon-log" ? " active" : ""}`}
                aria-current={currentRoute === "andon-log" ? "page" : undefined}
                onClick={() => navigate("andon-log")}
              >
                <span className="icon" aria-hidden="true">⬡</span>
                {t("nav.andon_log")}
              </button>
              <div className="nav-section">{t("nav.section_shift")}</div>
              <button
                type="button"
                className={`nav-item${currentRoute === "handover-builder" ? " active" : ""}`}
                aria-current={currentRoute === "handover-builder" ? "page" : undefined}
                onClick={() => navigate("handover-builder")}
              >
                <span className="icon" aria-hidden="true">↗</span>
                {t("nav.handover_builder")}
              </button>
            </>
          )}

          <button
            type="button"
            className={`nav-item${currentRoute === "settings" ? " active" : ""}`}
            aria-current={currentRoute === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <span className="icon" aria-hidden="true">⚙</span>
            {t("nav.settings")}
          </button>
        </nav>

        <main className="main-content" aria-label={t("command_center.title")}>
          {currentRoute === "ar-workbench" && (
            <ARWorkbenchPage
              vin="WBADT43453G PLACEHOLDER"
              userId={userId}
              onForbidden={handleForbiddenFromApi}
              onOutOfTolerance={handleOutOfTolerance}
            />
          )}

          {currentRoute === "command-center" && !isAssociate && (
            <>
              {torqueOotAlerts.map((payload) => (
                <TorqueOutOfToleranceToast
                  key={payload.alertId}
                  payload={payload}
                  onAcknowledge={() => dismissOotAlert(payload.alertId)}
                />
              ))}
              <CommandCenterPage />
            </>
          )}

          {currentRoute === "station-detail" && !isAssociate && (
            <div className="placeholder-view">
              <h2>{t("nav.station_detail")}</h2>
            </div>
          )}

          {currentRoute === "andon-log" && (
            <div className="placeholder-view">
              <h2>{t("nav.andon_log")}</h2>
            </div>
          )}

          {currentRoute === "handover-builder" && !isAssociate && (
            <div className="placeholder-view">
              <h2>{t("nav.handover_builder")}</h2>
            </div>
          )}

          {currentRoute === "settings" && (
            <div className="placeholder-view">
              <h2>{t("settings.title")}</h2>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
