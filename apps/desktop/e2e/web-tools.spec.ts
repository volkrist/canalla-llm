import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import path from "node:path";

async function login(page: Page, request: APIRequestContext) {
  const email =
    "web-" +
    Date.now() +
    "-" +
    Math.random().toString(16).slice(2) +
    "@example.com";
  const password = "test-password-123";
  const response = await request.post("http://127.0.0.1:8001/auth/register", {
    data: { email, password },
  });
  const headers = {
    Authorization: "Bearer " + (await response.json()).access_token,
  };
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Войти в Alex LLM", exact: true })
    .click();
  await expect(page.getByLabel("Web mode", { exact: true })).toHaveValue(
    "auto",
  );
  await expect(page.getByLabel("Computer mode", { exact: true })).toHaveValue(
    "ask",
  );
  return headers;
}
async function send(page: Page, value: string) {
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill(value);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test("Web settings warnings and explicit Browser commands require per-action approval", async ({
  page,
  request,
}) => {
  await login(page, request);
  await send(page, "Привет");
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 15000 });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Web & Tools", exact: true }).click();
  await expect(
    page.getByText("TinyFish API: Not configured", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Tor Search provider not configured/),
  ).toBeVisible();
  await expect(
    page.getByLabel("Computer mode default", { exact: true }),
  ).toHaveValue("ask");
  await expect(
    page.getByLabel("Разрешить платный Agent", { exact: true }),
  ).toBeDisabled();
  await expect(page.getByText(/Бюджет soft\/local/)).toBeVisible();
  await page.getByLabel("Разрешить Browser Advanced", { exact: true }).check();
  await page.getByLabel("Бюджет tool run", { exact: true }).fill("0.30");
  await page
    .getByRole("button", { name: "Сохранить Web & Tools", exact: true })
    .click();
  await expect(
    page.getByText("Настройки Web & Tools сохранены", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Открыть Browser Advanced", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Создать платную Browser-сессию",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Session: fake-browser", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Browser action", { exact: true })
    .selectOption("click");
  await page.getByLabel("Browser selector", { exact: true }).fill("#submit");
  await page
    .getByRole("button", { name: "Выполнить команду", exact: true })
    .click();
  await expect(
    page.getByRole("alertdialog", { name: "Подтвердить внешнее действие" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Выполнить команду", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Выполнить команду", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Разрешить один раз", exact: true })
    .click();
  await expect(
    page.getByText("Typed test operation completed.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop Browser", exact: true }).click();
  await expect(
    page.getByText("Browser остановлен у провайдера.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Закрыть Browser", exact: true })
    .click();
});

test("Web Search + Fetch snapshots, RAG D1 + W1 and isolation", async ({
  page,
  request,
}) => {
  const headers = await login(page, request);
  await page
    .getByRole("button", { name: "Прикрепить файл", exact: true })
    .click();
  await page
    .getByLabel("Загрузить файлы", { exact: true })
    .setInputFiles(
      path.resolve("../backend/tests/fixtures/documents/aurora.txt"),
    );
  await expect(page.locator(".document-row")).toContainText("Готов", {
    timeout: 30000,
  });
  await page
    .getByRole("button", { name: "Закрыть файлы", exact: true })
    .click();
  await page.getByLabel("Web mode", { exact: true }).selectOption("on");
  await expect(
    page.getByRole("button", { name: "Найти в интернете", exact: true }),
  ).toHaveCount(0);
  await send(
    page,
    "Где Аврора хранит резервные копии? Сравни с current Aurora documentation.",
  );
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 20000 });
  await expect(
    page.getByRole("button", { name: /\[D1\] aurora.txt/ }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Web sources").getByText(/\[W1\]/),
  ).toBeVisible();
  await expect(
    page.getByLabel("Web sources").getByText(/\[W2\]/),
  ).toBeVisible();
  const runs = await (
    await request.get("http://127.0.0.1:8001/tools/runs", { headers })
  ).json();
  expect(runs.map((r: { tool_name: string }) => r.tool_name).sort()).toEqual([
    "web_fetch",
    "web_search",
  ]);
  const other = await request.post("http://127.0.0.1:8001/auth/register", {
    data: {
      email: "other-" + Date.now() + "@example.com",
      password: "test-password-123",
    },
  });
  const otherHeaders = {
    Authorization: "Bearer " + (await other.json()).access_token,
  };
  expect(
    (
      await request.get("http://127.0.0.1:8001/tools/runs/" + runs[0].id, {
        headers: otherHeaders,
      })
    ).status(),
  ).toBe(404);
});

test("Planner does not auto-route TinyFish Agent", async ({
  page,
  request,
}) => {
  const headers = await login(page, request);
  await request.put("http://127.0.0.1:8001/tools/preferences", {
    headers,
    data: {
      agent_enabled: true,
      agent_run_budget: 0.25,
      agent_daily_budget: 2,
    },
  });
  await page.getByLabel("Web mode", { exact: true }).selectOption("on");
  await send(page, "Тест Agent действие");
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 15000 });
  await expect(
    page.getByRole("alertdialog", { name: "Подтвердить внешнее действие" }),
  ).toHaveCount(0);
  const runs = await (
    await request.get("http://127.0.0.1:8001/tools/runs", { headers })
  ).json();
  expect(
    runs.filter((row: { tool_name: string }) =>
      ["web_agent_read", "test_agent_action", "browser_start"].includes(
        row.tool_name,
      ),
    ),
  ).toEqual([]);
});

test("Missing TinyFish key degrades gracefully; Web Off makes no new tool calls", async ({
  page,
  request,
}) => {
  const headers = await login(page, request);
  await page
    .getByRole("textbox", { name: "Сообщение", exact: true })
    .fill("missing-key current docs");
  await page
    .getByRole("button", { name: "Найти в интернете", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 15000 });
  await expect(
    page.getByText("TinyFish API не настроен. Чат работает без web.", {
      exact: true,
    }),
  ).toBeVisible();
  const before = await (
    await request.get("http://127.0.0.1:8001/tools/runs", { headers })
  ).json();
  await page.getByLabel("Web mode", { exact: true }).selectOption("off");
  await expect(
    page.getByRole("button", { name: "Найти в интернете", exact: true }),
  ).toHaveCount(0);
  await send(page, "current news");
  await expect(
    page.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeHidden({ timeout: 15000 });
  const after = await (
    await request.get("http://127.0.0.1:8001/tools/runs", { headers })
  ).json();
  expect(after.length).toBe(before.length);
});
