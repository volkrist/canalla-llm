// The Tor service and the Computer service are always-on services, not on-demand features. A chip
// reports their health; the chat/composer mode is usage policy and must never make a healthy
// service look offline. These cases pin that separation, the transient starting state, the honest
// failure rows and the one manual action that really asks the service.

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import StatusChips, { StatusDetail } from "../components/StatusChips";
import TorServiceBlock from "../components/TorServiceBlock";
import { Api } from "./api";
import {
  detailRows,
  parseComputerDetails,
  type BalanceStatus,
  type ChipKey,
  type StatusSnapshot,
  type SubsystemState,
  type SubsystemStatus,
} from "./status";
import {
  ensureTorService,
  parseTorDetails,
  torBinaryText,
  torEndpoint,
  torEnsureText,
  torMissingBinary,
} from "./tor";

const api = new Api("http://127.0.0.1:8000", "token");

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
  fetched_at: "2026-09-22T10:00:00+00:00",
  last_success_at: "2026-09-22T10:00:00+00:00",
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
    message: `состояние: ${state}`,
    detail_code: null,
    recoverable: false,
    action: null,
    details: {},
    ...extra,
  };
}

function snapshot(
  chips: Partial<Record<ChipKey, SubsystemStatus>> = {},
): StatusSnapshot {
  return {
    generated_at: "2026-09-22T10:00:00+00:00",
    subsystems: {
      ai: chip("ready"),
      computer: chip("ready"),
      web: chip("ready"),
      tor: chip("ready"),
      memory: chip("ready"),
      ...chips,
    },
    balance: idleBalance,
  };
}

function renderChips(value: StatusSnapshot | null): string {
  return renderToStaticMarkup(
    <StatusChips
      snapshot={value}
      error=""
      refreshing={false}
      onAction={() => {}}
      api={api}
    />,
  );
}

function renderDetail(status: SubsystemStatus, chipKey: ChipKey = "tor") {
  return renderToStaticMarkup(
    <StatusDetail chip={chipKey} status={status} onAction={() => {}} />,
  );
}

/** A paid endpoint that is already listening and already proven. */
const provenTor = {
  mode: "auto",
  proxy_host: "127.0.0.1",
  proxy_port: 9050,
  configured_port: 9050,
  socks_listening: true,
  verified_chain: true,
  verified_at: "2026-09-22T13:36:39+00:00",
  method: "socks5h",
  managed: true,
  binary: {
    path: "C:\\Tor Browser\\Browser\\TorBrowser\\Tor\\tor.exe",
    source: "tor_browser",
  },
  candidates: [9050, 9150],
  required: false,
  fallback: "none",
};

afterEach(() => vi.restoreAllMocks());

describe("Tor chip health", () => {
  it("renders a proven route as a healthy chip and keeps the mode as its own row", () => {
    const status = chip("ready", {
      message: "Tor готов: цепь проверена.",
      details: provenTor,
    });
    const html = renderChips(snapshot({ tor: status }));
    expect(html).toContain('aria-label="Tor: Готово"');
    expect(html).toContain("state-ready");
    expect(html).not.toContain("status-recovery");

    const rows = detailRows("tor", status);
    expect(rows).toContainEqual(["Состояние", "Готово"]);
    expect(rows).toContainEqual(["Режим", "auto"]);
    expect(rows).toContainEqual(["SOCKS", "127.0.0.1:9050"]);
    expect(rows).toContainEqual(["Порт отвечает", "да"]);
    expect(rows).toContainEqual(["Цепь проверена", "да"]);
    expect(rows).toContainEqual(["Метод", "socks5h"]);
    expect(rows).toContainEqual(["Процесс", "Управляемый"]);
    expect(rows).toContainEqual(["Откат", "none"]);
  });

  it("shows a starting Tor service as a transient, never as an error", () => {
    const html = renderChips(
      snapshot({
        tor: chip("starting", {
          message: "Tor подключается…",
          action: "retry",
          recoverable: true,
          details: { ...provenTor, verified_chain: false },
        }),
      }),
    );
    expect(html).toContain('aria-label="Tor: Подключается…"');
    expect(html).toContain("state-starting");
    expect(html).not.toContain("state-unavailable");
    expect(html).not.toContain("state-error");
    expect(html).not.toContain("status-recovery");
    expect(html).not.toContain("Ошибка");
    // Only a missing snapshot is "Проверяем…": the other chips never claim to be connecting.
    const unknown = renderChips(null);
    expect((unknown.match(/>Проверяем…<\/span>/g) || []).length).toBe(5);
    expect(unknown).not.toContain("Подключается…");
  });

  it("shows the backend message and code for an unavailable circuit", () => {
    const message =
      "SOCKS отвечает, но цепь Tor не подтверждена. Запросы через Tor выполняться не будут.";
    const status = chip("unavailable", {
      message,
      detail_code: "tor_circuit_invalid",
      action: "retry",
      recoverable: true,
      details: {
        ...provenTor,
        verified_chain: false,
        verified_at: null,
        managed: false,
      },
    });
    const html = renderChips(snapshot({ tor: status }));
    expect(html).toContain('aria-label="Tor: Недоступно"');
    expect(html).toContain("state-unavailable");
    expect(html).toContain("status-recovery");
    expect(html).toContain(message);
    expect(html).toContain("Код: tor_circuit_invalid");

    const detail = renderDetail(status);
    expect(detail).toContain(message);
    expect(detail).toContain("Состояние");
    expect(detail).toContain("Недоступно");
    expect(detail).toContain("Цепь проверена");
    expect(detail).toContain("Процесс");
    expect(detail).toContain("Внешний");
    // Nothing verified is ever stamped, and no fallback is offered.
    expect(detail).not.toContain("Последняя проверка");
    expect(detail).toContain("Откат");
  });

  it("keeps a healthy chip green while the usage policy is off", () => {
    const status = chip("ready", {
      message:
        "Tor готов: цепь проверена. Использование Tor выключено в настройках.",
      details: { ...provenTor, mode: "off" },
    });
    const html = renderChips(snapshot({ tor: status }));
    expect(html).toContain('aria-label="Tor: Готово"');
    expect(html).toContain("state-ready");
    expect(html).not.toContain("state-off");

    const detail = renderDetail(status);
    expect(detail).toContain("Состояние");
    expect(detail).toContain("Готово");
    expect(detail).toContain("Режим");
    expect(detail).toContain("off");
    expect(detail).toContain("выключено в настройках");
    expect(detailRows("tor", status)).toContainEqual(["Режим", "off"]);
  });
});

