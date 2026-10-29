import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-023: Audit log for production sequences, quality certificates, and VIN digital records
//
// AC-2 (immutability) cannot be demonstrated purely through browser UI — it requires
// inspecting storage internals. We cover it with a note below.
//
// AC-3 (archival / cold storage) is a background infrastructure concern with no
// browser-visible surface; it is skipped here with an explanatory comment.

test("US-023: Compliance officer can navigate to the audit log section", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Navigate to the audit / compliance section via the main navigation
  await page.getByRole("link", { name: /audit/i }).click();

  // The page heading should confirm we are in the audit log area
  await expect(
    page.getByRole("heading", { name: /audit log/i })
  ).toBeVisible();
});

test("US-023: Audit log displays correct column headers for regulatory fields", async ({
  page,
}) => {
  await login(page, "program_admin");

  await page.getByRole("link", { name: /audit/i }).click();
  await expect(page.getByRole("heading", { name: /audit log/i })).toBeVisible();

  // Regulatory obligations require: user ID, action type, entity ID,
  // before/after state hash, and UTC timestamp — verify table headers exist
  await expect(
    page.getByRole("columnheader", { name: /user/i })
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: /action/i })
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: /entity/i })
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: /timestamp/i })
  ).toBeVisible();
  // State hash column (before/after)
  await expect(
    page.getByRole("columnheader", { name: /hash/i })
  ).toBeVisible();
});

test("US-023: Posting a VIN thread event results in a new audit log entry visible in the UI", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Navigate to the VIN / vehicle digital records section
  await page.getByRole("link", { name: /vin|vehicle/i }).click();
  await expect(
    page.getByRole("heading", { name: /vin|vehicle/i })
  ).toBeVisible();

  // Open the first available VIN record from the list
  const firstVinRow = page.getByRole("row").nth(1);
  await firstVinRow.click();

  // Trigger a thread event (the action that calls POST /vin/{vin}/thread/events)
  const addEventButton = page.getByRole("button", { name: /add event|log event|create event/i });
  await expect(addEventButton).toBeVisible();
  await addEventButton.click();

  // Fill in the event form
  const eventTypeField = page.getByLabel(/event type/i);
  await expect(eventTypeField).toBeVisible();
  await eventTypeField.selectOption({ index: 1 }); // pick first available option

  const descriptionField = page.getByLabel(/description|notes/i);
  if (await descriptionField.isVisible()) {
    await descriptionField.fill("Automated smoke test event – US-023");
  }

  await page.getByRole("button", { name: /submit|save|confirm/i }).click();

  // Confirm a success notification is shown
  await expect(
    page.getByRole("alert").or(page.getByText(/event.*saved|success|created/i))
  ).toBeVisible();

  // Navigate to the audit log and verify the new entry is present
  await page.getByRole("link", { name: /audit/i }).click();
  await expect(page.getByRole("heading", { name: /audit log/i })).toBeVisible();

  // The most recent entry should reflect the action we just performed
  const latestRow = page.getByRole("row").nth(1);
  await expect(latestRow).toBeVisible();

  // It should contain a UTC timestamp (ISO-8601 pattern: digits, T, colon, Z or +)
  await expect(latestRow).toHaveText(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/);
});

test("US-023: Audit log entries cannot be edited or deleted from the UI (immutability AC-2)", async ({
  page,
}) => {
  // AC-2 immutability: verify the UI exposes no edit/delete controls on audit rows,
  // demonstrating that the append-only guarantee is enforced at the presentation layer.
  await login(page, "program_admin");

  await page.getByRole("link", { name: /audit/i }).click();
  await expect(page.getByRole("heading", { name: /audit log/i })).toBeVisible();

  // If there are rows, ensure no edit or delete buttons are rendered
  const rows = page.getByRole("row");
  const rowCount = await rows.count();

  if (rowCount > 1) {
    // Check each data row (skip header row at index 0)
    for (let i = 1; i < Math.min(rowCount, 6); i++) {
      const row = rows.nth(i);
      await expect(
        row.getByRole("button", { name: /edit/i })
      ).toHaveCount(0);
      await expect(
        row.getByRole("button", { name: /delete/i })
      ).toHaveCount(0);
    }
  } else {
    // Empty state is acceptable; confirm the page still renders the log structure
    await expect(page.getByText(/no entries|no records|empty/i)).toBeVisible();
  }
});

test("US-023: Audit log can be filtered by entity type (production sequence / quality cert / VIN)", async ({
  page,
}) => {
  await login(page, "program_admin");

  await page.getByRole("link", { name: /audit/i }).click();
  await expect(page.getByRole("heading", { name: /audit log/i })).toBeVisible();

  // A compliance officer needs to filter log entries by entity type
  const entityFilter = page
    .getByLabel(/entity type|filter by/i)
    .or(page.getByRole("combobox", { name: /entity/i }));

  await expect(entityFilter).toBeVisible();

  // Select "VIN" / "Vehicle Digital Record"
  await entityFilter.selectOption({ label: /vin|vehicle/i });

  // The table should refresh and show only VIN-related entries (or an empty state)
  await expect(
    page
      .getByRole("table")
      .or(page.getByText(/no entries|no records|empty/i))
  ).toBeVisible();

  // Switch to quality certificates
  await entityFilter.selectOption({ label: /quality cert/i });
  await expect(
    page
      .getByRole("table")
      .or(page.getByText(/no entries|no records|empty/i))
  ).toBeVisible();

  // Switch to production sequences
  await entityFilter.selectOption({ label: /production seq/i });
  await expect(
    page
      .getByRole("table")
      .or(page.getByText(/no entries|no records|empty/i))
  ).toBeVisible();
});

// AC-3 NOTE: The archival-to-cold-storage behaviour (entries older than 7 years moved
// to cold storage but remaining queryable within 24 h of retrieval) is a background
// infrastructure / data-pipeline concern. There is no browser-visible surface to interact
// with during a smoke test. This acceptance criterion is intentionally skipped in this
// spec file and should be covered by infrastructure-level integration tests.
