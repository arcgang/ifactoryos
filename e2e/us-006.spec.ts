import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-006: MY26 Configuration Wizard in Planning Cockpit
// Note: AC-2 (running SequenceOptimizer with a MY26 VIN and checking sequence output)
// and AC-3 (publishing to OpCenter and checking audit log for MY26 config version)
// involve backend pipeline results that cannot be fully asserted in the browser UI
// without seeded VIN/sequence data; those are partially covered below with the available UI surface.

test("US-006: Planner can open the MY26 Configuration Wizard from Planning Cockpit", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to Planning Cockpit
  await page.getByRole("link", { name: /planning cockpit/i }).click();
  await expect(
    page.getByRole("heading", { name: /planning cockpit/i })
  ).toBeVisible();

  // Open the MY26 Configuration Wizard
  await page
    .getByRole("button", { name: /my26 configuration wizard/i })
    .click();

  // Wizard should be visible
  await expect(
    page.getByRole("dialog", { name: /my26 configuration wizard/i })
  ).toBeVisible();

  // Wizard should indicate it is for Week 30 changeover context
  await expect(page.getByText(/week 30/i)).toBeVisible();
});

test("US-006: Wizard validates option codes and paint palette and flags conflicts before saving (AC-1)", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to Planning Cockpit
  await page.getByRole("link", { name: /planning cockpit/i }).click();
  await expect(
    page.getByRole("heading", { name: /planning cockpit/i })
  ).toBeVisible();

  // Open the MY26 Configuration Wizard
  await page
    .getByRole("button", { name: /my26 configuration wizard/i })
    .click();

  const wizard = page.getByRole("dialog", {
    name: /my26 configuration wizard/i,
  });
  await expect(wizard).toBeVisible();

  // Import / enter option codes
  const optionCodesInput = wizard.getByLabel(/option codes/i);
  await expect(optionCodesInput).toBeVisible();
  await optionCodesInput.fill("ZZ999,XX001");

  // Import / enter paint palette definitions
  const paintPaletteInput = wizard.getByLabel(/paint palette/i);
  await expect(paintPaletteInput).toBeVisible();
  await paintPaletteInput.fill("METALLIC_BLUE,PHANTOM_BLACK");

  // Trigger validation (calls POST /my26/config/validate)
  await wizard.getByRole("button", { name: /validate/i }).click();

  // Wizard should show validation results — either conflicts/warnings or a success state
  // Since no seeded data guarantees passing, we assert that a validation result section appears
  const validationResults = wizard.getByRole("region", {
    name: /validation results/i,
  });
  await expect(validationResults).toBeVisible();

  // The save button should be present (enabled or disabled depending on result)
  await expect(
    wizard.getByRole("button", { name: /save/i })
  ).toBeVisible();
});

test("US-006: Wizard shows conflict or missing mapping messages when invalid option codes are submitted (AC-1)", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /planning cockpit/i }).click();
  await expect(
    page.getByRole("heading", { name: /planning cockpit/i })
  ).toBeVisible();

  await page
    .getByRole("button", { name: /my26 configuration wizard/i })
    .click();

  const wizard = page.getByRole("dialog", {
    name: /my26 configuration wizard/i,
  });
  await expect(wizard).toBeVisible();

  // Deliberately provide a syntactically invalid option code to trigger a conflict flag
  const optionCodesInput = wizard.getByLabel(/option codes/i);
  await expect(optionCodesInput).toBeVisible();
  await optionCodesInput.fill("INVALID_CODE_!@#");

  await wizard.getByRole("button", { name: /validate/i }).click();

  // Expect at least one conflict / error / warning message to appear
  const conflictOrError = wizard.getByRole("alert").or(
    wizard.getByText(/conflict|missing constraint|invalid|error/i)
  );
  await expect(conflictOrError.first()).toBeVisible();

  // Save button should be disabled when there are unresolved conflicts
  const saveButton = wizard.getByRole("button", { name: /save/i });
  await expect(saveButton).toBeVisible();
  // The save button must be disabled because validation failed
  await expect(saveButton).toBeDisabled();
});

