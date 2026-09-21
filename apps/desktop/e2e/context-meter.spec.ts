import { expect, test } from "@playwright/test";

// The composer's context meter against the real backend and the real endpoint:
// the window comes from the backend setting, the numbers move with the draft, and the
// breakdown names the parts of the model context (docs/context-usage.md).
test("composer context meter shows the served window and its breakdown", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`context-meter-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();

  // No chat yet: the meter has nothing to measure and stays hidden.
  await expect(
    page.getByRole("heading", { name: "О чём поговорим?" }),
  ).toBeVisible();
  await expect(page.locator(".context-meter")).toHaveCount(0);

  const composer = page.getByRole("textbox", {
    name: "Сообщение",
    exact: true,
  });
  await composer.fill("Покажи короткий пример на Python");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "Canalla LLM",
  );

  const meter = page.locator(".context-meter");
  await expect(meter).toBeVisible();
  await expect(meter).toContainText("Context");
  await expect(meter).toContainText("/ 32 768");
  await expect(meter).toContainText(/\d+%/);

  const before = await meter.innerText();
  await composer.fill(
    "Опиши подробнее, что делает этот пример и зачем нужен каждый шаг.",
  );
  await expect
    .poll(async () => meter.innerText(), { timeout: 10000 })
    .not.toBe(before);

  await meter.click();
  const breakdown = page.locator(".context-breakdown");
  await expect(breakdown).toBeVisible();
  await expect(breakdown).toContainText("System");
  await expect(breakdown).toContainText("Current draft");
  await expect(breakdown).toContainText("Total");
  await expect(breakdown).toContainText("32 768");
  await expect(breakdown).toContainText("Оценка по активному контексту");
});
