import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
const shots = "../../docs/screenshots/0.4";
mkdirSync(shots, { recursive: true });

/** The personal sections live inside the Settings dialog: the tab switches the section there. */
async function openSection(page: Page, section: string) {
  const settings = page.locator(".settings-dialog").first();
  if (!(await settings.isVisible().catch(() => false))) {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
  }
  await settings.getByRole("button", { name: section, exact: true }).click();
}

/** The sections render in place, so the dialog is Settings itself, not a panel over it. */
async function closeSection(page: Page) {
  await page.getByRole("button", { name: "Закрыть настройки" }).click();
}

test("personal memory, projects, source approval, context and real WebSocket", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      "alex-settings",
      JSON.stringify({ technicalDetails: true }),
    ),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`personal-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await openSection(page, "Профиль");
  const dialog = page.locator(".settings-dialog").first();
  await expect(page.locator(".personal-embedded").first()).toBeVisible();
  await dialog.getByLabel("Имя", { exact: true }).fill("Alex");
  await dialog
    .getByLabel("Custom instructions", { exact: true })
    .fill("Отвечай кратко по-русски");
  await dialog
    .getByRole("button", { name: "Сохранить профиль", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Сохранить профиль", exact: true }),
  ).toBeEnabled();
  await page.screenshot({ path: `${shots}/profile.png` });
  await closeSection(page);
  await openSection(page, "Пользователи");
  await expect(dialog).toContainText("Alex");
  await expect(dialog).toContainText("Online");
  await page.screenshot({ path: `${shots}/users-online.png` });
  await closeSection(page);
  await openSection(page, "Проекты");
  await dialog
    .getByRole("button", { name: "Создать проект", exact: true })
    .click();
  await dialog.getByLabel("Название проекта").fill("Alex LLM");
  await dialog.getByLabel("Описание проекта").fill("FastAPI + Tauri desktop");
  await dialog
    .getByRole("button", { name: "Сохранить проект", exact: true })
    .click();
  await expect(dialog.locator(".personal-card")).toContainText("Alex LLM");
  await page.screenshot({ path: `${shots}/projects.png` });
  await closeSection(page);
  await openSection(page, "Память");
  await dialog
    .getByRole("button", { name: "Добавить память", exact: true })
    .click();
  await dialog
    .getByLabel("Содержание памяти")
    .fill("Alex LLM backend использует FastAPI");
  await dialog.getByLabel("Категория", { exact: true }).selectOption("project");
  await dialog.getByLabel("Проект памяти").selectOption({ label: "Alex LLM" });
  await page.screenshot({ path: `${shots}/add-memory.png` });
  await dialog
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await expect(dialog.locator(".personal-card")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Закрепить", exact: true }).click();
  await expect(
    dialog.getByRole("button", { name: "Открепить", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Изменить", exact: true }).click();
  await dialog
    .getByLabel("Содержание памяти")
    .fill("Alex LLM backend использует FastAPI и JWT");
  await page.screenshot({ path: `${shots}/edit-memory.png` });
  await dialog
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await dialog.getByLabel("Поиск памяти").fill("FastAPI");
  await dialog.getByLabel("Фильтр категории").selectOption("project");
  await expect(dialog.locator(".personal-card")).toHaveCount(1);
  await page.screenshot({ path: `${shots}/memory-list.png` });
  await closeSection(page);
  await page
    .getByRole("textbox", { name: "Сообщение" })
    .fill("Привет, Alex LLM");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByLabel("Проект чата")).toBeVisible();
  // Presence lives in the sections themselves; the chat screen keeps only the compact bars.
  await openSection(page, "Пользователи");
  await expect(dialog).toContainText(/Использует AI|Online/);
  await page.screenshot({ path: `${shots}/using-ai.png` });
  await closeSection(page);
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Проект чата").selectOption({ label: "Alex LLM" });
  await page.screenshot({ path: `${shots}/chat-project-picker.png` });
  await page
    .getByRole("button", { name: "Предпросмотр контекста", exact: true })
    .click();
  const preview = page.locator(".settings-dialog").first();
  await expect(preview).toContainText("FastAPI и JWT");
  await page.screenshot({ path: `${shots}/context-preview.png` });
  await preview.getByRole("button", { name: "Закрыть", exact: true }).click();
  await page
    .getByRole("button", { name: "Запомнить", exact: true })
    .first()
    .click();
  await expect(dialog.getByLabel("Содержание памяти")).toHaveValue(
    "Привет, Alex LLM",
  );
  await dialog
    .getByLabel("Содержание памяти")
    .fill("Предпочитает русский язык");
  await dialog
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await dialog.getByLabel("Поиск памяти").fill("");
  await dialog.getByLabel("Фильтр категории").selectOption("");
  await expect(dialog.locator(".personal-card")).toHaveCount(2);
  await dialog
    .getByRole("button", { name: "Удалить", exact: true })
    .first()
    .click();
  await expect(dialog.getByRole("alertdialog")).toBeVisible();
  await dialog
    .getByRole("button", { name: "Да, удалить память", exact: true })
    .click();
  await expect(dialog.locator(".personal-card")).toHaveCount(1);
});

test("presence unavailable and reconnect, idle/offline display, logout closes socket", async ({
  page,
}) => {
  let wire: import("@playwright/test").WebSocketRoute | undefined;
  let connects = 0,
    closed = false;
  await page.routeWebSocket("**/ws/presence?*", (ws) => {
    wire = ws;
    connects++;
    ws.onClose(() => {
      closed = true;
    });
    ws.send(
      JSON.stringify({
        type: "snapshot",
        self_key: "self",
        users: [
          {
            key: "self",
            display_name: "Alex",
            status: "online",
            using_ai: false,
            last_seen: new Date().toISOString(),
          },
          {
            key: "other",
            display_name: "Regina",
            status: "idle",
            using_ai: false,
            last_seen: new Date(Date.now() - 360000).toISOString(),
          },
        ],
      }),
    );
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`presence-ui-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await openSection(page, "Пользователи");
  const dialog = page.locator(".settings-dialog").first();
  await expect(dialog).toContainText("Idle");
  await expect(dialog).toContainText("6 мин назад");
  await page.screenshot({ path: `${shots}/users-idle-test-fixture.png` });
  wire!.send(
    JSON.stringify({
      type: "user_offline",
      user: {
        key: "other",
        display_name: "Regina",
        status: "offline",
        using_ai: false,
        last_seen: new Date(Date.now() - 840000).toISOString(),
      },
    }),
  );
  await expect(dialog).toContainText("Offline");
  await page.screenshot({ path: `${shots}/users-offline-test-fixture.png` });
  wire!.close();
  await expect(dialog).toContainText("Presence unavailable");
  await expect.poll(() => connects).toBeGreaterThan(1);
  await expect(dialog).not.toContainText("Presence unavailable");
  await closeSection(page);
  closed = false;
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect.poll(() => closed).toBe(true);
});
