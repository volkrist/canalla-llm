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
}) {
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
          {chats.map((chat) => (
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
              <button
                className="delete-chat icon-button"
                aria-label={`Удалить ${chat.title}`}
                disabled={busy}
                onClick={() => onDelete(chat.id)}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
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
