import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  AudioLines,
  Code2,
  Lightbulb,
  Menu,
  MessageSquare,
  X,
} from "lucide-react";
import type { Api } from "../lib/api";
import type { Health, LLMStatus, Settings, User } from "../types";
import { useChat } from "../hooks/useChat";
import Sidebar from "./Sidebar";
import MessageList from "./MessageList";
import Composer from "./Composer";
import { draftPrefix, readDraft, writeDraft } from "../lib/drafts";
import ComputePanel from "./ComputePanel";
import { exportChats } from "../lib/files";
import UsageDialog from "./UsageDialog";

export default function Workspace({
  api,
  user,
  health,
  llm,
  settings,
  onLogout,
  onSettings,
}: {
  api: Api;
  user: User;
  health: Health | null;
  llm: LLMStatus | null;
  settings: Settings;
  onLogout: () => void;
  onSettings: () => void;
}) {
  const chat = useChat(api, onLogout);
  const [sidebar, setSidebar] = useState(false);
  const prefix = draftPrefix(api.base, user.id);
  const [draft, updateDraft] = useState(() => readDraft(prefix, null));
  const setDraft = (value: string) => {
    updateDraft(value);
    writeDraft(prefix, chat.selected, value);
  };
  useEffect(() => {
    updateDraft(readDraft(prefix, chat.selected));
  }, [prefix, chat.selected]);
  useEffect(() => {
    const clear = () => updateDraft(readDraft(prefix, chat.selected));
    window.addEventListener("alex-drafts-cleared", clear);
    return () => window.removeEventListener("alex-drafts-cleared", clear);
  }, [prefix, chat.selected]);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey) {
        if (event.key.toLowerCase() === "n") {
          event.preventDefault();
          void chat.select(null);
        }
        if (event.key.toLowerCase() === "k") {
          event.preventDefault();
          setSidebar(true);
          document.getElementById("chat-search")?.focus();
        }
        if (event.key === ",") {
          event.preventDefault();
          onSettings();
        }
      }
      if (event.key === "Escape") {
        chat.stop();
        setDeleteId(null);
      }
    }
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  });
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [exportError, setExportError] = useState("");
  const [usage, setUsage] = useState<"mine" | "admin" | null>(null);
  const title =
    chat.chats.find((item) => item.id === chat.selected)?.title ||
    "Новое начало";
  const ideas = [
    {
      icon: Lightbulb,
      title: "Разобраться в идее",
      text: "Объясни сложную идею простыми словами",
    },
    {
      icon: Code2,
      title: "Посмотреть пример",
      text: "Покажи пример функции на Python",
    },
    {
      icon: MessageSquare,
      title: "Начать разговор",
      text: "Привет! Что ты умеешь?",
    },
  ];
  return (
    <div className="app-shell">
      <Sidebar
        chats={chat.chats}
        selected={chat.selected}
        user={user}
        busy={chat.busy}
        open={sidebar}
        onClose={() => setSidebar(false)}
        onSelect={(id) => {
          void chat.select(id);
        }}
        onDelete={setDeleteId}
        onUpdate={chat.update}
        onExport={(id, format) => {
          setExportError("");
          void exportChats(api, format, id).catch((error: Error) =>
            setExportError(error.message),
          );
        }}
        onUsage={() => setUsage("mine")}
        onAdmin={() => setUsage("admin")}
        onSettings={onSettings}
        onLogout={onLogout}
      />
      <main className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-only"
            aria-label="Открыть меню"
            onClick={() => setSidebar(true)}
          >
            <Menu size={21} />
          </button>
          <div className="chat-title">{title}</div>
          <div
            className={`connection ${health ? "connected" : ""}`}
            role="status"
          >
            <span className="tiny-dot" />
            {health ? "Connected" : "Offline"}
          </div>
        </header>
        <ComputePanel
          api={api}
          llm={llm}
          technical={settings.technicalDetails}
        />
        {exportError && (
          <p role="alert" className="error">
            {exportError}
          </p>
        )}
        <div className="mode-line">
          <span>{health?.provider === "mock" ? "MOCK MODE" : "ALEX LLM"}</span>
          <span>
            {health?.provider === "mock"
              ? "Демонстрационный режим · без GPU"
              : health?.llm_ready
                ? "Готов к диалогу"
                : "Ожидание сервера"}
          </span>
        </div>
        {chat.messages.length ? (
          <MessageList
            messages={chat.messages}
            streaming={chat.streaming}
            fontSize={settings.fontSize}
            settings={settings}
            busy={chat.busy}
            onEdit={chat.edit}
            onResend={(id, content) =>
              chat.send(content, { kind: "resend", messageId: id })
            }
            onRegenerate={(id) =>
              chat.send("", { kind: "regenerate", messageId: id })
            }
          />
        ) : (
          <div className="welcome">
            <div className="welcome-symbol">
              <AudioLines size={36} strokeWidth={1.5} />
            </div>
            <span className="eyebrow">МЕСТО ДЛЯ ВАШИХ МЫСЛЕЙ</span>
            <h1>О чём поговорим?</h1>
            <p>Один вопрос может стать началом чего-то большего.</p>
            <div className="suggestions">
              {ideas.map((idea) => (
                <button
                  key={idea.title}
                  disabled={chat.busy}
                  onClick={() => setDraft(idea.text)}
                >
                  <idea.icon size={21} strokeWidth={1.5} />
                  <span>{idea.title}</span>
                  <ArrowUpRight size={16} />
                </button>
              ))}
            </div>
          </div>
        )}
        {chat.busy && !chat.streaming && (
          <div className="loading" role="status">
            Загрузка…
          </div>
        )}
        {chat.error && (
          <div className="chat-error error" role="alert">
            {chat.error}
            <button
              className="icon-button"
              aria-label="Скрыть ошибку"
              onClick={chat.clearError}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {!health && (
          <p className="offline-note">
            Backend недоступен. Проверьте адрес в Settings и запустите сервер.
          </p>
        )}
        <Composer
          busy={chat.busy}
          streaming={chat.streaming}
          connected={!!health && !!llm?.available}
          onSend={chat.send}
          onStop={chat.stop}
          draft={draft}
          setDraft={setDraft}
          enterSends={settings.enterSends}
        />
      </main>
      {deleteId && (
        <div className="confirm-overlay">
          <div
            className="confirm-box"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-title"
          >
            <h2 id="delete-title">Удалить диалог?</h2>
            <p>Диалог и все его сообщения будут удалены.</p>
            <div>
              <button autoFocus onClick={() => setDeleteId(null)}>
                Отмена
              </button>
              <button
                className="danger"
                onClick={() => {
                  void chat.remove(deleteId);
                  setDeleteId(null);
                }}
              >
                Удалить
              </button>
            </div>
          </div>
        </div>
      )}
      {usage && (
        <UsageDialog
          api={api}
          admin={usage === "admin"}
          onClose={() => setUsage(null)}
        />
      )}
    </div>
  );
}
