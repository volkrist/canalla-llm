import type { AutonomousTask } from "../lib/tools";

export default function TaskHistory({
  tasks,
  onOpen,
}: {
  tasks: AutonomousTask[];
  onOpen: (id: string, chatId: string | null) => void;
}) {
  if (!tasks.length) return null;
  return (
    <details className="task-history">
      <summary>История задач</summary>
      <ul>
        {tasks.map((task) => (
          <li key={task.id}>
            <button type="button" onClick={() => onOpen(task.id, task.chat_id)}>
              {task.title || "Задача"} · {task.message || task.status} ·{" "}
              {task.files_changed} файлов
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
