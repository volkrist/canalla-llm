// Context meter: formatting, thresholds and the rendered markup the composer shows.
//
// The numbers themselves are the backend's job (tests/test_context_usage.py); here we
// pin what the user sees and the exact warning boundaries (70 / 85).

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ContextUsageMeter, {
  ContextBreakdown,
} from "../components/ContextUsageMeter";
import {
  CONTEXT_DANGER_PERCENT,
  CONTEXT_UNAVAILABLE_LABEL,
  CONTEXT_WARNING_PERCENT,
  contextHint,
  contextLevel,
  contextSentence,
  contextUsageRequest,
  formatPercent,
  formatTokens,
  isEstimated,
  measuredLine,
  meterState,
  ringDash,
  usedLabel,
  type ContextUsage,
} from "./context-usage";

const usage: ContextUsage = {
  model: "orcarouter-qwen38-27b-q5km",
  limit_tokens: 32768,
  used_tokens: 12480,
  remaining_tokens: 20288,
  percent: 38.1,
  estimated: true,
  method: "chars_per_token",
  parts: [
    { key: "system", label: "System", chars: 1200, tokens: 302 },
    { key: "history", label: "History", chars: 40000, tokens: 11800 },
    { key: "draft", label: "Current draft", chars: 900, tokens: 378 },
  ],
  measured: null,
};

function at(percent: number, extra: Partial<ContextUsage> = {}): ContextUsage {
  const used = Math.round((32768 * percent) / 100);
  return {
    ...usage,
    percent,
    used_tokens: used,
    remaining_tokens: Math.max(0, 32768 - used),
    ...extra,
  };
}

describe("context level thresholds", () => {
  it("is normal below 70 percent", () => {
    expect(contextLevel(0)).toBe("ok");
    expect(contextLevel(38.1)).toBe("ok");
    expect(contextLevel(CONTEXT_WARNING_PERCENT - 0.1)).toBe("ok");
  });

  it("warns from 70 up to 85 percent", () => {
    expect(contextLevel(CONTEXT_WARNING_PERCENT)).toBe("warning");
    expect(contextLevel(77)).toBe("warning");
    expect(contextLevel(CONTEXT_DANGER_PERCENT)).toBe("warning");
  });

  it("is dangerous above 85 percent", () => {
    expect(contextLevel(CONTEXT_DANGER_PERCENT + 0.1)).toBe("danger");
    expect(contextLevel(150)).toBe("danger");
  });

  it("treats a broken number as normal instead of alarming", () => {
    expect(contextLevel(Number.NaN)).toBe("ok");
  });
});

describe("formatting", () => {
  it("groups tokens the way the composer shows them", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1000)).toBe("1 000");
    expect(formatTokens(12480)).toBe("12 480");
    expect(formatTokens(32768)).toBe("32 768");
    expect(formatTokens(1234567)).toBe("1 234 567");
  });

  it("never shows a negative or fractional count", () => {
    expect(formatTokens(-5)).toBe("0");
    expect(formatTokens(12.6)).toBe("13");
  });

  it("rounds the percentage for the badge", () => {
    expect(formatPercent(38.1)).toBe("38%");
    expect(formatPercent(38.6)).toBe("39%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(-3)).toBe("0%");
  });

  it("describes the meter in one accessible sentence", () => {
    expect(contextSentence(usage)).toBe(
      "Context: ~12 480 из 32 768 токенов, 38%",
    );
    expect(contextSentence({ ...usage, count_type: "exact" })).toBe(
      "Context: 12 480 из 32 768 токенов, 38%",
    );
  });

  it("marks an estimate with a tilde and leaves an exact count clean", () => {
    expect(usedLabel(usage)).toBe("~12 480 / 32 768");
    expect(usedLabel({ ...usage, count_type: "exact" })).toBe(
      "12 480 / 32 768",
    );
    expect(isEstimated(usage)).toBe(true);
    expect(isEstimated({ ...usage, count_type: "exact" })).toBe(false);
  });

  it("only names a measured prompt when the backend measured one", () => {
    expect(measuredLine(null)).toBeNull();
    expect(measuredLine({ prompt_tokens: null, at: null })).toBeNull();
    expect(
      measuredLine({ prompt_tokens: 11800, at: "2026-09-21T10:00:00Z" }),
    ).toBe("Последний запрос: 11 800 токенов (измерено)");
  });
});

describe("ring geometry", () => {
  it("fills with the percentage of the circumference", () => {
    const circumference = 2 * Math.PI * 13.25;
    expect(ringDash(0, 13.25).dash).toBe(0);
    expect(ringDash(50, 13.25).dash).toBeCloseTo(circumference / 2, 1);
    expect(ringDash(100, 13.25).dash).toBeCloseTo(circumference, 1);
  });

  it("never draws more than a full circle, however full the context is", () => {
    const full = ringDash(100, 13.25);
    expect(ringDash(180, 13.25)).toEqual(full);
    expect(ringDash(180, 13.25).gap).toBe(0);
  });

  it("grows monotonically and always sums to the circumference", () => {
    let previous = -1;
    for (const percent of [0, 10, 35, 70, 85, 99, 100]) {
      const { dash, gap } = ringDash(percent, 13.25);
      expect(dash).toBeGreaterThan(previous);
      expect(dash + gap).toBeCloseTo(2 * Math.PI * 13.25, 1);
      previous = dash;
    }
  });
});

