import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import { openExternal } from "../lib/files";

interface WebSource {
  id: string;
  label: string;
  final_url: string;
  title: string;
  excerpt: string;
  publisher: string | null;
  published_at: string | null;
  fetched_at: string | null;
  searched_at: string | null;
}
export default function WebSources({
  api,
  messageId,
  status,
}: {
  api: Api;
  messageId: string;
  status: string;
}) {
  const [sources, setSources] = useState<WebSource[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const refresh = () => {
      void api
        .json<WebSource[]>("/messages/" + messageId + "/web-sources")
        .then((value) => {
          if (live) setSources(value);
        })
        .catch(() => {
          if (live) setError("Не удалось загрузить web-источники.");
        });
    };
    refresh();
    window.addEventListener("alex-web-sources", refresh);
    return () => {
      live = false;
      window.removeEventListener("alex-web-sources", refresh);
    };
  }, [api, messageId, status]);
  if (!sources.length) return error ? <p role="status">{error}</p> : null;
  return (
    <section aria-label="Web sources" className="web-sources">
      <strong>Web sources</strong>
      {sources.map((source) => (
        <details key={source.id}>
          <summary>
            [{source.label}] {source.title || source.final_url}
          </summary>
          <button
            type="button"
            onClick={() =>
              void openExternal(source.final_url).catch(() =>
                setError("Не удалось открыть источник"),
              )
            }
          >
            {source.final_url}
          </button>
          {source.publisher && <p>{source.publisher}</p>}
          {source.published_at && <p>Опубликовано: {source.published_at}</p>}
          <p>
            Проверено:{" "}
            {new Date(
              source.fetched_at || source.searched_at || "",
            ).toLocaleString()}
          </p>
          <pre>{source.excerpt}</pre>
        </details>
      ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
