// Browser tests against the REAL FastAPI app with a FAKE orchestrator.
// They verify the frontend <-> API contract and UI states, NOT the AI pipeline.
import { expect, test } from "@playwright/test";

const rgb = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`;
};

test("empty state, health and disabled submit", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ready when you are" })).toBeVisible();
  await expect(page.getByText("API online")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeDisabled();
});

test("submit -> poll -> result shown in response and inspector", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("hello world");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator("pre")).toContainText("FAKE ANSWER for: hello world");
  await expect(page.locator(".state")).toHaveText("success");
  await expect(page.locator(".metric", { hasText: "Route" })).toContainText("python");
});

test("loading state while a slow task runs, then completes", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.getByRole("button", { name: /Running/ })).toBeDisabled();
  await expect(page.getByText("Waiting for an execution result…")).toBeVisible();
  await expect(page.locator("pre")).toContainText("FAKE ANSWER", { timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("failed task shows failed_final and the error text", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("fail please");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".state")).toHaveText("failed_final");
  await expect(page.getByText("scripted failure")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("New task during a running task re-enables Run", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("slow task");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.getByRole("button", { name: /Running/ })).toBeVisible();
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("second");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("brand palette and Inter typography", async ({ page }) => {
  await page.goto("/");
  const s = await page.evaluate(() => {
    const cs = (el: Element) => getComputedStyle(el);
    return {
      bodyColor: cs(document.body).color,
      bodyBg: cs(document.body).backgroundColor,
      font: cs(document.body).fontFamily,
      sendBg: cs(document.querySelector(".send")!).backgroundColor,
      brandBg: cs(document.querySelector(".brand-mark")!).backgroundColor,
    };
  });
  expect(s.bodyColor).toBe(rgb("#050315"));
  expect(s.bodyBg).toBe(rgb("#FBFBFE"));
  expect(s.font).toContain("Inter");
  // Primary #40C6B7 is the brand color for the main actions.
  await page.getByRole("textbox").fill("x");
  // toHaveCSS retries, so the 120ms button background transition does not make this flaky.
  await expect(page.locator(".send")).toHaveCSS("background-color", rgb("#40C6B7"));
  expect(s.brandBg).toBe(rgb("#40C6B7"));
});

test("keyboard: textarea focus ring, Tab reaches Run, Enter on button submits", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab"); // New task button
  await page.keyboard.press("Tab"); // nav is not focusable links (no href) -> textarea expected next
  const ta = page.getByRole("textbox");
  await ta.focus();
  // The visible focus ring is drawn on the surrounding composer panel via :focus-within.
  const ring = await page.locator(".composer").evaluate((e) => getComputedStyle(e).boxShadow);
  expect(ring).not.toBe("none");
  expect(ring).toContain("56, 147, 158"); // brand secondary #38939E
  await ta.fill("keyboard run");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("pre")).toContainText("keyboard run");
});

for (const [name, w, h] of [["mobile", 375, 740], ["tablet", 768, 900], ["desktop", 1280, 800]] as const) {
  test(`responsive ${name}: no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/");
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
  await expect(page.getByRole("button", { name: /New task/ })).toBeVisible();
  for (const name of ["Console", "Task history", "Benchmarks"]) {
    const item = page.getByRole("navigation", { name: "Workspace" }).getByText(name);
    await expect(item).toBeVisible();
    const box = await item.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(390); // fully inside the viewport
  }
  await expect(page.getByText("API online")).toBeVisible();
});
