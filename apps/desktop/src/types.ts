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
}
export interface LLMStatus {
  provider: "mock" | "llamacpp";
  available: boolean;
  state: string;
  model?: string;
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
