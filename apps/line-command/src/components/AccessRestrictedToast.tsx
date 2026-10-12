import { useEffect } from "react";
import { useTranslation } from "react-i18next";

interface Props {
  onDismiss: () => void;
  /** Auto-dismiss timeout in ms (default: 4000). */
  durationMs?: number;
}

/**
 * Transient toast shown when an Assembly Associate navigates to a suppressed
 * route and is redirected to /ar-workbench. Satisfies WCAG 2.1 AA via
 * role="status" and aria-live="polite".
 */
export function AccessRestrictedToast({ onDismiss, durationMs = 4000 }: Props) {
  const { t } = useTranslation();

  useEffect(() => {
    const id = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(id);
  }, [onDismiss, durationMs]);

  return (
    <div
      className="restricted-toast"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span aria-hidden="true">🔒</span>
      <span>{t("rbac.access_restricted_toast")}</span>
      <button
        type="button"
        className="restricted-toast-dismiss"
        aria-label={t("rbac.toast_dismiss_label")}
        onClick={onDismiss}
      >
        ✕
      </button>
    </div>
  );
}
