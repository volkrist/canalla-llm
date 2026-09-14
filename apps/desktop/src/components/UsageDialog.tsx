import { useEffect, useRef, useState } from "react";
import type { Api } from "../lib/api";
interface Usage {
  periods: Record<
    string,
    {
      gpu_seconds: number;
      estimated_cost: number;
      requests: number;
      input_tokens?: number | null;
      output_tokens?: number | null;
      total_tokens?: number | null;
    }
  >;
}
interface Session {
  id: string;
  gpu_type: string;
  status: string;
  started_at: string | null;
  billable_seconds: number;
  estimated_cost: number;
  actual_cost: number | null;
  stop_reason: string | null;
  pod_id?: string;
}
export default function UsageDialog({
  api,
  admin,
  onClose,
}: {
  api: Api;
  admin: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [users, setUsers] = useState<Array<{ email: string; role: string }>>(
    [],
  );
  const [allUsage, setAllUsage] = useState<Array<Usage & { email: string }>>(
    [],
  );
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    let active = true;
    setBusy(true);
    void Promise.all([
      api.json<Usage>("/compute/usage/me"),
      api.json<Session[]>(
        `${admin ? "/admin/compute/sessions" : "/compute/sessions/me"}?limit=50&offset=${offset}`,
      ),
      admin
        ? api.json<Array<{ email: string; role: string }>>(
            `/admin/users?limit=50&offset=${offset}`,
          )
        : Promise.resolve([]),
      admin
        ? api.json<Array<Usage & { email: string }>>(
            `/admin/compute/usage?limit=50&offset=${offset}`,
          )
        : Promise.resolve([]),
    ])
      .then(([mine, rows, accounts, totals]) => {
        if (active) {
          setUsage(mine);
          setSessions(rows);
          setUsers(accounts);
          setAllUsage(totals);
        }
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [api, admin, offset, revision]);
  return (
    <dialog
      ref={dialog}
      className="settings-dialog usage-dialog"
      onCancel={onClose}
    >
      <div className="dialog-title">
        <h2>{admin ? "Администрирование" : "Моё использование"}</h2>
        <button onClick={onClose}>Закрыть</button>
      </div>
      <p className="muted">
        Периоды в UTC. Compute относится к инициатору сессии. Стоимость
        приблизительная; mock-запросы не используют GPU.
      </p>
      <table>
        <thead>
          <tr>
            <th>Период</th>
            <th>GPU, мин</th>
            <th>Оценка, $</th>
            <th>Запросы</th>
            <th>Токены: вход / выход / всего</th>
          </tr>
        </thead>
        <tbody>
          {usage &&
            Object.entries(usage.periods).map(([period, value]) => (
              <tr key={period}>
                <td>
                  {{
                    today: "Сегодня",
                    week: "Неделя",
                    month: "Месяц",
                    all: "Всё время",
                  }[period] || period}
                </td>
                <td>{(value.gpu_seconds / 60).toFixed(1)}</td>
                <td>{value.estimated_cost.toFixed(4)}</td>
                <td>{value.requests}</td>
                <td>
                  {value.input_tokens ?? "—"} / {value.output_tokens ?? "—"} /{" "}
                  {value.total_tokens ?? "—"}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      <h3>Сессии</h3>
      {admin && (
        <button
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void api
              .json("/admin/compute/billing/refresh", {
                method: "POST",
                signal: AbortSignal.timeout(100000),
              })
              .then(() => setRevision((value) => value + 1))
              .catch((e: Error) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          Обновить фактические расходы RunPod
        </button>
      )}
      {sessions.length === 0 && <p>Сессий пока нет.</p>}
      <table>
        <thead>
          <tr>
            <th>GPU / начало</th>
            <th>Состояние</th>
            <th>Время</th>
            <th>Оценка / факт</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((row) => (
            <tr key={row.id}>
              <td>
                {row.gpu_type}
                <small>
                  {row.started_at
                    ? new Date(row.started_at).toLocaleString("ru-RU")
                    : "Не запущен"}
                </small>
                {admin && <small>Pod: {row.pod_id || "ожидается"}</small>}
              </td>
              <td>
                {row.status}
                <small>{row.stop_reason}</small>
              </td>
              <td>{(row.billable_seconds / 60).toFixed(1)} мин</td>
              <td>
                ${row.estimated_cost.toFixed(4)} /{" "}
                {row.actual_cost === null
                  ? "нет данных"
                  : `$${row.actual_cost.toFixed(4)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {admin && (
        <>
          <h3>Пользователи</h3>
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Роль</th>
                <th>Запросы / оценка $</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const total = allUsage.find((row) => row.email === user.email)
                  ?.periods.all;
                return (
                  <tr key={user.email}>
                    <td>{user.email}</td>
                    <td>{user.role}</td>
                    <td>
                      {total
                        ? `${total.requests} / ${total.estimated_cost.toFixed(4)}`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p>
            Активный compute, остановка и отмена поиска — в панели AI / Compute.
          </p>
        </>
      )}
      <button
        disabled={busy || offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 50))}
      >
        Назад
      </button>
      <span> Страница {offset / 50 + 1} </span>
      <button
        disabled={busy || Math.max(sessions.length, users.length) < 50}
        onClick={() => setOffset(offset + 50)}
      >
        Далее
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </dialog>
  );
}
