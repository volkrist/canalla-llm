import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import AlexCloudPanel from "../components/AlexCloudPanel";
import SettingsDialog from "../components/SettingsDialog";
import type { Settings } from "../types";
import { Api } from "./api";
import {
  cloudLabel,
  ensureCloudCompute,
  sessionSummary,
  stopCloudCompute,
  cloudSnapshot,
  disconnectGateway,
  enrollError,
  enrollGateway,
  fetchCloudStatus,
  isSharedMode,
  refreshCloud,
  setCloudClient,
  type CloudComputeStatus,
  type CloudState,
  type CloudStatus,
  type GatewayStatus,
} from "./cloud";

const invokeMock = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

// Alex Cloud can only be managed in the installed app; the node test environment
// has no window at all.
const tauri = vi.hoisted(() => ({ enabled: true }));
vi.mock("./backend", () => ({ isTauriRuntime: () => tauri.enabled }));

const api = new Api("http://127.0.0.1:8000", "token");

const settings: Settings = {
  backendUrl: "http://127.0.0.1:8000",
  fontSize: 15,
  theme: "dark",
  language: "ru",
  enterSends: true,
  autoScroll: true,
  timestamps: true,
  technicalDetails: false,
  autoCheckUpdates: true,
  launchAtLogin: true,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function cloudStatus(extra: Partial<CloudStatus> = {}): CloudStatus {
  return {
    mode: "shared",
    state: "not_connected",
    message: "Gateway не подключён.",
    detail_code: "gateway_not_connected",
    url: null,
    default_url: null,
    installation_id: null,
    enrolled: false,
    reachable: false,
    protocol_version: null,
    balance_source: "gateway",
    recoverable: true,
    action: "configure",
    details: {},
    ...extra,
  };
}

function gatewayStatus(extra: Partial<GatewayStatus> = {}): GatewayStatus {
  return {
    configured: false,
    url: null,
    default_url: null,
    installation_id: null,
    state: "not_connected",
    message: null,
    ...extra,
  };
}

/** `gateway_status` plus one authenticated `/cloud/status` answer. */
function mockCloud(status: CloudStatus, gateway: Partial<GatewayStatus> = {}) {
  invokeMock.mockImplementation(async (command: string) =>
    command === "gateway_status" ? gatewayStatus(gateway) : {},
  );
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(jsonResponse(200, status));
}

/** One full read, as the panel does on mount. */
async function loadCloud(
  status: CloudStatus,
  gateway: Partial<GatewayStatus> = {},
) {
  const fetchMock = mockCloud(status, gateway);
  setCloudClient(api);
  await refreshCloud();
  return fetchMock;
}

/** JSX text keeps its source line breaks, so assertions run on flat markup. */
function flat(html: string): string {
  return html.replace(/\s+/g, " ");
}

function renderPanel(): string {
  return flat(renderToStaticMarkup(<AlexCloudPanel />));
}

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockImplementation(async () => gatewayStatus());
  tauri.enabled = true;
  // A new session must not inherit the previous snapshot.
  setCloudClient(null);
});

afterEach(() => vi.restoreAllMocks());

describe("cloud labels", () => {
  it("names each of the six states in Russian", () => {
    const expected: Array<[CloudState, string]> = [
      ["connected", "Подключено"],
      ["connecting", "Подключаемся…"],
      ["not_connected", "Не подключено"],
      ["unavailable", "Недоступно"],
      ["revoked", "Отозвано"],
      ["protocol_mismatch", "Несовместимая версия"],
    ];
    for (const [state, label] of expected) {
      expect(cloudLabel(state)).toBe(label);
    }
  });

  it("never upgrades an unknown state to a friendly one", () => {
    expect(cloudLabel("future_state" as CloudState)).toBe("future_state");
  });
});

describe("shared mode gate", () => {
  it("treats an unknown mode as the local key path, never as shared", () => {
    expect(isSharedMode(null)).toBe(false);
    expect(isSharedMode(cloudStatus({ mode: "direct" }))).toBe(false);
    expect(isSharedMode(cloudStatus({ mode: "shared" }))).toBe(true);
  });
});

