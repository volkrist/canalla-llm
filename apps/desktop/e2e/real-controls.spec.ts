import { expect, test } from "@playwright/test";

test("real-provider controls: offline, loading, ready, generating and stop-after-answer", async ({
  page,
}) => {
  let state = "offline",
    available = false,
    active = 0,
    pending = false;
  const stops: Array<{ after_generation: boolean }> = [];
  const preferences = {
    selection: "automatic",
    min_vram_gb: 48,
    max_hourly_price: 1.2,
    session_budget: 3,
    auto_stop_minutes: 10,
    auto_search: false,
    search_interval: 30,
  };
  await page.route("**/llm/status", (route) =>
    route.fulfill({
      json: {
        provider: "llamacpp",
        available,
        state: available
          ? "ready"
          : state === "loading_model"
            ? state
            : "offline",
        model: "orcarouter-qwen38-27b-q5km",
      },
    }),
  );
  await page.route("**/health", (route) =>
    route.fulfill({
      json: { status: "ok", provider: "llamacpp", llm_ready: available },
    }),
  );
  await page.route("**/compute/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/status"))
      return route.fulfill({
        json: {
          configured: true,
          state,
          can_control: true,
          active_generations: active,
          server_now: new Date().toISOString(),
          datacenter: "US-TX-3",
          can_cancel_search: true,
          message: null,
          session: [
            "starting_pod",
            "loading_model",
            "ready",
            "generating",
          ].includes(state)
            ? {
                id: "fixture",
                gpu_type: "NVIDIA L40S TEST",
                gpu_vram_mb: 49152,
                hourly_rate: 1.09,
                billable_seconds: 120,
                estimated_cost: 0.036,
                managed: true,
                pending_stop: pending,
                started_at: new Date().toISOString(),
              }
            : null,
        },
      });
    if (path.endsWith("/preferences"))
      return route.fulfill({ json: preferences });
    if (path.endsWith("/search")) {
      state = "searching";
      return route.fulfill({
        json: {
          quote_id: "test",
          expires_at: new Date(Date.now() + 90000).toISOString(),
          preferences,
          options: [],
          selected_gpu_id: null,
        },
      });
    }
    if (path.endsWith("/search/cancel")) {
      state = "offline";
      return route.fulfill({ json: {} });
    }
    if (path.endsWith("/stop")) {
      stops.push(route.request().postDataJSON());
      pending = true;
      if (!active) {
        state = "stopped";
        available = false;
      }
      return route.fulfill({ json: {} });
    }
    return route.fulfill({
      status: 404,
      json: { detail: "Test route not configured" },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`real-controls-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  const panel = page.locator(".compute-summary");
  await expect(
    panel.getByRole("button", { name: "Запустить AI", exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "Запустить AI", exact: true })
    .click();
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await expect(
    panel.getByRole("button", { name: "Отменить поиск", exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "Отменить поиск", exact: true })
    .click();
  state = "starting_pod";
  await expect(
    panel.getByRole("button", { name: "Остановить AI", exact: true }),
  ).toBeVisible({ timeout: 10000 });
  state = "loading_model";
  await expect(panel.getByText("Загрузка модели", { exact: true })).toBeVisible(
    { timeout: 10000 },
  );
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  state = "ready";
  available = true;
  await expect(panel.getByText("AI готов", { exact: true })).toBeVisible({
    timeout: 10000,
  });
  state = "generating";
  active = 1;
  await expect(
    panel.getByRole("button", { name: "Остановить после ответа", exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await panel
    .getByRole("button", { name: "Остановить после ответа", exact: true })
    .click();
  expect(stops).toHaveLength(0);
  await expect(
    page.getByText("Сейчас AI отвечает.", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Остановить после ответа", exact: true })
    .click();
  expect(stops).toEqual([{ after_generation: true, confirm_external: false }]);
  await expect(
    page.getByText("Остановка после текущего ответа", { exact: true }),
  ).toBeVisible();
  active = 0;
  state = "ready";
  pending = false;
  await expect(
    panel.getByRole("button", { name: "Остановить AI", exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await panel
    .getByRole("button", { name: "Остановить AI", exact: true })
    .click();
  expect(stops[1].after_generation).toBe(false);
  await expect(
    panel.getByRole("button", { name: "Остановить AI", exact: true }),
  ).toBeHidden();
  state = "error";
  await expect(
    panel.getByRole("button", { name: "Повторить", exact: true }),
  ).toBeVisible({ timeout: 10000 });
});
