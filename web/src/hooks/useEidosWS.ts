import { useEffect, useRef, useState, useCallback } from 'react';
import { AgentState, BackgroundActivity, ChatAction, ChatMessage, ConfirmationRecord, InboundWSEvent, OutboundWSMessage, SharedFile } from '../types/gateway';

/** Turn gateway tool events into the action chips shown on an assistant message. */
function normalizeActions(raw: any): ChatAction[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((action) => ({
    tool: String(action?.tool || action?.name || 'action'),
    arguments:
      action?.arguments == null
        ? ''
        : typeof action.arguments === 'string'
        ? action.arguments
        : JSON.stringify(action.arguments),
    result: action?.result == null ? '' : String(action.result),
    pending: Boolean(action?.pending),
  }));
}

/**
 * Map one stored turn into a chat message.
 * A failed attempt already writes its reasoning and actions into the body, so those
 * are not also attached as separate chips.
 */
function mapHistoryMessage(m: any, index: number): ChatMessage {
  const content = m?.content || '';
  const traceInBody = content.includes('Actions already taken:') || content.includes('\nReasoning:');
  const thoughts = Array.isArray(m?.thoughts) ? m.thoughts.map((item: any) => String(item)).filter(Boolean) : [];
  const actions = normalizeActions(m?.actions);
  return {
    id: m?.id || `hist_${index}_${m?.role || 'turn'}`,
    role: m?.role || 'assistant',
    content,
    thoughts: traceInBody ? [] : thoughts,
    actions: traceInBody ? [] : actions,
    timestamp: m?.timestamp || Date.now(),
    isHistorical: true,
  };
}

function sameText(left: string | undefined, right: string | undefined): boolean {
  return (left || '').trim() === (right || '').trim();
}

function sameMessage(left: ChatMessage | undefined, right: ChatMessage | undefined): boolean {
  return !!left && !!right && left.role === right.role && sameText(left.content, right.content);
}

/** The reply that is still being written, if this turn already has one. */
function openReply(messages: ChatMessage[]): ChatMessage | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'assistant' && message.streaming) return message;
  }
  return undefined;
}

/**
 * Start the on-screen turn for a task.
 * A repeat of the same start (a second socket, or the echo of a message already shown)
 * leaves the existing user bubble and reply in place.
 */
function beginLiveTurn(prev: ChatMessage[], goal: string, assistantId: string): ChatMessage[] {
  const streaming = openReply(prev);
  if (streaming) {
    const index = prev.indexOf(streaming);
    const userBefore = index > 0 ? prev[index - 1] : undefined;
    const alreadyThisTurn = !goal || (userBefore?.role === 'user' && sameText(userBefore.content, goal));
    if (alreadyThisTurn) return prev;
  }

  const last = prev[prev.length - 1];
  const needsUser = !!goal && !(last?.role === 'user' && sameText(last.content, goal));
  const withUser = needsUser
    ? [
        ...prev,
        {
          id: `user_${Date.now()}`,
          role: 'user' as const,
          content: goal,
          timestamp: Date.now(),
        },
      ]
    : prev;

  if (openReply(withUser)) return withUser;
  return [
    ...withUser,
    {
      id: assistantId,
      role: 'assistant' as const,
      content: '',
      thoughts: [],
      timestamp: Date.now(),
      streaming: true,
      isHistorical: false,
    },
  ];
}

/** Fold a token, thought, or tool update into the open reply instead of starting another one. */
function updateOpenReply(
  prev: ChatMessage[],
  update: (message: ChatMessage) => ChatMessage,
  create: () => ChatMessage
): ChatMessage[] {
  const streaming = openReply(prev);
  const target = streaming || (prev[prev.length - 1]?.role === 'assistant' ? prev[prev.length - 1] : undefined);
  if (!target) return [...prev, create()];
  const index = prev.lastIndexOf(target);
  return [...prev.slice(0, index), update(target), ...prev.slice(index + 1)];
}

/**
 * Keep a live reply on screen when a rewind snapshot arrives after that reply started.
 * A different conversation replaces the thread outright.
 * The snapshot's copy of this turn is kept once; the live user bubble is not appended again.
 */
