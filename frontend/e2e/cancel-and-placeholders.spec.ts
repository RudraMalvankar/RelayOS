// Cancel runs against the REAL FastAPI app + FAKE orchestrator ("slow" prompt = 2.5s).
import { expect, test } from "@playwright/test";

test("Cancel stops a running task end-to-end", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".state")).toHaveText("cancelled");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);
  await page.waitForTimeout(3000); // the cancelled task must not later flip to success
  await expect(page.locator(".state")).toHaveText("cancelled");
});

test("Cancel is not offered when idle", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);
});

test("unsupported controls are honestly disabled", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /Automatic/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: /Local/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Cloud/ })).toBeDisabled();
  for (const name of ["Task history", "Benchmarks"]) {
    await expect(page.getByText(name).first()).toHaveAttribute("aria-disabled", "true");
  }
});
