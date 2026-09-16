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
import PersonalPanel from "./PersonalPanel";
import FilesPanel from "./FilesPanel";
import ToolActivity from "./ToolActivity";
import BrowserPanel from "./BrowserPanel";
import {
  pairLocalDevice,
  readDeviceStatus,
  runHostJobs,
  type DeviceStatus,
} from "../lib/host";

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
  const [browser, setBrowser] = useState(false);
  const [device, setDevice] = useState<DeviceStatus>({
    paired: false,
    online: false,
  });
  useEffect(() => {
    const open = () => setBrowser(true);
    window.addEventListener("alex-browser-advanced", open);
    return () => window.removeEventListener("alex-browser-advanced", open);
  }, []);
  useEffect(() => {
    let live = true;
    const token = api.authToken();
    if (!token) return;
    const refresh = async () => {
      try {
        const prefs = await api.json<{
          device_display_name?: string;
          workspace_roots?: string[];
        }>("/tools/preferences");
        const alias = prefs.device_display_name?.trim() || "Alex-PC";
        await pairLocalDevice(api.base, token, alias);
        const status = await readDeviceStatus();
        if (live) setDevice(status);
        await runHostJobs(api.base, token, prefs.workspace_roots || []);
      } catch {
        const status = await readDeviceStatus().catch(() => ({
          paired: false,
          online: false,
        }));
        if (live) setDevice(status);
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 8000);
    const onJobs = () => void refresh();
    window.addEventListener("alex-host-jobs", onJobs);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("alex-host-jobs", onJobs);
    };
  }, [api]);
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
        <PersonalPanel
          api={api}
          onLogout={onLogout}
          chatId={chat.selected}
          projectId={
            chat.chats.find((c) => c.id === chat.selected)?.project_id || null
          }
          onProject={async (id) => {
            if (chat.selected)
              await chat.update(chat.selected, { project_id: id });
          }}
          prompt={draft}
          technical={settings.technicalDetails}
        />
        <FilesPanel
          api={api}
          projectId={
            chat.chats.find((c) => c.id === chat.selected)?.project_id || null
          }
        />
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
            api={api}
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
          webMode={chat.webMode}
          setWebMode={chat.setWebMode}
          computerMode={chat.computerMode}
          setComputerMode={chat.setComputerMode}
          deviceLabel={device.display_name || "Alex-PC"}
          deviceOnline={!!device.online}
          phase={chat.phase}
          busy={chat.busy}
          streaming={chat.streaming}
          connected={!!health && !!llm?.available}
          onSend={(text, mode) => chat.send(text, undefined, mode)}
          onStop={chat.stop}
          draft={draft}
          setDraft={setDraft}
          enterSends={settings.enterSends}
        />
        <ToolActivity
          api={api}
          runs={chat.toolRuns}
          state={chat.webState}
          error={chat.webError}
        />
      </main>
      {browser && (
        <BrowserPanel
          api={api}
          chatId={chat.selected}
          onClose={() => setBrowser(false)}
        />
      )}
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
