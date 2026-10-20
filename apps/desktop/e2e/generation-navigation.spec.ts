import { expect, test, type Page } from "@playwright/test";

const password = "test-password-123";

async function register(page: Page, email: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(page.getByTestId("ai-connection")).toBeVisible();
}

test("the UI stays navigable while a long answer is streaming", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await register(page, `nav-${Date.now()}@example.com`);

  // The first chat is titled from the message, so the sidebar has its own unambiguous class.
  const newChat = page.locator("button.new-chat");
  const composer = page.getByRole("textbox", {
    name: "Сообщение",
    exact: true,
  });
  const stop = page.getByRole("button", { name: "Stop generation" });
  // The mock provider echoes the prompt in seven-character chunks, so a long prompt is a
  // genuinely long generation. That is what a multi-step agent task looks like to the UI.
  const prompt =
    "Осенний лес медленно меняет цвет, и каждый день приносит новую картину. ".repeat(
      60,
    );
  await composer.fill(prompt);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(stop).toBeVisible();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "демонстрационный",
    { timeout: 30000 },
  );
  await expect(stop).toBeVisible();

  // 1. A new chat opens during generation and keeps it running.
  await newChat.click();
  await expect(
    page.getByRole("heading", { name: "О чём поговорим?" }),
  ).toBeVisible();
  await expect(stop).toBeVisible();

  // 2. The composer accepts text while the first chat still streams, and the draft is per chat.
  await composer.fill("Черновик во время генерации");
  await expect(composer).toHaveValue("Черновик во время генерации");

  // 3. Settings opens and closes without waiting for the stream to finish.
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const dialog = page.locator("dialog.settings-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Settings" })).toBeVisible();
  await dialog.getByRole("button", { name: "Закрыть настройки" }).click();
  await expect(dialog).toBeHidden();
  await expect(stop).toBeVisible();

  // 4. Switching to the streaming chat and back leaves the draft and the stream in place.
  await page.locator(".chat-select").first().click();
  await expect(stop).toBeVisible();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "Осенний лес",
  );
  await newChat.click();
  await expect(composer).toHaveValue("Черновик во время генерации");

  // 5. Stop is available from wherever the user is and really stops the stream.
  await stop.click();
  await expect(stop).toBeHidden({ timeout: 10000 });
  expect(errors).toEqual([]);
});
