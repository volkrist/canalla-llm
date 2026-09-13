export interface User {
  id: string;
  email: string;
  created_at: string;
}
export interface Chat {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
export interface Health {
  status: string;
  provider: string;
  llm_ready: boolean;
}
export interface Settings {
  backendUrl: string;
  fontSize: number;
}
