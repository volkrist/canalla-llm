import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ToolActivity from "../components/ToolActivity";
import Composer from "../components/Composer";
import type { Api } from "./api";
import type { ToolRun } from "./tools";

const run: ToolRun = {
  id: "one",
  chat_id: "chat",
  generation_id: "generation",
  tool_name: "browser_write",
  provider: "tinyfish",
  status: "waiting_confirmation",
  risk_level: "EXTERNAL_SIDE_EFFECT",
  input_summary: { site: "example.com", action: "click", target: "Submit" },
  cost_actual: null,
  cost_estimate: null,
  result_metadata: {},
  error_code: null,
};
describe("tool presentation", () => {
  it("shows a concrete one-time confirmation and no invented charge", () => {
    const html = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[run]}
        state="waiting_confirmation"
      />,
    );
    expect(html).toContain("Разрешить один раз");
    expect(html).toContain("Отменить действие");
    expect(html).toContain("example.com");
    expect(html).not.toContain("фактически");
  });
  it("escapes provider text and distinguishes estimated from actual cost", () => {
    const html = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[
          {
            ...run,
            status: "completed",
            tool_name: "<script>bad</script>",
            cost_estimate: 0.048,
          },
        ]}
        state="completed"
      />,
    );
    expect(html).not.toContain("<script>");
    expect(html).toContain("оценка $0.0480");
    expect(html).not.toContain("фактически");
  });
  it("does not show duplicate missing-provider errors", () => {
    const html = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[
          { ...run, status: "failed", error_code: "provider_not_configured" },
        ]}
        state="failed"
        error="provider_not_configured"
      />,
    );
    expect(html.split("TinyFish API не настроен").length - 1).toBe(1);
  });
  it("exposes all Web modes and an explicit search action", () => {
    const html = renderToStaticMarkup(
      <Composer
        busy={false}
        streaming={false}
        connected
        onSend={async () => true}
        onStop={() => {}}
        draft="question"
        setDraft={() => {}}
        webMode="auto"
      />,
    );
    expect(html).toContain('value="off"');
    expect(html).toContain('value="auto" selected');
    expect(html).toContain('value="on"');
    expect(html).toContain("Найти в интернете");
  });
});