describe("Tor manual action", () => {
  it("posts to /tools/tor/ensure and returns before the work is done", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ requested: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await ensureTorService(api);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0][0];
    const init = fetchMock.mock.calls[0][1];
    expect(String(url)).toBe("http://127.0.0.1:8000/tools/tor/ensure");
    expect(init?.method).toBe("POST");
    expect(String((init?.headers as Headers).get("Authorization"))).toBe(
      "Bearer token",
    );
  });

  it("offers the action in the popover and names what it will do", () => {
    const stale = chip("unavailable", {
      message: "Tor запущен, но SOCKS не отвечает.",
      detail_code: "tor_no_endpoint",
      action: "retry",
      recoverable: true,
      details: { ...provenTor, socks_listening: false, verified_chain: false },
    });
    const html = renderToStaticMarkup(
      <StatusDetail
        chip="tor"
        status={stale}
        onAction={() => {}}
        onEnsureTor={() => {}}
      />,
    );
    expect(html).toContain('data-testid="tor-ensure"');
    expect(html).toContain("Запустить Tor");
    // The generic "Повторить" is replaced, never doubled: for a service, retry is the ensure.
    expect(html).not.toContain("Повторить");
    expect(torEnsureText(parseTorDetails(stale.details))).toBe("Запустить Tor");

    const nothingToStart = chip("unavailable", {
      message: "Tor не найден на этом компьютере.",
      detail_code: "tor_not_installed",
      action: "retry",
      recoverable: true,
      details: { ...provenTor, socks_listening: false, binary: null },
    });
    expect(
      renderToStaticMarkup(
        <StatusDetail
          chip="tor"
          status={nothingToStart}
          onAction={() => {}}
          onEnsureTor={() => {}}
        />,
      ),
    ).toContain("Проверить снова");
    // Without a client the popover keeps the backend action instead of inventing a dead button.
    expect(renderDetail(nothingToStart)).not.toContain(
      'data-testid="tor-ensure"',
    );
    expect(renderDetail(nothingToStart)).toContain("Повторить");
  });
});

