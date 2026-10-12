import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { App } from "./App";
import i18n from "./i18n";

describe("App shell", () => {
  it("renders the Quality Studio heading in en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Quality Studio", level: 1 }),
    ).toBeTruthy();
  });

  it("renders the Quality Studio heading in de-DE (AC-edd6ce81)", async () => {
    await i18n.changeLanguage("de-DE");
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Quality Studio", level: 1 }),
    ).toBeTruthy();
  });

  it("renders German navigation labels when language is de-DE (AC-edd6ce81)", async () => {
    await i18n.changeLanguage("de-DE");
    render(<App />);
    expect(screen.getAllByText("Defekt-Dashboard").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Prozess-Replay")).toBeTruthy();
  });

  it("renders English navigation labels when language is en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    render(<App />);
    expect(screen.getAllByText("Defect Dashboard").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Process Replay")).toBeTruthy();
  });

  it("renders German language toggle buttons", async () => {
    await i18n.changeLanguage("de-DE");
    render(<App />);
    expect(screen.getByTestId("lang-btn-de-DE")).toBeTruthy();
    expect(screen.getByTestId("lang-btn-en-GB")).toBeTruthy();
  });

  it("renders the defect cluster alert when both translations exist (AC-1a65ae08)", async () => {
    await i18n.changeLanguage("en-GB");
    render(<App />);
    expect(screen.getByTestId("alert-defect-cluster")).toBeTruthy();
  });
});
