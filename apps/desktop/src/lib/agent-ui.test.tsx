import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import AiConnectionBadge from "../components/AiConnectionBadge";
import Composer from "../components/Composer";
import StatusChips from "../components/StatusChips";
import { aiConnection } from "./ai-connection";
import type { BalanceStatus, StatusSnapshot, SubsystemStatus } from "./status";

const balance: BalanceStatus = {
  configured: true,
  available: true,
  balance_usd: "8.73",
  account_spend_per_hr: "0.12",
  low: false,
  low_threshold_usd: "5.00",
  stale: false,
  error_code: null,
  message: null,
  fetched_at: "2026-10-19T08:00:00+00:00",
  last_success_at: "2026-10-19T08:00:00+00:00",
  refresh_seconds: 15,
  shared_account: true,
  read_only: true,
  active_session: null,
};

function subsystem(
  state: SubsystemStatus["state"],
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

function snapshot(ai: SubsystemStatus = subsystem("ready")): StatusSnapshot {
  return {
    generated_at: "2026-10-19T08:00:00+00:00",
    subsystems: {
      ai,
      computer: subsystem("ready"),
      web: subsystem("configured"),
      tor: subsystem("ready"),
      memory: subsystem("ready"),
    },
    balance,
  };
}

describe("navigation and status survive a running generation", () => {
  it("keeps the composer editable while the answer streams", () => {
    const html = renderToStaticMarkup(
      <Composer
        busy
        streaming
        connected
        draft="второе сообщение"
        setDraft={() => {}}
        onSend={async () => true}
        onStop={() => {}}
      />,
    );

    expect(html).toContain("второе сообщение");
    expect(html).not.toMatch(/<textarea[^>]*disabled/);
  });

  it("shows a reconnecting hint without blanking every chip", () => {
    const html = renderToStaticMarkup(
      <StatusChips
        snapshot={snapshot()}
        error=""
        refreshing={false}
        reconnecting
        onAction={() => {}}
      />,
    );

    expect(html).toContain("Переподключение…");
    expect(html).toContain(">Готово</span>");
  });

  it("never shows connected when the AI chip contradicts a stopped GPU", () => {
    const connection = aiConnection({
      snapshot: snapshot(
        subsystem("ready", { details: { compute_state: "off" } }),
      ),
      stale: false,
      backendReady: true,
    });

    expect(connection.state).toBe("disconnected");
    expect(connection.code).toBe("off");
  });

  it("stays connected while generating even after the idle readiness TTL", () => {
    const connection = aiConnection({
      snapshot: snapshot(
        subsystem("ready", {
          details: {
            compute_state: "generating",
            readiness_valid_until: "2026-10-19T07:00:00+00:00",
          },
        }),
      ),
      stale: false,
      backendReady: true,
      now: Date.parse("2026-10-19T08:00:00+00:00"),
    });

    expect(connection.state).toBe("connected");
    expect(connection.code).toBe("ready");
  });

  it("offers a stop action for a connected paid AI", () => {
    const html = renderToStaticMarkup(
      <AiConnectionBadge
        snapshot={snapshot()}
        stale={false}
        backendReady
        cloud={{ state: "connected", enrolled: true }}
        onAction={() => {}}
        onStopAi={() => {}}
      />,
    );

    expect(html).toContain('data-testid="ai-stop"');
    expect(html).toContain("Отключить AI");
  });

  it("never shows the stop action when there is no AI", () => {
    const html = renderToStaticMarkup(
      <AiConnectionBadge
        snapshot={snapshot(subsystem("off"))}
        stale={false}
        backendReady
        cloud={{ state: "connected", enrolled: true }}
        onAction={() => {}}
        onStopAi={() => {}}
      />,
    );

    expect(html).not.toContain('data-testid="ai-stop"');
  });
});
