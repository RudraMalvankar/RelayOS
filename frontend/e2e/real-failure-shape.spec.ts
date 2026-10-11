// MOCKED API: shape the real orchestrator returns when no model/credentials exist
// (observed manually: state=failed_retryable, answer="", error_code=provider_error).
import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8006";

test("failed_retryable result with empty answer shows the error, not 'Waiting'", async ({ page }) => {
  const result = { task_id: "r1", answer: "", state: "failed_retryable", route_taken: "fireworks", fireworks_tokens: 0,
    error_code: "provider_error", error_message: "RetryError ConnectError" };
  await page.route(`${API}/api/tasks`, (r) => r.fulfill({ status: 202, json: { task_id: "r1", status: "queued", created_at: 0, updated_at: 0 } }));
  await page.route(`${API}/api/tasks/r1`, (r) => r.fulfill({ json: { task_id: "r1", status: "failed_retryable", result, created_at: 0, updated_at: 1 } }));
  await page.goto("/");
  await page.getByRole("textbox").fill("x");
  await page.getByRole("button", { name: /Run task/ }).click();
  await expect(page.locator(".state")).toHaveText("failed_retryable");
  await expect(page.getByText("Waiting for an execution result…")).toHaveCount(0);
  await expect(page.locator(".result-body")).toContainText("provider_error");
  await expect(page.getByRole("button", { name: /Run task/ })).toBeEnabled();
});
