// Browser tests against the REAL FastAPI app with a FAKE orchestrator.
// They verify the frontend <-> API contract and UI states, NOT the AI pipeline.
import { expect, test } from "@playwright/test";

const rgb = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`;
};

test("empty state, health and disabled submit", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.getByText("No result selected", { exact: true })).toBeVisible();
  await expect(page.getByText("API online")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeDisabled();
});

test("submit -> poll -> result shown in response and inspector", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("hello world");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator("pre")).toContainText("FAKE ANSWER for: hello world");
  await expect(page.locator(".trace-pane .status")).toHaveText("success");
  await expect(page.locator(".trace-meta > div", { hasText: "Route" })).toContainText("python");
});

test("loading state while a slow task runs, then completes", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.getByRole("button", { name: /Executing/ })).toBeDisabled();
  await expect(page.getByText("Execution in progress")).toBeVisible();
  await expect(page.locator("pre")).toContainText("FAKE ANSWER", { timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("failed task shows failed_final and the error text", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("fail please");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".trace-pane .status")).toHaveText("failed_final");
  await expect(page.getByText("scripted failure")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("New task during a running task re-enables Run", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.getByRole("button", { name: /Executing/ })).toBeVisible();
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("second");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("current palette and Inter typography", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.locator("body")).toHaveCSS("background-color", rgb("#111214"));
  await expect(page.locator("body")).toHaveCSS("color", rgb("#f0f1f3"));
  expect(await page.locator("body").evaluate(e => getComputedStyle(e).fontFamily)).toContain("Inter");
  await page.getByRole("textbox").fill("x");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("keyboard focus and submission", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  const ta = page.getByRole("textbox");
  await ta.focus();
  await ta.fill("keyboard run");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "automatic", exact: true })).toBeFocused();
  await page.getByRole("button", { name: /Run task/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("pre.answer")).toContainText("keyboard run");
});

for (const [name, w, h] of [["mobile", 375, 740], ["tablet", 768, 900], ["desktop", 1280, 800]] as const) {
  test(`responsive ${name}: no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
    await page.getByRole("textbox").fill("hi");
    await page.getByRole("button", { name: /Run task/ }).click();
    await expect(page.locator("pre")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page.getByRole("button", { name: /Run task/ })).toBeVisible();
  });
}

test("mobile: New task and all navigation items are reachable (no hidden sidebar)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.getByRole("button", { name: /New task/ })).toBeVisible();
  for (const name of ["Playground", "Task Runs", "Benchmarks"]) {
    const item = page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name, exact: true });
    await item.scrollIntoViewIfNeeded();
    await expect(item).toBeVisible();
    const box = await item.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(390); // fully inside the viewport
  }
  await expect(page.getByText("API online")).toBeVisible();
});
