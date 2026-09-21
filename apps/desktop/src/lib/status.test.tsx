import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import StatusChips from "../components/StatusChips";
import { recoveryPlan } from "./errors";
import {
  balanceLine,
  balanceNote,
  chipState,
  detailRows,
  gpuRateLine,
  lowBalanceWarning,
  sessionSpendLine,
  statusDelaySeconds,
  STATE_TEXT,
  type BalanceStatus,
  type ChipKey,
  type StatusSnapshot,
  type SubsystemState,
  type SubsystemStatus,
} from "./status";

const idleBalance: BalanceStatus = {
  configured: true,
  available: true,
  balance_usd: "8.73",
  account_spend_per_hr: "0.12",
  low: false,
  low_threshold_usd: "5.00",
  stale: false,
  error_code: null,
  message: null,
  fetched_at: "2026-09-21T10:00:00+00:00",
  last_success_at: "2026-09-21T10:00:00+00:00",
  refresh_seconds: 15,
  shared_account: true,
  read_only: true,
  active_session: null,
};

function chip(
  state: SubsystemState,
  extra: Partial<SubsystemStatus> = {},
): SubsystemStatus {
  return {
    state,
    message: `сообщение:${state}`,
    detail_code: null,
    recoverable: false,
    action: null,
    details: {},
    ...extra,
  };
}

function snapshot(
  balance: Partial<BalanceStatus> = {},
  chips: Partial<Record<ChipKey, SubsystemStatus>> = {},
): StatusSnapshot {
  return {
    generated_at: "2026-09-21T10:00:00+00:00",
    subsystems: {
      ai: chip("ready"),
      computer: chip("ready"),
      web: chip("ready"),
      tor: chip("off"),
      memory: chip("ready"),
      ...chips,
    },
    balance: { ...idleBalance, ...balance },
  };
}

function render(value: StatusSnapshot | null, error = "", refreshing = false) {
  return renderToStaticMarkup(
    <StatusChips
      snapshot={value}
      error={error}
      refreshing={refreshing}
      onAction={() => {}}
    />,
  );
}

