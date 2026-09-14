import { expect, test } from "@playwright/test";

test("rename, pin, drafts, edit/resend, export and usage", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`manage-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  const composer = page.getByRole("textbox", {
    name: "Сообщение",
    exact: true,
  });
  await composer.fill("Первый вопрос");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeHidden({ timeout: 20000 });
  await expect(page.locator(".message")).toHaveCount(2);
  await composer.fill("Черновик этого диалога");
  await page.getByRole("button", { name: "New Chat" }).click();
  await expect(composer).toHaveValue("");
  await composer.fill("Черновик нового диалога");
  await page
    .getByRole("button", { name: "Первый вопрос", exact: true })
    .click();
  await expect(composer).toHaveValue("Черновик этого диалога");
  await page.getByLabel("Действия Первый вопрос").click();
  await page
    .getByRole("button", { name: "Переименовать", exact: true })
    .click();
  await page.getByLabel("Название диалога").fill("Рабочий диалог");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await page.getByLabel("Действия Рабочий диалог").click();
  await page.getByRole("button", { name: "Закрепить", exact: true }).click();
  await expect(page.getByText("ЗАКРЕПЛЕНО", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByLabel("Действия Рабочий диалог").click();
  await page.getByRole("button", { name: "Экспорт JSON", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.json$/);
  await page.locator(".message.user").hover();
  await page.getByRole("button", { name: "Изменить", exact: true }).click();
  await page.getByLabel("Редактировать сообщение").fill("Исправленный вопрос");
  await page
    .getByRole("button", { name: "Изменить и отправить", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeHidden({ timeout: 20000 });
  await expect(page.locator(".message")).toHaveCount(2);
  await expect(page.locator(".message.user")).toContainText(
    "Исправленный вопрос",
  );
  await page
    .getByRole("button", { name: "Моё использование", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "Всё время", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/usage.png" });
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Чат", exact: true }).click();
  await page.screenshot({ path: "test-results/settings.png" });
  await page.getByLabel("Enter отправляет сообщение").uncheck();
  await page
    .getByRole("button", { name: "Сохранить настройки", exact: true })
    .click();
  await composer.fill("Многострочный");
  await composer.press("Enter");
  await expect(composer).toHaveValue("Многострочный\n");
});

test("test-only compute fixtures: search, price confirmation, startup and ready", async ({
  page,
}) => {
  // All compute requests are intercepted in this test. No production fake-state switch exists.
  let state = "offline",
    creates = 0;
  const preferences = {
    selection: "automatic",
    min_vram_gb: 48,
    max_hourly_price: 1.2,
    session_budget: 3,
    auto_stop_minutes: 10,
    auto_search: false,
    search_interval: 30,
  };
  await page.route("**/compute/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const session = ["starting_pod", "ready"].includes(state)
      ? {
          id: "fixture-session",
          gpu_type: "NVIDIA L40S · TEST",
          hourly_rate: 1.09,
          billable_seconds: 120,
          estimated_cost: 0.036333,
          managed: true,
          pending_stop: false,
          started_at: new Date().toISOString(),
          ready_at: null,
          stop_reason: null,
        }
      : null;
    if (path.endsWith("/status"))
      return route.fulfill({
        json: {
          configured: true,
          state,
          can_control: true,
          message: null,
          quote_id: null,
          session,
          datacenter: "US-TX-3",
          active_generations: 0,
          can_cancel_search: true,
          server_now: new Date().toISOString(),
          next_search_at: null,
        },
      });
    if (path.endsWith("/preferences"))
      return route.fulfill({ json: preferences });
    if (path.endsWith("/search"))
      return route.fulfill({
        json: {
          quote_id: "fixture",
          expires_at: new Date(Date.now() + 90000).toISOString(),
          preferences,
          selected_gpu_id: "l40s",
          options: [
            {
              id: "l40s",
              name: "NVIDIA L40S · TEST",
              vram_gb: 48,
              hourly_rate: 1.09,
              selectable: true,
              availability: "LOW",
              reason: null,
            },
          ],
        },
      });
    if (path.endsWith("/start")) {
      creates++;
      state = "starting_pod";
      return route.fulfill({ json: {} });
    }
    return route.fulfill({
      status: 404,
      json: { detail: "Unmocked test route" },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`gpu-ui-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await page.evaluate(() => {
    const badge = document.createElement("div");
    badge.textContent = "TEST FIXTURE — GPU НЕ ЗАПУСКАЛСЯ";
    badge.style.cssText =
      "position:fixed;bottom:0;left:0;background:#653611;color:white;padding:6px;z-index:999999;font-size:12px";
    document.body.append(badge);
  });
  await expect(page.getByText("AI выключен", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/gpu-offline-test.png" });
  state = "searching";
  await expect(page.getByText("Поиск GPU", { exact: true })).toBeVisible({
    timeout: 10000,
  });
  await page.screenshot({ path: "test-results/gpu-search-test.png" });
  await page.getByRole("button", { name: "AI / Compute", exact: true }).click();
  await page.getByRole("button", { name: "Найти GPU", exact: true }).click();
  await expect(
    page.getByText("Подтверждение платного запуска", { exact: true }),
  ).toBeVisible();
  expect(creates).toBe(0);
  await page.screenshot({ path: "test-results/gpu-confirm-test.png" });
  await page
    .getByRole("button", {
      name: "Подтверждаю запуск за $1.090/час",
      exact: true,
    })
    .click();
  await expect(page.getByText("Запуск Pod", { exact: true })).toBeVisible();
  expect(creates).toBe(1);
  await page.screenshot({ path: "test-results/gpu-starting-test.png" });
  state = "ready";
  await expect(page.getByText("AI готов", { exact: true })).toBeVisible({
    timeout: 10000,
  });
  await page.screenshot({ path: "test-results/gpu-ready-test.png" });
});