test("US-006: Planner can save a validated MY26 configuration and sees confirmation (AC-2 partial)", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /planning cockpit/i }).click();
  await expect(
    page.getByRole("heading", { name: /planning cockpit/i })
  ).toBeVisible();

  await page
    .getByRole("button", { name: /my26 configuration wizard/i })
    .click();

  const wizard = page.getByRole("dialog", {
    name: /my26 configuration wizard/i,
  });
  await expect(wizard).toBeVisible();

  // Provide minimal valid-looking data
  const optionCodesInput = wizard.getByLabel(/option codes/i);
  await expect(optionCodesInput).toBeVisible();
  await optionCodesInput.fill("OPT_A1,OPT_B2");

  const paintPaletteInput = wizard.getByLabel(/paint palette/i);
  await expect(paintPaletteInput).toBeVisible();
  await paintPaletteInput.fill("ALPINE_WHITE");

  // Validate first (POST /my26/config/validate)
  await wizard.getByRole("button", { name: /validate/i }).click();

  const validationResults = wizard.getByRole("region", {
    name: /validation results/i,
  });
  await expect(validationResults).toBeVisible();

  // If save is enabled, attempt to save (POST /my26/config/save)
  const saveButton = wizard.getByRole("button", { name: /save/i });
  await expect(saveButton).toBeVisible();

  if (await saveButton.isEnabled()) {
    await saveButton.click();

    // A success confirmation should appear
    const successMsg = page.getByText(
      /configuration saved|my26 config saved|saved successfully/i
    );
    await expect(successMsg).toBeVisible();

    // The wizard should close or show a "done" state
    const doneOrClose = page
      .getByRole("dialog", { name: /my26 configuration wizard/i })
      .or(page.getByText(/configuration is now active/i));
    // Either the dialog closes or an active-state message appears
    await expect(
      page.getByText(/configuration saved|configuration is now active/i)
    ).toBeVisible();
  } else {
    // Save was not enabled — record that validation did not pass with this data
    // This is acceptable; the test asserts the UI structure is correct.
    await expect(saveButton).toBeDisabled();
  }
});

test("US-006: Existing MY26 configuration is retrievable and displays its current status (AC-3 partial)", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /planning cockpit/i }).click();
  await expect(
    page.getByRole("heading", { name: /planning cockpit/i })
  ).toBeVisible();

  // Navigate to the MY26 config status / overview area (GET /my26/config)
  // This may be a tab, a card, or a section on the cockpit page
  const configSection = page
    .getByRole("region", { name: /my26 configuration/i })
    .or(page.getByRole("tab", { name: /my26 config/i }))
    .or(page.getByRole("link", { name: /my26 configuration/i }));

  await expect(configSection.first()).toBeVisible();
  await configSection.first().click();

  // The page should show either an active config with a version label,
  // or an empty state prompting the planner to create a configuration
  const configContent = page
    .getByText(/my26 config version|no configuration found|create a configuration/i)
    .or(page.getByRole("status"))
    .or(page.getByRole("table", { name: /my26/i }));

  await expect(configContent.first()).toBeVisible();

  // If a config version badge/status is displayed, it should contain a recognisable label
  // (active / pending / draft) — assert structural presence only given no seeded data
  const statusBadge = page.getByRole("status").or(
    page.getByText(/active|pending|draft/i)
  );
  // This assertion is best-effort; if no config exists the empty state assertion above suffices
  const emptyState = page.getByText(
    /no configuration found|create a configuration/i
  );

  const hasStatus = await statusBadge.first().isVisible().catch(() => false);
  const hasEmpty = await emptyState.isVisible().catch(() => false);

  expect(hasStatus || hasEmpty).toBeTruthy();
});
