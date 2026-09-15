import { test, expect } from "@playwright/test";

test("Embedding lifecycle: visible preparation, real-byte display, cancel, chat and retry", async ({
  page,
  request,
}) => {
  const email = `embedding-${Date.now()}@example.com`;
  const password = "test-password-123";
  await request.post("http://127.0.0.1:8001/auth/register", {
    data: { email, password },
  });
  let state = "NOT_INSTALLED";
  let bytes = 0;
  const value = () => ({
    state,
    ready: state === "READY",
    downloaded_bytes: bytes,
    total_bytes: 1000,
    can_cancel: state === "DOWNLOADING",
    cancel_requested: false,
    error: null,
  });
  await page.route("**/rag/model**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/prepare")) {
      state = "DOWNLOADING";
      bytes = 317;
    }
    if (url.pathname.endsWith("/cancel")) state = "CANCELLED";
    if (url.pathname.endsWith("/retry")) {
      state = "READY";
      bytes = 1000;
    }
    if (url.pathname.endsWith("/events")) {
      await route.fulfill({
        contentType: "text/event-stream",
        body: `event: model\ndata: ${JSON.stringify(value())}\n\n`,
      });
    } else await route.fulfill({ json: value() });
  });
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Войти в Alex LLM", exact: true })
    .click();
  expect(state).toBe("NOT_INSTALLED");
  await page
    .getByRole("button", { name: "Прикрепить файл", exact: true })
    .click();
  await expect(
    page.getByText("Поиск по файлам ещё не подготовлен", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Загрузить файлы", { exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Подготовить поиск по файлам", exact: true })
    .click();
  await expect(page.getByRole("progressbar")).toHaveAttribute("value", "317");
  await page
    .getByRole("button", { name: "Отменить подготовку", exact: true })
    .click();
  await expect(
    page.getByText("Подготовка остановлена", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Закрыть файлы", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("Привет без RAG");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 15000 });
  await expect(
    page
      .getByRole("log", { name: "Сообщения" })
      .getByText("Привет без RAG", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Прикрепить файл", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Повторить подготовку", exact: true })
    .click();
  await expect(
    page.getByText("Поиск по файлам готов", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Загрузить файлы", { exact: true }),
  ).toBeEnabled();
});
