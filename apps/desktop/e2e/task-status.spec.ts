import { expect, test, type Page } from "@playwright/test";

/**
 * The screen used to say «Ожидаем первый ответ модели», «Working», «3/3 steps» and «Завершено»
 * at the same time. The task status line is now the single authority: it renders the backend
 * `phase`, and a finished step counter never reads as a finished task.
 */

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

function payload(extra: Record<string, unknown>) {
  return {
    id: "task-status-1",
    title: "Fix the parser",
    status: "VERIFYING",
    chat_id: null,
    workspace: "C:\\work",
    current_step: "verify",
    current_phase: "VERIFYING",
    plan_revision: 1,
    tool_calls_used: 9,
    tool_budget: 40,
    files_changed: 2,
    file_change_budget: 20,
    elapsed_runtime: 272,
    runtime_budget: 1800,
    last_error: null,
    completion_summary: null,
    message: "Working",
    phase: "verifying",
    terminal: false,
    waiting: false,
    current_action: "Run pytest",
    completed_count: 3,
    remaining_count: 0,
    last_progress_at: new Date().toISOString(),
    idle_seconds: 3,
    stalled: false,
    resume_available: false,
    budgets: {
      tools_used: 9,
      tools_max: 40,
      files_changed: 2,
      files_max: 20,
      seconds_used: 272,
      seconds_max: 1800,
    },
    web: {
      searches: 1,
      fetches: 0,
      tor_searches: 0,
      tor_fetches: 0,
      pages_requested: 0,
      pages_opened: 0,
      pages_failed: 0,
      by_origin: { model: 1, server_policy: 0, other: 0 },
    },
    steps: [
      {
        id: "s1",
        title: "Inspect",
        description: "",
        status: "COMPLETED",
        tool_category: "local_fs",
        verification_required: false,
        attempts: 1,
        result_summary: "",
        key: "inspect",
      },
      {
        id: "s2",
        title: "Write",
        description: "",
        status: "COMPLETED",
        tool_category: "local_fs",
        verification_required: false,
        attempts: 1,
        result_summary: "",
        key: "write",
      },
      {
        id: "s3",
        title: "Verify",
        description: "",
        status: "COMPLETED",
        tool_category: "verification",
        verification_required: true,
        attempts: 1,
        result_summary: "",
        key: "verify",
      },
    ],
    ...extra,
  };
}

async function openChatWithTask(page: Page, task: Record<string, unknown>) {
  await register(page, `status-${Date.now()}@example.com`);
  const composer = page.getByRole("textbox", {
    name: "Сообщение",
    exact: true,
  });
  await composer.fill("Привет");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".message.assistant").last()).not.toBeEmpty({
    timeout: 30000,
  });

  await page.route("**/tasks?chat_id=*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([task]),
    }),
  );
  await page.locator(".chat-select").first().click();
  await expect(page.locator(".task-panel")).toBeVisible();
}

test("3/3 steps with verification running is never «Завершено»", async ({
  page,
}) => {
  await openChatWithTask(page, payload({}));
  const panel = page.locator(".task-panel");
  await expect(panel.locator(".task-status-line strong")).toHaveText(
    "Работает",
  );
  await expect(panel.locator(".task-status-detail")).toContainText(
    "Run pytest",
  );
  await expect(panel.locator(".task-status-detail")).not.toContainText(
    "Завершено",
  );
  await expect(panel.locator(".task-actions")).toContainText("Stop");
  await page.screenshot({
    path: "../../artifacts/acceptance-agent-status/status-verifying.png",
    fullPage: false,
  });
});

test("a finished tool family never claims the task is over", async ({
  page,
}) => {
  await openChatWithTask(
    page,
    payload({
      phase: "researching",
      status: "RESEARCHING",
      current_action: "Read sources",
      completed_count: 1,
      remaining_count: 3,
    }),
  );
  const panel = page.locator(".task-panel");
  await expect(panel.locator(".task-status-line strong")).toHaveText(
    "Работает",
  );
  await expect(panel.locator(".task-status-detail")).toContainText(
    "источники найдены, страницы не прочитаны",
  );
});

test("a completed task states what was actually done", async ({ page }) => {
  await openChatWithTask(
    page,
    payload({
      phase: "completed",
      status: "COMPLETED",
      terminal: true,
      completion_summary: "Проект создан, 5 тестов пройдено.",
    }),
  );
  const panel = page.locator(".task-panel");
  await expect(panel.locator(".task-status-line strong")).toHaveText(
    "Завершено",
  );
  await expect(panel.locator(".task-status-detail")).toContainText(
    "5 тестов пройдено",
  );
  await expect(panel.locator(".task-actions")).not.toContainText("Stop");
});

test("a page limit names the limit and the counters", async ({ page }) => {
  await openChatWithTask(
    page,
    payload({
      phase: "researching",
      status: "RESEARCHING",
      limit: {
        code: "page_limit",
        pages: 10,
        max_pages: 10,
        fetches: 3,
        max_fetch: 12,
        searches: 1,
        max_search: 5,
        calls: 4,
        max_calls: 40,
      },
    }),
  );
  const limit = page.locator(".task-panel .task-limit");
  await expect(limit).toBeVisible();
  await expect(limit).toContainText("Лимит страниц: 10 из 10 страниц");
  await expect(limit).toContainText("3 из 12 загрузок");
  await page.screenshot({
    path: "../../artifacts/acceptance-agent-status/status-page-limit.png",
    fullPage: false,
  });
});
