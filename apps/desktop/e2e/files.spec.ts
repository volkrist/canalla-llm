import { expect, test } from "@playwright/test";
import path from "node:path";

test("Files: real CPU indexing, RAG sources, reindex, rename and deletion", async ({
  page,
  request,
}) => {
  const email = `files-${Date.now()}@example.com`;
  const password = "test-password-123";
  const registered = await request.post("http://127.0.0.1:8001/auth/register", {
    data: { email, password },
  });
  const headers = {
    Authorization: `Bearer ${(await registered.json()).access_token}`,
  };
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Войти в Alex LLM", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Прикрепить файл", exact: true })
    .click();
  await page
    .getByLabel("Загрузить файлы", { exact: true })
    .setInputFiles(
      path.resolve("../backend/tests/fixtures/documents/aurora.txt"),
    );
  const row = page.locator(".document-row").filter({ hasText: "aurora.txt" });
  await expect(row).toContainText("Готов", { timeout: 30000 });
  await row
    .getByRole("button", { name: "Переиндексировать", exact: true })
    .click();
  await expect(row).toContainText("Готов", { timeout: 30000 });
  await row
    .getByRole("button", { name: "Переименовать файл", exact: true })
    .click();
  await page
    .getByLabel("Название файла", { exact: true })
    .fill("Аврора: резервные копии");
  await page
    .getByRole("button", { name: "Сохранить название", exact: true })
    .click();
  await expect(page.locator(".document-row")).toContainText(
    "Аврора: резервные копии",
  );
  const docs = await (
    await request.get("http://127.0.0.1:8001/documents", { headers })
  ).json();
  const second = await request.post("http://127.0.0.1:8001/auth/register", {
    data: { email: `second-${email}`, password },
  });
  const otherHeaders = {
    Authorization: `Bearer ${(await second.json()).access_token}`,
  };
  expect(
    (
      await request.get(`http://127.0.0.1:8001/documents/${docs[0].id}`, {
        headers: otherHeaders,
      })
    ).status(),
  ).toBe(404);
  await page
    .getByRole("button", { name: "Закрыть файлы", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("Где проект Аврора хранит резервные копии и каков срок хранения?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".message.assistant")).toContainText(
    "В запрос передан контекст документов",
    { timeout: 25000 },
  );
  await page.getByRole("button", { name: /\[D1\] Аврора/ }).click();
  await expect(
    page.getByRole("dialog", { name: "Источник", exact: true }),
  ).toContainText("17 дней");
  await page.getByRole("button", { name: "Закрыть источник" }).click();
  await page.getByRole("button", { name: "Файлы", exact: true }).click();
  await page
    .locator(".document-row")
    .getByRole("button", { name: "Удалить файл", exact: true })
    .click();
  await expect(
    page.getByRole("alertdialog", { name: "Удаление файла" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Подтвердить удаление файла" })
    .click();
  await expect(page.getByText("Документов нет", { exact: true })).toBeVisible();
  const chats = await (
    await request.get("http://127.0.0.1:8001/chats", { headers })
  ).json();
  const preview = await (
    await request.get(
      `http://127.0.0.1:8001/chats/${chats[0].id}/context-preview?prompt=Aurora`,
      { headers },
    )
  ).json();
  expect(preview.sources).toHaveLength(0);
  await page
    .getByRole("button", { name: "Закрыть файлы", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /Источник был удалён/ }),
  ).toBeVisible();
});

test("Files: project scope and PDF page source", async ({ page, request }) => {
  const email = `pdf-${Date.now()}@example.com`,
    password = "test-password-123";
  const auth = await request.post("http://127.0.0.1:8001/auth/register", {
    data: { email, password },
  });
  const headers = {
    Authorization: `Bearer ${(await auth.json()).access_token}`,
  };
  const project = await (
    await request.post("http://127.0.0.1:8001/projects", {
      headers,
      data: { name: "PDF project" },
    })
  ).json();
  const other = await (
    await request.post("http://127.0.0.1:8001/projects", {
      headers,
      data: { name: "Other project" },
    })
  ).json();
  const chat = await (
    await request.post("http://127.0.0.1:8001/chats", { headers, data: {} })
  ).json();
  await request.patch(`http://127.0.0.1:8001/chats/${chat.id}`, {
    headers,
    data: { project_id: project.id },
  });
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Войти в Alex LLM", exact: true })
    .click();
  await page.getByRole("button", { name: "Файлы", exact: true }).click();
  await page
    .getByLabel("Проект загрузки", { exact: true })
    .selectOption(project.id);
  await page
    .getByLabel("Загрузить файлы", { exact: true })
    .setInputFiles(
      path.resolve("../backend/tests/fixtures/documents/aurora.pdf"),
    );
  await expect(page.locator(".document-row")).toContainText("Готов", {
    timeout: 25000,
  });
  const preview = await (
    await request.get(
      `http://127.0.0.1:8001/chats/${chat.id}/context-preview?prompt=Aurora%20backups%20Seoul`,
      { headers },
    )
  ).json();
  expect(preview.sources[0].page_number).toBe(2);
  await request.patch(`http://127.0.0.1:8001/chats/${chat.id}`, {
    headers,
    data: { project_id: other.id },
  });
  const excluded = await (
    await request.get(
      `http://127.0.0.1:8001/chats/${chat.id}/context-preview?prompt=Aurora`,
      { headers },
    )
  ).json();
  expect(excluded.sources).toHaveLength(0);
  await request.patch(`http://127.0.0.1:8001/chats/${chat.id}`, {
    headers,
    data: { project_id: project.id },
  });
  await request.post(`http://127.0.0.1:8001/chats/${chat.id}/stream`, {
    headers,
    data: { content: "Aurora backups Seoul" },
  });
  await page
    .getByRole("button", { name: "Закрыть файлы", exact: true })
    .click();
  await page.reload();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Войти в Alex LLM", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Aurora backups Seoul", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /\[D1\] aurora.pdf — стр. 2/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /\[D1\] aurora.pdf — стр. 2/ })
    .click();
  await page.screenshot({ path: "test-results/rag-source.png" });
});

test("Files: invalid UTF-8 reports failed and remains retryable", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`failed-files-${Date.now()}@example.com`);
  await page.getByLabel("Пароль", { exact: true }).fill("test-password-123");
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await page.getByRole("button", { name: "Файлы", exact: true }).click();
  await page.getByLabel("Загрузить файлы", { exact: true }).setInputFiles({
    name: "invalid.txt",
    mimeType: "text/plain",
    buffer: Buffer.from([255, 254, 253]),
  });
  const row = page.locator(".document-row");
  await expect(row).toContainText("Ошибка", { timeout: 15000 });
  await expect(row.getByRole("alert")).toContainText(
    "Не удалось извлечь текст",
  );
  await row.getByRole("button", { name: "Переиндексировать" }).click();
  await expect(row).toContainText("Ошибка");
  await page.screenshot({ path: "test-results/rag-files.png" });
});