describe("five-chip status", () => {
  it("renders all five chips with a text state, never colour alone", () => {
    const html = render(snapshot());
    for (const title of ["AI", "Computer", "Web", "Tor", "Memory"]) {
      expect(html).toContain(`>${title}</span>`);
    }
    expect(html).toContain("Готово");
    expect(html).toContain("Выключено");
    expect((html.match(/>Готово<\/span>/g) || []).length).toBe(4);
    expect((html.match(/status-chip-state/g) || []).length).toBe(5);
  });

  it("starts in a checking state instead of flashing an error", () => {
    const html = render(null);
    expect((html.match(/>Проверяем…<\/span>/g) || []).length).toBe(5);
    expect(html).not.toContain("Ошибка");
    expect(chipState(null, "ai")).toBe("starting");
  });

  it("distinguishes ready, starting, configured, off, not configured, unavailable and error", () => {
    const html = render(
      snapshot(
        {},
        {
          ai: chip("error", { detail_code: "multiple_compute" }),
          computer: chip("not_configured"),
          web: chip("off"),
          tor: chip("unavailable"),
          memory: chip("starting"),
        },
      ),
    );
    expect(html).toContain("Ошибка");
    expect(html).toContain("Не настроено");
    expect(html).toContain("Выключено");
    expect(html).toContain("Недоступно");
    expect(html).toContain("Проверяем…");
  });

  it("never renders a configured subsystem as ready", () => {
    const html = render(
      snapshot(
        {},
        {
          web: chip("configured", {
            message:
              "Web настроен. Доступность провайдера проверяется при использовании.",
            details: { provider: "TinyFish", probe: "configuration" },
          }),
          tor: chip("configured", {
            message:
              "Tor доступен, цепь ещё не проверена. Откат в clearnet не выполняется.",
            details: {
              mode: "auto",
              socks_listening: true,
              verified_chain: false,
              fallback: "none",
            },
          }),
        },
      ),
    );
    expect((html.match(/>Настроено<\/span>/g) || []).length).toBe(2);
    expect(html).toContain("state-configured");
    // The three mock-ready chips still say ready; the configured two must not.
    expect((html.match(/>Готово<\/span>/g) || []).length).toBe(3);
    expect(html).not.toContain("status-recovery");
    expect(STATE_TEXT.configured).toBe("Настроено");
  });

  it("renders no ready chip at all when every subsystem is only configured", () => {
    const html = render(
      snapshot(
        {},
        {
          ai: chip("configured"),
          computer: chip("configured"),
          web: chip("configured"),
          tor: chip("configured"),
          memory: chip("configured"),
        },
      ),
    );
    expect(html).not.toContain("Готово");
    expect((html.match(/>Настроено<\/span>/g) || []).length).toBe(5);
  });

  it("treats configured as informational, not as something to recover from", () => {
    const html = render(
      snapshot({}, { web: chip("configured"), tor: chip("configured") }),
    );
    expect(html).not.toContain("status-recovery");
    expect(html).not.toContain("Повторить");
  });

  it("shows a recovery card with the existing action for a recoverable failure", () => {
    const html = render(
      snapshot(
        {},
        {
          ai: chip("error", {
            detail_code: "startup_timeout",
            message: "Превышено время запуска AI.",
            action: "retry",
            recoverable: true,
          }),
        },
      ),
    );
    expect(html).toContain("status-recovery");
    expect(html).toContain("Запуск модели");
    expect(html).toContain("Превышено время запуска AI.");
    expect(html).toContain("Код: startup_timeout");
    expect(html).toContain("Повторить");
  });

  it("offers no destructive action for multiple compute", () => {
    const html = render(
      snapshot(
        {},
        {
          ai: chip("error", {
            detail_code: "multiple_compute",
            message: "Обнаружено несколько Pods.",
          }),
        },
      ),
    );
    expect(html).toContain("Обнаружено несколько Pods.");
    expect(html).not.toContain("Остановить");
    expect(html).not.toContain("Повторить");
  });

  it("reports a failed status read without inventing a state", () => {
    const html = render(null, "Не удалось получить состояние");
    expect(html).toContain("Состояние недоступно");
    expect(html).toContain("Повторить");
  });
});

describe("shared RunPod balance", () => {
  it("renders the shared balance and the update moment", () => {
    const html = render(snapshot());
    expect(html).toContain("RunPod balance: $8.73");
    expect(html).toContain("обновлено");
  });

  it("updates when the next snapshot carries a new value", () => {
    expect(render(snapshot())).toContain("$8.73");
    const next = render(
      snapshot({ balance_usd: "9.95", account_spend_per_hr: "0.24" }),
    );
    expect(next).toContain("$9.95");
    expect(next).not.toContain("$8.73");
  });

  it("keeps a stale balance and never turns it into zero", () => {
    const stale: BalanceStatus = {
      ...idleBalance,
      stale: true,
      error_code: "runpod_timeout",
      message: "RunPod не ответил вовремя.",
    };
    const html = render(snapshot(stale));
    expect(html).toContain("$8.73");
    expect(html).not.toContain("$0.00");
    expect(html).toContain("Устарело");
    expect(balanceNote(stale)).toContain("Устарело");
  });

  it("shows unavailable when no snapshot was ever fetched", () => {
    const html = render(
      snapshot({
        available: false,
        balance_usd: null,
        error_code: "runpod_unavailable",
        message: "RunPod сейчас недоступен.",
      }),
    );
    expect(html).toContain("Баланс недоступен");
    expect(html).not.toContain("$0.00");
    expect(
      balanceLine({ ...idleBalance, available: false, balance_usd: null }),
    ).toBe("Баланс недоступен");
  });

  it("shows not configured instead of a zero balance", () => {
    const html = render(
      snapshot({ configured: false, available: false, balance_usd: null }),
    );
    expect(html).toContain("RunPod не настроен");
    expect(html).not.toContain("$0.00");
  });

  it("renders the real GPU rate and session spend only when the data exists", () => {
    const without = render(snapshot());
    expect(without).not.toContain("GPU:");
    expect(without).not.toContain("Сессия:");
    const withSession = render(
      snapshot({
        active_session: {
          gpu: "NVIDIA L40S",
          hourly_rate_usd: "1.09",
          estimated_usd: "0.018000",
          billable_seconds: 60,
          managed: true,
          started_at: "2026-09-21T09:59:00+00:00",
          budget_usd: "3.00",
        },
        refresh_seconds: 5,
      }),
    );
    expect(withSession).toContain("GPU: NVIDIA L40S · $1.09/ч");
    expect(withSession).toContain("Сессия: ≈$0.018");
    expect(gpuRateLine(idleBalance)).toBeNull();
    expect(sessionSpendLine(idleBalance)).toBeNull();
  });

  it("warns about a low balance without blocking compute", () => {
    const low: BalanceStatus = {
      ...idleBalance,
      balance_usd: "1.20",
      low: true,
    };
    const html = render(snapshot(low));
    expect(html).toContain("Низкий баланс: $1.20");
    expect(lowBalanceWarning(idleBalance)).toBeNull();
  });

  it("never renders a malformed amount as a fake value", () => {
    const html = render(
      snapshot({ available: true, balance_usd: "not-a-number" }),
    );
    expect(html).toContain("RunPod balance: —");
    expect(html).not.toContain("$0.00");
  });
});

