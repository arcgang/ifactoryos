import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "./components/LanguageSwitcher";

export function App() {
  const { t } = useTranslation();

  return (
    <main>
      <h1>{t("topbar.appName")}</h1>
      <LanguageSwitcher />
      <p>{t("nav.sequenceBuilder")}</p>
    </main>
  );
}
