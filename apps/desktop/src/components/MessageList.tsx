import {
  Children,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import { AudioLines, Check, Copy, UserRound } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { openExternal } from "../lib/files";
import type { Message, Settings } from "../types";

function plainText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      typeof child === "string" || typeof child === "number"
        ? String(child)
        : isValidElement<{ children?: ReactNode }>(child)
          ? plainText(child.props.children)
          : "",
    )
    .join("");
}

function CodeBlock({ children, ...props }: ComponentPropsWithoutRef<"pre">) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const code = Children.toArray(children)[0];
  const language = isValidElement<{ className?: string }>(code)
    ? code.props.className?.replace("language-", "")
    : "";
  async function copy() {
    try {
      await navigator.clipboard.writeText(
        plainText(children).replace(/\n$/, ""),
      );
      setCopied(true);
      setFailed(false);
    } catch {
      setFailed(true);
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setCopied(false);
      setFailed(false);
    }, 2000);
  }
  return (
    <div className="code-block">
      <div className="code-heading">
        <span>{language || "code"}</span>
        <button onClick={copy} aria-label="Copy code">
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {failed ? "Не удалось скопировать" : copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre {...props}>{children}</pre>
    </div>
  );
}

export default function MessageList({
  messages,
  streaming,
  fontSize,
  settings,
  busy,
  onEdit,
  onResend,
  onRegenerate,
}: {
  messages: Message[];
  streaming: boolean;
  fontSize: number;
  settings: Settings;
  busy: boolean;
  onEdit: (id: string, content: string) => Promise<void>;
  onResend: (id: string, content: string) => Promise<boolean>;
  onRegenerate: (id: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [edited, setEdited] = useState("");
  const [away, setAway] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    if (stick.current && settings.autoScroll)
      bottom.current?.scrollIntoView({ behavior: "instant" });
  }, [messages, settings.autoScroll]);
  return (
    <div
      className="message-scroll"
      ref={scroll}
      onScroll={() => {
        const el = scroll.current!;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        setAway(!stick.current);
      }}
    >
      <div
        className="messages"
        style={{ fontSize }}
        role="log"
        aria-label="Сообщения"
        aria-live="off"
      >
        {messages.map((message) => (
          <article key={message.id} className={`message ${message.role}`}>
            <div className="message-avatar">
              {message.role === "assistant" ? (
                <AudioLines size={20} />
              ) : (
                <UserRound size={17} />
              )}
            </div>
            <div className="message-body">
              <div className="message-author">
                {message.role === "assistant" ? "Alex LLM" : "Вы"}
                <span>{message.role === "assistant" ? "ASSISTANT" : ""}</span>
                {settings.timestamps && (
                  <time dateTime={message.created_at}>
                    {new Date(message.created_at).toLocaleTimeString("ru-RU", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                )}
                {message.edited_at && <span>изменено</span>}
              </div>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  pre: CodeBlock,
                  img: () => <span className="muted">[изображение]</span>,
                  a: ({ children, href }) => (
                    <button
                      className="markdown-link"
                      onClick={() => {
                        if (href)
                          void openExternal(href).catch(() =>
                            setCopyStatus("link-error"),
                          );
                      }}
                    >
                      {children}
                    </button>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
              {!message.content && streaming && (
                <span className="typing" aria-label="Генерация ответа">
                  Ожидаем первый ответ модели…
                </span>
              )}
              {editing === message.id ? (
                <div className="message-editor">
                  <textarea
                    aria-label="Редактировать сообщение"
                    value={edited}
                    maxLength={32000}
                    onChange={(event) => setEdited(event.target.value)}
                  />
                  <button
                    disabled={busy || !edited.trim()}
                    onClick={() => {
                      void onEdit(message.id, edited.trim());
                      setEditing(null);
                    }}
                  >
                    Сохранить
                  </button>
                  <button
                    disabled={busy || !edited.trim()}
                    onClick={() => {
                      void onResend(message.id, edited.trim());
                      setEditing(null);
                    }}
                  >
                    Изменить и отправить
                  </button>
                  <button onClick={() => setEditing(null)}>Отмена</button>
                  <p className="muted">
                    Повторная отправка удалит все сообщения после этого.
                  </p>
                </div>
              ) : (
                <div className="message-actions">
                  <button
                    disabled={message.status === "generating"}
                    onClick={() =>
                      window.dispatchEvent(
                        new CustomEvent("alex-remember", { detail: message }),
                      )
                    }
                  >
                    Запомнить
                  </button>
                  {message.role === "assistant" && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("alex-used-memory", {
                            detail: message.id,
                          }),
                        )
                      }
                    >
                      Использованная память
                    </button>
                  )}
                  <button
                    onClick={() => {
                      void navigator.clipboard.writeText(message.content).then(
                        () => setCopyStatus(message.id),
                        () => setCopyStatus("error"),
                      );
                    }}
                  >
                    {copyStatus === message.id ? "Скопировано" : "Копировать"}
                  </button>
                  {message.role === "user" ? (
                    <button
                      disabled={busy}
                      onClick={() => {
                        setEditing(message.id);
                        setEdited(message.content);
                      }}
                    >
                      Изменить
                    </button>
                  ) : (
                    <button
                      disabled={busy}
                      onClick={() => void onRegenerate(message.id)}
                    >
                      {message.status === "error" ||
                      message.status === "stopped"
                        ? "Повторить"
                        : "Перегенерировать"}
                    </button>
                  )}
                </div>
              )}
              {copyStatus === "error" && (
                <span role="alert">Не удалось скопировать текст</span>
              )}
              {copyStatus === "link-error" && (
                <span role="alert">Не удалось открыть ссылку</span>
              )}
            </div>
          </article>
        ))}
        <div ref={bottom} />
      </div>
      {away && (
        <button
          className="scroll-latest"
          onClick={() => {
            stick.current = true;
            bottom.current?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          ↓ К последнему сообщению
        </button>
      )}
    </div>
  );
}
