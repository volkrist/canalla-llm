// Readiness smoke against the real-provider backend with no paid compute.
import { chromium, expect } from "@playwright/test";

const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const page = browser
  .contexts()[0]
  .pages()
  .find((page) => page.url().includes("tauri.localhost"));
if (!page) throw new Error("Native WebView not found");
await page.reload();
await page.getByRole("button", { name: "Регистрация", exact: true }).click();
await page
  .getByLabel("Email", { exact: true })
  .fill(`native-real-offline-${Date.now()}@example.com`);
await page
  .getByLabel("Пароль", { exact: true })
  .fill("native-test-password-123");
await page
  .getByRole("button", { name: "Создать аккаунт", exact: true })
  .click();
await expect(page.getByTestId("ai-connection")).toBeVisible();
await expect(
  page.getByText("orcarouter-qwen38-27b-q5km", { exact: false }).first(),
).toBeVisible();
await expect(
  page.getByRole("button", { name: "Запустить AI", exact: true }),
).toBeVisible();
await expect(
  page.getByRole("button", { name: "Send", exact: true }),
).toBeDisabled();
await expect(page.getByText("MOCK MODE", { exact: true })).toBeHidden();
await page.screenshot({ path: "test-results/native-real-offline.png" });
await page.getByRole("button", { name: "Выйти", exact: true }).click();
console.log(
  "PASS: native real-provider Offline, model label, Start control and disabled generation without GPU",
);
await browser.close();