describe("polling policy", () => {
  it("follows the backend cadence and slows down while hidden", () => {
    expect(statusDelaySeconds(null, false)).toBe(15);
    expect(statusDelaySeconds(snapshot({ refresh_seconds: 5 }), false)).toBe(5);
    expect(statusDelaySeconds(snapshot({ refresh_seconds: 15 }), false)).toBe(
      15,
    );
    expect(statusDelaySeconds(snapshot({ refresh_seconds: 15 }), true)).toBe(
      30,
    );
    expect(statusDelaySeconds(snapshot({ refresh_seconds: 5 }), true)).toBe(30);
  });
});

describe("recovery actions reuse existing machinery", () => {
  it("maps every action to an existing code path", () => {
    expect(recoveryPlan("retry")).toEqual({ kind: "refresh", event: null });
    expect(recoveryPlan("configure")).toEqual({
      kind: "settings",
      event: null,
    });
    expect(recoveryPlan("reconnect")).toEqual({
      kind: "device",
      event: "alex-host-jobs",
    });
    expect(recoveryPlan("stop")).toEqual({
      kind: "compute",
      event: "alex-open-compute",
    });
    expect(recoveryPlan("cancel_search")).toEqual({
      kind: "compute",
      event: "alex-open-compute",
    });
  });
});

describe("chip details", () => {
  it("renders only whitelisted fields, so nothing unexpected can reach the DOM", () => {
    const status = chip("ready", {
      details: {
        provider: "TinyFish",
        probe: "configuration",
        runpod_api_key: "test-only-fake-key",
        refresh_token: "secret",
      },
    });
    const rows = detailRows("web", status);
    expect(rows).toContainEqual(["Провайдер", "TinyFish"]);
    expect(rows).toContainEqual(["Профиль готовности", "configuration"]);
    const rendered = JSON.stringify(rows);
    expect(rendered).not.toContain("test-only-fake-key");
    expect(rendered).not.toContain("secret");
  });

  it("explains that Tor readiness is not a verified circuit", () => {
    const rows = detailRows(
      "tor",
      chip("configured", {
        details: {
          mode: "auto",
          proxy_port: 9050,
          socks_listening: true,
          verified_chain: false,
          proof_store: "none",
          fallback: "none",
        },
      }),
    );
    expect(rows).toContainEqual(["Цепь проверена", "нет"]);
    expect(rows).toContainEqual(["Откат", "none"]);
  });

  it("never stores a provider key in the rendered snapshot", () => {
    const html = render(
      snapshot(
        {},
        { web: chip("ready", { details: { key: "test-only-fake-key" } }) },
      ),
    );
    expect(html).not.toContain("test-only-fake-key");
  });
});
