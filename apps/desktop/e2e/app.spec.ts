import { expect, test, type Page } from "@playwright/test";

const password = "test-password-123";
async function register(page: Page, email: string) {
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
}

test("register, streamed markdown, copy, stop, history, isolation and delete", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const email = `alice-${Date.now()}@example.com`;
  await page.goto("/");
  await page.screenshot({ path: "test-results/login.png" });
  await register(page, email);
  await page.screenshot({ path: "test-results/welcome.png" });
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("Покажи пример Python");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeVisible();
  const assistant = page.locator(".message.assistant").last();
  await expect(assistant).toContainText("демонстрационный");
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeHidden({ timeout: 15000 });
  await expect(page.getByRole("button", { name: "Copy code" })).toBeVisible();
  await page.getByRole("button", { name: "Copy code" }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("def greet");
  await page.screenshot({ path: "test-results/chat.png" });
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("Останови этот ответ");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "Alex LLM",
  );
  await page.getByRole("button", { name: "Stop generation" }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeHidden();
  const partial = await page.locator(".message.assistant").last().innerText();
  await page.getByRole("button", { name: "New Chat" }).click();
  await expect(
    page.getByRole("heading", { name: "О чём поговорим?" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Покажи пример Python", exact: true })
    .click();
  await expect(page.locator(".message.assistant")).toHaveCount(2);
  expect(await page.locator(".message.assistant").last().innerText()).toBe(
    partial,
  );
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await register(page, `bob-${Date.now()}@example.com`);
  await expect(
    page.getByRole("button", { name: "Покажи пример Python", exact: true }),
  ).toBeHidden();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти в Alex LLM" }).click();
  await page
    .getByRole("button", { name: "Покажи пример Python", exact: true })
    .click();
  await expect(page.locator(".message")).toHaveCount(4);
  await page
    .getByRole("button", { name: "Удалить Покажи пример Python", exact: true })
    .click();
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Покажи пример Python", exact: true }),
  ).toBeHidden();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "С возвращением" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("small window, settings and offline recovery", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 650 });
  await page.goto("/");
  await register(page, `small-${Date.now()}@example.com`);
  await page.getByRole("button", { name: "Открыть меню" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Размер текста").selectOption("17");
  await page.getByRole("button", { name: "Сохранить настройки" }).click();
  await page.locator(".sidebar .mobile-only").click();
  await expect
    .poll(async () => {
      const bounds = await page.locator(".sidebar").boundingBox();
      return bounds ? bounds.x + bounds.width : 0;
    })
    .toBeLessThanOrEqual(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/mobile.png" });
  await page.route("**/health", (route) => route.abort());
  await expect(page.getByText("Offline", { exact: true })).toBeVisible({
    timeout: 18000,
  });
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("Offline test");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  await page.unroute("**/health");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible({
    timeout: 18000,
  });
});
