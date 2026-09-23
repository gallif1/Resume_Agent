export type ConnectionStatus =
  | "CONNECTED"
  | "CONNECTING"
  | "DISCONNECTED"
  | "AUTHENTICATION_REQUIRED"
  | "SESSION_ERROR"
  | "RECONNECTING";

export type MonitorStatus = "RUNNING" | "PAUSED" | "STOPPED";

export type GroupInfo = {
  group_id: string;
  group_name: string;
  participant_count?: number | null;
};

export type MonitoredGroup = {
  group_id: string;
  group_name: string;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
};

export type WaMessage = {
  id?: number;
  message_id: string;
  group_id: string;
  group_name: string;
  sender_id?: string | null;
  sender_name?: string | null;
  message_text: string;
  message_type: string;
  timestamp?: string | null;
  received_at: string;
  links: string[];
  has_media: boolean;
  media_type?: string | null;
};

export type Snapshot = {
  connection: {
    status: ConnectionStatus;
    qr?: string | null;
    last_error?: string | null;
    groups_count?: number;
  };
  monitor_status: MonitorStatus;
  monitored_groups_count: number;
  monitored_groups: MonitoredGroup[];
  messages_received: number;
  messages_today: number;
  total_messages: number;
  last_message_at?: string | null;
  groups: GroupInfo[];
};

const API_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.error || detail;
    } catch {
      // ignore
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getStatus() {
  return api<Snapshot>("/status");
}

export function connect() {
  return api<Snapshot>("/connect", { method: "POST" });
}

export function startMonitor() {
  return api<Snapshot>("/monitor/start", { method: "POST" });
}

export function pauseMonitor() {
  return api<Snapshot>("/monitor/pause", { method: "POST" });
}

export function stopMonitor() {
  return api<Snapshot>("/monitor/stop", { method: "POST" });
}

export function clearMessages() {
  return api<Snapshot & { removed?: number }>("/monitor/clear", { method: "POST" });
}

export function fetchGroups(refresh = false) {
  return api<{
    groups: GroupInfo[];
    selected: MonitoredGroup[];
    connection: Snapshot["connection"];
  }>(`/groups${refresh ? "?refresh=1" : ""}`);
}

export function saveGroupSelection(groups: { group_id: string; group_name: string; enabled: boolean }[]) {
  return api<Snapshot>("/groups/selection", {
    method: "POST",
    body: JSON.stringify({ groups }),
  });
}

export function fetchMessages(params: {
  search?: string;
  group_id?: string;
  sender?: string;
  message_type?: string;
}) {
  const q = new URLSearchParams();
  if (params.search) q.set("search", params.search);
  if (params.group_id) q.set("group_id", params.group_id);
  if (params.sender) q.set("sender", params.sender);
  if (params.message_type) q.set("message_type", params.message_type);
  const qs = q.toString();
  return api<{ messages: WaMessage[] }>(`/messages${qs ? `?${qs}` : ""}`);
}

export function eventsUrl() {
  return `${API_BASE}/events`;
}
