import { useTranslation } from "react-i18next";

interface Props {
  /** The path or resource that was denied. */
  resource?: string;
  /** The user's email / UPN to display on the denial page. */
  userPrincipal?: string;
  /** The role assigned to the user. */
  assignedRole?: string;
  /** The scope required to access this console. */
  requiredScope?: string;
  /** The audit event ID returned by the backend. */
  auditEventId?: string;
  /** Called when the user clicks "Return to Authorised Console". */
  onReturn?: () => void;
}

/**
 * Full-page 403 Forbidden screen (US-022, AC1).
 * Rendered when an unauthorised role attempts to access Line Command or any
 * suppressed route within it. Satisfies WCAG 2.1 AA via role="alert" and
 * aria-live="assertive".
 */
export function ForbiddenScreen({
  resource = "/line-command",
  userPrincipal,
  assignedRole,
  requiredScope = "factory_os:console:line-command",
  auditEventId,
  onReturn,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className="forbidden-page" role="alert" aria-live="assertive">
      <div className="forbidden-card">
        <h1 className="forbidden-code" aria-label={t("rbac.forbidden_heading")}>
          403
        </h1>
        <h2 className="forbidden-title">{t("rbac.forbidden_title")}</h2>
        <p className="forbidden-desc">{t("rbac.forbidden_desc")}</p>

        <dl className="forbidden-meta">
          <div className="forbidden-meta-row">
            <dt>{t("rbac.meta_resource")}</dt>
            <dd className="mono">{resource}</dd>
          </div>
          <div className="forbidden-meta-row">
            <dt>{t("rbac.meta_status")}</dt>
            <dd className="forbidden-status-value">403 Forbidden</dd>
          </div>
          {userPrincipal && (
            <div className="forbidden-meta-row">
              <dt>{t("rbac.meta_user")}</dt>
              <dd className="mono">{userPrincipal}</dd>
            </div>
          )}
          {assignedRole && (
            <div className="forbidden-meta-row">
              <dt>{t("rbac.meta_role")}</dt>
              <dd className="mono">{assignedRole}</dd>
            </div>
          )}
          <div className="forbidden-meta-row">
            <dt>{t("rbac.meta_scope")}</dt>
            <dd className="mono">{requiredScope}</dd>
          </div>
        </dl>

        {auditEventId && (
          <div className="forbidden-audit-notice" role="status" aria-live="polite">
            <span aria-hidden="true">⚠</span>
            <span>
              {t("rbac.audit_logged")}{" "}
              <span className="mono">{auditEventId}</span>
            </span>
          </div>
        )}

        {onReturn && (
          <div className="forbidden-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={onReturn}
            >
              {t("rbac.return_button")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
