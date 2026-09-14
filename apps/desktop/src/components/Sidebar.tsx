import {
  LogOut,
  MessageSquare,
  Plus,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import type { Chat, User } from "../types";
import Brand from "./Brand";
import { useState } from "react";

export default function Sidebar({
  chats,
  selected,
  user,
  busy,
  open,
  onClose,
  onSelect,
  onDelete,
  onSettings,
  onLogout,
  onUpdate,
  onExport,
  onUsage,
  onAdmin,
}: {
  chats: Chat[];
  selected: string | null;
  user: User;
  busy: boolean;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string | null) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
  onLogout: () => void;
  onUpdate: (
    id: string,
    patch: Partial<Pick<Chat, "title" | "pinned">>,
  ) => Promise<void>;
  onExport: (id: string, format: "json" | "markdown") => void;
  onUsage: () => void;
  onAdmin: () => void;
}) {
  const [search, setSearch] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const filtered = chats.filter((chat) =>
    chat.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  return (
    <>
      <button
        className={`sidebar-backdrop ${open ? "shown" : ""}`}
        aria-label="Закрыть меню"
        onClick={onClose}
      />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-only"
            aria-label="Закрыть меню"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>
        <button
          className="new-chat"
          disabled={busy}
          onClick={() => {
            onSelect(null);
            onClose();
          }}
        >
          <Plus size={18} /> New Chat <span>+</span>
        </button>
        <input
          id="chat-search"
          className="chat-search"
          aria-label="Поиск диалогов"
          placeholder="Поиск · Ctrl K"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="history-heading">
          ВАШИ ДИАЛОГИ <span>{chats.length.toString().padStart(2, "0")}</span>
        </div>
        <nav className="history" aria-label="История диалогов">
          {chats.length === 0 && (
            <p className="history-empty">
              Здесь появятся
              <br />
              ваши разговоры.
            </p>
          )}
          {filtered.map((chat, index) => (
            <div key={chat.id}>
              {(index === 0 || chat.pinned !== filtered[index - 1].pinned) && (
                <p className="history-heading">
                  {chat.pinned ? "ЗАКРЕПЛЕНО" : "ДИАЛОГИ"}
                </p>
              )}
              <div
                key={chat.id}
                className={`chat-item ${selected === chat.id ? "selected" : ""}`}
              >
                <button
                  className="chat-select"
                  disabled={busy}
                  onClick={() => {
                    onSelect(chat.id);
                    onClose();
                  }}
                >
                  <MessageSquare size={15} />
                  <span>{chat.title}</span>
                </button>
                <details className="chat-menu">
                  <summary aria-label={`Действия ${chat.title}`}>•••</summary>
                  <div
                    className="chat-menu-items"
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("button"))
                        event.currentTarget
                          .closest("details")
                          ?.removeAttribute("open");
                    }}
                  >
                    <button
                      disabled={busy}
                      onClick={() => {
                        setRenaming(chat.id);
                        setTitle(chat.title);
                      }}
                    >
                      Переименовать
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void onUpdate(chat.id, { pinned: !chat.pinned })
                      }
                    >
                      {chat.pinned ? "Открепить" : "Закрепить"}
                    </button>
                    <button onClick={() => onExport(chat.id, "markdown")}>
                      Экспорт Markdown
                    </button>
                    <button onClick={() => onExport(chat.id, "json")}>
                      Экспорт JSON
                    </button>
                  </div>
                </details>
                <button
                  className="delete-chat icon-button"
                  aria-label={`Удалить ${chat.title}`}
                  disabled={busy}
                  onClick={() => onDelete(chat.id)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              {renaming === chat.id && (
                <form
                  className="rename-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (title.trim()) {
                      void onUpdate(chat.id, { title: title.trim() });
                      setRenaming(null);
                    }
                  }}
                >
                  <input
                    autoFocus
                    aria-label="Название диалога"
                    maxLength={120}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                  <button disabled={busy || !title.trim()}>Сохранить</button>
                  <button type="button" onClick={() => setRenaming(null)}>
                    Отмена
                  </button>
                </form>
              )}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className="settings-link" onClick={onUsage}>
            Моё использование
          </button>
          {user.role === "admin" && (
            <button className="settings-link" onClick={onAdmin}>
              Администрирование
            </button>
          )}
          <button
            className="settings-link"
            disabled={busy}
            onClick={onSettings}
          >
            <Settings2 size={17} /> Settings
          </button>
          <div className="account">
            <span className="avatar">
              {user.email.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <strong>{user.email.split("@")[0]}</strong>
              <span title={user.email}>{user.email}</span>
            </div>
            <button
              className="icon-button"
              aria-label="Выйти"
              onClick={onLogout}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
