// Start the built executable with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=
// --remote-debugging-port=9223, and a local backend on port 8000.
// This script only tests that explicitly launched development instance.
import { chromium, expect } from "@playwright/test";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const context = browser.contexts()[0];
const page = context
  .pages()
  .find((page) => page.url().includes("tauri.localhost"));
if (!page) throw new Error("Alex LLM WebView not found");
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
await expect(
  page.getByRole("heading", { name: "С возвращением" }),
).toBeVisible();
if (new URL(page.url()).protocol !== "https:")
  throw new Error("Native app must use HTTPS origin");
await page.getByRole("button", { name: "Регистрация", exact: true }).click();
await page
  .getByLabel("Email", { exact: true })
  .fill(`native-${Date.now()}@example.com`);
await page
  .getByLabel("Пароль", { exact: true })
  .fill("native-test-password-123");
await page
  .getByRole("button", { name: "Создать аккаунт", exact: true })
  .click();
await expect(page.getByText("Connected", { exact: true })).toBeVisible();
await expect(page.locator(".personal-nav").first()).toContainText("Online", {
  timeout: 15000,
});
await page.getByRole("button", { name: "Пользователи", exact: true }).click();
await expect(page.getByRole("dialog")).toContainText("Online");
await page.screenshot({
  path: "../../docs/screenshots/0.4/native-presence.png",
});
await page
  .getByRole("dialog")
  .getByRole("button", { name: "Закрыть", exact: true })
  .click();
await page
  .getByRole("textbox", { name: "Сообщение", exact: true })
  .fill("Привет из Windows!");
await page.getByRole("button", { name: "Send", exact: true }).click();
await expect(
  page.getByRole("button", { name: "Stop generation" }),
).toBeVisible();
await expect(page.getByRole("button", { name: "Copy code" })).toBeVisible({
  timeout: 15000,
});
await expect(
  page.getByRole("button", { name: "Stop generation" }),
).toBeHidden();
await page.getByRole("button", { name: "Copy code" }).click();
await expect(page.getByRole("button", { name: "Copy code" })).toContainText(
  "Copied",
);
await page.screenshot({ path: "test-results/native.png" });
await page
  .getByRole("textbox", { name: "Сообщение", exact: true })
  .fill("Проверка остановки");
await page.getByRole("button", { name: "Send", exact: true }).click();
await expect(page.locator(".message.assistant").last()).toContainText("Это");
await page.getByRole("button", { name: "Stop generation" }).click();
await expect(
  page.getByRole("button", { name: "Stop generation" }),
).toBeHidden();
await page.getByRole("button", { name: "New Chat" }).click();
await page
  .getByRole("button", { name: "Привет из Windows!", exact: true })
  .click();
await expect(page.locator(".message")).toHaveCount(4);
await page
  .getByRole("button", { name: "Удалить Привет из Windows!", exact: true })
  .click();
await page.getByRole("button", { name: "Удалить", exact: true }).click();
await page.getByRole("button", { name: "Выйти", exact: true }).click();
await expect(
  page.getByRole("heading", { name: "С возвращением" }),
).toBeVisible();
expect(errors).toEqual([]);
console.log(
  "PASS: native WebView2 HTTPS origin, register, streaming, copy, stop, persisted history, delete, logout",
);
await browser.close();
