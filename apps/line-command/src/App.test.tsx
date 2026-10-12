import { render, screen, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach } from "vitest";
import i18n from "./i18n";
import { App } from "./App";

function renderApp() {
  return render(
    <I18nextProvider i18n={i18n}>
      <App />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
});

describe("App — AC-1 browser language detection", () => {
  it("renders English nav labels when locale is en-GB", () => {
    renderApp();
    expect(screen.getByText("Command Centre")).toBeTruthy();
    expect(screen.getByText("Settings")).toBeTruthy();
  });

  it("renders German nav labels when locale is de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderApp();
    expect(screen.getByText("Leitstand")).toBeTruthy();
    expect(screen.getByText("Einstellungen")).toBeTruthy();
  });
});

describe("App — AC-2 language switch without reload", () => {
  it("re-renders German labels after language switches to de-DE without unmounting", async () => {
    const { rerender } = renderApp();
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    rerender(
      <I18nextProvider i18n={i18n}>
        <App />
      </I18nextProvider>,
    );
    expect(screen.getByText("Leitstand")).toBeTruthy();
  });

  it("re-renders English labels after language switches to en-GB without unmounting", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    const { rerender } = renderApp();
    await act(async () => { await i18n.changeLanguage("en-GB"); });
    rerender(
      <I18nextProvider i18n={i18n}>
        <App />
      </I18nextProvider>,
    );
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });
});
