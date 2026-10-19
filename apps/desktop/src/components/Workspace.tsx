import { useCallback, useEffect, useRef, useState } from "react";
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
import { useContextUsage } from "../lib/context-usage";
import { markBusy, clearBusy } from "../lib/busy";
import ComputePanel from "./ComputePanel";
import ComputeBar from "./ComputeBar";
import ComputerBar from "./ComputerBar";
import StatusChips, { StatusBilling } from "./StatusChips";
import AiConnectionBadge from "./AiConnectionBadge";
import { useCloud, stopCloudCompute, refreshCloud } from "../lib/cloud";
import { useStatus } from "../hooks/useStatus";
import type { RecoveryAction } from "../lib/status";
import { recoveryPlan } from "../lib/errors";
import { exportChats } from "../lib/files";
import UsageDialog from "./UsageDialog";
import PersonalPanel from "./PersonalPanel";
import FilesPanel from "./FilesPanel";
import ToolActivity from "./ToolActivity";
import TaskPanel from "./TaskPanel";
import TaskHistory from "./TaskHistory";
import BrowserPanel from "./BrowserPanel";
import {
  pairLocalDevice,
  readDeviceStatus,
  runHostJobs,
  type DeviceStatus,
} from "../lib/host";
import { DEVICE_REFRESH_EVENT, publishDevice } from "../lib/device";

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
  // While the user is waiting for an answer, an update install must not restart the app.
  useEffect(() => {
    if (chat.streaming) markBusy("generation");
    else clearBusy("generation");
    return () => clearBusy("generation");
  }, [chat.streaming]);
  const status = useStatus(api, true);
  // The same store the cloud panels read: the badge only reports it, it never polls it. The
  // installation may be enrolled but still be unable to reach the Gateway, so both facts travel.
  const cloudState = useCloud(api);
  const cloud = {
    state: cloudState.status?.state ?? null,
    enrolled: Boolean(cloudState.status?.enrolled),
  };
  const [sidebar, setSidebar] = useState(false);
  const [browser, setBrowser] = useState(false);
  const [stoppingAi, setStoppingAi] = useState(false);
  const [stopError, setStopError] = useState("");
  const rootsRef = useRef<string[]>([]);
  const stopAi = useCallback(async () => {
    setStoppingAi(true);
    setStopError("");
    try {
      // The same Gateway operation the chat uses; it also writes the manual-off latch, so no
      // automatic request recreates the Pod after this click.
      try {
        await stopCloudCompute();
      } catch {
        // Direct (dev/private) mode has no shared Gateway: use the local compute stop.
        await api.json("/compute/stop", { method: "POST", body: "{}" });
      }
      await status.refresh();
      await refreshCloud();
    } catch (error) {
      setStopError(
        error instanceof Error ? error.message : "Не удалось остановить AI",
      );
    } finally {
      setStoppingAi(false);
    }
  }, [status]);
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
    // Heartbeat and job execution are deliberately separate loops. A missed heartbeat must not
    // stop the host from claiming jobs, and a busy generation must not pause either one.
    const heartbeat = async () => {
      const token = api.authToken();
      if (!token) return;
      try {
        const prefs = await api.json<{
          device_display_name?: string;
          workspace_roots?: string[];
        }>("/tools/preferences");
        rootsRef.current = prefs.workspace_roots || [];
        const alias = prefs.device_display_name?.trim() || "Windows device";
        const heartbeat = await pairLocalDevice(
          api.base,
          token,
          alias,
          user.id,
        );
        const local = await readDeviceStatus();
        const status = {
          ...local,
          paired: true,
          display_name: heartbeat.display_name || local.display_name,
          online: !!heartbeat.online,
        };
        if (live) {
          setDevice(status);
          publishDevice(status);
        }
      } catch {
        const local = await readDeviceStatus().catch(() => ({
          paired: false,
          online: false,
        }));
        if (live) {
          const status = { ...local, online: false };
          setDevice(status);
          publishDevice(status);
        }
      }
    };
    const runJobs = async () => {
      const token = api.authToken();
      if (!token) return;
      try {
        await runHostJobs(api.base, token, rootsRef.current);
      } catch {
        // The next tick retries; a failed claim is not a reason to drop the heartbeat.
      }
    };
    void heartbeat();
    void runJobs();
    const heartbeatTimer = setInterval(() => void heartbeat(), 8000);
    const jobsTimer = setInterval(() => void runJobs(), 2000);
    const onJobs = () => {
      void heartbeat();
      void runJobs();
    };
    window.addEventListener("alex-host-jobs", onJobs);
    window.addEventListener(DEVICE_REFRESH_EVENT, onJobs);
    return () => {
      live = false;
      clearInterval(heartbeatTimer);
      clearInterval(jobsTimer);
      window.removeEventListener("alex-host-jobs", onJobs);
      window.removeEventListener(DEVICE_REFRESH_EVENT, onJobs);
    };
  }, [api, user.id]);
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
  // The meter follows the live context: the draft, the open chat and every new message.
  const contextUsage = useContextUsage(api, chat.selected, draft, {
    enabled: !!health,
    refreshKey: chat.messages.length,
  });
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
  function statusAction(action: RecoveryAction) {
    // Each branch reuses machinery that already exists: the settings dialog, the device
    // loop, the compute panel with its own confirmation, or a plain re-read of the
    // authoritative snapshot. Nothing here starts compute.
    const plan = recoveryPlan(action);
    if (plan.kind === "settings") onSettings();
    if (plan.event) window.dispatchEvent(new Event(plan.event));
    void status.refresh();
  }
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
        busy={chat.loading}
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
          <StatusChips
            compact
            snapshot={status.snapshot}
            error={status.error}
            refreshing={status.refreshing}
            onAction={statusAction}
            api={api}
            stale={status.stale}
            reconnecting={status.reconnecting}
          />
          <AiConnectionBadge
            snapshot={status.snapshot}
            stale={status.stale}
            backendReady={Boolean(health)}
            cloud={cloud}
            onAction={statusAction}
            onStopAi={() => void stopAi()}
            stopping={stoppingAi}
            reconnecting={status.reconnecting}
          />
        </header>
        <div className="status-line">
          <StatusBilling snapshot={status.snapshot} />
          {stopError && (
            <span className="error" role="alert">
              {stopError}
            </span>
          )}
        </div>
        <ComputerBar
          snapshot={status.stale ? null : status.snapshot}
          onAction={statusAction}
        />
        <ComputeBar api={api} llm={llm} />
        <PersonalPanel
          api={api}
          onLogout={onLogout}
          chatId={chat.selected}
          projectId={
            chat.chats.find((c) => c.id === chat.selected)?.project_id || null
          }
          variant="bar"
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
          variant="dialog"
        />
        {exportError && (
          <p role="alert" className="error">
            {exportError}
          </p>
        )}
        <div className="mode-line">
          <span>
            {health?.provider === "mock" ? "MOCK MODE" : "CANALLA LLM"}
          </span>
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
            busy={chat.loading}
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
                  disabled={chat.loading}
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
            {chat.phase === "starting_ai" || llm?.ai === "starting"
              ? "Запускаю AI…"
              : "Загрузка…"}
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
          torMode={chat.torMode}
          setTorMode={chat.setTorMode}
          computerMode={chat.computerMode}
          setComputerMode={chat.setComputerMode}
          deviceLabel={device.display_name || "Windows device"}
          deviceOnline={!!device.online}
          phase={chat.phase}
          busy={chat.loading}
          streaming={chat.streaming}
          queued={chat.queued}
          connected={!!health}
          onSend={(text, mode) => chat.send(text, undefined, mode)}
          onStop={chat.stop}
          draft={draft}
          setDraft={setDraft}
          enterSends={settings.enterSends}
          contextUsage={contextUsage.usage}
          contextFailed={contextUsage.failed}
        />
        <TaskPanel
          task={chat.task}
          busy={chat.streaming}
          onPause={() => void chat.pauseTask()}
          onResume={() => void chat.resumeTask()}
          onStop={() => void chat.stopTask()}
        />
        <TaskHistory
          tasks={chat.tasks}
          onOpen={(_id, chatId) => {
            if (chatId) void chat.select(chatId);
          }}
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
