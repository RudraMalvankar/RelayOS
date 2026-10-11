// Cancel runs against the REAL FastAPI app + FAKE orchestrator ("slow" prompt = 2.5s).
import { expect, test } from "@playwright/test";

test("Cancel stops a running task end-to-end", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".trace-pane .status")).toHaveText("cancelled");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);
  await page.waitForTimeout(3000); // the cancelled task must not later flip to success
  await expect(page.locator(".trace-pane .status")).toHaveText("cancelled");
});

test("Cancel is not offered when idle", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);
});

test("execution modes and workspace navigation are available", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.getByRole("button", { name: /automatic/i })).toBeEnabled();
  await expect(page.getByRole("button", { name: /local/i })).toBeEnabled();
  await expect(page.getByRole("button", { name: /cloud/i })).toBeEnabled();
  for (const name of ["Task Runs", "Benchmarks"]) {
    await expect(page.getByRole("navigation").getByRole("button", { name })).toBeEnabled();
  }
});
