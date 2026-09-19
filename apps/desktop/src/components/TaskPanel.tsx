import type { AutonomousTask } from "../lib/tools";

function mark(status: string) {
  if (status === "COMPLETED") return "✓";
  if (status === "RUNNING" || status === "WAITING") return "●";
  if (status === "FAILED") return "!";
  if (status === "CANCELLED" || status === "SKIPPED") return "–";
  return "○";
}

function clock(seconds: number) {
  const value = Math.max(0, seconds || 0);
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export default function TaskPanel({
  task,
  busy,
  onPause,
  onResume,
  onStop,
}: {
  task: AutonomousTask | null;
  busy?: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
}) {
  if (!task) return null;
  const done = task.steps.filter((step) => step.status === "COMPLETED").length;
  const finished = ["COMPLETED", "FAILED"].includes(task.status);
  return (
    <section className="task-panel" aria-label="Автономная задача">
      <header>
        <strong>{task.title || "Задача"}</strong>
        <span>
          {done} / {task.steps.length} steps
        </span>
      </header>
      <p role="status">
        {task.message || task.status} · {clock(task.elapsed_runtime)} · tools{" "}
        {task.tool_calls_used} / {task.tool_budget} · files {task.files_changed}{" "}
        / {task.file_change_budget}
        {task.status === "WAITING_WORKSPACE" && task.queue_position
          ? ` · очередь ${task.queue_position}`
          : ""}
      </p>
      <ol className="task-plan">
        {task.steps.map((step) => (
          <li key={step.id} data-status={step.status}>
            {mark(step.status)} {step.title}
          </li>
        ))}
      </ol>
      {task.completion_summary && <p>{task.completion_summary}</p>}
      <div className="task-actions">
        {task.status !== "PAUSED" && !finished && task.status !== "STOPPED" && (
          <button type="button" disabled={busy} onClick={onPause}>
            Pause
          </button>
        )}
        {(task.status === "PAUSED" ||
          task.status === "INTERRUPTED" ||
          task.status === "WAITING_LLM" ||
          task.status === "WAITING_DEVICE" ||
          task.status === "WAITING_WORKSPACE" ||
          task.status === "STOPPED") && (
          <button type="button" disabled={busy} onClick={onResume}>
            Resume
          </button>
        )}
        {task.status !== "STOPPED" && !finished && (
          <button type="button" disabled={busy} onClick={onStop}>
            Stop
          </button>
        )}
      </div>
    </section>
  );
}
