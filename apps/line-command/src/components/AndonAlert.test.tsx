import { render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { AndonAlert, type AndonAlertContent } from "./AndonAlert";

const BILINGUAL_CONTENT: AndonAlertContent = {
  title: {
    "de-DE": "Prädikativer Andon — Elektrodenverschleiß",
    "en-GB": "Predictive Andon — Electrode Wear",
  },
  body: {
    "de-DE": "Ausfall in 14 Minuten. Sofort Wartung beauftragen.",
    "en-GB": "Failure in 14 minutes. Dispatch maintenance immediately.",
  },
  source: {
    "de-DE": "Heute · 06:02 Uhr",
    "en-GB": "Today · 06:02",
  },
};

const MISSING_EN_CONTENT: AndonAlertContent = {
  title: {
    "de-DE": "Nur Deutsch Titel",
    "en-GB": "",
  },
  body: {
    "de-DE": "Nur Deutsch Inhalt",
    "en-GB": "",
  },
};

function renderAlert(content: AndonAlertContent, variant: "error" | "warning" = "warning") {
  return render(
    <I18nextProvider i18n={i18n}>
      <AndonAlert content={content} variant={variant} />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
});

describe("AndonAlert — AC-1 locale rendering", () => {
  it("renders English title when locale is en-GB and both translations present", () => {
    renderAlert(BILINGUAL_CONTENT);
    expect(screen.getByText("Predictive Andon — Electrode Wear")).toBeTruthy();
  });

  it("renders German title when locale is de-DE and both translations present", async () => {
    await i18n.changeLanguage("de-DE");
    renderAlert(BILINGUAL_CONTENT);
    expect(screen.getByText("Prädikativer Andon — Elektrodenverschleiß")).toBeTruthy();
  });
});

describe("AndonAlert — AC-3 translation gate", () => {
  it("renders 'Translation pending' placeholder when en-GB body is absent", () => {
    renderAlert(MISSING_EN_CONTENT);
    expect(screen.getAllByText(i18n.t("andon.translation_pending")).length).toBeGreaterThanOrEqual(1);
  });

  it("does NOT render the German-only title when en-GB translation is absent", () => {
    renderAlert(MISSING_EN_CONTENT);
    expect(screen.queryByText("Nur Deutsch Titel")).toBeNull();
  });
});
