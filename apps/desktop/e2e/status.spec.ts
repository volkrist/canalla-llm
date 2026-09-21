import { expect, test, type Page } from "@playwright/test";

const password = "test-password-123";

type Chip = Record<string, unknown>;

function chip(state: string, extra: Chip = {}) {
  return {
    state,
    message: `состояние: ${state}`,
    detail_code: null,
    recoverable: false,
    action: null,
    details: {},
    ...extra,
  };
}

function payload({
  chips = {},
  balance = {},
}: { chips?: Record<string, Chip>; balance?: Record<string, unknown> } = {}) {
  return {
    generated_at: new Date().toISOString(),
    subsystems: {
      ai: chip("ready"),
      computer: chip("ready"),
      web: chip("ready"),
      tor: chip("off"),
      memory: chip("ready"),
      ...chips,
    },
    balance: {
      configured: true,
      available: true,
      balance_usd: "10.00",
      account_spend_per_hr: "0.12",
      low: false,
      low_threshold_usd: "5.00",
      stale: false,
      error_code: null,
      message: null,
      fetched_at: new Date().toISOString(),
      last_success_at: new Date().toISOString(),
      refresh_seconds: 5,
      shared_account: true,
      read_only: true,
      active_session: null,
      ...balance,
    },
  };
}

async function register(page: Page, email: string) {
  await page.getByRole("button", { name: "Регистрация", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
}

test("five chips, live shared balance and a stale value that never becomes zero", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let calls = 0;
  let balance = "10.00";
  let aiState = "starting";
  let stale = false;
  await page.route(
    (url) => url.pathname === "/status",
    async (route) => {
      calls += 1;
      await route.fulfill({
        json: payload({
          chips: { ai: chip(aiState, { details: { provider: "llamacpp" } }) },
          balance: { balance_usd: balance, stale },
        }),
      });
    },
  );
  await page.goto("/");
  await register(page, `status-${Date.now()}@example.com`);

  // Five user-facing chips, each with readable text.
  for (const title of ["AI", "Computer", "Web", "Tor", "Memory"]) {
    await expect(
      page.locator(".status-chip-name", { hasText: title }),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("button", { name: "AI: Проверяем…" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tor: Выключено" }),
  ).toBeVisible();
  await expect(page.getByTestId("runpod-balance")).toContainText("$10.00");
  expect(await page.locator("body").innerText()).not.toContain("$0.00");

  // Starting -> Ready without a reload.
  aiState = "ready";
  await expect(page.getByRole("button", { name: "AI: Готово" })).toBeVisible({
    timeout: 12000,
  });

  // A new upstream value replaces the old one automatically.
  balance = "9.95";
  await expect(page.getByTestId("runpod-balance")).toContainText("$9.95", {
    timeout: 12000,
  });
  expect(await page.locator("body").innerText()).not.toContain("$10.00");

  // A failed refresh keeps the last known balance and marks it stale.
  stale = true;
  await expect(page.getByTestId("runpod-balance")).toContainText("$9.95", {
    timeout: 12000,
  });
  const text = await page.locator("body").innerText();
  expect(text).not.toContain("$0.00");
  expect(calls).toBeLessThanOrEqual(4); // one timer, no duplicate polling
  expect(errors).toEqual([]);
});

test("an AI failure explains itself and retries the authoritative snapshot", async ({
  page,
}) => {
  let calls = 0;
  let failing = true;
  await page.route(
    (url) => url.pathname === "/status",
    async (route) => {
      calls += 1;
      await route.fulfill({
        json: payload(
          failing
            ? {
                chips: {
                  ai: chip("error", {
                    detail_code: "startup_timeout",
                    message:
                      "Превышено время запуска AI. Compute освобождается.",
                    action: "retry",
                    recoverable: true,
                  }),
                },
              }
            : {},
        ),
      });
    },
  );
  await page.goto("/");
  await register(page, `recovery-${Date.now()}@example.com`);

  const card = page.getByTestId("status-recovery");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Запуск модели");
  await expect(card).toContainText("Превышено время запуска AI.");
  await expect(page.getByRole("button", { name: "AI: Ошибка" })).toBeVisible();
  // The provider key is never part of the payload and never reaches the DOM.
  expect(await page.locator("body").innerText()).not.toContain("api_key");

  const before = calls;
  failing = false;
  await card.getByRole("button", { name: "Повторить" }).click();
  await expect(card).toBeHidden({ timeout: 12000 });
  expect(calls).toBeGreaterThan(before);
  await expect(page.getByRole("button", { name: "AI: Готово" })).toBeVisible();
});

test("chip details are available without a diagnostics dashboard", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname === "/status",
    async (route) => {
      await route.fulfill({
        json: payload({
          chips: {
            tor: chip("unavailable", {
              detail_code: "tor_unavailable",
              message: "Tor не подтверждён.",
              action: "retry",
              recoverable: true,
              details: {
                mode: "auto",
                proxy_port: 9050,
                socks_listening: false,
                verified_chain: false,
                fallback: "none",
              },
            }),
          },
        }),
      });
    },
  );
  await page.goto("/");
  await register(page, `details-${Date.now()}@example.com`);
  await page.getByRole("button", { name: "Tor: Недоступно" }).click();
  const detail = page.getByTestId("status-detail");
  await expect(detail).toContainText("Tor не подтверждён.");
  await expect(detail).toContainText("Цепь проверена");
  await expect(detail).toContainText("Откат");
});