function mergeHistoryWithLiveTail(loaded: ChatMessage[], prev: ChatMessage[]): ChatMessage[] {
  if (loaded.length === 0) return loaded;
  const streamIdx = prev.findIndex((message) => message.streaming);
  if (streamIdx === -1) return loaded;
  const isSameThread = loaded.every((message, index) => sameMessage(prev[index], message));
  if (!isSameThread) return loaded;

  const liveTail = prev.slice(streamIdx).filter((live, offset) => !sameMessage(loaded[streamIdx + offset], live));
  const before = streamIdx > 0 ? prev[streamIdx - 1] : undefined;
  const userAlreadySaved =
    !!before &&
    before.role === 'user' &&
    loaded
      .slice(Math.max(0, streamIdx - 1))
      .some((saved) => saved.role === 'user' && sameText(saved.content, before.content));
  if (before && before.role === 'user' && !userAlreadySaved) {
    return [...loaded, before, ...liveTail];
  }
  return [...loaded, ...liveTail];
}

/** Coerce a shared-file payload into the shape the file list renders. */
function normalizeSharedFile(raw: any): SharedFile {
  return {
    id: String(raw?.id || ''),
    name: String(raw?.name || 'file'),
    path: raw?.path ? String(raw.path) : '',
    mime: String(raw?.mime || 'application/octet-stream'),
    size: Number(raw?.size || 0),
    direction: raw?.direction === 'user' ? 'user' : 'bot',
    note: raw?.note ? String(raw.note) : '',
    conversation_id: raw?.conversation_id ? String(raw.conversation_id) : '',
    created_at: Number(raw?.created_at || Date.now() / 1000),
    missing: Boolean(raw?.missing),
  };
}

export interface UseEidosWSOptions {
  onSendNotification?: (title: string, options?: NotificationOptions) => Promise<boolean> | void;
}

