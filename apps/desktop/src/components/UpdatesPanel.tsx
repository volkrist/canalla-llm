// Settings → Обновления: the one place that answers "what version is this, is there a newer one,
// and what happens if I install it". Downloading is a user action by default; installing is only
// ever a confirmed action, and never while the app is busy.

import { useEffect, useState } from "react";
import { UPDATE_CHANNEL } from "../lib/updates";
import type { UpdateStore } from "../hooks/useUpdates";

function when(value: string | null): string {
  if (!value) return "ещё не проверялось";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "ещё не проверялось";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function UpdatesPanel({
  autoCheck,
  onAutoCheck,
  busy,
  updates,
}: {
  autoCheck: boolean;
  onAutoCheck: (value: boolean) => void;
  /** What the app is doing right now: an install waits for a safe point. */
  busy: boolean;
  /** The single update store, owned by the app so exactly one timer exists. */
  updates: UpdateStore;
}) {
  const { state, checkNow, download, install } = updates;
  const [manual, setManual] = useState(false);

  useEffect(() => {
    if (!autoCheck) void checkNow();
    // The panel opens with the state it has; the button refreshes it on demand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const action = async (work: () => Promise<unknown>) => {
    setManual(true);
    try {
      await work();
    } finally {
      setManual(false);
    }
  };

  return (
    <section className="settings-section" aria-label="Обновления">
      <h3>Обновления</h3>
      <p className="muted">
        Канал: {UPDATE_CHANNEL}. Обновление скачивается только по вашему
        действию, а устанавливается — только после подтверждения и только когда
        ничего не выполняется.
      </p>
      <dl className="settings-facts">
        <div>
          <dt>Текущая версия</dt>
          <dd>{state.current ?? "—"}</dd>
        </div>
        <div>
          <dt>Доступная версия</dt>
          <dd>{state.available?.version ?? "нет"}</dd>
        </div>
        <div>
          <dt>Последняя проверка</dt>
          <dd>{when(state.checkedAt)}</dd>
        </div>
      </dl>

      <label className="settings-toggle">
        <input
          type="checkbox"
          checked={autoCheck}
          onChange={(event) => onAutoCheck(event.target.checked)}
        />
        <span>Проверять обновления автоматически</span>
      </label>

      {state.available?.notes ? (
        <details className="compact-details" open>
          <summary>Что нового в {state.available.version}</summary>
          <p className="muted">{state.available.notes}</p>
        </details>
      ) : null}

      {state.phase === "downloading" ? (
        <p className="muted" role="status" data-testid="update-progress">
          Скачивание обновления · {state.progress}%
        </p>
      ) : null}

      {state.message ? (
        <p
          className="settings-note"
          role="alert"
          data-testid={
            state.phase === "refused" ? "update-refused" : "update-message"
          }
          data-reason={state.refusal ?? undefined}
        >
          {state.message}
        </p>
      ) : null}

      <div className="settings-actions">
        <button
          type="button"
          className="status-action"
          disabled={
            manual ||
            state.phase === "checking" ||
            state.phase === "downloading"
          }
          onClick={() => void action(checkNow)}
        >
          {state.phase === "checking" ? "Проверяю…" : "Проверить обновления"}
        </button>
        {state.phase === "available" ? (
          <button
            type="button"
            className="status-action"
            disabled={manual}
            onClick={() => void action(download)}
          >
            Скачать {state.available?.version}
          </button>
        ) : null}
        {state.phase === "ready" ? (
          <button
            type="button"
            className="primary"
            data-testid="update-install"
            disabled={manual || busy}
            onClick={() => void action(install)}
          >
            Перезапустить и обновить
          </button>
        ) : null}
      </div>

      {state.phase === "ready" && busy ? (
        <p className="muted">
          Обновление готово. Установка начнётся, когда завершится текущая
          операция.
        </p>
      ) : null}

      <p className="muted">
        Первая установка 1.2.0 выполняется вручную: обычная 1.1.x не умеет
        обновляться сама. Дальше обновления приходят через этот раздел.
      </p>
    </section>
  );
}
