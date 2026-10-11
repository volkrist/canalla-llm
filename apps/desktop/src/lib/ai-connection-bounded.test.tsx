import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import AiConnectionBadge, {
  AiConnectionDetail,
} from "../components/AiConnectionBadge";
import {
  aiConnection,
  aiConnectionRows,
  readTransition,
  STAGE_BUDGET_SECONDS,
  type AiConnectionInput,
} from "./ai-connection";
import type {
  BalanceStatus,
  ChipKey,
  StatusSnapshot,
  SubsystemState,
  SubsystemStatus,
} from "./status";

/** A30/A31/A32: a transition is a bounded claim about one attempt, never a mood.
 *
 * Every case below drives the clocks explicitly — the snapshot's own timestamp and an injected
 * «now» — so «one hour later» is a test input rather than a wait. Nothing here touches compute:
 * these tests also prove the module is pure and issues no request of any kind.
 */

const BALANCE: BalanceStatus = {
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

const BASE = Date.parse("2026-09-23T10:00:00+00:00");

function chip(
  state: SubsystemState,
  details: Record<string, unknown>,
): SubsystemStatus {
  return {
    state,
    message: "",
    detail_code: null,
    recoverable: false,
    action: null,
    details,
  };
}

function snapshot(
  ai: SubsystemStatus,
  generatedAt = "2026-09-23T10:00:00+00:00",
): StatusSnapshot {
  const subsystems = {
    ai,
    computer: chip("ready", {}),
    web: chip("configured", {}),
    tor: chip("ready", {}),
    memory: chip("off", {}),
  } as Record<ChipKey, SubsystemStatus>;
  return { generated_at: generatedAt, subsystems, balance: BALANCE };
}

/** An AI chip in a given compute stage, exactly as `app/status/snapshot.py` shapes it. */
function running(
  compute: string,
  extra: Record<string, unknown> = {},
): SubsystemStatus {
  return chip("starting", {
    provider: "alex-cloud",
    model: "Qwen3.8-27B",
    configured: true,
    compact_ai: "starting",
    compute_state: compute,
    compute_search_active: false,
    ...extra,
  });
}

function verdict(
  ai: SubsystemStatus,
  input: Partial<AiConnectionInput> = {},
  generatedAt?: string,
) {
  return aiConnection({
    snapshot: snapshot(ai, generatedAt),
    stale: false,
    backendReady: true,
    now: BASE + 30_000,
    ...input,
  });
}

describe("A30 — a transition is never an endless Connecting", () => {
  it("a starting_pod whose snapshot nobody refreshed stops promising after an hour", () => {
    // The exact acceptance case: starting_pod, the Gateway is unreachable, and an hour passes.
    const result = verdict(running("starting_pod"), { now: BASE + 3_600_000 });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("expired");
    expect(result.label).not.toBe("Connecting…");
    // A terminal failure is a sentence the user can act on, plus one explicit action.
    expect(result.reason).not.toContain("starting_pod");
    expect(result.reason).toMatch(/[А-Яа-я]/);
    expect(result.action).toBe("retry");
  });

  it("the same stage stays Connecting while the snapshot is genuinely fresh", () => {
    const result = verdict(running("starting_pod"), { now: BASE + 30_000 });

    expect(result.state).toBe("connecting");
    expect(result.code).toBe("starting");
  });

  it("a snapshot that goes stale never keeps the amber promise", () => {
    const result = verdict(running("loading_model"), { stale: true });

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("snapshot_stale");
    expect(result.action).toBe("retry");
  });

  it("an attempt with a server deadline that has passed is a typed terminal failure", () => {
    const result = verdict(
      running("starting_pod", {
        startup_deadline: "2026-09-23T10:00:10+00:00",
      }),
      { now: BASE + 30_000 },
    );

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("expired");
    expect(result.deadlineSource).toBe("server");
  });

  it("a live attempt with a server deadline keeps the stage and reports where the bound came from", () => {
    const result = verdict(
      running("starting_pod", {
        operation_id: "op-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        started_at: "2026-09-23T10:00:05+00:00",
        startup_deadline: "2026-09-23T10:05:00+00:00",
      }),
      { now: BASE + 30_000 },
    );

    expect(result.state).toBe("connecting");
    expect(result.stage).toBe("Запускаем Pod");
    expect(result.stageSince).toBe("2026-09-23T10:00:05+00:00");
    expect(result.elapsedSeconds).toBe(25);
    expect(result.deadlineSource).toBe("server");
    // No transition offers a button: the status surface is read-only and starts nothing.
    expect(result.action).toBeNull();
    // The operation id is safe to show in the expanded diagnostics.
    expect(result.operationId).toBe("op-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
  });

  it("an expired operation does not block a later attempt", () => {
    // The same operation id observed late is terminal; a *new* attempt (fresh observation) is amber
    // again, with no state carried over from the dead one.
    const expired = verdict(running("starting_pod"), { now: BASE + 3_600_000 });
    const fresh = aiConnection({
      snapshot: snapshot(running("starting_pod"), "2026-09-23T12:00:00+00:00"),
      stale: false,
      backendReady: true,
      now: Date.parse("2026-09-23T12:00:30+00:00"),
    });

    expect(expired.code).toBe("expired");
    expect(fresh.state).toBe("connecting");
    expect(fresh.code).toBe("starting");
  });
});

describe("A31 — the bound is derived, named and bounded", () => {
  it("a stage with no server deadline gets the client's own budget, labelled as such", () => {
    const transition = readTransition(
      { compute_state: "starting_pod" },
      "2026-09-23T10:00:00+00:00",
    );

    expect(transition.deadlineSource).toBe("derived");
    expect(Date.parse(transition.deadline!)).toBe(
      BASE + STAGE_BUDGET_SECONDS.starting_pod * 1000,
    );
  });

  it("no stage budget is longer than the documented server-side windows", () => {
    // Never hours: the client must not promise a transition the product already bounds.
    for (const [stage, seconds] of Object.entries(STAGE_BUDGET_SECONDS)) {
      expect(seconds, stage).toBeGreaterThan(0);
      expect(seconds, stage).toBeLessThanOrEqual(300);
    }
  });

  it("the stage name comes from the client only when the server does not send one", () => {
    const derived = readTransition({ compute_state: "loading_model" }, null);
    const server = readTransition(
      { compute_state: "starting_pod", current_stage: "queueing" },
      null,
    );

    expect(derived.stage).toBe("loading_model");
    expect(derived.stageSource).toBe("derived");
    expect(server.stage).toBe("queueing");
    expect(server.stageSource).toBe("server");
  });
});

describe("A32 / stages — the seven stages are named, none of them invented", () => {
  it("renders the server's own stages, including the queue and the context prefill", () => {
    for (const [stage, words] of [
      ["searching", "Ищем GPU"],
      ["creating", "Создаём Pod"],
      ["loading_model", "Загружаем модель"],
      ["queueing", "Ожидаем очередь"],
      ["prefill", "Обрабатываем контекст"],
      ["generating", "Генерируем ответ"],
    ] as const) {
      const result = verdict(running(stage, { compute_search_active: true }));

      expect(result.state, stage).toBe("connecting");
      expect(result.stage, stage).toBe(words);
    }
  });

  it("shows no percentage anywhere: a stage is a fact, not a progress bar", () => {
    const html = renderToStaticMarkup(
      <AiConnectionDetail
        connection={verdict(
          running("loading_model", { started_at: "2026-09-23T10:00:00+00:00" }),
        )}
        rows={aiConnectionRows(
          snapshot(running("loading_model")),
          false,
          verdict(running("loading_model")),
        )}
        infra={{ backend: "Готов", cloud: "Подключено" }}
        onAction={() => {}}
      />,
    );

    expect(html).toContain("Стадия: Загружаем модель");
    expect(html).not.toMatch(/\d+\s?%/u);
  });

  it("keeps the diagnostics free of anything secret", () => {
    const connection = verdict(
      running("starting_pod", {
        operation_id: "op-1234",
        started_at: "2026-09-23T10:00:00+00:00",
      }),
    );
    const rows = aiConnectionRows(
      snapshot(running("starting_pod")),
      false,
      connection,
    );
    const rendered =
      JSON.stringify(rows) +
      renderToStaticMarkup(
        <AiConnectionDetail
          connection={connection}
          rows={rows}
          infra={{ backend: "Готов", cloud: "Подключено" }}
          onAction={() => {}}
        />,
      );

    expect(rendered).toContain("Операция");
    expect(rendered).toContain("op-1234");
    // No token, no installation credential, no provider key: the row set is a whitelist.
    for (const forbidden of [
      "Bearer",
      "eyJ",
      "jwt",
      "secret",
      "installation_secret",
    ]) {
      expect(rendered.toLowerCase()).not.toContain(forbidden.toLowerCase());
    }
  });

  it("names the Gateway check as a stage instead of blaming the model", () => {
    const result = verdict(
      chip("unavailable", {
        configured: true,
        compute_state: null,
        compact_ai: "unavailable",
      }),
      { cloudState: "unavailable" },
    );

    expect(result.state).toBe("connecting");
    expect(result.stage).toBe("Проверяем Gateway");
  });
});

describe("honest indicators — green only for a fresh proof", () => {
  const ready = (extra: Record<string, unknown> = {}) =>
    chip("ready", {
      provider: "alex-cloud",
      configured: true,
      compact_ai: "ready",
      compute_state: "ready",
      compute_search_active: false,
      ...extra,
    });

  it("a readiness proof that has expired is not green", () => {
    const result = verdict(
      ready({ readiness_valid_until: "2026-09-23T09:59:00+00:00" }),
    );

    expect(result.state).toBe("disconnected");
    expect(result.code).toBe("expired");
    expect(result.action).toBe("retry");
  });

  it("a readiness proof that is still valid is green", () => {
    const result = verdict(
      ready({ readiness_valid_until: "2026-09-23T10:05:00+00:00" }),
    );

    expect(result.state).toBe("connected");
    expect(result.code).toBe("ready");
  });

  it("a stale snapshot can never be green, even when it was ready", () => {
    const result = aiConnection({
      snapshot: snapshot(ready()),
      stale: true,
      backendReady: true,
      now: BASE + 30_000,
    });

    expect(result.state).toBe("disconnected");
  });

  it("a Pod that is merely RUNNING is not a green model", () => {
    // `loading_model` is the state a RUNNING Pod has before the alias answered.
    const result = verdict(running("loading_model"));

    expect(result.state).not.toBe("connected");
    expect(result.code).not.toBe("ready");
  });

  it("renders the stage line in the badge's own popover with the elapsed time", () => {
    const now = Date.now();
    const started = new Date(now - 42_000).toISOString();
    const ai = running("loading_model", {
      started_at: started,
      startup_deadline: new Date(now + 300_000).toISOString(),
    });
    const html = renderToStaticMarkup(
      <AiConnectionBadge
        snapshot={snapshot(ai, new Date(now).toISOString())}
        stale={false}
        backendReady
        cloud={{ state: "connected", enrolled: true }}
        onAction={() => {}}
      />,
    );

    expect(html).toContain("Connecting");
  });
});

describe("no write path — a timer is never a Pod", () => {
  it("computing a verdict performs no I/O, whatever the clock says", () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    try {
      for (const seconds of [0, 30, 60, 300, 3_600, 86_400]) {
        for (const compute of [
          "searching",
          "creating",
          "starting_pod",
          "loading_model",
        ]) {
          verdict(running(compute, { compute_search_active: true }), {
            now: BASE + seconds * 1000,
          });
        }
      }
      expect(spy).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("is pure: the same input and clock give the same verdict", () => {
    const ai = running("starting_pod", { compute_search_active: false });
    const first = verdict(ai, { now: BASE + 10_000 });
    const second = verdict(ai, { now: BASE + 10_000 });

    expect(second).toEqual(first);
  });
});
