import { useCallback, useEffect, useMemo, useState } from "react";
import {
  LOCAL_AGENT_ORIGIN,
  START_LOCAL_COMMAND,
  clearMessages,
  connect,
  eventsUrl,
  fetchGroups,
  fetchMessages,
  getStatus,
  pauseMonitor,
  pingLocalAgent,
  saveGroupSelection,
  startMonitor,
  stopMonitor,
  type GroupInfo,
  type MonitorStatus,
  type Snapshot,
  type WaMessage,
} from "./api";

function connectionLabel(status?: string): { emoji: string; text: string; className: string } {
  switch (status) {
    case "CONNECTED":
      return { emoji: "🟢", text: "CONNECTED", className: "pill pill-ok" };
    case "CONNECTING":
      return { emoji: "🟡", text: "CONNECTING", className: "pill pill-warn" };
    case "RECONNECTING":
      return { emoji: "🟡", text: "RECONNECTING", className: "pill pill-warn" };
    case "AUTHENTICATION_REQUIRED":
      return { emoji: "🔴", text: "AUTHENTICATION REQUIRED", className: "pill pill-danger" };
    case "SESSION_ERROR":
      return { emoji: "🔴", text: "SESSION ERROR", className: "pill pill-danger" };
    case "DISCONNECTED":
    default:
      return { emoji: "🔴", text: "DISCONNECTED", className: "pill pill-danger" };
  }
}

function monitorLabel(status?: MonitorStatus): { emoji: string; text: string; className: string } {
  switch (status) {
    case "RUNNING":
      return { emoji: "🟢", text: "RUNNING", className: "pill pill-ok" };
    case "PAUSED":
      return { emoji: "🟡", text: "PAUSED", className: "pill pill-warn" };
    case "STOPPED":
    default:
      return { emoji: "🔴", text: "STOPPED", className: "pill pill-muted" };
  }
}

function formatLocalTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function formatClock(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

const emptySnap: Snapshot = {
  connection: { status: "DISCONNECTED" },
  monitor_status: "STOPPED",
  monitored_groups_count: 0,
  monitored_groups: [],
  messages_received: 0,
  messages_today: 0,
  total_messages: 0,
  last_message_at: null,
  groups: [],
};

export default function App() {
  const [snap, setSnap] = useState<Snapshot>(emptySnap);
  const [messages, setMessages] = useState<WaMessage[]>([]);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [groupSearch, setGroupSearch] = useState("");
  const [msgSearch, setMsgSearch] = useState("");
  const [filterGroup, setFilterGroup] = useState("");
  const [filterSender, setFilterSender] = useState("");
  const [filterType, setFilterType] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null);
  const [copied, setCopied] = useState(false);

  const applySnap = useCallback((s: Snapshot) => {
    setSnap(s);
    if (s.groups?.length) setGroups(s.groups);
    if (s.monitored_groups) {
      setSelected(new Set(s.monitored_groups.filter((g) => g.enabled).map((g) => g.group_id)));
    }
  }, []);

  const reloadMessages = useCallback(async () => {
    const data = await fetchMessages({
      search: msgSearch || undefined,
      group_id: filterGroup || undefined,
      sender: filterSender || undefined,
      message_type: filterType || undefined,
    });
    setMessages(data.messages);
  }, [msgSearch, filterGroup, filterSender, filterType]);

  const bootstrapFromAgent = useCallback(async () => {
    setBootstrapping(true);
    const online = await pingLocalAgent();
    setAgentOnline(online);
    if (!online) {
      setBootstrapping(false);
      return;
    }
    try {
      const status = await getStatus();
      applySnap(status);
      if (status.connection.status === "DISCONNECTED") {
        applySnap(await connect());
      }
      const g = await fetchGroups(status.connection.status === "CONNECTED");
      setGroups(g.groups);
      setSelected(new Set(g.selected.filter((x) => x.enabled).map((x) => x.group_id)));
      await reloadMessages();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
      setAgentOnline(false);
    } finally {
      setBootstrapping(false);
    }
  }, [applySnap, reloadMessages]);

  useEffect(() => {
    void bootstrapFromAgent();
    const id = window.setInterval(() => {
      void pingLocalAgent().then((online) => {
        setAgentOnline((prev) => {
          if (prev === false && online) void bootstrapFromAgent();
          return online;
        });
      });
    }, 4000);
    return () => window.clearInterval(id);
  }, [bootstrapFromAgent]);

  useEffect(() => {
    if (!agentOnline) return;
    const es = new EventSource(eventsUrl());
    es.addEventListener("status", (ev) => {
      try {
        applySnap(JSON.parse((ev as MessageEvent).data));
      } catch {
        // ignore
      }
    });
    es.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse((ev as MessageEvent).data) as WaMessage;
        setMessages((prev) => {
          if (prev.some((m) => m.message_id === msg.message_id)) return prev;
          if (filterGroup && msg.group_id !== filterGroup) return prev;
          if (filterSender) {
            const s = filterSender.toLowerCase();
            const name = (msg.sender_name || "").toLowerCase();
            const id = (msg.sender_id || "").toLowerCase();
            if (!name.includes(s) && !id.includes(s)) return prev;
          }
          if (filterType && msg.message_type !== filterType) return prev;
          if (msgSearch && !(msg.message_text || "").toLowerCase().includes(msgSearch.toLowerCase())) {
            return prev;
          }
          return [msg, ...prev].slice(0, 500);
        });
      } catch {
        // ignore
      }
    });
    es.addEventListener("cleared", () => setMessages([]));
    return () => es.close();
  }, [agentOnline, applySnap, filterGroup, filterSender, filterType, msgSearch]);

  useEffect(() => {
    if (bootstrapping || !agentOnline) return;
    const t = window.setTimeout(() => {
      void reloadMessages().catch(() => undefined);
    }, 250);
    return () => window.clearTimeout(t);
  }, [msgSearch, filterGroup, filterSender, filterType, reloadMessages, bootstrapping, agentOnline]);

  const run = async (fn: () => Promise<Snapshot | (Snapshot & { removed?: number })>) => {
    setBusy(true);
    setError(null);
    try {
      applySnap(await fn());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const filteredGroups = useMemo(() => {
    const q = groupSearch.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter(
      (g) => g.group_name.toLowerCase().includes(q) || g.group_id.toLowerCase().includes(q)
    );
  }, [groups, groupSearch]);

  const conn = connectionLabel(snap.connection.status);
  const mon = monitorLabel(snap.monitor_status);
  const showQr =
    snap.connection.status === "AUTHENTICATION_REQUIRED" ||
    (snap.connection.status === "CONNECTING" && Boolean(snap.connection.qr));

  const toggleGroup = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSaveSelection = () =>
    run(() =>
      saveGroupSelection(
        groups
          .filter((g) => selected.has(g.group_id))
          .map((g) => ({ group_id: g.group_id, group_name: g.group_name, enabled: true }))
      )
    );

  const handleClearConfirm = async () => {
    setClearOpen(false);
    await run(() => clearMessages());
    setMessages([]);
  };

  const messageTypes = useMemo(() => {
    const set = new Set(messages.map((m) => m.message_type).filter(Boolean));
    return [...set].sort();
  }, [messages]);

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              WA
            </span>
            <div>
              <div className="brand-title">WhatsApp Monitor</div>
              <div className="brand-sub">Client-side agent · Listen → Store → Display</div>
            </div>
          </div>
          <a className="btn btn-ghost" href="/">
            ← Resume Agent
          </a>
        </div>
      </header>

      <main className="main">
        {bootstrapping ? <p className="muted">בודק סוכן מקומי…</p> : null}
        {error ? <div className="banner banner-error">{error}</div> : null}

        {agentOnline === false ? (
          <section className="panel agent-setup" dir="rtl">
            <h2>חיבור וואטסאפ מהמחשב שלך</h2>
            <p>
              סריקת ה-QR וה-session רצים <strong>אצלך במחשב</strong> — לא על שרת Resume Agent. כך לא
              צריך Node על השרת בענן.
            </p>
            <ol className="setup-steps">
              <li>פתח טרמינל במחשב שבו מותקן הפרויקט (עם Node.js 18+).</li>
              <li>הפעל את הסוכן המקומי:</li>
            </ol>
            <pre className="setup-cmd" dir="ltr">
              {START_LOCAL_COMMAND}
            </pre>
            <div className="row-actions" dir="ltr">
              <button
                type="button"
                className="btn btn-primary"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(START_LOCAL_COMMAND);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 2000);
                  } catch {
                    // ignore
                  }
                }}
              >
                {copied ? "Copied" : "Copy command"}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void bootstrapFromAgent()}
              >
                Retry connection
              </button>
            </div>
            <p className="muted" dir="ltr">
              Local agent: {LOCAL_AGENT_ORIGIN} · keep this browser tab on the same PC
            </p>
          </section>
        ) : null}

        {agentOnline ? (
          <>
            <section className="stats-grid" dir="ltr">
              <div className="stat">
                <div className="stat-label">Connection</div>
                <div className={conn.className}>
                  {conn.emoji} {conn.text}
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Monitor</div>
                <div className={mon.className}>
                  {mon.emoji} {mon.text}
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Selected Groups</div>
                <div className="stat-value">{snap.monitored_groups_count}</div>
              </div>
              <div className="stat">
                <div className="stat-label">Messages Today</div>
                <div className="stat-value">{snap.messages_today}</div>
              </div>
              <div className="stat">
                <div className="stat-label">Total Messages</div>
                <div className="stat-value">{snap.total_messages}</div>
              </div>
              <div className="stat">
                <div className="stat-label">Last Message</div>
                <div className="stat-value stat-value-sm">
                  {formatLocalTime(snap.last_message_at)}
                </div>
              </div>
            </section>

            <section className="controls" dir="ltr">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => run(() => startMonitor())}
              >
                ▶ START
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => run(() => pauseMonitor())}
              >
                ⏸ PAUSE
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => run(() => stopMonitor())}
              >
                ⏹ STOP
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => setClearOpen(true)}
              >
                🗑 CLEAR
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => run(() => connect())}
              >
                Connect / Refresh QR
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy || snap.connection.status !== "CONNECTED"}
                onClick={() =>
                  run(async () => {
                    const g = await fetchGroups(true);
                    setGroups(g.groups);
                    return getStatus();
                  })
                }
              >
                Refresh Groups
              </button>
            </section>

            {showQr || snap.connection.status === "AUTHENTICATION_REQUIRED" ? (
              <section className="panel qr-panel" dir="ltr">
                <h2>WhatsApp Status: NOT CONNECTED</h2>
                <p className="muted">
                  Scan this QR with WhatsApp on your phone: Linked devices → Link a device.
                </p>
                {snap.connection.qr ? (
                  <img className="qr-image" src={snap.connection.qr} alt="WhatsApp QR code" />
                ) : (
                  <p className="muted">Waiting for QR…</p>
                )}
                {snap.connection.last_error ? (
                  <p className="error-text">{snap.connection.last_error}</p>
                ) : null}
              </section>
            ) : null}

            <div className="layout">
              <section className="panel" dir="ltr">
                <div className="panel-head">
                  <h2>WhatsApp Groups</h2>
                  <span className="muted">{groups.length} available</span>
                </div>
                <input
                  className="input"
                  placeholder="Search groups…"
                  value={groupSearch}
                  onChange={(e) => setGroupSearch(e.target.value)}
                />
                <div className="row-actions">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setSelected(new Set(filteredGroups.map((g) => g.group_id)))}
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setSelected(new Set())}
                  >
                    Deselect All
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={busy}
                    onClick={() => void handleSaveSelection()}
                  >
                    Save Selection
                  </button>
                </div>
                <ul className="group-list">
                  {filteredGroups.length === 0 ? (
                    <li className="muted">
                      {snap.connection.status === "CONNECTED"
                        ? "No groups found."
                        : "Connect WhatsApp to load groups."}
                    </li>
                  ) : (
                    filteredGroups.map((g) => (
                      <li key={g.group_id}>
                        <label className="group-item">
                          <input
                            type="checkbox"
                            checked={selected.has(g.group_id)}
                            onChange={() => toggleGroup(g.group_id)}
                          />
                          <span>{g.group_name}</span>
                        </label>
                      </li>
                    ))
                  )}
                </ul>
              </section>

              <section className="panel feed-panel" dir="ltr">
                <div className="panel-head">
                  <h2>Live Feed</h2>
                  <span className="muted">Newest first</span>
                </div>
                <div className="filters">
                  <input
                    className="input"
                    placeholder="Search messages…"
                    value={msgSearch}
                    onChange={(e) => setMsgSearch(e.target.value)}
                  />
                  <select
                    className="input"
                    value={filterGroup}
                    onChange={(e) => setFilterGroup(e.target.value)}
                  >
                    <option value="">All groups</option>
                    {snap.monitored_groups.map((g) => (
                      <option key={g.group_id} value={g.group_id}>
                        {g.group_name}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    placeholder="Filter sender…"
                    value={filterSender}
                    onChange={(e) => setFilterSender(e.target.value)}
                  />
                  <select
                    className="input"
                    value={filterType}
                    onChange={(e) => setFilterType(e.target.value)}
                  >
                    <option value="">All types</option>
                    {messageTypes.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="feed">
                  {messages.length === 0 ? (
                    <p className="muted">No messages yet. Select groups and press START.</p>
                  ) : (
                    messages.map((m) => (
                      <article key={m.message_id} className="msg">
                        <div className="msg-meta">
                          <time dateTime={m.timestamp || m.received_at}>
                            {formatClock(m.timestamp || m.received_at)}
                          </time>
                          <span className="msg-group">{m.group_name}</span>
                          {m.has_media ? (
                            <span className="msg-media">
                              {(m.media_type || "media").toUpperCase()}
                            </span>
                          ) : null}
                        </div>
                        <div className="msg-sender">Sender: {m.sender_name || "Unknown"}</div>
                        <pre className="msg-body">
                          {m.message_text ||
                            (m.has_media ? `[${m.media_type || "media"}]` : "")}
                        </pre>
                        {m.links?.length ? (
                          <ul className="msg-links">
                            {m.links.map((href) => (
                              <li key={href}>
                                <a href={href} target="_blank" rel="noreferrer noopener">
                                  {href}
                                </a>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </article>
                    ))
                  )}
                </div>
              </section>
            </div>
          </>
        ) : null}
      </main>

      {clearOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setClearOpen(false)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="clear-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="clear-title">Clear message history?</h3>
            <p>Are you sure you want to delete all stored WhatsApp Monitor messages?</p>
            <p className="muted">This does not remove your WhatsApp authentication session.</p>
            <div className="row-actions">
              <button type="button" className="btn btn-ghost" onClick={() => setClearOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void handleClearConfirm()}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
