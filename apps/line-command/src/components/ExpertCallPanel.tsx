import { useTranslation } from "react-i18next";
import type { ExpertCallState, HolographicAnnotation } from "../hooks/useExpertCall";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = (seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

interface AnnotationOverlayProps {
  annotations: HolographicAnnotation[];
}

function AnnotationOverlay({ annotations }: AnnotationOverlayProps) {
  const { t } = useTranslation();

  if (annotations.length === 0) return null;

  return (
    <ul
      className="expert-call-annotations"
      aria-label={t("expert_call.annotations_label")}
    >
      {annotations.map((ann) => (
        <li key={ann.annotationId} className="expert-call-annotation-item">
          <span className="expert-call-annotation-type">{ann.annotationType}</span>
          {ann.content.labelText && (
            <span className="expert-call-annotation-text">{ann.content.labelText}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface ExpertCallPanelProps {
  state: ExpertCallState;
  onCallExpert: () => void;
  onEndCall: () => void;
}

export function ExpertCallPanel({ state, onCallExpert, onEndCall }: ExpertCallPanelProps) {
  const { t } = useTranslation();

  // ---------- Idle ----------
  if (state.kind === "idle") {
    return (
      <div className="expert-call-panel expert-call-panel--idle">
        <button
          type="button"
          role="button"
          className="expert-call-btn"
          aria-label={t("expert_call.call_button_label")}
          onClick={onCallExpert}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.8 19.79 19.79 0 01.01 1.18a2 2 0 012-2.18h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 6a16 16 0 006.91 6.91l.6-.6a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 14.92z" />
          </svg>
          {t("expert_call.call_button_text")}
        </button>
      </div>
    );
  }

  // ---------- Connecting ----------
  if (state.kind === "connecting") {
    return (
      <div
        className="expert-call-panel expert-call-panel--connecting"
        role="status"
        aria-live="assertive"
        aria-label={t("expert_call.connecting_label")}
      >
        <div className="expert-call-spinner" aria-hidden="true" />
        <p className="expert-call-connecting-text">{t("expert_call.connecting_text")}</p>
        <p className="expert-call-sla-text">{t("expert_call.sla_hint")}</p>
        <button
          type="button"
          className="expert-call-btn expert-call-btn--cancel"
          aria-label={t("expert_call.cancel_label")}
          onClick={onEndCall}
        >
          {t("expert_call.cancel_text")}
        </button>
      </div>
    );
  }

  // ---------- Active ----------
  if (state.kind === "active") {
    const isVideo = state.callMode === "video";

    return (
      <div
        className={`expert-call-panel expert-call-panel--active expert-call-panel--${state.callMode}`}
        role="status"
        aria-live="assertive"
        aria-label={
          isVideo
            ? t("expert_call.active_video_label")
            : t("expert_call.active_voice_label")
        }
      >
        <div className="expert-call-active-header">
          {isVideo ? (
            <>
              <span className="expert-call-mode-badge expert-call-mode-badge--video">
                {t("expert_call.mode_video")}
              </span>
              <span className="expert-call-no-biometrics">{t("expert_call.no_biometrics_note")}</span>
            </>
          ) : (
            <>
              <span className="expert-call-mode-badge expert-call-mode-badge--voice">
                {t("expert_call.mode_voice")}
              </span>
              <span className="expert-call-voice-note">{t("expert_call.voice_only_note")}</span>
            </>
          )}
          <span className="expert-call-timer" aria-label={t("expert_call.duration_label")}>
            {formatElapsed(state.elapsedSeconds)}
          </span>
        </div>

        {isVideo && <AnnotationOverlay annotations={state.annotations} />}

        <button
          type="button"
          className="expert-call-btn expert-call-btn--end"
          aria-label={t("expert_call.end_call_label")}
          onClick={onEndCall}
        >
          {t("expert_call.end_call_text")}
        </button>
      </div>
    );
  }

  // ---------- Ended ----------
  if (state.kind === "ended") {
    return (
      <div
        className="expert-call-panel expert-call-panel--ended"
        role="status"
        aria-live="assertive"
        aria-label={t("expert_call.ended_label")}
      >
        <p className="expert-call-ended-text">{t("expert_call.ended_text")}</p>
        <p className="expert-call-audit-id">
          {t("expert_call.audit_entry_label")}:{" "}
          <code>{state.auditEntryId}</code>
        </p>
      </div>
    );
  }

  // ---------- Error ----------
  if (state.kind === "error") {
    return (
      <div
        className="expert-call-panel expert-call-panel--error"
        role="alert"
        aria-live="assertive"
      >
        <p className="expert-call-error-text">{state.message}</p>
        <button
          type="button"
          className="expert-call-btn"
          aria-label={t("expert_call.call_button_label")}
          onClick={onCallExpert}
        >
          {t("expert_call.retry_text")}
        </button>
      </div>
    );
  }

  return null;
}
