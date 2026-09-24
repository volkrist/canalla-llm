import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import AiConnectionBadge, {
  AiConnectionDetail,
} from "../components/AiConnectionBadge";
import {
  aiConnection,
  aiConnectionRows,
  aiInfrastructure,
  type AiConnectionInput,
} from "./ai-connection";
import {
  chipState,
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
  fetched_at: "2026-09-23T10:00:00+00:00",
  last_success_at: "2026-09-23T10:00:00+00:00",
  refresh_seconds: 15,
  shared_account: false,
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

/** The AI chip exactly as `app/status/snapshot.py::ai_status` shapes it. */
function aiChip(
  state: SubsystemState,
  details: Record<string, unknown>,
  extra: Partial<SubsystemStatus> = {},
): SubsystemStatus {
  return chip(state, { details, ...extra });
}

function snapshot(
  ai: SubsystemStatus,
  chips: Partial<Record<ChipKey, SubsystemStatus>> = {},
): StatusSnapshot {
  return {
    generated_at: "2026-09-23T10:00:00+00:00",
    subsystems: {
      ai,
      computer: chip("ready"),
      web: chip("configured"),
      tor: chip("ready"),
      memory: chip("ready"),
      ...chips,
    },
    balance: idleBalance,
  };
}

function state(input: Partial<AiConnectionInput> & { ai?: SubsystemStatus }) {
  const { ai, ...rest } = input;
  return aiConnection({
    snapshot: ai ? snapshot(ai) : null,
    stale: false,
    backendReady: true,
    ...rest,
  });
}

const NOT_CONFIGURED = aiChip("not_configured", {
  provider: null,
  configured: false,
  compact_ai: "unavailable",
  compute_state: "not_configured",
});
const KEY_MISSING = aiChip("unavailable", {
  provider: "runpod",
  configured: false,
  compact_ai: "unavailable",
  compute_state: "not_configured",
});
const NO_POD = aiChip("off", {
  provider: "runpod",
  configured: true,
  compact_ai: "off",
  compute_state: "stopped",
});
const READY = aiChip("ready", {
  provider: "runpod",
  model: "Qwen3.8-27B",
  configured: true,
  compact_ai: "ready",
  compute_state: "ready",
});

describe("the global state is AI readiness, never infrastructure", () => {
  it("AI unconfigured is red, and says so instead of checking forever", () => {
    const result = state({ ai: NOT_CONFIGURED });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("not_configured");
    expect(result.reason).toBe("AI не настроен");
    expect(result.action).toBe("configure");
    expect(result.label).not.toBe("Connecting…");
  });

  it("a missing RunPod key is red with the configuration action", () => {
    const result = state({ ai: KEY_MISSING });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("credentials_missing");
    expect(result.reason).toContain("RunPod не настроен");
    expect(result.action).toBe("configure");
  });

  it("a configured provider with no Pod is red, not green", () => {
    const result = state({ ai: NO_POD });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("off");
    // Nothing to press: the status surface never starts compute.
    expect(result.action).toBeNull();
  });

  it("a terminated Pod is red", () => {
    const stopped = aiChip("off", {
      provider: "runpod",
      configured: true,
      compact_ai: "off",
      compute_state: "stopped",
    });

    expect(state({ ai: stopped }).state).toBe("disconnected");
  });

  it("provisioning is amber, one stage at a time", () => {
    for (const [compute, words] of [
      ["searching", "Ищем GPU"],
      ["creating", "Создаём Pod"],
      ["starting_pod", "Запускаем Pod"],
      ["mounting_storage", "Подключаем хранилище"],
    ] as const) {
      const result = state({
        ai: aiChip("starting", {
          provider: "runpod",
          configured: true,
          compact_ai: "starting",
          compute_state: compute,
        }),
      });

      expect(result.state).toBe("connecting");
      expect(result.label).toBe("Connecting…");
      expect(result.reason).toContain(words);
    }
  });

  it("a running Pod with the model still loading is amber, not green", () => {
    const result = state({
      ai: aiChip("starting", {
        provider: "runpod",
        configured: true,
        compact_ai: "starting",
        compute_state: "loading_model",
      }),
    });

    expect(result.state).toBe("connecting");
    expect(result.reason).toContain("Загружаем модель");
  });

  it("a ready model is green", () => {
    const result = state({ ai: READY });

    expect(result.state).toBe("connected");
    expect(result.code).toBe("ready");
    expect(result.label).toBe("Connected");
    expect(result.action).toBeNull();
  });

  it("a model that answers mid-generation is still green", () => {
    const generating = aiChip("ready", {
      provider: "runpod",
      configured: true,
      compact_ai: "ready",
      compute_state: "generating",
    });

    expect(state({ ai: generating }).state).toBe("connected");
  });

  it("lost model health goes red again", () => {
    const lost = aiChip(
      "error",
      {
        provider: "runpod",
        configured: true,
        compact_ai: "error",
        compute_state: "ready",
      },
      {
        message: "Модель перестала отвечать",
        detail_code: "connection_failed",
      },
    );
    const result = state({ ai: lost });

    expect(result.state).toBe("disconnected");
    expect(result.reason).toBe("Модель перестала отвечать");
  });

  it("a provider error is red and keeps the backend's own recovery", () => {
    const failed = aiChip("error", {
      provider: "runpod",
      configured: true,
      compact_ai: "error",
      compute_state: "error",
    });
    const result = state({ ai: failed });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("error");
    expect(result.action).toBe("retry");
  });

  it("a Pod being stopped is red, not a transition to green", () => {
    const stopping = aiChip("off", {
      provider: "runpod",
      configured: true,
      compact_ai: "waiting",
      compute_state: "stopping",
    });
    const result = state({ ai: stopping });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("stopping");
  });

  it("an unknown create outcome is never a promise of green", () => {
    const unknown = aiChip("degraded", {
      provider: "runpod",
      configured: true,
      compact_ai: "waiting",
      compute_state: "create_unknown",
    });
    const result = state({ ai: unknown });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("waiting");
  });

  it("a degraded runtime with a transition really in flight is amber", () => {
    const booting = aiChip("degraded", {
      provider: "runpod",
      configured: true,
      compact_ai: "waiting",
      compute_state: "starting_llm",
    });

    expect(state({ ai: booting }).state).toBe("connecting");
  });

  it("configured-but-unproven is red: configured is not healthy", () => {
    const configured = aiChip("configured", {
      provider: "openai",
      configured: true,
      compact_ai: "ready",
      compute_state: "ready",
    });
    const result = state({ ai: configured });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("configured_only");
  });

  it("a stale snapshot is never green, even when the last one was ready", () => {
    const result = state({ ai: READY, stale: true });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("snapshot_stale");
    expect(result.action).toBe("retry");
  });

  it("a failed read is not health even without a snapshot", () => {
    const result = aiConnection({
      snapshot: null,
      stale: true,
      backendReady: true,
    });

    expect(result.state).toBe("disconnected");
  });

  it("a silent backend is red, and a first read in flight is amber", () => {
    expect(
      aiConnection({ snapshot: null, stale: false, backendReady: false }).state,
    ).toBe("disconnected");
    expect(
      aiConnection({ snapshot: null, stale: false, backendReady: true }),
    ).toMatchObject({ state: "connecting", code: "checking" });
  });

  it("recovery walks red → amber → green and back", () => {
    const steps = [
      NO_POD,
      aiChip("starting", {
        provider: "runpod",
        configured: true,
        compact_ai: "starting",
        compute_state: "starting_pod",
      }),
      READY,
      aiChip("off", {
        provider: "runpod",
        configured: true,
        compact_ai: "off",
        compute_state: "stopped",
      }),
    ].map((ai) => state({ ai }).state);

    expect(steps).toEqual([
      "disconnected",
      "connecting",
      "connected",
      "disconnected",
    ]);
  });

  it("only a proven-ready AI can ever be green", () => {
    const everythingElse: SubsystemState[] = [
      "starting",
      "configured",
      "off",
      "not_configured",
      "unavailable",
      "error",
      "degraded",
    ];
    for (const chipStateValue of everythingElse) {
      const result = state({ ai: aiChip(chipStateValue, {}) });
      expect(result.state).not.toBe("connected");
    }
  });

  it("the other chips stay green while the AI is disconnected", () => {
    const board = snapshot(NOT_CONFIGURED);

    expect(
      aiConnection({ snapshot: board, stale: false, backendReady: true }).state,
    ).toBe("disconnected");
    expect(chipState(board, "computer")).toBe("ready");
    expect(chipState(board, "tor")).toBe("ready");
    expect(chipState(board, "memory")).toBe("ready");
    expect(chipState(board, "web")).toBe("configured");
  });
});

describe("a stage is only amber while a bounded operation is running", () => {
  /** The AI chip as the backend shapes it while shared compute searches for a GPU. `active` is the
   *  backend's own verdict about the search operation: an identity, a deadline, and no expiry. */
  function searching(extra: Record<string, unknown> = {}) {
    return aiChip("starting", {
      provider: "alex-cloud",
      model: "Qwen3.8-27B",
      configured: true,
      compact_ai: "starting",
      compute_state: "searching",
      compute_search_active: true,
      compute_search_deadline: "2026-09-23T10:01:00+00:00",
      ...extra,
    });
  }

  it("a live bounded search is amber", () => {
    const result = state({ ai: searching() });

    expect(result.state).toBe("connecting");
    expect(result.label).toBe("Connecting…");
    expect(result.reason).toContain("Ищем GPU");
  });

  it("a search the backend cannot point at is red, never an endless Connecting", () => {
    // This is the 481-second symptom: `searching` with no operation behind it. The backend now
    // says so itself, and the badge must not turn that into amber.
    const result = state({
      ai: searching({
        compute_search_active: false,
        compute_search_deadline: null,
      }),
    });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("waiting");
    expect(result.label).not.toBe("Connecting…");
    expect(result.action).toBe("retry");
  });

  it("a search whose deadline has passed is red", () => {
    const result = state({
      ai: searching({
        compute_search_active: undefined,
        compute_search_deadline: "2026-09-23T09:59:00+00:00",
      }),
    });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("waiting");
  });

  it("carries the backend's typed capacity reason when the search is over", () => {
    const result = state({
      ai: aiChip(
        "starting",
        {
          provider: "alex-cloud",
          configured: true,
          compact_ai: "unavailable",
          compute_state: "searching",
          compute_search_active: false,
          compute_search_deadline: null,
        },
        {
          message:
            "Подходящих GPU сейчас нет в наличии. Поиск можно повторить.",
        },
      ),
    });

    expect(result.state).toBe("disconnected");
    expect(result.reason).toContain("GPU");
  });

  it("a degraded search with no live operation is red too", () => {
    const result = state({
      ai: aiChip("degraded", {
        provider: "alex-cloud",
        configured: true,
        compact_ai: "waiting",
        compute_state: "searching",
        compute_search_active: false,
      }),
    });

    expect(result.state).toBe("disconnected");
  });

  it("a Pod that really is starting stays amber in every stage", () => {
    for (const [compute, words] of [
      ["creating", "Создаём Pod"],
      ["starting_pod", "Запускаем Pod"],
      ["mounting_storage", "Подключаем хранилище"],
      ["loading_model", "Загружаем модель"],
    ] as const) {
      const result = state({
        ai: aiChip("starting", {
          provider: "alex-cloud",
          configured: true,
          compact_ai: "starting",
          compute_state: compute,
          compute_search_active: false,
        }),
      });

      expect(result.state).toBe("connecting");
      expect(result.reason).toContain(words);
    }
  });

  it("a ready compute is green even if the last search flag is stale", () => {
    const result = state({
      ai: aiChip("ready", {
        provider: "alex-cloud",
        configured: true,
        compact_ai: "ready",
        compute_state: "ready",
        compute_search_active: false,
      }),
    });

    expect(result.state).toBe("connected");
  });
});

describe("the global word belongs to the AI alone", () => {
  const cloud = { state: "connected", enrolled: true };

  function html(ai: SubsystemStatus, input: Partial<AiConnectionInput> = {}) {
    const board = snapshot(ai);
    const value = input.snapshot === undefined ? board : input.snapshot;
    const stale = input.stale ?? false;
    const connection = aiConnection({
      snapshot: value,
      stale,
      backendReady: input.backendReady ?? true,
    });
    return renderToStaticMarkup(
      <>
        <AiConnectionBadge
          snapshot={value}
          stale={stale}
          backendReady={input.backendReady ?? true}
          cloud={cloud}
          onAction={() => {}}
        />
        <AiConnectionDetail
          connection={connection}
          rows={aiConnectionRows(value, stale)}
          infra={aiInfrastructure({
            backendReady: true,
            cloudState: cloud.state,
            cloudEnrolled: cloud.enrolled,
          })}
          onAction={() => {}}
        />
      </>,
    );
  }

  it("renders the headline state with its own data attributes", () => {
    const markup = html(READY);

    expect(markup).toContain('data-testid="ai-connection"');
    expect(markup).toContain('data-state="connected"');
    expect(markup).toContain('data-code="ready"');
    expect(markup).toContain(">Connected<");
  });

  it("keeps the backend and the Cloud as named rows instead of the headline", () => {
    const markup = html(NOT_CONFIGURED);

    expect(markup).toContain('data-state="disconnected"');
    expect(markup).toContain('data-testid="ai-connection-backend">Готов<');
    expect(markup).toContain('data-testid="ai-connection-cloud">Подключено<');
    // The global word is the badge's alone: the two infrastructure rows are named, and the word
    // the old badge used for a live backend appears nowhere as a headline.
    expect(markup).toContain(">Disconnected<");
    expect(markup).not.toMatch(/>Connected</);
    expect(markup).toContain("<dt>Backend</dt>");
    expect(markup).toContain("<dt>Canalla Cloud</dt>");
  });

  it("offers the configuration call to action for a missing configuration", () => {
    const markup = html(KEY_MISSING);

    expect(markup).toContain('data-testid="ai-connection-action"');
    expect(markup).toContain("Настроить AI");
  });

  it("offers no button for a state that only compute may change", () => {
    const markup = html(NO_POD);

    expect(markup).not.toContain('data-testid="ai-connection-action"');
    expect(markup).toContain("Запустить AI можно в управлении compute.");
  });

  it("shows the AI rows the backend proved, and no rows at all when stale", () => {
    expect(html(READY)).toContain("<dt>Провайдер</dt>");
    expect(html(READY, { stale: true })).not.toContain("<dt>Провайдер</dt>");
  });

  it("reports an unreachable Cloud as its own fact, never as the AI state", () => {
    const connection = aiConnection({
      snapshot: snapshot(READY),
      stale: false,
      backendReady: true,
    });
    const markup = renderToStaticMarkup(
      <AiConnectionDetail
        connection={connection}
        rows={[]}
        infra={aiInfrastructure({
          backendReady: true,
          cloudState: "unavailable",
          cloudEnrolled: true,
        })}
        onAction={() => {}}
      />,
    );

    // A Cloud the installation cannot reach is its own row; the AI that just answered stays green.
    expect(connection.state).toBe("connected");
    expect(markup).toContain("AI: Connected");
    expect(markup).toContain('data-testid="ai-connection-cloud">Недоступно<');
  });

  it("says nothing about a Cloud the installation never enrolled in", () => {
    expect(
      aiInfrastructure({
        backendReady: true,
        cloudState: null,
        cloudEnrolled: false,
      }).cloud,
    ).toBe("Не подключено");
  });

  it("reports a backend that stopped answering", () => {
    const markup = html(READY, { snapshot: null, backendReady: false });

    expect(markup).toContain('data-state="disconnected"');
    expect(markup).toContain("Backend не отвечает");
  });
});
