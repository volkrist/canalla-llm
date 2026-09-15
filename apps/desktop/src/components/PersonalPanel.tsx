import { useEffect, useRef, useState } from "react";
import type { Api } from "../lib/api";
import type { Message } from "../types";
import { lastSeen, usePresence } from "../hooks/usePresence";
interface Project {
  id: string;
  name: string;
  description: string;
  status: "active" | "archived";
}
interface Memory {
  id: string;
  content: string;
  category: string;
  importance: number;
  project_id: string | null;
  is_pinned: boolean;
  is_active: boolean;
  source_chat_id?: string;
  source_message_id?: string;
}
interface Profile {
  display_name: string;
  email: string;
  created_at: string;
  custom_instructions: string;
  use_memory: boolean;
  relevant_memory: boolean;
  max_memories: number;
}
const categories = [
  "identity",
  "preference",
  "project",
  "decision",
  "fact",
  "instruction",
  "other",
];
const categoryNames: Record<string, string> = {
  identity: "Личность",
  preference: "Предпочтения",
  project: "Проект",
  decision: "Решение",
  fact: "Факт",
  instruction: "Инструкция",
  other: "Другое",
};
const emptyMemory = (): Omit<Memory, "id"> => ({
  content: "",
  category: "fact",
  importance: 3,
  project_id: null,
  is_pinned: false,
  is_active: true,
});
export default function PersonalPanel({
  api,
  onLogout,
  chatId,
  projectId,
  onProject,
  prompt,
  technical,
}: {
  api: Api;
  onLogout: () => void;
  chatId: string | null;
  projectId: string | null;
  onProject: (id: string | null) => Promise<void>;
  prompt: string;
  technical: boolean;
}) {
  const presence = usePresence(api, onLogout);
  const [tab, setTab] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState<
    (Omit<Memory, "id"> & { id?: string }) | null
  >(null);
  const [project, setProject] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [preview, setPreview] = useState<null | {
    memories: { id: string; content: string }[];
    project: string | null;
    recent_message_count: number;
    total_chars: number;
    messages?: { role: string; content: string }[];
  }>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const [page, setPage] = useState(0);
  const [, clock] = useState(0);
  const self = presence.users.find((u) => u.key === presence.selfKey);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие",
      );
    } finally {
      setBusy(false);
    }
  }
  async function refreshProjects() {
    const rows: Project[] = [];
    for (let offset = 0; ; offset += 100) {
      const next = await api.json<Project[]>(`/projects?offset=${offset}`);
      rows.push(...next);
      if (next.length < 100) break;
    }
    setProjects(rows);
  }
  async function refreshMemory() {
    setMemories(
      await api.json<Memory[]>(
        `/memory?q=${encodeURIComponent(query)}&offset=${page * 100}${category ? `&category=${category}` : ""}`,
      ),
    );
  }
  useEffect(() => {
    void api
      .json<Profile>("/profile")
      .then(setProfile)
      .catch(() => {});
    void refreshProjects().catch(() => {});
    const timer = setInterval(() => clock((v) => v + 1), 30000);
    return () => clearInterval(timer);
  }, [api]);
  useEffect(() => {
    if (tab) dialog.current?.showModal();
  }, [tab]);
  useEffect(() => {
    if (tab !== "Память" || busy) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void api
        .json<Memory[]>(
          `/memory?q=${encodeURIComponent(query)}&offset=${page * 100}${category ? `&category=${category}` : ""}`,
        )
        .then((rows) => {
          if (!cancelled) setMemories(rows);
        })
        .catch((e) => {
          if (!cancelled)
            setError(e instanceof Error ? e.message : "Память недоступна");
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, tab, query, category, page, busy]);
  useEffect(() => {
    const remember = (event: Event) => {
      const message = (event as CustomEvent<Message>).detail;
      setEditing({
        ...emptyMemory(),
        content: message.content.slice(0, 3000),
        project_id: projectId,
        source_chat_id: message.chat_id,
        source_message_id: message.id,
      });
      setTab("Память");
    };
    const used = (event: Event) => {
      setTab("Контекст");
      setPreview(null);
      void run(async () =>
        setPreview({
          memories: await api.json<Memory[]>(
            `/messages/${(event as CustomEvent<string>).detail}/memory`,
          ),
          project: null,
          recent_message_count: 0,
          total_chars: 0,
        }),
      );
    };
    const settings = () => setTab("Профиль");
    window.addEventListener("alex-remember", remember);
    window.addEventListener("alex-used-memory", used);
    window.addEventListener("alex-personal-settings", settings);
    return () => {
      window.removeEventListener("alex-remember", remember);
      window.removeEventListener("alex-used-memory", used);
      window.removeEventListener("alex-personal-settings", settings);
    };
  }, [api, projectId]);
  function stateText(status: string, using: boolean) {
    return `${status === "online" ? "● Online" : status === "idle" ? "◐ Idle" : "○ Offline"}${using ? " · Использует AI" : ""}`;
  }
  return (
    <section className="personal-panel">
      <div className="personal-nav">
        <span>
          {profile?.display_name || "Профиль"} ·{" "}
          {presence.connected && self
            ? stateText(self.status, self.using_ai)
            : "Presence unavailable"}
        </span>
        {["Пользователи", "Проекты", "Память", "Профиль"].map((name) => (
          <button
            key={name}
            onClick={() => {
              setTab(name);
              setError("");
            }}
          >
            {name}
          </button>
        ))}
      </div>
      {chatId && (
        <div className="project-picker">
          <label>
            Проект чата{" "}
            <select
              aria-label="Проект чата"
              value={projectId || ""}
              disabled={busy}
              onChange={(e) =>
                void run(() => onProject(e.target.value || null))
              }
            >
              <option value="">Без проекта</option>
              {projects
                .filter((p) => p.status === "active" || p.id === projectId)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.status === "archived" ? " (архив)" : ""}
                  </option>
                ))}
            </select>
          </label>
          {technical && (
            <button
              onClick={() => {
                setTab("Контекст");
                void run(async () =>
                  setPreview(
                    await api.json(
                      `/chats/${chatId}/context-preview?prompt=${encodeURIComponent(prompt)}`,
                    ),
                  ),
                );
              }}
            >
              Предпросмотр контекста
            </button>
          )}
        </div>
      )}
      {tab && (
        <dialog
          ref={dialog}
          className="settings-dialog personal-dialog"
          onCancel={() => setTab("")}
        >
          <div className="dialog-title">
            <h2>{tab}</h2>
            <button
              onClick={() => {
                setTab("");
                setEditing(null);
                setProject(null);
              }}
            >
              Закрыть
            </button>
          </div>
          {tab === "Пользователи" && (
            <>
              <p>Видны только имя, статус и последняя активность.</p>
              {!presence.connected && <p role="status">Presence unavailable</p>}
              {presence.users.length <= 1 && (
                <p>Других пользователей пока нет.</p>
              )}
              {presence.users.map((u) => (
                <article className="personal-card" key={u.key}>
                  <span className="initials">
                    {u.display_name.slice(0, 2).toUpperCase()}
                  </span>
                  <div>
                    <strong>{u.display_name}</strong>
                    <p>
                      {presence.connected
                        ? stateText(u.status, u.using_ai)
                        : "Статус недоступен"}
                    </p>
                    <small
                      title={
                        u.last_seen
                          ? new Date(u.last_seen).toLocaleString("ru-RU")
                          : ""
                      }
                    >
                      Был в сети: {lastSeen(u.last_seen)}
                    </small>
                  </div>
                </article>
              ))}
            </>
          )}
          {tab === "Профиль" && profile && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  const { email, created_at, ...body } = profile;
                  void email;
                  void created_at;
                  const updated = await api.json<Profile>("/profile", {
                    method: "PATCH",
                    body: JSON.stringify({
                      display_name: body.display_name,
                      custom_instructions: body.custom_instructions,
                      use_memory: body.use_memory,
                      relevant_memory: body.relevant_memory,
                      max_memories: body.max_memories,
                    }),
                  });
                  setProfile(updated);
                });
              }}
            >
              <h3>Personalization</h3>
              <label>
                Имя
                <input
                  required
                  maxLength={80}
                  value={profile.display_name}
                  onChange={(e) =>
                    setProfile({ ...profile, display_name: e.target.value })
                  }
                />
              </label>
              <p>Email: {profile.email}</p>
              <p>
                Создан: {new Date(profile.created_at).toLocaleString("ru-RU")}
              </p>
              <p>
                {presence.connected && self
                  ? stateText(self.status, self.using_ai)
                  : "Presence unavailable"}
              </p>
              <label>
                Custom instructions
                <textarea
                  maxLength={2000}
                  value={profile.custom_instructions}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      custom_instructions: e.target.value,
                    })
                  }
                />
              </label>
              <h3>Memory</h3>
              <label>
                <input
                  type="checkbox"
                  checked={profile.use_memory}
                  onChange={(e) =>
                    setProfile({ ...profile, use_memory: e.target.checked })
                  }
                />
                Use long-term memory
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={profile.relevant_memory}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      relevant_memory: e.target.checked,
                    })
                  }
                />
                Automatically use relevant memory
              </label>
              <label>
                Maximum memories per request
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={profile.max_memories}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      max_memories: Number(e.target.value),
                    })
                  }
                />
              </label>
              <p>Automatic memory capture: Off — пока недоступен</p>
              <button className="primary" disabled={busy}>
                Сохранить профиль
              </button>
            </form>
          )}
          {tab === "Проекты" && (
            <>
              <button
                onClick={() =>
                  setProject({
                    id: "",
                    name: "",
                    description: "",
                    status: "active",
                  })
                }
              >
                Создать проект
              </button>
              {projects.map((p) => (
                <article className="personal-card" key={p.id}>
                  <div>
                    <strong>{p.name}</strong>
                    <p>{p.description}</p>
                    <small>{p.status}</small>
                  </div>
                  <button onClick={() => setProject(p)}>Изменить проект</button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api.json(`/projects/${p.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({
                            name: p.name,
                            description: p.description,
                            status:
                              p.status === "active" ? "archived" : "active",
                          }),
                        });
                        await refreshProjects();
                      })
                    }
                  >
                    {p.status === "active" ? "Архивировать" : "Восстановить"}
                  </button>
                </article>
              ))}
              {project && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      await api.json(
                        `/projects${project.id ? `/${project.id}` : ""}`,
                        {
                          method: project.id ? "PATCH" : "POST",
                          body: JSON.stringify({
                            name: project.name,
                            description: project.description,
                            status: project.status,
                          }),
                        },
                      );
                      setProject(null);
                      await refreshProjects();
                    });
                  }}
                >
                  <label>
                    Название проекта
                    <input
                      required
                      maxLength={120}
                      value={project.name}
                      onChange={(e) =>
                        setProject({ ...project, name: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Описание проекта
                    <textarea
                      maxLength={3000}
                      value={project.description}
                      onChange={(e) =>
                        setProject({ ...project, description: e.target.value })
                      }
                    />
                  </label>
                  <button disabled={busy}>Сохранить проект</button>
                  <button type="button" onClick={() => setProject(null)}>
                    Отмена
                  </button>
                </form>
              )}
            </>
          )}
          {tab === "Память" && (
            <>
              <p>Память используется между разными чатами.</p>
              <div className="personal-nav">
                <input
                  aria-label="Поиск памяти"
                  placeholder="Поиск памяти"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setPage(0);
                  }}
                />
                <select
                  aria-label="Фильтр категории"
                  value={category}
                  onChange={(e) => {
                    setCategory(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="">Все категории</option>
                  {categories.map((c) => (
                    <option key={c} value={c}>
                      {categoryNames[c]}
                    </option>
                  ))}
                </select>
                <button onClick={() => setEditing(emptyMemory())}>
                  Добавить память
                </button>
              </div>
              {!memories.length && <p>AI пока ничего не запомнил.</p>}
              {memories.map((m) => (
                <article className="personal-card" key={m.id}>
                  <div>
                    <small>
                      {categoryNames[m.category]} · важность {m.importance}{" "}
                      {m.is_pinned ? "★" : ""}{" "}
                      {!m.is_active ? "· Отключено" : ""}
                    </small>
                    <p>{m.content}</p>
                    <div className="personal-nav">
                      <button onClick={() => setEditing(m)}>Изменить</button>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await api.json(`/memory/${m.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({ is_pinned: !m.is_pinned }),
                            });
                            await refreshMemory();
                          })
                        }
                      >
                        {m.is_pinned ? "Открепить" : "Закрепить"}
                      </button>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await api.json(`/memory/${m.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({ is_active: !m.is_active }),
                            });
                            await refreshMemory();
                          })
                        }
                      >
                        {m.is_active ? "Отключить" : "Включить"}
                      </button>
                      <button onClick={() => setDeleting(m.id)}>Удалить</button>
                    </div>
                  </div>
                </article>
              ))}
              <button disabled={page === 0} onClick={() => setPage(page - 1)}>
                Назад
              </button>
              <button
                disabled={memories.length < 100}
                onClick={() => setPage(page + 1)}
              >
                Далее
              </button>
              {deleting && (
                <div role="alertdialog" aria-label="Удалить память">
                  <p>Удалить эту память? Она больше не попадёт в контекст.</p>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api.json(`/memory/${deleting}`, {
                          method: "DELETE",
                        });
                        setDeleting(null);
                        await refreshMemory();
                      })
                    }
                  >
                    Да, удалить память
                  </button>
                  <button onClick={() => setDeleting(null)}>Отмена</button>
                </div>
              )}
              {editing && (
                <form
                  className="memory-editor"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      const body = {
                        content: editing.content,
                        category: editing.category,
                        importance: editing.importance,
                        project_id: editing.project_id,
                        is_pinned: editing.is_pinned,
                        is_active: editing.is_active,
                        ...(!editing.id
                          ? {
                              source_chat_id: editing.source_chat_id,
                              source_message_id: editing.source_message_id,
                            }
                          : {}),
                      };
                      await api.json(
                        `/memory${editing.id ? `/${editing.id}` : ""}`,
                        {
                          method: editing.id ? "PATCH" : "POST",
                          body: JSON.stringify(body),
                        },
                      );
                      setEditing(null);
                      await refreshMemory();
                    });
                  }}
                >
                  <h3>
                    {editing.id ? "Изменить память" : "Сохранить в память"}
                  </h3>
                  <label>
                    Содержание памяти
                    <textarea
                      required
                      maxLength={3000}
                      value={editing.content}
                      onChange={(e) =>
                        setEditing({ ...editing, content: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Категория
                    <select
                      aria-label="Категория"
                      value={editing.category}
                      onChange={(e) =>
                        setEditing({ ...editing, category: e.target.value })
                      }
                    >
                      {categories.map((c) => (
                        <option key={c} value={c}>
                          {categoryNames[c]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Важность
                    <input
                      type="number"
                      min={1}
                      max={5}
                      value={editing.importance}
                      onChange={(e) =>
                        setEditing({
                          ...editing,
                          importance: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    Проект памяти
                    <select
                      value={editing.project_id || ""}
                      onChange={(e) =>
                        setEditing({
                          ...editing,
                          project_id: e.target.value || null,
                        })
                      }
                    >
                      <option value="">Общая память</option>
                      {projects
                        .filter((p) => p.status === "active")
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button disabled={busy} className="primary">
                    Сохранить память
                  </button>
                  <button type="button" onClick={() => setEditing(null)}>
                    Отмена
                  </button>
                </form>
              )}
            </>
          )}
          {tab === "Контекст" && preview && (
            <>
              <p>
                Проект: {preview.project || "Без проекта"} · Сообщений истории:{" "}
                {preview.recent_message_count} · Символов: {preview.total_chars}
              </p>
              <h3>Использовано памяти: {preview.memories.length}</h3>
              {preview.memories.map((m) => (
                <p key={m.id}>{m.content}</p>
              ))}
              {preview.messages && (
                <details>
                  <summary>Порядок контекста</summary>
                  {preview.messages.map((m, i) => (
                    <article key={i}>
                      <strong>{m.role}</strong>
                      <pre className="context-text">{m.content}</pre>
                    </article>
                  ))}
                </details>
              )}
            </>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </dialog>
      )}
    </section>
  );
}