describe("hints", () => {
  it("stays quiet while there is room", () => {
    expect(contextHint("ok")).toBeNull();
  });

  it("warns and then explains the trimming risk", () => {
    expect(contextHint("warning")).toContain("Контекст заполняется");
    expect(contextHint("danger")).toContain("обрезаны");
    expect(contextHint("danger")).toContain("Контекст почти заполнен");
  });
});

describe("context meter rendering", () => {
  it("renders nothing before the first snapshot arrives", () => {
    expect(renderToStaticMarkup(<ContextUsageMeter usage={null} />)).toBe("");
  });

  it("shows a compact unavailable state instead of a stale number", () => {
    const html = renderToStaticMarkup(
      <ContextUsageMeter usage={null} failed />,
    );
    expect(html).toContain(CONTEXT_UNAVAILABLE_LABEL);
    expect(html).toContain('data-level="unavailable"');
    expect(html).not.toContain("context-ring-value");
    expect(html).not.toContain("context-percent");
  });

  it("never shows the previous percent as current after a failure", () => {
    // The hook drops the snapshot on a failed read; the component must not resurrect it.
    const html = renderToStaticMarkup(
      <ContextUsageMeter usage={usage} failed />,
    );
    expect(html).not.toContain("38%");
    expect(html).toContain(CONTEXT_UNAVAILABLE_LABEL);
  });

  it("shows label, used/limit and percentage", () => {
    const html = renderToStaticMarkup(<ContextUsageMeter usage={usage} />);
    expect(html).toContain("Context");
    expect(html).toContain("~12 480 / 32 768");
    expect(html).toContain("38%");
    expect(html).toContain('data-level="ok"');
    expect(html).toContain(
      'aria-label="Context: ~12 480 из 32 768 токенов, 38%"',
    );
    expect(html).toContain("context-ring-value");
  });

  it("keeps the breakdown closed until the user asks for it", () => {
    const html = renderToStaticMarkup(<ContextUsageMeter usage={usage} />);
    expect(html).not.toContain("Разбивка контекста");
    expect(html).toContain('aria-expanded="false"');
  });

  it("marks a nearly full context as dangerous and says why", () => {
    const html = renderToStaticMarkup(<ContextUsageMeter usage={at(92)} />);
    expect(html).toContain('data-level="danger"');
    expect(html).toContain("Контекст почти заполнен");
    expect(html).toContain("92%");
  });

  it("marks the warning band without shouting", () => {
    const html = renderToStaticMarkup(<ContextUsageMeter usage={at(75)} />);
    expect(html).toContain("level-warning");
    expect(html).not.toContain("Контекст почти заполнен");
  });

  it("renders the breakdown with every part, the total and the estimate note", () => {
    const snapshot = at(38.1, {
      measured: { prompt_tokens: 11800, at: "2026-09-21T10:00:00Z" },
    });
    const html = renderToStaticMarkup(
      <ContextBreakdown
        usage={snapshot}
        hint={null}
        measured={measuredLine({ prompt_tokens: 11800, at: null })}
      />,
    );
    expect(html).toContain("System");
    expect(html).toContain("History");
    expect(html).toContain("Current draft");
    expect(html).toContain("11 800");
    expect(html).toContain("Total");
    expect(html).toContain(`${formatTokens(snapshot.used_tokens)} / 32 768`);
    expect(html).toContain("Оценка по активному контексту");
    expect(html).toContain("Последний запрос: 11 800 токенов (измерено)");
  });

  it("shows the hint inside the breakdown when the window is tight", () => {
    const html = renderToStaticMarkup(
      <ContextBreakdown
        usage={at(90)}
        hint={contextHint("danger")}
        measured={null}
      />,
    );
    expect(html).toContain("обрезаны");
    expect(html).not.toContain("измерено");
  });

  it("keeps an overflow visible instead of clamping it into a reassuring number", () => {
    const html = renderToStaticMarkup(<ContextUsageMeter usage={at(140)} />);
    expect(html).toContain('data-level="danger"');
    expect(html).toContain("140%");
    expect(html).toContain(
      'aria-label="Context: ~45 875 из 32 768 токенов, 140%"',
    );
  });
});

describe("preview transport", () => {
  it("carries the draft in the body and never in the URL", () => {
    const draft = "Почему важно проверять контекст перед отправкой? ".repeat(
      700,
    );
    const request = contextUsageRequest("chat-1", draft);
    expect(request.path).toBe("/chats/chat-1/context-usage");
    expect(request.path.length).toBeLessThan(120);
    expect(request.path).not.toContain("Почему");
    expect(request.init.method).toBe("POST");
    expect(JSON.parse(String(request.init.body))).toEqual({ prompt: draft });
  });
});

describe("failure and recovery", () => {
  it("drops the snapshot when the read fails", () => {
    expect(meterState(null, false)).toEqual({ usage: null, failed: true });
  });

  it("publishes a fresh snapshot once the read succeeds again", () => {
    expect(meterState(usage, true)).toEqual({ usage, failed: false });
    expect(meterState(usage, true).failed).toBe(false);
  });

  it("recovers after a failure without keeping the unavailable flag", () => {
    const failed = meterState(null, false);
    expect(failed.failed).toBe(true);
    const recovered = meterState(at(72), true);
    expect(recovered.failed).toBe(false);
    expect(recovered.usage?.percent).toBe(72);
  });
});
