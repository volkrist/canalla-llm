import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ToolActivity from "../components/ToolActivity";
import TaskPanel from "../components/TaskPanel";
import Composer from "../components/Composer";
import type { Api } from "./api";
import { publicAssistantText, summarizeFamily, type ToolRun } from "./tools";

const run: ToolRun = {
  id: "one",
  chat_id: "chat",
  generation_id: "generation",
  tool_name: "browser_write",
  provider: "tinyfish",
  status: "waiting_confirmation",
  risk_level: "SENSITIVE",
  input_summary: {
    reason: "нужно нажать Submit",
    action_detail: "click",
    target: "Submit",
    consequences: "страница изменится",
    risk_level: "SENSITIVE",
    elevation_required: "no",
    site: "example.com",
    action: "click",
  },
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
    expect(html).toContain("Отмена");
    expect(html).not.toContain("Always allow");
    expect(html).toContain("Причина");
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
  it("collapses web search/fetch and keeps confirmation outside the summary", () => {
    const html = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[
          {
            ...run,
            id: "s1",
            tool_name: "web_search",
            status: "completed",
            risk_level: "READ",
          },
          {
            ...run,
            id: "f1",
            tool_name: "web_fetch",
            status: "completed",
            risk_level: "READ",
          },
          {
            ...run,
            id: "f2",
            tool_name: "web_fetch",
            status: "completed",
            risk_level: "READ",
          },
          run,
        ]}
        state="completed"
      />,
    );
    expect(html).toContain("Веб · 3 запросов · 1 Search · 2 Fetch · Завершено");
    expect(html).toContain("Разрешить один раз");
    expect(html.indexOf("alertdialog")).toBeLessThan(html.indexOf("Веб ·"));
  });
  it("collapses TinyFish Agent and Browser cost lines", () => {
    expect(
      summarizeFamily("web", [
        {
          ...run,
          tool_name: "web_agent",
          status: "completed",
          cost_estimate: 0.096,
          result_metadata: { steps: 6 },
        },
        {
          ...run,
          id: "b1",
          tool_name: "web_browser",
          status: "completed",
          cost_estimate: 0.006,
          result_metadata: { duration_seconds: 134 },
        },
      ]),
    ).toContain("TinyFish Agent · 6 steps · ~$0.096");
    expect(
      summarizeFamily("web", [
        {
          ...run,
          tool_name: "web_browser",
          status: "completed",
          cost_estimate: 0.006,
          result_metadata: { duration_seconds: 134 },
        },
      ]),
    ).toContain("TinyFish Browser · 2m 14s · ~$0.006");
  });
  it("summarizes computer file actions", () => {
    expect(
      summarizeFamily("computer", [
        { ...run, tool_name: "write_file", status: "completed" },
        { ...run, tool_name: "read_file", status: "completed" },
      ]),
    ).toBe("Компьютер · 2 действий · 1 файлов изменено · Завершено");
  });
  it("shows Network Direct or Tor and never Always allow for CRITICAL", () => {
    const html = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[
          {
            ...run,
            tool_name: "system_shutdown",
            provider: "local_device",
            risk_level: "CRITICAL",
            result_metadata: {},
            input_summary: {
              reason: "нужна перезагрузка",
              action_detail: "reboot",
              target: "system",
              consequences: "работа может быть потеряна",
              risk_level: "CRITICAL",
              elevation_required: "yes",
            },
          },
        ]}
        state="waiting_confirmation"
      />,
    );
    expect(html).toContain("Я понимаю риск — разрешить один раз");
    expect(html).toContain("Высокий риск");
    expect(html).not.toContain("Always allow");
    const tor = renderToStaticMarkup(
      <ToolActivity
        api={{} as Api}
        runs={[
          {
            ...run,
            id: "t1",
            tool_name: "tor_search",
            provider: "tor",
            status: "completed",
            risk_level: "READ",
            result_metadata: { network: "tor" },
          },
          {
            ...run,
            id: "w1",
            tool_name: "web_search",
            provider: "tinyfish",
            status: "completed",
            risk_level: "READ",
            result_metadata: { network: "direct" },
          },
        ]}
        state="completed"
      />,
    );
    expect(tor).toContain("Network: Tor");
    expect(tor).toContain("Network: Direct");
    expect(tor).toContain(
      "Tor · 1 действий · 1 Search · 0 Fetch · 0 Browser · Завершено",
    );
  });
  it("shows force-web only in Auto mode", () => {
    const auto = renderToStaticMarkup(
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
    expect(auto).toContain('aria-label="Tor mode"');
    expect(auto).toContain('value="off"');
    expect(auto).toContain('value="auto" selected');
    expect(auto).toContain('value="on"');
    expect(auto).toContain("Найти в интернете");
    const on = renderToStaticMarkup(
      <Composer
        busy={false}
        streaming={false}
        connected
        onSend={async () => true}
        onStop={() => {}}
        draft="question"
        setDraft={() => {}}
        webMode="on"
      />,
    );
    const off = renderToStaticMarkup(
      <Composer
        busy={false}
        streaming={false}
        connected
        onSend={async () => true}
        onStop={() => {}}
        draft="question"
        setDraft={() => {}}
        webMode="off"
      />,
    );
    expect(on).not.toContain("Найти в интернете");
    expect(off).not.toContain("Найти в интернете");
  });
});

describe("public assistant text", () => {
  it("strips raw tool protocol", () => {
    expect(
      publicAssistantText('<tool_call>{"name":"write_file"} leftover'),
    ).not.toContain("tool_call");
  });
});

describe("task panel", () => {
  it("shows plan progress and pause stop", () => {
    const html = renderToStaticMarkup(
      <TaskPanel
        task={{
          id: "t1",
          title: "Fix authentication tests",
          status: "EXECUTING",
          chat_id: "c1",
          workspace: "C:\\work",
          current_step: "fix",
          current_phase: "EXECUTING",
          plan_revision: 1,
          tool_calls_used: 12,
          tool_budget: 40,
          files_changed: 2,
          file_change_budget: 20,
          elapsed_runtime: 272,
          runtime_budget: 1800,
          last_error: null,
          completion_summary: null,
          message: "Working",
          steps: [
            {
              id: "s1",
              title: "Inspect project",
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
              title: "Fix refresh token handling",
              description: "",
              status: "RUNNING",
              tool_category: "local_fs",
              verification_required: false,
              attempts: 1,
              result_summary: "",
              key: "fix",
            },
          ],
        }}
        onPause={() => {}}
        onResume={() => {}}
        onStop={() => {}}
      />,
    );
    expect(html).toContain("Fix authentication tests");
    expect(html).toContain("Inspect project");
    expect(html).toContain("Pause");
    expect(html).toContain("Stop");
    expect(html).toContain("12 / 40");
  });
});
