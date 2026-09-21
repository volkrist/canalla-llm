export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "user";
  created_at: string;
}
export interface Chat {
  id: string;
  title: string;
  pinned: boolean;
  project_id: string | null;
  created_at: string;
  updated_at: string;
}
export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant";
  content: string;
  status: "generating" | "complete" | "stopped" | "error";
  edited_at: string | null;
  created_at: string;
}
export interface Health {
  status: string;
  provider: string;
  llm_ready: boolean;
  product?: string;
  version?: string;
  runtime_protocol_version?: number;
  instance?: string | null;
}
export interface LLMStatus {
  provider: "mock" | "llamacpp";
  available: boolean;
  state: string;
  model?: string;
  ai?: "off" | "starting" | "ready" | "waiting" | "unavailable" | "error";
  ai_label?: string;
  diagnostic?: {
    pod_id?: string | null;
    gpu?: string | null;
    datacenter?: string | null;
    price_per_hour?: number | string | null;
    estimated_spend?: number | string | null;
    started_at?: string | null;
    idle_deadline?: string | null;
    managed?: boolean | null;
    last_error?: string | null;
    compute_state?: string | null;
  };
}
export interface Settings {
  backendUrl: string;
  fontSize: number;
  theme: "dark" | "system";
  language: "ru";
  enterSends: boolean;
  autoScroll: boolean;
  timestamps: boolean;
  technicalDetails: boolean;
}
