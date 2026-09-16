import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import WebSources from "./WebSources";

export interface Source {
  document_id: string;
  chunk_id: string;
  display_name: string;
  page_number: number | null;
  section_title: string | null;
  project_id: string | null;
  project_name?: string | null;
  uploaded_at: string;
  label: string;
  similarity: number;
  excerpt: string | null;
  deleted: boolean;
}

export function SourceList({ sources }: { sources: Source[] }) {
  const [selectedId, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const selected = sources.find((source) => source.chunk_id === selectedId);
  const visible = expanded ? sources : sources.slice(0, 3);
  return (
    <>
      {sources.length > 0 && (
        <div className="source-list">
          <strong>Документы D</strong>
          {visible.map((source) => (
            <button
              key={source.chunk_id}
              onClick={() => setSelected(source.chunk_id)}
            >
              [{source.label}] {source.display_name}
              {source.page_number ? ` — стр. ${source.page_number}` : ""}
              {source.section_title ? ` — ${source.section_title}` : ""}
              {source.deleted ? " · Источник был удалён" : ""}
            </button>
          ))}
          {sources.length > 3 && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "Скрыть" : "Показать ещё"}
            </button>
          )}
        </div>
      )}
      {selected && (
        <dialog
          open
          className="settings-dialog source-dialog"
          aria-label="Источник"
        >
          <button onClick={() => setSelected(null)}>Закрыть источник</button>
          <h2>{selected.display_name}</h2>
          <p>
            {selected.page_number ? `Страница ${selected.page_number}` : ""}{" "}
            {selected.section_title}
          </p>
          <p>
            Проект: {selected.project_name || "Общий документ"} · Загружен:{" "}
            {new Date(selected.uploaded_at).toLocaleString("ru-RU")}
          </p>
          <p>Сходство: {selected.similarity.toFixed(3)}</p>
          <pre className="context-text">
            {selected.deleted ? "Источник был удалён" : selected.excerpt}
          </pre>
        </dialog>
      )}
    </>
  );
}

export default function SourcePanel({
  api,
  messageId,
  status,
}: {
  api: Api;
  messageId: string;
  status: string;
}) {
  const [sources, setSources] = useState<Source[]>([]);
  const [warning, setWarning] = useState("");
  useEffect(() => {
    let active = true;
    const refresh = () => {
      void api
        .json<{ sources?: Source[]; rag_warning?: string }>(
          `/messages/${messageId}/context`,
        )
        .then((result) => {
          if (active) {
            setSources(result.sources || []);
            setWarning(result.rag_warning || "");
          }
        })
        .catch(() => {
          if (active) setWarning("Не удалось загрузить источники ответа.");
        });
    };
    refresh();
    window.addEventListener("alex-documents-changed", refresh);
    return () => {
      window.removeEventListener("alex-documents-changed", refresh);
      active = false;
    };
  }, [api, messageId, status]);
  return (
    <>
      {warning && <p role="status">{warning}</p>}
      <SourceList sources={sources} />
      <WebSources api={api} messageId={messageId} status={status} />
    </>
  );
}