describe("cloud status read", () => {
  it("parses a valid payload with the normal auth header", async () => {
    const payload = cloudStatus({
      state: "connected",
      enrolled: true,
      reachable: true,
      url: "https://gateway.example",
      installation_id: "6f0f98a2-1111-2222-3333-444455556666",
      protocol_version: 1,
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, payload));
    const status = await fetchCloudStatus(api);
    expect(status).toEqual(payload);
    expect(status.state).toBe("connected");
    expect(status.enrolled).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/cloud/status");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer token",
    );
  });

  it("rejects on a failed read instead of inventing a state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(503, { detail: "Gateway недоступен" }),
    );
    await expect(fetchCloudStatus(api)).rejects.toMatchObject({ status: 503 });
  });

  it("drops a stale snapshot when the next read fails", async () => {
    const fetchMock = await loadCloud(
      cloudStatus({ state: "connected", enrolled: true, reachable: true }),
    );
    expect(renderPanel()).toContain("Подключено");
    fetchMock.mockResolvedValue(
      jsonResponse(503, { detail: "Gateway недоступен" }),
    );
    await refreshCloud();
    const html = renderPanel();
    expect(html).toContain("Статус недоступен");
    expect(html).toContain("Gateway недоступен");
    expect(html).not.toContain("Подключено");
  });

  it("claims no state at all while no session can read one", async () => {
    await refreshCloud();
    const html = renderPanel();
    expect(html).toContain("Статус недоступен");
    expect(html).toContain("доступно после входа в аккаунт");
    expect(html).not.toContain("Подключено");
  });

  it("settles the mount read instead of keeping a transient «Подключаемся…»", async () => {
    // Right after the backend starts, the first authoritative answer is not in yet: the
    // backend reports `connecting`. The panel reads once on mount, so that read waits.
    let reads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      reads += 1;
      return jsonResponse(
        200,
        reads === 1
          ? cloudStatus({ state: "connecting", enrolled: true })
          : cloudStatus({
              state: "connected",
              enrolled: true,
              reachable: true,
            }),
      );
    });
    setCloudClient(api);
    await refreshCloud();
    expect(reads).toBeGreaterThan(1);
    expect(renderPanel()).toContain("Подключено");
  });
});

describe("Canalla Cloud panel", () => {
  it("asks for the activation code while the installation is not connected", async () => {
    await loadCloud(cloudStatus(), { default_url: "https://gateway.example" });
    const html = renderPanel();
    expect(html).toContain("Canalla Cloud");
    expect(html).toContain("Не подключено");
    expect(html).toContain("Код активации");
    expect(html).toMatch(/<input type="password"[^>]*autocomplete="off"/i);
    expect(html).toContain("Подключить");
    expect(html).toContain("Адрес Gateway");
    expect(html).toMatch(
      /<input type="url"[^>]*value="https:\/\/gateway\.example"/i,
    );
    expect(html).not.toContain("Отключить Canalla Cloud");
  });

  it("shows the installation, the url, the backend message and disconnect", async () => {
    const installation = "6f0f98a2-1111-2222-3333-444455556666";
    await loadCloud(
      cloudStatus({
        state: "connected",
        enrolled: true,
        reachable: true,
        url: "https://gateway.example",
        installation_id: installation,
        message: "Gateway отвечает, общий аккаунт RunPod используется.",
      }),
      { configured: true, url: "https://gateway.example" },
    );
    const html = renderPanel();
    expect(html).toContain("Подключено");
    expect(html).toContain(`Установка: ${installation}`);
    expect(html).toContain("https://gateway.example");
    expect(html).toContain(
      "Gateway отвечает, общий аккаунт RunPod используется.",
    );
    expect(html).toContain("Отключить Canalla Cloud");
    expect(html).not.toMatch(/<input type="password"/i);
    expect(html).not.toContain("Код активации");
  });

  it("treats a configured installation as connected before the status read", async () => {
    await loadCloud(cloudStatus({ enrolled: false, message: "" }), {
      configured: true,
      url: "https://gateway.example",
      message: "Установка подключена.",
    });
    const html = renderPanel();
    expect(html).toContain("Отключить Canalla Cloud");
    expect(html).toContain("https://gateway.example");
    expect(html).toContain("Установка подключена.");
    expect(html).not.toMatch(/<input type="password"/i);
  });

  it("never renders a secret from the payload", async () => {
    await loadCloud(
      cloudStatus({
        state: "connected",
        enrolled: true,
        installation_id: "public-installation-id",
        details: {
          runpod_api_key: "test-only-fake-key",
          installation_secret: "test-only-fake-secret",
          access_token: "test-only-fake-token",
        },
      }),
      { configured: true, installation_id: "public-installation-id" },
    );
    const html = renderPanel();
    for (const secret of [
      "runpod_api_key",
      "test-only-fake-key",
      "installation_secret",
      "test-only-fake-secret",
      "access_token",
      "test-only-fake-token",
    ]) {
      expect(html).not.toContain(secret);
    }
    expect(html).toContain("public-installation-id");
  });

  it("explains that Canalla Cloud belongs to the installed app in a browser", () => {
    tauri.enabled = false;
    const html = renderPanel();
    expect(html).toContain("доступно в установленном приложении Canalla LLM");
    expect(html).not.toContain("Код активации");
  });
});