/** Live gateway connection: chat, agent status, files, and safety confirmations. */
export function useEidosWS(options: UseEidosWSOptions = {}) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const connectRef = useRef<() => void>(() => {});

  const [token, setTokenState] = useState<string>(() => {
    const urlToken = new URLSearchParams(window.location.search).get('token');
    if (urlToken) {
      localStorage.setItem('eidos_token', urlToken);
      // Clean token out of address bar and browser history immediately
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete('token');
        window.history.replaceState({}, '', url.pathname + (url.search || '') + url.hash);
      } catch {}
      return urlToken;
    }
    return localStorage.getItem('eidos_token') || '';
  });

  const [connected, setConnected] = useState(false);
  const [agentState, setAgentState] = useState<AgentState>('idle');
  const [stateDetails, setStateDetails] = useState('');
  const [isKilled, setIsKilled] = useState(false);
  const [confirmations, setConfirmations] = useState<ConfirmationRecord[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sharedFiles, setSharedFiles] = useState<SharedFile[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [conversationTitle, setConversationTitle] = useState<string>('New Chat');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isSyncing, setIsSyncing] = useState<boolean>(false);
  const [retryNotice, setRetryNotice] = useState<string | null>(null);
  const [backgroundWork, setBackgroundWork] = useState<BackgroundActivity>({
    active: false,
    task: '',
    details: '',
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const currentAssistantMsgIdRef = useRef<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);

  /** Save the gateway auth token and keep it for later visits. */
  const setToken = useCallback((newToken: string) => {
    localStorage.setItem('eidos_token', newToken);
    setTokenState(newToken);
  }, []);

  /** Prepend a timestamped line to the on-screen activity log, keeping the latest 100. */
  const addLog = useCallback((msg: string) => {
    setLogs((prev) => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev.slice(0, 99)]);
  }, []);

  /** Send a command over the socket, or the matching REST route when the socket is down. */
  const sendMessage = useCallback((msg: OutboundWSMessage) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    } else {
      // HTTP Fallback
      const headers = {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      };
      if (msg.type === 'prompt' && msg.prompt) {
        fetch('/api/prompt', {
          method: 'POST',
          headers,
          body: JSON.stringify({ prompt: msg.prompt }),
        }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'stop' || msg.type === 'cancel' || msg.type === 'stop_task') {
        fetch('/api/stop', { method: 'POST', headers }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'kill') {
        fetch('/api/kill', { method: 'POST', headers }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'reset') {
        fetch('/api/reset', { method: 'POST', headers }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'resolve_confirmation' && msg.id) {
        fetch(`/api/confirmations/${msg.id}`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ approved: msg.approved, allowlist: msg.allowlist }),
        }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'edit_retry' && msg.prompt) {
        fetch('/api/edit_retry', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            prompt: msg.prompt,
            id: msg.id,
            message_index: msg.message_index,
          }),
        })
          .then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.ok === false) {
              setRetryNotice(data.error || 'Could not retry that message.');
            }
          })
          .catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'new_chat') {
        fetch('/api/conversations/new', {
          method: 'POST',
          headers,
          body: JSON.stringify({ title: msg.title || 'New Chat' }),
        }).catch((e) => addLog(`HTTP error: ${e}`));
      } else if (msg.type === 'switch_conversation' && msg.conversation_id) {
        fetch('/api/conversations/switch', {
          method: 'POST',
          headers,
          body: JSON.stringify({ conversation_id: msg.conversation_id }),
        }).catch((e) => addLog(`HTTP error: ${e}`));
      }
    }
  }, [token, addLog]);

  /** Replace the visible chat with the active conversation from the gateway. */
  const loadHistory = useCallback(async (force: boolean = false) => {
    if (!token) return;
    try {
      const res = await fetch('/api/history', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          if (data.title) setConversationTitle(data.title);
          if (data.conversation_id) {
            conversationIdRef.current = data.conversation_id;
            setConversationId(data.conversation_id);
          }
          if (Array.isArray(data.files)) {
            setSharedFiles(data.files.map(normalizeSharedFile));
          }
          if (Array.isArray(data.messages)) {
            setMessages((prev) => {
              const loaded: ChatMessage[] = data.messages.map((m: any, i: number) => ({
                ...mapHistoryMessage(m, i),
                timestamp: m.timestamp || Date.now() - (data.messages.length - i) * 1000,
              }));
              // Preserve any live streaming tail so an active response is never wiped when syncing
              return mergeHistoryWithLiveTail(loaded, prev);
            });
          }
        }
      }
    } catch {
      // Ignore network errors on initial load
    }
  }, [token]);

  /** Pull chat, status, and files so this client matches the desktop session. */
  const refreshSync = useCallback(async () => {
    if (!token) return;
    setIsSyncing(true);
    try {
      await loadHistory(true);
      const res = await fetch('/api/status', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          if (data.state) setAgentState(data.state);
          if (data.task !== undefined) setStateDetails(data.task);
          if (data.killed !== undefined) setIsKilled(data.killed);
          if (data.background_active !== undefined) {
            setBackgroundWork({
              active: Boolean(data.background_active),
              task: data.background_task || '',
              details: data.background_details || '',
            });
          }
        }
      }
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'sync' }));
      }
      addLog('Synced chat and state with desktop app.');
    } catch (e) {
      addLog(`Sync error: ${e}`);
    } finally {
      setTimeout(() => setIsSyncing(false), 450);
    }
  }, [token, loadHistory, addLog]);

  /** Clear the local thread and ask the gateway to open a new conversation. */
  const startNewChat = useCallback((title: string = 'New Chat') => {
    setMessages([]);
    setSharedFiles([]);
    setConversationTitle(title);
    currentAssistantMsgIdRef.current = null;
    sendMessage({ type: 'new_chat', title });
    addLog('Started new conversation thread.');
  }, [sendMessage, addLog]);

  /** Ask the gateway to activate a past conversation, then reload its messages. */
  const switchConversation = useCallback((convId: string) => {
    sendMessage({ type: 'switch_conversation', conversation_id: convId });
    setTimeout(() => loadHistory(true), 150);
    addLog(`Switching to conversation ${convId}...`);
  }, [sendMessage, loadHistory, addLog]);

  // Connect WebSocket
  useEffect(() => {
    if (!token) return;

    let isUnmounted = false;
    let pingTimer: number | null = null;

    /** Open one gateway socket. A second call while this socket is up does not open another. */
    function connect() {
      if (isUnmounted) return;
      const existing = wsRef.current;
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return;
      }
      if (pingTimer) clearInterval(pingTimer);
      if (existing) {
        existing.onopen = null;
        existing.onmessage = null;
        existing.onerror = null;
        existing.onclose = null;
        if (existing.readyState !== WebSocket.CLOSED) existing.close();
      }
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const wsUrl = `${proto}//${host}/ws?token=${encodeURIComponent(token)}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      // Keepalive heartbeat every 20 seconds to prevent mobile carriers, NAT, and Tailscale from dropping idle socket
      pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try {
            ws.send(JSON.stringify({ type: 'ping' }));
          } catch {}
        }
      }, 20000);

      ws.onopen = () => {
        if (isUnmounted || wsRef.current !== ws) return;
        setConnected(true);
        addLog('Connected to EidosBot Gateway.');
        refreshSync();
      };

      ws.onmessage = (event) => {
        if (isUnmounted || wsRef.current !== ws) return;
        try {
          const data: InboundWSEvent = JSON.parse(event.data);
          const ev = data.event;

          if (ev === 'status_change') {
            if (data.state) {
              setAgentState(data.state);
              if (data.state.toLowerCase() === 'killed') {
                setIsKilled(true);
              } else if (['idle', 'thinking', 'executing', 'perceiving'].includes(data.state.toLowerCase()) && !data.killed) {
                setIsKilled(false);
              }
              // When transitioning to a terminal state (idle, error, killed),
              // clear streaming flag on all active messages so nothing gets stuck
              if (['idle', 'error', 'killed'].includes(data.state.toLowerCase())) {
                setMessages((prev) =>
                  prev.map((m) => {
                    if (m.streaming) {
                      return {
                        ...m,
                        streaming: false,
                        content: m.content || (data.state === 'error' ? (data.details || 'Task encountered an error.') : m.content),
                      };
                    }
                    return m;
                  })
                );
                currentAssistantMsgIdRef.current = null;
              }
            }
            if (data.details !== undefined) setStateDetails(data.details);
            if (data.killed !== undefined) setIsKilled(data.killed);
            if (data.conversation_title) setConversationTitle(data.conversation_title);
            if (data.conversation_id) {
              conversationIdRef.current = data.conversation_id;
              setConversationId(data.conversation_id);
            }
          } else if (ev === 'conversation_changed') {
            if (data.title) setConversationTitle(data.title);
            if (data.conversation_id) {
              conversationIdRef.current = data.conversation_id;
              setConversationId(data.conversation_id);
            }
            currentAssistantMsgIdRef.current = null;
            if (Array.isArray(data.files)) {
              setSharedFiles(data.files.map(normalizeSharedFile));
            }
            if (Array.isArray(data.messages)) {
              const loaded = data.messages.map((m: any, i: number) => mapHistoryMessage(m, i));
              setMessages((prev) => mergeHistoryWithLiveTail(loaded, prev));
              setRetryNotice(null);
            } else {
              loadHistory(true);
            }
            addLog(`Conversation updated: ${data.title || 'New Chat'}`);
          } else if (ev === 'conversation_title_changed') {
            if (data.title) setConversationTitle(data.title);
          } else if (ev === 'sync_history') {
            if (data.title) setConversationTitle(data.title);
            if (data.conversation_id) {
              conversationIdRef.current = data.conversation_id;
              setConversationId(data.conversation_id);
            }
            if (Array.isArray(data.files)) {
              setSharedFiles(data.files.map(normalizeSharedFile));
            }
            if (Array.isArray(data.messages)) {
              const loaded = data.messages.map((m: any, i: number) => mapHistoryMessage(m, i));
              setMessages((prev) => mergeHistoryWithLiveTail(loaded, prev));
            }
          } else if (ev === 'task_start') {
            // One user bubble and one open reply. A repeated start for this turn is ignored.
            const goal = (data.goal || '').trim();
            const newAssistantId = `msg_${Date.now()}`;
            setMessages((prev) => {
              const next = beginLiveTurn(prev, goal, newAssistantId);
              const streaming = openReply(next);
              currentAssistantMsgIdRef.current = streaming?.id || null;
              return next;
            });
          } else if (ev === 'token_chunk' && data.chunk) {
            const chunk = data.chunk;
            setMessages((prev) =>
              updateOpenReply(
                prev,
                (message) => ({ ...message, content: message.content + chunk }),
                () => {
                  const newId = `msg_${Date.now()}`;
                  currentAssistantMsgIdRef.current = newId;
                  return {
                    id: newId,
                    role: 'assistant',
                    content: chunk,
                    thoughts: [],
                    timestamp: Date.now(),
                    streaming: true,
                    isHistorical: false,
                  };
                }
              )
            );
          } else if (ev === 'thought_chunk' && data.chunk) {
            const thought = data.chunk;
            setMessages((prev) =>
              updateOpenReply(
                prev,
                (message) => {
                  const existingThoughts = message.thoughts || [];
                  if (existingThoughts[existingThoughts.length - 1] === thought) return message;
                  return { ...message, thoughts: [...existingThoughts, thought] };
                },
                () => ({
                  id: `msg_${Date.now()}`,
                  role: 'assistant',
                  content: '',
                  thoughts: [thought],
                  timestamp: Date.now(),
                  streaming: true,
                  isHistorical: false,
                })
              )
            );
          } else if (ev === 'tool_start') {
            const tool = String(data.tool || 'action');
            const args =
              data.arguments == null
                ? ''
                : typeof data.arguments === 'string'
                ? data.arguments
                : JSON.stringify(data.arguments);
            const action: ChatAction = { tool, arguments: args, result: '', pending: true };
            setMessages((prev) =>
              updateOpenReply(
                prev,
                (message) => {
                  const actions = message.actions || [];
                  const lastAction = actions[actions.length - 1];
                  if (lastAction && lastAction.tool === action.tool && lastAction.arguments === action.arguments && lastAction.pending) {
                    return message;
                  }
                  return { ...message, actions: [...actions, action] };
                },
                () => ({
                  id: `msg_${Date.now()}`,
                  role: 'assistant' as const,
                  content: '',
                  thoughts: [],
                  actions: [action],
                  timestamp: Date.now(),
                  streaming: true,
                  isHistorical: false,
                })
              )
            );
          } else if (ev === 'tool_end') {
            const tool = String(data.tool || 'action');
            const result = data.result == null ? '' : String(data.result);
            setMessages((prev) =>
              updateOpenReply(
                prev,
                (message) => {
                  const actions = [...(message.actions || [])];
                  for (let i = actions.length - 1; i >= 0; i -= 1) {
                    if (actions[i].tool === tool && actions[i].pending) {
                      actions[i] = { ...actions[i], result, pending: false };
                      break;
                    }
                  }
                  return { ...message, actions };
                },
                () => ({
                  id: `msg_${Date.now()}`,
                  role: 'assistant',
                  content: '',
                  thoughts: [],
                  actions: [{ tool, arguments: '', result, pending: false }],
                  timestamp: Date.now(),
                  streaming: true,
                  isHistorical: false,
                })
              )
            );
          } else if (ev === 'task_finished') {
            const failed = data.success === false;
            const record = failed ? (data.detail || data.summary || '').trim() : '';
            const headline = (data.headline || (!failed ? data.summary : '') || '').trim();
            const incomingThoughts = Array.isArray(data.thoughts)
              ? data.thoughts.map((item) => String(item)).filter(Boolean)
              : [];
            const incomingActions = normalizeActions(data.actions);
            /** Close a streaming reply. A failed run keeps the written attempt as the message body. */
            const applyFinish = (message: ChatMessage): ChatMessage => {
              if (record) {
                return {
                  ...message,
                  content: record,
                  thoughts: [],
                  actions: [],
                  streaming: false,
                  isHistorical: false,
                };
              }
              return {
                ...message,
                content:
                  message.content ||
                  headline ||
                  (failed ? 'Task ended before it finished.' : 'Task completed.'),
                thoughts: message.thoughts?.length ? message.thoughts : incomingThoughts,
                actions: message.actions?.length ? message.actions : incomingActions,
                streaming: false,
                isHistorical: false,
              };
            };
            setMessages((prev) => {
              const streaming = openReply(prev);
              if (streaming) {
                const index = prev.lastIndexOf(streaming);
                return [...prev.slice(0, index), applyFinish(streaming), ...prev.slice(index + 1)];
              }
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                // A reply that already finished stays as it was sent. Stopping
                // read-aloud, or a late cancel, must not replace that text.
                if (!last.streaming && last.content.trim() && failed) return prev;
                const finished = applyFinish(last);
                if (sameText(last.content, finished.content) && !last.streaming) return prev;
                return [...prev.slice(0, -1), finished];
              }
              const text = record || headline;
              if (text && prev.some((message) => message.role === 'assistant' && sameText(message.content, text))) {
                return prev;
              }
              if (record || headline || incomingThoughts.length || incomingActions.length) {
                return [
                  ...prev,
                  applyFinish({
                    id: `msg_${Date.now()}`,
                    role: 'assistant',
                    content: '',
                    thoughts: [],
                    actions: [],
                    timestamp: Date.now(),
                  }),
                ];
              }
              return prev;
            });
            currentAssistantMsgIdRef.current = null;
            addLog(`Task finished: ${data.headline || data.summary || 'Done'}`);

            // Dispatch notification if app is in background or permission granted
            if (optionsRef.current?.onSendNotification) {
              const headline =
                data.headline ||
                data.summary ||
                (data.success !== false ? 'Task completed successfully.' : 'Task ended before it finished.');
              optionsRef.current.onSendNotification(
                data.success !== false ? 'EidosBot: Task Complete' : 'EidosBot: Task Failed',
                {
                  body: headline,
                  tag: 'eidos-task-finished',
                  data: { url: '/' },
                }
              );
            }
          } else if (ev === 'background_activity') {
            const active = Boolean(data.active);
            const task = data.task || '';
            const details = data.details || '';
            setBackgroundWork({ active, task, details });
            if (active) {
              addLog(`Background activity: ${task}${details ? ` - ${details}` : ''}`);
              if (optionsRef.current?.onSendNotification) {
                optionsRef.current.onSendNotification('EidosBot: Background Task Active', {
                  body: details ? `${task}: ${details}` : `EidosBot is working in the background on ${task}`,
                  tag: 'eidos-background-task',
                  data: { url: '/' },
                });
              }
            } else {
              addLog(`Background activity completed: ${task}`);
            }
          } else if (ev === 'edit_retry_failed') {
            setRetryNotice(data.error || data.message || 'Could not retry that message.');
          } else if (ev === 'error') {
            const errorMsg = data.error || data.message || 'Unknown error occurred.';
            setMessages((prev) =>
              prev.map((m) =>
                m.streaming
                  ? {
                      ...m,
                      content: m.content || `Error: ${errorMsg}`,
                      streaming: false,
                      isHistorical: false,
                    }
                  : m
              )
            );
            currentAssistantMsgIdRef.current = null;
            addLog(`Task error: ${errorMsg}`);
          } else if (ev === 'confirmation_requested') {
            const parsedDetails =
              typeof data.details === 'object' && data.details !== null
                ? data.details
                : data.details
                ? { info: String(data.details) }
                : {};
            const rec: ConfirmationRecord = {
              id: data.id || 'unknown',
              action_type: data.action_type || 'action',
              details: parsedDetails,
              risk_level: data.risk_level || 2,
              reason: data.reason || 'Safety confirmation requested',
              timestamp: Date.now(),
            };
            setConfirmations((prev) => [...prev.filter((c) => c.id !== rec.id), rec]);
            addLog(`Safety confirmation required: ${rec.action_type}`);

            if (optionsRef.current?.onSendNotification) {
              optionsRef.current.onSendNotification('EidosBot: Approval Required', {
                body: `${rec.action_type || 'Action'}: ${rec.reason || 'Safety confirmation requested'}`,
                tag: `eidos-confirm-${rec.id}`,
                data: { url: '/' },
              });
            }
          } else if (ev === 'confirmation_resolved') {
            setConfirmations((prev) => prev.filter((c) => c.id !== data.id));
            addLog(`Confirmation ${data.id} resolved.`);
          } else if (ev === 'confirmations_list') {
            setConfirmations(data.confirmations || []);
          } else if (ev === 'conversation_cleared') {
            setMessages([]);
            setConversationTitle('New Chat');
            addLog('Conversation cleared.');
          } else if (ev === 'file_shared' && data.file) {
            const file = normalizeSharedFile(data.file);
            const active = conversationIdRef.current;
            if (file.conversation_id && active && file.conversation_id !== active) {
              addLog(`File shared in another chat: ${file.name}`);
            } else {
              setSharedFiles((prev) => {
                const idx = prev.findIndex((item) => item.id === file.id);
                if (idx >= 0) {
                  const next = prev.slice();
                  next[idx] = file;
                  return next;
                }
                return [...prev, file];
              });
              addLog(`File ready: ${file.name}`);
            }
          } else if (ev === 'log' && data.message) {
            addLog(data.message);
          }
        } catch (e) {
          console.debug('Failed to parse WebSocket message:', e);
        }
      };

      ws.onclose = () => {
        if (pingTimer) clearInterval(pingTimer);
        if (wsRef.current === ws) wsRef.current = null;
        if (isUnmounted || wsRef.current !== null) return;
        setConnected(false);
        addLog('Disconnected from EidosBot Gateway. Reconnecting in 3s...');
        reconnectTimeoutRef.current = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        if (wsRef.current !== ws) return;
        if (pingTimer) clearInterval(pingTimer);
        ws.close();
      };
    }

    connectRef.current = connect;
    connect();

    return () => {
      isUnmounted = true;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [token, addLog, refreshSync]);

  // Auto-sync when user returns to browser tab or phone wakes up
  useEffect(() => {
    /** Refresh when the tab or phone becomes visible again, so a backgrounded client is not stale. */
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        const socket = wsRef.current;
        if (!socket || socket.readyState === WebSocket.CLOSED) {
          if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
          connectRef.current();
        } else if (socket.readyState === WebSocket.OPEN) {
          refreshSync();
        }
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleVisibilityChange);
    };
  }, [refreshSync]);

  /** Rewind to a user turn and run that prompt again, optionally with edited text. */
  const editAndRetry = useCallback(
    (messageIndex: number, promptText: string, messageId?: string) => {
      const clean = promptText.trim();
      if (!clean) return;
      setRetryNotice(null);
      currentAssistantMsgIdRef.current = null;
      sendMessage({
        type: 'edit_retry',
        prompt: clean,
        id: messageId,
        message_index: messageIndex,
      });
    },
    [sendMessage]
  );

  /** Show the user's message immediately, then send it to the agent. */
  const sendPrompt = useCallback(
    (promptText: string) => {
      const clean = promptText.trim();
      if (!clean) return;
      setRetryNotice(null);

      // Optimistically add user turn
      setMessages((prev) => [
        ...prev,
        {
          id: `user_${Date.now()}`,
          role: 'user',
          content: clean,
          timestamp: Date.now(),
        },
      ]);

      sendMessage({ type: 'prompt', prompt: clean });
    },
    [sendMessage]
  );

  /** Trip the emergency stop and freeze any reply that is still streaming. */
  const triggerKill = useCallback(() => {
    setIsKilled(true);
    setAgentState('killed');
    setStateDetails('Emergency stop tripped remotely');
    setMessages((prev) =>
      prev.map((m) =>
        m.streaming ? { ...m, streaming: false, content: (m.content || '') + ' [Emergency Stopped]' } : m
      )
    );
    currentAssistantMsgIdRef.current = null;
    sendMessage({ type: 'kill' });
  }, [sendMessage]);

  /** Clear the emergency stop so a new task can start. */
  const resetKill = useCallback(() => {
    setIsKilled(false);
    setAgentState('idle');
    setStateDetails('Emergency stop reset');
    sendMessage({ type: 'reset' });
  }, [sendMessage]);

  /** Cancel the running task without tripping the emergency stop. */
  const stopTask = useCallback(() => {
    setAgentState('idle');
    setStateDetails('Task stopped');
    setMessages((prev) =>
      prev.map((m) =>
        m.streaming ? { ...m, streaming: false, content: (m.content || '') + ' [Stopped]' } : m
      )
    );
    currentAssistantMsgIdRef.current = null;
    sendMessage({ type: 'stop' });
  }, [sendMessage]);

  /** Approve or deny a safety prompt, optionally remembering the command. */
  const resolveConfirmation = useCallback(
    (id: string, approved: boolean, allowlist: boolean = false) => {
      sendMessage({ type: 'resolve_confirmation', id, approved, allowlist });
    },
    [sendMessage]
  );

  /** Ask the host to speak a line with the selected voice. */
  const speakMessage = useCallback(
    (message: string, voice?: string) => {
      sendMessage({ type: 'speak', message, voice });
    },
    [sendMessage]
  );

  /** Ask the gateway to wipe the active conversation. */
  const clearHistory = useCallback(() => {
    sendMessage({ type: 'clear' });
  }, [sendMessage]);

  return {
    token,
    setToken,
    connected,
    agentState,
    stateDetails,
    isKilled,
    backgroundWork,
    confirmations,
    messages,
    sharedFiles,
    logs,
    conversationTitle,
    conversationId,
    isSyncing,
    refreshSync,
    startNewChat,
    switchConversation,
    sendPrompt,
    editAndRetry,
    retryNotice,
    clearRetryNotice: () => setRetryNotice(null),
    stopTask,
    triggerKill,
    resetKill,
    resolveConfirmation,
    speakMessage,
    clearHistory,
  };
}
