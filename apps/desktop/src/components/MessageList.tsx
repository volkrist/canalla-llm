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
import type { Message } from "../types";

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
}: {
  messages: Message[];
  streaming: boolean;
  fontSize: number;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    if (stick.current) bottom.current?.scrollIntoView({ behavior: "instant" });
  }, [messages]);
  return (
    <div
      className="message-scroll"
      ref={scroll}
      onScroll={() => {
        const el = scroll.current!;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
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
              </div>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre: CodeBlock,
                  img: () => <span className="muted">[изображение]</span>,
                  a: ({ children }) => (
                    <span className="markdown-link">{children}</span>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
              {!message.content && streaming && (
                <span className="typing" aria-label="Генерация ответа">
                  ● ● ●
                </span>
              )}
            </div>
          </article>
        ))}
        <div ref={bottom} />
      </div>
    </div>
  );
}