describe("enrollment and disconnect", () => {
  it("sends the code once, restarts the owned backend and re-reads the status", async () => {
    const commands: string[] = [];
    let enrollArgs: unknown = null;
    invokeMock.mockImplementation(async (command: string, args?: unknown) => {
      commands.push(command);
      if (command === "gateway_enroll") {
        enrollArgs = args;
        return { configured: true, url: "https://gateway.example" };
      }
      if (command === "restart_backend") return { state: "ready" };
      return gatewayStatus();
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse(200, cloudStatus({ state: "connected", enrolled: true })),
      );
    setCloudClient(api);
    await expect(
      enrollGateway("https://gateway.example", "code-abc"),
    ).resolves.toBe("ok");
    expect(commands).toEqual([
      "gateway_enroll",
      "restart_backend",
      "gateway_status",
    ]);
    expect(enrollArgs).toEqual({
      url: "https://gateway.example",
      activationCode: "code-abc",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // The activation code is used once and kept nowhere: not in the shared state,
    // not in the markup.
    expect(JSON.stringify(cloudSnapshot())).not.toContain("code-abc");
    expect(renderPanel()).not.toContain("code-abc");
  });

  it("settles on the real state instead of keeping a transient «Подключаемся…»", async () => {
    // The backend is restarted by the enrollment and reaches the Gateway a moment
    // later, so the first read after the restart can still be `connecting`.
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "gateway_enroll") return { configured: true };
      if (command === "restart_backend") return { state: "ready" };
      return gatewayStatus();
    });
    let reads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      reads += 1;
      return jsonResponse(
        200,
        reads === 1
          ? cloudStatus({ state: "connecting", enrolled: true })
          : cloudStatus({ state: "connected", enrolled: true }),
      );
    });
    setCloudClient(api);
    await expect(
      enrollGateway("https://gateway.example", "code-abc"),
    ).resolves.toBe("ok");
    expect(reads).toBeGreaterThan(1);
    expect(cloudSnapshot().status?.state).toBe("connected");
    expect(cloudLabel(cloudSnapshot().status?.state ?? "not_connected")).toBe(
      "Подключено",
    );
  });

  it("does not claim a restart it could not perform on an external backend", async () => {
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "gateway_disconnect") return { configured: false };
      if (command === "restart_backend") throw "backend_not_owned";
      return gatewayStatus();
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, cloudStatus()),
    );
    setCloudClient(api);
    await expect(disconnectGateway()).resolves.toBe("external");
  });

  it("reports a failed restart separately from a rejected code", async () => {
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "restart_backend") throw "backend_stop_timeout";
      return gatewayStatus({ configured: true });
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, cloudStatus()),
    );
    setCloudClient(api);
    await expect(disconnectGateway()).resolves.toBe("failed");
  });

  it("maps the stable gateway codes to Russian and echoes no raw code", () => {
    const codes: Array<[string, string]> = [
      ["invalid_url", "Адрес Gateway"],
      ["insecure_gateway_url", "HTTPS"],
      ["invalid_activation_code", "Код активации"],
      ["activation_code_rejected", "отклонил код активации"],
      ["gateway_unreachable", "не отвечает"],
      ["gateway_protocol_mismatch", "несовместима"],
    ];
    for (const [code, part] of codes) {
      expect(enrollError(code)).toContain(part);
      expect(enrollError(code)).not.toContain(code);
    }
    expect(enrollError("something_new")).toBe(
      "Не удалось подключиться к Canalla Cloud: something_new.",
    );
    expect(enrollError(new Error("offline"))).toContain("offline");
  });
});

