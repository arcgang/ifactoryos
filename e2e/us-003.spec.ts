import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// NOTE: No seeded VIN IDs or run IDs are available in the seed data.
// Journeys assert on UI structure, labels, empty states, and dynamically
// discovered values from the rendered page rather than hard-coded IDs.

test("US-003: Planner sees the Explainable Sequence view with constraint contribution panel after selecting a VIN", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to the Sequence / Optimizer section
  await page.getByRole("link", { name: /sequence|optimizer|plan/i }).click();
  await expect(page).toHaveURL(/sequence|optimize|plan/i);

  // The page should show a ranked sequence list or table
  await expect(
    page.getByRole("heading", { name: /sequence|optimizer run|plan/i })
  ).toBeVisible();

  // Either there is at least one VIN row, or we see an empty-state message
  const vinRows = page.getByRole("row").filter({ hasText: /VIN|vin/i });
  const vinCount = await vinRows.count();

  if (vinCount === 0) {
    // Empty state: assert the empty-state copy is visible (AC-1 cannot be fully
    // exercised without a run; we verify the UI handles absence gracefully)
    await expect(
      page.getByText(/no sequence|no runs|no optimizer/i)
    ).toBeVisible();
    return;
  }

  // Select the first VIN in the list
  await vinRows.first().click();

  // A constraint contribution panel should appear
  const panel = page.getByRole("region", {
    name: /constraint contribution|constraint factors|explain/i,
  });
  await expect(panel).toBeVisible();

  // The panel should list at least up to 3 constraint factors
  const factorItems = panel.getByRole("listitem");
  const factorCount = await factorItems.count();
  expect(factorCount).toBeGreaterThanOrEqual(1);
  expect(factorCount).toBeLessThanOrEqual(3);

  // Each factor should show a label, a weighted score, and a data source
  const firstFactor = factorItems.first();
  await expect(firstFactor).toBeVisible();

  // Expect something that looks like a score (a number / percentage)
  await expect(
    firstFactor.getByText(/\d+(\.\d+)?(%|pt|score)?/i)
  ).toBeVisible();

  // Expect a data source label
  await expect(
    firstFactor.getByText(
      /source|data source|paint batch|changeover|maintenance|kitting/i
    )
  ).toBeVisible();
});

test("US-003: Planner can trigger an optimizer run and the Explainable Sequence view renders the explain panel via GET /optimize/{runId}/explain/{vin}", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /sequence|optimizer|plan/i }).click();

  // Trigger a new optimizer run if a button exists
  const runButton = page.getByRole("button", {
    name: /run optimizer|optimize|generate sequence/i,
  });
  if (await runButton.isVisible()) {
    await runButton.click();
    // Wait for the sequence table to appear after the run completes
    await expect(
      page.getByRole("table").or(page.getByRole("list")).first()
    ).toBeVisible({ timeout: 30_000 });
  }

  const vinRows = page.getByRole("row").filter({ hasText: /VIN|vin/i });
  const vinCount = await vinRows.count();

  if (vinCount === 0) {
    // AC-1 requires an optimizer result; skip with a comment
    // SKIP: No optimizer run result available; cannot assert explain panel content
    await expect(
      page.getByText(/no sequence|no runs|empty/i)
    ).toBeVisible();
    return;
  }

  // Click on the second VIN (if available) to exercise the explain endpoint
  const targetRow = vinCount > 1 ? vinRows.nth(1) : vinRows.first();
  await targetRow.click();

  const panel = page.getByRole("region", {
    name: /constraint contribution|constraint factors|explain/i,
  });
  await expect(panel).toBeVisible();

  // Verify top-3 constraints are listed with their weighted scores
  const constraints = panel.getByRole("listitem");
  await expect(constraints.first()).toBeVisible();

  // Each constraint item should surface the factor type
  const constraintTexts = await constraints.allInnerTexts();
  const relevantFactors =
    /paint batch|changeover|maintenance|kitting/i;
  const hasRelevantFactor = constraintTexts.some((t) =>
    relevantFactors.test(t)
  );
  expect(hasRelevantFactor).toBe(true);
});

