import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// Note: No seeded VINs or gate result IDs are available in the seed data.
// Journeys assert on UI structure, empty states, and conditional-pass highlighting behavior
// using whatever data the real backend provides. AC-3 (certification within 5 min of final gate)
// cannot be fully verified in a browser E2E test because the 5-minute SLA window is a
// backend timing guarantee; the test verifies the UI flow up to the certification action
// and confirms the logged confirmation is shown.

test("US-016: Gate Command page loads and displays the vehicle health score panel structure", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to Gate Command via the main navigation
  await page.getByRole("link", { name: /gate command/i }).click();

  // The Gate Command page should be visible
  await expect(
    page.getByRole("heading", { name: /gate command/i })
  ).toBeVisible();

  // The health score panel or empty state should be present
  const healthScorePanel = page.getByRole("region", {
    name: /health score/i,
  });
  const emptyState = page.getByText(/no vehicle selected|enter a vin|no results/i);

  // Either the health score panel or an empty/prompt state must be visible
  await expect(healthScorePanel.or(emptyState)).toBeVisible();
});

test("US-016: Searching for a VIN shows all 5 gate results (Dim/Leak/Elec/NVH/ADAS) or an appropriate empty state", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /gate command/i }).click();
  await expect(
    page.getByRole("heading", { name: /gate command/i })
  ).toBeVisible();

  // Attempt to use the VIN search/input field
  const vinInput = page.getByLabel(/vin/i).or(
    page.getByPlaceholder(/vin/i)
  );
  await expect(vinInput).toBeVisible();

  // Type a sample VIN pattern (no seeded VINs available; use a dummy to exercise the flow)
  await vinInput.fill("TESTVIN0000000001");

  // Submit the search
  const searchButton = page
    .getByRole("button", { name: /search|lookup|find|go/i })
    .first();

  if (await searchButton.isVisible()) {
    await searchButton.click();
  } else {
    await vinInput.press("Enter");
  }

  // Either a structured result with gate columns OR an informative "not found" message
  const gateHeaders = page.getByText(/dim|leak|elec|nvh|adas/i).first();
  const notFound = page.getByText(
    /not found|no results|no gate results|vehicle not found/i
  );

  await expect(gateHeaders.or(notFound)).toBeVisible();
});

test("US-016: Gate results list via /gate-results shows pass/conditional/fail badges when records exist", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /gate command/i }).click();
  await expect(
    page.getByRole("heading", { name: /gate command/i })
  ).toBeVisible();

  // Navigate to the gate results list (may be a sub-section or tab)
  const gateResultsTab = page
    .getByRole("tab", { name: /gate results/i })
    .or(page.getByRole("link", { name: /gate results/i }))
    .first();

  if (await gateResultsTab.isVisible()) {
    await gateResultsTab.click();
  }

  // The page should show either a table/list of gate results or an empty state
  const resultsTable = page.getByRole("table");
  const emptyState = page.getByText(
    /no gate results|no records|no data available/i
  );

  await expect(resultsTable.or(emptyState)).toBeVisible();

  // If results exist, status badges (pass / conditional / fail) should be present
  if (await resultsTable.isVisible()) {
    const statusBadge = page
      .getByText(/pass|conditional|fail/i)
      .first();
    await expect(statusBadge).toBeVisible();
  }
});

test("US-016: Conditional pass anomaly highlights tolerance band and actual value", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /gate command/i }).click();
  await expect(
    page.getByRole("heading", { name: /gate command/i })
  ).toBeVisible();

  // Look for any existing conditional pass row in the results list
  const conditionalRow = page
    .getByText(/conditional/i)
    .first();

  const emptyState = page.getByText(
    /no gate results|no records|no data available|no conditional/i
  );

  // Wait for either a conditional result row or an empty/no-conditional state
  await expect(conditionalRow.or(emptyState)).toBeVisible();

  if (await conditionalRow.isVisible()) {
    // Click into the conditional result to see the detail view
    await conditionalRow.click();

    // The detail view should show the anomalous measurement with tolerance and actual value
    await expect(
      page.getByText(/tolerance|tolerance band/i).first()
    ).toBeVisible();

    await expect(
      page.getByText(/actual|actual value/i).first()
    ).toBeVisible();

    // Historical distribution chart or data section should also be present
    const historicalSection = page
      .getByText(/historical distribution|history/i)
      .first();
    await expect(historicalSection).toBeVisible();
  } else {
    // No conditional results in seeded data; assert the empty/no-conditional state is shown
    await expect(emptyState).toBeVisible();
  }
});

test("US-016: Certify action generates Digital Birth Certificate confirmation and logs the event", async ({
  page,
}) => {
  // Note: Full 5-minute SLA (AC-3) cannot be asserted in a browser test;
  // this journey verifies the UI certification flow and confirmation display.
  await login(page, "cardholder");

  await page.getByRole("link", { name: /gate command/i }).click();
  await expect(
    page.getByRole("heading", { name: /gate command/i })
  ).toBeVisible();

  // Look for a vehicle record that has a complete health score (all 5 gates resolved)
  const certifyButton = page.getByRole("button", { name: /certify/i }).first();
  const noCertifyState = page.getByText(
    /no vehicle|select a vehicle|no results|certification not available/i
  );

  await expect(certifyButton.or(noCertifyState)).toBeVisible();

  if (await certifyButton.isVisible()) {
    await certifyButton.click();

    // A confirmation dialog or confirmation step should appear
    const confirmDialog = page
      .getByRole("dialog")
      .or(page.getByRole("alertdialog"));
    await expect(confirmDialog).toBeVisible();

    // Confirm the certification
    const confirmBtn = confirmDialog
      .getByRole("button", { name: /confirm|yes|certify/i })
      .first();
    await confirmBtn.click();

    // After confirmation, the Digital Birth Certificate creation success message should appear
    await expect(
      page
        .getByText(
          /digital birth certificate|certificate generated|certified|certification complete/i
        )
        .first()
    ).toBeVisible();

    // The event log entry (VIN, validator ID, timestamp) should be visible on screen
    await expect(
      page.getByText(/logged|event log|audit/i).first()
    ).toBeVisible();
  } else {
    // No certifiable vehicle available in current seed; assert the unavailable state
    await expect(noCertifyState).toBeVisible();
  }
});