describe("settings integration", () => {
  it("lists the Canalla Cloud tab next to AI / Compute", () => {
    const html = flat(
      renderToStaticMarkup(
        <SettingsDialog
          value={settings}
          onSave={() => {}}
          onClose={() => {}}
          api={api}
        />,
      ),
    );
    expect(html).toContain(">Canalla Cloud<");
    expect(html.indexOf(">AI / Compute<")).toBeLessThan(
      html.indexOf(">Canalla Cloud<"),
    );
    expect(html.indexOf(">Память<")).toBeLessThan(
      html.indexOf(">Canalla Cloud<"),
    );
  });
});

describe("shared compute control", () => {
  function computeStatus(
    extra: Partial<CloudComputeStatus> = {},
  ): CloudComputeStatus {
    return {
      state: "ready",
      ai: "ready",
      ai_label: "AI Ready",
      message: "AI готов.",
      error_code: null,
      detail: null,
      managed: true,
      adopted: false,
      session: {
        gpu: "NVIDIA L40S",
        hourly_rate_usd: "0.790000",
        budget_usd: "3.000000",
        estimated_usd: "0.210000",
        billable_seconds: 900,
        auto_stop_minutes: 10,
        managed: true,
        adopted: false,
      },
      last_session: null,
      idle_deadline: null,
      ...extra,
    };
  }

  it("asks the Gateway for compute without sending any money limit", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, {
        ...cloudStatus({ state: "connected" }),
        compute: computeStatus(),
      }),
    );
    setCloudClient(api);
    const result = await ensureCloudCompute("task-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/cloud/compute/ensure");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ task_id: "task-1" }));
    expect(result.compute.state).toBe("ready");
    // The desktop never names a price or a budget: this user's own policy is attached
    // by the local backend, and the Gateway stays the authority for money.
    expect(String(init.body)).not.toContain("hourly");
    expect(String(init.body)).not.toContain("budget");
  });

  it("stops shared compute through the Gateway", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, {
        ...cloudStatus({ state: "connected" }),
        compute: computeStatus({ state: "stopped", ai: "off", session: null }),
      }),
    );
    setCloudClient(api);
    const result = await stopCloudCompute();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/cloud/compute/stop");
    expect(init.method).toBe("POST");
    expect(result.compute.state).toBe("stopped");
  });

  it("remembers the Gateway compute answer for the panels", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, {
        ...cloudStatus({ state: "connected" }),
        compute: computeStatus(),
      }),
    );
    setCloudClient(api);
    expect(cloudSnapshot().compute).toBeNull();
    await ensureCloudCompute();
    expect(cloudSnapshot().compute?.session?.hourly_rate_usd).toBe("0.790000");
    // A later status read cannot erase what the Gateway proved about the Pod: the status
    // payload carries no compute session of its own.
    fetchMock.mockResolvedValue(
      jsonResponse(200, cloudStatus({ state: "connected" })),
    );
    await refreshCloud();
    expect(cloudSnapshot().compute?.session?.hourly_rate_usd).toBe("0.790000");
    // A new session (or logout) inherits nothing.
    setCloudClient(null);
    expect(cloudSnapshot().compute).toBeNull();
  });

  it("summarizes a shared session honestly, or not at all", () => {
    expect(sessionSummary(computeStatus().session)).toBe(
      "NVIDIA L40S · $0.79/ч · бюджет $3.00 · израсходовано ~$0.21",
    );
    expect(sessionSummary(null)).toBeNull();
    expect(
      sessionSummary({
        ...computeStatus().session!,
        hourly_rate_usd: "unknown",
      }),
    ).toBeNull();
  });

  it("renders AI controls only for a connected installation", () => {
    const disconnected = flat(renderToStaticMarkup(<AlexCloudPanel />));
    expect(disconnected).not.toContain("Запустить AI");
  });
});
