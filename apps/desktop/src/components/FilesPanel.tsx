import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import EmbeddingModelStatus from "./EmbeddingModelStatus";

interface Document {
  id: string;
  project_id: string | null;
  display_name: string;
  original_filename: string;
  extension: string;
  size_bytes: number;
  status: string;
  chunk_count: number;
  error_message: string | null;
}
interface Project {
  id: string;
  name: string;
  status: string;
}
interface Rag {
  enabled: boolean;
  include_general: boolean;
  max_chunks: number;
  max_chars: number;
  similarity_threshold: number;
}
const phases: Record<string, string> = {
  uploaded: "В очереди",
  extracting: "Извлечение текста",
  chunking: "Разбиение на фрагменты",
  embedding: "Вычисление embeddings",
  ready: "Готов",
  failed: "Ошибка",
};

export default function FilesPanel({
  api,
  projectId,
}: {
  api: Api;
  projectId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState("");
  const [rag, setRag] = useState<Rag | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<Document | null>(null);
  const [renaming, setRenaming] = useState<Document | null>(null);
  async function refresh() {
    setDocuments(await api.json<Document[]>("/documents"));
  }
  useEffect(() => {
    const attach = () => setOpen(true);
    window.addEventListener("alex-attach", attach);
    return () => window.removeEventListener("alex-attach", attach);
  }, []);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setProject(projectId || "");
    void Promise.all([
      api.json<Document[]>("/documents"),
      api.json<Project[]>("/projects"),
      api.json<Rag>("/rag/preferences"),
    ])
      .then(([docs, ps, prefs]) => {
        if (active) {
          setDocuments(docs);
          setProjects(ps);
          setRag(prefs);
        }
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    const timer = setInterval(() => {
      void api
        .json<Document[]>("/documents")
        .then((docs) => {
          if (active) setDocuments(docs);
        })
        .catch((e: Error) => {
          if (active) setError(e.message);
        });
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [open, api, projectId]);
  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      window.dispatchEvent(new Event("alex-documents-changed"));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const shown = documents.filter(
    (d) =>
      `${d.display_name} ${d.original_filename}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()) &&
      (!type || d.extension === type) &&
      (!status || d.status === status) &&
      (filter === "all" || (d.project_id || "general") === filter),
  );
  const scoped = projectId
    ? documents.filter((d) => d.project_id === projectId)
    : documents;
  // Closed and nothing for this scope: no heading and no empty block. The composer's
  // attach button still opens the dialog through the `alex-attach` event.
  if (!open && !scoped.length) return null;
  return (
    <section className="files-toolbar">
      <button onClick={() => setOpen(true)}>Файлы</button>
      {open && (
        <dialog
          open
          className="settings-dialog files-dialog"
          aria-label="Файлы"
        >
          <button onClick={() => setOpen(false)}>Закрыть файлы</button>
          <h2>Файлы и RAG</h2>
          <EmbeddingModelStatus api={api} onReady={setModelReady} />
          <p>
            PDF, DOCX, TXT, MD · до 25 MB. Документы станут доступны чату после
            индексации. Сканированные PDF без текста не поддерживаются.
          </p>
          <label>
            Проект загрузки
            <select
              aria-label="Проект загрузки"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">Общие документы</option>
              {projects
                .filter((p) => p.status === "active")
                .map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            {busy ? "Загрузка / сохранение…" : "Загрузить файлы"}
            <input
              aria-label="Загрузить файлы"
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              disabled={busy || !modelReady}
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                e.target.value = "";
                void action(async () => {
                  for (const file of files) {
                    const body = new FormData();
                    body.append("file", file);
                    if (project) body.append("project_id", project);
                    await api.json("/documents", {
                      method: "POST",
                      body,
                      signal: AbortSignal.timeout(60000),
                    });
                  }
                });
              }}
            />
          </label>
          {rag && (
            <fieldset>
              <legend>Контекст из файлов</legend>
              <label>
                <input
                  type="checkbox"
                  checked={rag.enabled}
                  onChange={(e) =>
                    setRag({ ...rag, enabled: e.target.checked })
                  }
                />
                Использовать документы
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={rag.include_general}
                  onChange={(e) =>
                    setRag({ ...rag, include_general: e.target.checked })
                  }
                />
                Включать общие документы в проектный чат
              </label>
              <label>
                Максимум фрагментов
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={rag.max_chunks}
                  onChange={(e) =>
                    setRag({ ...rag, max_chunks: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Лимит символов RAG
                <input
                  type="number"
                  min={100}
                  max={20000}
                  value={rag.max_chars}
                  onChange={(e) =>
                    setRag({ ...rag, max_chars: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Минимальное сходство
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={rag.similarity_threshold}
                  onChange={(e) =>
                    setRag({
                      ...rag,
                      similarity_threshold: Number(e.target.value),
                    })
                  }
                />
              </label>
              <button
                disabled={busy}
                onClick={() =>
                  void action(async () =>
                    setRag(
                      await api.json<Rag>("/rag/preferences", {
                        method: "PUT",
                        body: JSON.stringify(rag),
                      }),
                    ),
                  )
                }
              >
                Сохранить настройки RAG
              </button>
            </fieldset>
          )}
          <label>
            Поиск файлов
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label>
            Фильтр проекта
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">Все проекты</option>
              <option value="general">Общие</option>
              {projects.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Тип файла
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">Все типы</option>
              {["pdf", "docx", "txt", "md"].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            Статус файла
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Все статусы</option>
              {Object.entries(phases).map(([key, value]) => (
                <option key={key} value={key}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          {shown.map((d) => (
            <article className="document-row" key={d.id}>
              <h3>{d.display_name}</h3>
              <p>
                {d.extension.toUpperCase()} · {(d.size_bytes / 1024).toFixed(1)}{" "}
                KB · {phases[d.status] || d.status} · Фрагментов:{" "}
                {d.chunk_count}
              </p>
              <p>
                {projects.find((p) => p.id === d.project_id)?.name ||
                  "Общий документ"}
              </p>
              {d.error_message && <p role="alert">{d.error_message}</p>}
              <button disabled={busy} onClick={() => setRenaming(d)}>
                Переименовать файл
              </button>
              <button
                disabled={busy || !["ready", "failed"].includes(d.status)}
                onClick={() =>
                  void action(() =>
                    api.json(`/documents/${d.id}/reindex`, { method: "POST" }),
                  )
                }
              >
                Переиндексировать
              </button>
              <button disabled={busy} onClick={() => setDeleting(d)}>
                Удалить файл
              </button>
            </article>
          ))}
          {!shown.length && <p>Документов нет</p>}
          {renaming && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action(() =>
                  api.json(`/documents/${renaming.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({
                      display_name: renaming.display_name,
                    }),
                  }),
                );
                setRenaming(null);
              }}
            >
              <label>
                Название файла
                <input
                  required
                  maxLength={255}
                  value={renaming.display_name}
                  onChange={(e) =>
                    setRenaming({ ...renaming, display_name: e.target.value })
                  }
                />
              </label>
              <button>Сохранить название</button>
              <button type="button" onClick={() => setRenaming(null)}>
                Отмена
              </button>
            </form>
          )}
          {deleting && (
            <div role="alertdialog" aria-label="Удаление файла">
              <p>
                Удалить {deleting.display_name} вместе с текстом и индексом?
                Старые ответы сохранят только сведения об удалённом источнике.
              </p>
              <button
                disabled={busy}
                onClick={() => {
                  void action(() =>
                    api.json(`/documents/${deleting.id}`, { method: "DELETE" }),
                  );
                  setDeleting(null);
                }}
              >
                Подтвердить удаление файла
              </button>
              <button onClick={() => setDeleting(null)}>Отмена</button>
            </div>
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