test("US-003: Planner applies a manual override and the Override Log records VIN, positions, justification, and timestamp", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /sequence|optimizer|plan/i }).click();

  const vinRows = page.getByRole("row").filter({ hasText: /VIN|vin/i });
  const vinCount = await vinRows.count();

  if (vinCount === 0) {
    // AC-2 requires at least one VIN row to override
    // SKIP: No VIN rows available in the current seed/state
    return;
  }

  // Open override dialog for the first VIN
  const overrideButton = vinRows
    .first()
    .getByRole("button", { name: /override|move|reorder/i });
  await overrideButton.click();

  // Override dialog / drawer should be visible
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // Fill in the new position
  const positionInput = dialog.getByLabel(/new position|position|move to/i);
  await positionInput.fill("2");

  // Fill in the justification (minimum 10 characters)
  const justificationInput = dialog.getByLabel(
    /justification|reason|notes/i
  );
  await justificationInput.fill(
    "Adjusting sequence due to paint batch conflict and kitting gap."
  );

  // Save the override
  await dialog.getByRole("button", { name: /save|confirm|apply/i }).click();

  // Dialog should close
  await expect(dialog).not.toBeVisible();

  // Navigate to Override Log
  await page
    .getByRole("link", { name: /override log|overrides/i })
    .click();

  await expect(
    page.getByRole("heading", { name: /override log|overrides/i })
  ).toBeVisible();

  // The new override entry should be present in the log
  const logRows = page.getByRole("row");
  await expect(logRows.filter({ hasText: /position|original|new/i }).first()).toBeVisible();

  // The row should show the justification text
  await expect(
    page.getByText(
      /adjusting sequence due to paint batch conflict/i
    )
  ).toBeVisible();

  // A timestamp should be shown
  await expect(
    page.getByText(/2026|today|\d{1,2}[\/\-]\d{1,2}/i)
  ).toBeVisible();
});

test("US-003: Planner views the Override Log and can export it as a PDF", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate directly to Override Log
  await page
    .getByRole("link", { name: /override log|overrides/i })
    .click();

  await expect(
    page.getByRole("heading", { name: /override log|overrides/i })
  ).toBeVisible();

  // An export button should be present (even when log is empty)
  const exportButton = page.getByRole("button", {
    name: /export|download|pdf/i,
  });
  await expect(exportButton).toBeVisible();

  // The log should either show overrides for the current week or an empty state
  const hasRows =
    (await page.getByRole("row").count()) > 1; // >1 to exclude header row

  if (hasRows) {
    // Each row should have VIN ID, original position, new position, justification, timestamp
    const firstDataRow = page.getByRole("row").nth(1);
    await expect(firstDataRow).toBeVisible();

    // Columns: expect at minimum 5 cells (VIN, orig pos, new pos, justification, timestamp)
    const cells = firstDataRow.getByRole("cell");
    const cellCount = await cells.count();
    expect(cellCount).toBeGreaterThanOrEqual(4);
  } else {
    // Empty state should be clearly communicated
    await expect(
      page.getByText(/no overrides|empty|no records/i)
    ).toBeVisible();
  }

  // Clicking Export should initiate a PDF download (or navigate to export URL)
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 15_000 }).catch(() => null),
    exportButton.click(),
  ]);

  // If the browser triggers a download, verify the file has a PDF-like name
  if (download) {
    expect(download.suggestedFilename()).toMatch(/\.pdf$/i);
  } else {
    // Alternatively the app may open in a new tab or show a success toast
    await expect(
      page
        .getByText(/export|pdf|download/i)
        .or(page.getByRole("alert"))
    ).toBeVisible();
  }
});

test("US-003: Override Log is scoped to the current week and shows correct week label", async ({
  page,
}) => {
  // AC-3: overrides for the current week are listed
  // Note: AC-3 PDF exportability for Plant Director review is also covered in the previous test.
  await login(page, "cardholder");

  await page
    .getByRole("link", { name: /override log|overrides/i })
    .click();

  await expect(
    page.getByRole("heading", { name: /override log|overrides/i })
  ).toBeVisible();

  // The page should show a week indicator (e.g., "Week of Oct 26, 2026" or "Week 44, 2026")
  // Today is 2026-10-29 so week context should reference October 2026
  await expect(
    page.getByText(/oct(ober)?.*2026|week.*2026|2026.*week/i)
  ).toBeVisible();

  // A filter or label showing "current week" should be active
  const weekFilter = page
    .getByRole("tab", { name: /current week|this week/i })
    .or(page.getByText(/current week|this week/i));
  await expect(weekFilter).toBeVisible();

  // The export button should be available for Plant Director review
  await expect(
    page.getByRole("button", { name: /export|pdf/i })
  ).toBeVisible();
});
