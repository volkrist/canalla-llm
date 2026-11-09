import { describe, expect, it } from "vitest";
import type { AutonomousTask } from "./tools";
import { phaseOf, taskStatusLine } from "./task-status";

const NOW = Date.parse("2026-11-09T12:00:00Z");

function task(extra: Partial<AutonomousTask> = {}): AutonomousTask {
  return {
    id: "t1",
    title: "Fix the parser",
    status: "EXECUTING",
    chat_id: "c1",
    workspace: "C:\\work",
    current_step: "",
    current_phase: "EXECUTING",
    plan_revision: 1,
    tool_calls_used: 4,
    tool_budget: 40,
    files_changed: 1,
    file_change_budget: 20,
    elapsed_runtime: 90,
    runtime_budget: 1800,
    last_error: null,
    completion_summary: null,
    message: "Working",
    steps: [
      {
        id: "s1",
        title: "Inspect",
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
        title: "Build",
        description: "",
        status: "RUNNING",
        tool_category: "local_fs",
        verification_required: false,
        attempts: 1,
        result_summary: "",
        key: "build",
      },
    ],
    phase: "executing",
    terminal: false,
    completed_count: 1,
    remaining_count: 1,
    current_action: "Build",
    idle_seconds: 3,
    stalled: false,
    ...extra,
  };
}

describe("the task status line", () => {
  it("reads the backend phase, never a guessed one", () => {
    expect(phaseOf(task())).toBe("executing");
    // An older payload without `phase` falls back to the persisted status.
    expect(
      phaseOf(task({ phase: undefined, status: "WAITING_CONFIRMATION" })),
    ).toBe("waiting_permission");
  });

  it("Test A: a finished web family does not finish the task", () => {
    const line = taskStatusLine(
      task({
        phase: "researching",
        current_action: "Read sources",
        web: {
          searches: 1,
          fetches: 0,
          tor_searches: 0,
          tor_fetches: 0,
          pages_requested: 0,
          pages_opened: 0,
          pages_failed: 0,
          by_origin: { model: 1 },
        },
      }),
      NOW,
    );
    expect(line.terminal).toBe(false);
    expect(line.label).toBe("Работает");
    expect(line.detail).toContain("Read sources");
  });

  it("Test B: 3/3 steps with verification running is still working", () => {
    const line = taskStatusLine(
      task({
        phase: "verifying",
        completed_count: 3,
        remaining_count: 0,
        current_action: "Run pytest",
        steps: task().steps.map((step) => ({ ...step, status: "COMPLETED" })),
      }),
      NOW,
    );
    expect(line.terminal).toBe(false);
    expect(line.label).toBe("Работает");
    expect(line.detail).toContain("Run pytest");
    expect(line.detail).not.toContain("Завершено");
  });

  it("Test C: no first token says so with the real wait", () => {
    const line = taskStatusLine(
      task({
        phase: "waiting_model",
        error_code: "llm_timeout",
        idle_seconds: 680,
        stalled: true,
      }),
      NOW,
    );
    expect(line.label).toBe("Ожидается ответ модели");
    expect(line.detail).toContain("11 мин назад");
    expect(line.detail).toContain("llm_timeout");
  });

  it("Test D: found but not read is stated honestly", () => {
    const line = taskStatusLine(
      task({
        phase: "researching",
        web: {
          searches: 1,
          fetches: 0,
          tor_searches: 0,
          tor_fetches: 0,
          pages_requested: 0,
          pages_opened: 0,
          pages_failed: 0,
          by_origin: { model: 1 },
        },
      }),
      NOW,
    );
    expect(line.terminal).toBe(false);
    expect(line.label).toBe("Работает");
    expect(line.detail).toContain("источники найдены, страницы не прочитаны");
  });

  it("claims completion only for a closed task", () => {
    const line = taskStatusLine(
      task({
        phase: "completed",
        terminal: true,
        completion_summary: "Проект создан, 5 тестов пройдено.",
      }),
      NOW,
    );
    expect(line.label).toBe("Завершено");
    expect(line.detail).toContain("5 тестов пройдено");
    expect(line.tone).toBe("done");
  });

  it("a partial failure names what is left", () => {
    const line = taskStatusLine(
      task({
        phase: "partial",
        terminal: true,
        stop_reason: "tests_not_verified",
        completed_count: 2,
        remaining_count: 1,
        steps: [
          ...task().steps,
          {
            id: "s3",
            title: "Verify",
            description: "",
            status: "PENDING",
            tool_category: "verification",
            verification_required: true,
            attempts: 0,
            result_summary: "",
            key: "verify",
          },
        ],
      }),
      NOW,
    );
    expect(line.label).toBe("Не удалось завершить");
    expect(line.detail).toContain("tests_not_verified");
    expect(line.detail).toContain("выполнено 2 из 3");
  });

  it("a user stop is a cancellation, not a failure", () => {
    const line = taskStatusLine(
      task({ phase: "cancelled", terminal: true, stop_reason: "user" }),
      NOW,
    );
    expect(line.label).toBe("Остановлено");
    expect(line.detail).toBe("остановлено вами");
  });

  it("a stall is visible instead of an eternal Working", () => {
    const line = taskStatusLine(
      task({ phase: "executing", stalled: true, idle_seconds: 1200 }),
      NOW,
    );
    expect(line.tone).toBe("error");
    expect(line.detail).toContain("нет событий");
    expect(line.detail).toContain("20 мин назад");
  });
});