describe("Tor service in Settings", () => {
  const ready = chip("ready", {
    message: "Tor готов: цепь проверена.",
    details: provenTor,
  });

  it("shows the health, the endpoint, the proof and the binary", () => {
    const html = renderToStaticMarkup(
      <TorServiceBlock status={ready} busy={false} onEnsure={() => {}} />,
    );
    expect(html).toContain('data-testid="tor-service"');
    expect(html).toContain("Служба Tor");
    expect(html).toContain("Готово");
    expect(html).toContain("Tor готов: цепь проверена.");
    expect(html).toContain("127.0.0.1:9050");
    expect(html).toContain("Цепь проверена");
    expect(html).toContain("Tor Browser");
    expect(html).toContain("tor.exe");
    expect(html).toContain('data-testid="tor-service-ensure"');
    expect(html).not.toContain('data-testid="tor-dependency"');
  });

  it("names the required dependency only when there really is no binary", () => {
    const missing = chip("unavailable", {
      message:
        "Tor не найден на этом компьютере: установите Tor Browser или укажите tor.exe в настройках.",
      detail_code: "tor_not_installed",
      action: "retry",
      recoverable: true,
      details: {
        ...provenTor,
        socks_listening: false,
        verified_chain: false,
        verified_at: null,
        binary: null,
      },
    });
    const html = renderToStaticMarkup(
      <TorServiceBlock status={missing} busy={false} onEnsure={() => {}} />,
    );
    expect(html).toContain('data-testid="tor-dependency"');
    expect(html).toContain("Tor Browser — обязательная зависимость");
    expect(html).toContain("TOR_BINARY_PATH");
    expect(html).toContain("не найден");
    // No download is offered or promised anywhere.
    expect(html).not.toContain("Скачать");

    // A payload that never answered is not a missing dependency, and busy disables the action.
    const unanswered = chip("unavailable", {
      message: "Tor не подтверждён.",
      detail_code: "tor_unavailable",
      action: "retry",
      recoverable: true,
      details: { mode: "auto", proxy_port: 9050, socks_listening: false },
    });
    const quiet = renderToStaticMarkup(
      <TorServiceBlock status={unanswered} busy onEnsure={() => {}} />,
    );
    expect(quiet).not.toContain('data-testid="tor-dependency"');
    expect(quiet).toContain("неизвестно");
    expect(quiet).toContain("disabled");
    expect(quiet).toContain("Проверить снова");

    // No snapshot at all: the block claims nothing and still offers the check.
    const blank = renderToStaticMarkup(
      <TorServiceBlock status={null} busy={false} onEnsure={() => {}} />,
    );
    expect(blank).toContain("Проверка…");
    expect(blank).not.toContain("Готово");
    expect(blank).not.toContain('data-testid="tor-dependency"');
  });
});

describe("Tor payload reading", () => {
  it("never invents a field the backend did not send", () => {
    const empty = parseTorDetails({});
    expect(empty).toEqual({
      mode: null,
      proxy_host: null,
      proxy_port: null,
      configured_port: null,
      socks_listening: null,
      verified_chain: null,
      verified_at: null,
      method: null,
      managed: null,
      binary: null,
      binary_known: false,
      fallback: null,
    });
    expect(torEndpoint(empty)).toBeNull();
    expect(torBinaryText(empty)).toBe("неизвестно");
    expect(torMissingBinary(empty)).toBe(false);

    // A listening port without a proof is never a verified circuit.
    const raw = {
      proxy_port: 9150,
      socks_listening: true,
      verified_chain: false,
    };
    expect(torEndpoint(parseTorDetails(raw))).toBe("9150");
    expect(
      detailRows("tor", chip("configured", { details: raw })),
    ).toContainEqual(["Цепь проверена", "нет"]);
    expect(torMissingBinary(parseTorDetails({ binary: null }))).toBe(true);
  });
});

describe("Computer chip health", () => {
  it("keeps health, policy and the heartbeat apart", () => {
    const status = chip("ready", {
      message: "Компьютер готов.",
      details: {
        computer_mode: "ask",
        paired: true,
        device: {
          display_name: "Рабочий ноутбук",
          platform: "windows",
          last_seen: "2026-09-22T13:30:00+00:00",
          capabilities: ["read_file"],
        },
      },
    });
    const html = renderChips(snapshot({ computer: status }));
    expect(html).toContain('aria-label="Computer: Готово"');
    expect(html).toContain("state-ready");

    const rows = detailRows("computer", status);
    expect(rows).toContainEqual(["Состояние", "Готово"]);
    expect(rows).toContainEqual(["Режим", "ask"]);
    expect(rows).toContainEqual(["Сопряжён", "да"]);
    expect(rows).toContainEqual(["Отклик", "в норме"]);
    expect(rows).toContainEqual(["Устройство", "Рабочий ноутбук"]);
    expect(rows.some(([label]) => label === "Последний отклик")).toBe(true);
    expect(JSON.stringify(rows)).not.toContain("capabilities");
  });

  it("never reports a heartbeat the backend did not prove", () => {
    const off = chip("off", {
      message: "Работа с компьютером выключена в настройках.",
      details: {
        computer_mode: "off",
        paired: true,
        device: { display_name: "Ноутбук", platform: "windows" },
      },
    });
    const rows = detailRows("computer", off);
    expect(rows).toContainEqual(["Состояние", "Выключено"]);
    expect(rows).toContainEqual(["Режим", "off"]);
    expect(rows).toContainEqual(["Сопряжён", "да"]);
    // A policy that is off is not proof that the device stopped answering.
    expect(rows.some(([label]) => label === "Отклик")).toBe(false);

    const lost = chip("unavailable", {
      message: "Подключённое устройство не отвечает.",
      detail_code: "host_offline",
      action: "reconnect",
      recoverable: true,
      details: { computer_mode: "ask", paired: true, device: null },
    });
    expect(detailRows("computer", lost)).toContainEqual([
      "Отклик",
      "нет ответа",
    ]);
    expect(parseComputerDetails(null)).toEqual({
      mode: null,
      paired: null,
      device: null,
    });
    expect(parseComputerDetails({ paired: "yes" }).paired).toBeNull();
  });
});
