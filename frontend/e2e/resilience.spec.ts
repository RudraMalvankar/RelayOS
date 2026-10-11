// Browser tests with a MOCKED API (page.route) to force error/edge responses.
import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8006";

test("health offline shows 'API offline' and later recovers", async ({ page }) => {
  let up = false;
  await page.route(`${API}/api/health`, (route) =>
    up ? route.fulfill({ json: { status: "ok" } }) : route.abort("connectionrefused"));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await expect(page.locator(".conn").filter({ hasText: "API offline" })).toBeVisible();
  up = true;
  await expect(page.getByText("API online")).toBeVisible({ timeout: 15_000 });
});

test("validation error (422 with array detail) renders readable text", async ({ page }) => {
  await page.route(`${API}/api/tasks`, (route) =>
    route.fulfill({ status: 422, json: { detail: [{ loc: ["body", "prompt"], msg: "String should have at least 1 character", type: "string_too_short" }] } }));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  const alert = page.locator(".alert");
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText("[object Object]");
  await expect(alert).toContainText("at least 1 character");
});

test("413 size error shows the server message", async ({ page }) => {
  await page.route(`${API}/api/tasks`, (route) =>
    route.fulfill({ status: 413, json: { detail: "Prompt exceeds configured size limit" } }));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".alert")).toContainText("Prompt exceeds configured size limit");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("network failure on submit shows an error and re-enables Run", async ({ page }) => {
  await page.route(`${API}/api/tasks`, (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".alert")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});

test("task disappearing while polling (404) does not leave UI stuck", async ({ page }) => {
  await page.route(`${API}/api/tasks`, (route) =>
    route.fulfill({ status: 202, json: { task_id: "lost", status: "queued", created_at: 0, updated_at: 0 } }));
  await page.route(`${API}/api/tasks/lost`, (route) =>
    route.fulfill({ status: 404, json: { detail: "Task not found" } }));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled({ timeout: 6_000 });
  await expect(page.locator(".alert")).toBeVisible();
});

test("polling network failure does not leave UI stuck", async ({ page }) => {
  await page.route(`${API}/api/tasks`, (route) =>
    route.fulfill({ status: 202, json: { task_id: "flaky", status: "queued", created_at: 0, updated_at: 0 } }));
  await page.route(`${API}/api/tasks/flaky`, (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await page.getByRole("button", { name: /New task/ }).click();
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".alert")).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});
