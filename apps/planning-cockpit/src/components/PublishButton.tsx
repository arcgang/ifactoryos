import { useTranslation } from "react-i18next";

interface PublishButtonProps {
  disabled?: boolean;
  publishing?: boolean;
  onClick: () => void;
}

export function PublishButton({
  disabled = false,
  publishing = false,
  onClick,
}: PublishButtonProps) {
  const { t } = useTranslation();

  return (
    <button
      type="button"
      className={`btn btn-primary publish-btn${disabled || publishing ? " btn-disabled" : ""}`}
      disabled={disabled || publishing}
      onClick={onClick}
      aria-busy={publishing}
      aria-label={
        publishing
          ? t("publish.buttonPublishing")
          : t("publish.buttonLabel")
      }
    >
      {publishing && (
        <span
          className="publish-spinner"
          role="img"
          aria-hidden="true"
        />
      )}
      {publishing ? t("publish.buttonPublishing") : t("publish.buttonLabel")}
    </button>
  );
}
