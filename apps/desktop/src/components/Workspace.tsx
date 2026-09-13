import { useState } from "react";
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
import type { Health, Settings, User } from "../types";
import { useChat } from "../hooks/useChat";
import Sidebar from "./Sidebar";
import MessageList from "./MessageList";
import Composer from "./Composer";

export default function Workspace({
  api,
  user,
  health,
  settings,
  onLogout,
  onSettings,
}: {
  api: Api;
  user: User;
  health: Health | null;
  settings: Settings;
  onLogout: () => void;
  onSettings: () => void;
}) {
  const chat = useChat(api, onLogout);
  const [sidebar, setSidebar] = useState(false);
  const [draft, setDraft] = useState("");
  const [deleteId, setDeleteId] = useState<string | null>(null);
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
          setDraft("");
          void chat.select(id);
        }}
        onDelete={setDeleteId}
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
          connected={!!health?.llm_ready}
          onSend={chat.send}
          onStop={chat.stop}
          draft={draft}
          setDraft={setDraft}
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
    </div>
  );
}
