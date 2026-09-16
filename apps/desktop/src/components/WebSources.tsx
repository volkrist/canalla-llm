import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import { openExternal } from "../lib/files";
import { dedupeSources } from "../lib/tools";

export interface WebSource {
  id: string;
  label: string;
  url?: string;
  final_url: string;
  title: string;
  excerpt: string;
  publisher: string | null;
  published_at: string | null;
  fetched_at: string | null;
  searched_at: string | null;
  channel?: "web" | "tor";
  authority?: string | null;
  kind?: string | null;
}
function SourceItems({
  sources,
  onError,
}: {
  sources: WebSource[];
  onError: (message: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const unique = dedupeSources(sources);
  const visible = expanded ? unique : unique.slice(0, 3);
  return (
    <>
      {visible.map((source) => (
        <details key={source.id}>
          <summary>
            [{source.label}] {source.title || source.final_url}
            {source.authority ? ` · ${source.authority}` : ""}
          </summary>
          <button
            type="button"
            onClick={() =>
              void openExternal(source.final_url).catch(() =>
                onError("Не удалось открыть источник"),
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
      {unique.length > 3 && (
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Скрыть" : "Показать ещё"}
        </button>
      )}
    </>
  );
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
  const web = sources.filter(
    (source) =>
      (source.channel || (source.label.startsWith("T") ? "tor" : "web")) ===
      "web",
  );
  const tor = sources.filter(
    (source) => source.channel === "tor" || source.label.startsWith("T"),
  );
  return (
    <>
      {web.length > 0 && (
        <section aria-label="Web sources" className="web-sources">
          <strong>Интернет W</strong>
          <SourceItems sources={web} onError={setError} />
        </section>
      )}
      {tor.length > 0 && (
        <section aria-label="Tor sources" className="web-sources">
          <strong>Tor T</strong>
          <SourceItems sources={tor} onError={setError} />
        </section>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
