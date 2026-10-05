import { useEffect, useRef, useState, useCallback } from 'react';
import { AgentState, ChatAction, ChatMessage, ConfirmationRecord, InboundWSEvent, OutboundWSMessage, SharedFile } from '../types/gateway';

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

/** Live gateway connection: chat, agent status, files, and safety confirmations. */
export function useEidosWS() {
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
              // If not forced and prev already has active streaming message, keep it
              if (!force && prev.length > 0 && prev.length >= data.messages.length && prev.some((m) => m.streaming)) {
                return prev;
              }
              const loaded: ChatMessage[] = data.messages.map((m: any, i: number) => ({
                ...mapHistoryMessage(m, i),
                timestamp: m.timestamp || Date.now() - (data.messages.length - i) * 1000,
              }));
              return loaded;
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

    /** Open the gateway socket and keep reconnecting until this hook unmounts. */
    function connect() {
      if (isUnmounted) return;
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const wsUrl = `${proto}//${host}/ws?token=${encodeURIComponent(token)}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (isUnmounted) return;
        setConnected(true);
        addLog('Connected to EidosBot Gateway.');
        loadHistory(true);
      };

      ws.onmessage = (event) => {
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
              setMessages(data.messages.map((m: any, i: number) => mapHistoryMessage(m, i)));
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
              setMessages(data.messages.map((m: any, i: number) => mapHistoryMessage(m, i)));
            }
          } else if (ev === 'task_start') {
            // Task initiated (either locally from web or remotely from desktop app)
            const goal = (data.goal || '').trim();
            const newAssistantId = `msg_${Date.now()}`;
            currentAssistantMsgIdRef.current = newAssistantId;

            setMessages((prev) => {
              const last = prev[prev.length - 1];
              const alreadyHasUserGoal = last && last.role === 'user' && last.content.trim() === goal;

              const base = alreadyHasUserGoal || !goal
                ? prev
                : [
                    ...prev,
                    {
                      id: `user_${Date.now()}`,
                      role: 'user' as const,
                      content: goal,
                      timestamp: Date.now(),
                    },
                  ];

              return [
                ...base,
                {
                  id: newAssistantId,
                  role: 'assistant' as const,
                  content: '',
                  thoughts: [],
                  timestamp: Date.now(),
                  streaming: true,
                  isHistorical: false,
                },
              ];
            });
          } else if (ev === 'token_chunk' && data.chunk) {
            const chunk = data.chunk;
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant' && last.streaming) {
                return [
                  ...prev.slice(0, -1),
                  { ...last, content: last.content + chunk },
                ];
              } else {
                const newId = `msg_${Date.now()}`;
                currentAssistantMsgIdRef.current = newId;
                return [
                  ...prev,
                  {
                    id: newId,
                    role: 'assistant',
                    content: chunk,
                    thoughts: [],
                    timestamp: Date.now(),
                    streaming: true,
                    isHistorical: false,
                  },
                ];
              }
            });
          } else if (ev === 'thought_chunk' && data.chunk) {
            const thought = data.chunk;
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                const existingThoughts = last.thoughts || [];
                return [
                  ...prev.slice(0, -1),
                  { ...last, thoughts: [...existingThoughts, thought] },
                ];
              } else {
                return [
                  ...prev,
                  {
                    id: `msg_${Date.now()}`,
                    role: 'assistant',
                    content: '',
                    thoughts: [thought],
                    timestamp: Date.now(),
                    streaming: true,
                    isHistorical: false,
                  },
                ];
              }
            });
          } else if (ev === 'tool_start') {
            const tool = String(data.tool || 'action');
            const args =
              data.arguments == null
                ? ''
                : typeof data.arguments === 'string'
                ? data.arguments
                : JSON.stringify(data.arguments);
            setMessages((prev) => {
              const action: ChatAction = { tool, arguments: args, result: '', pending: true };
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                return [
                  ...prev.slice(0, -1),
                  { ...last, actions: [...(last.actions || []), action] },
                ];
              }
              return [
                ...prev,
                {
                  id: `msg_${Date.now()}`,
                  role: 'assistant' as const,
                  content: '',
                  thoughts: [],
                  actions: [action],
                  timestamp: Date.now(),
                  streaming: true,
                  isHistorical: false,
                },
              ];
            });
          } else if (ev === 'tool_end') {
            const tool = String(data.tool || 'action');
            const result = data.result == null ? '' : String(data.result);
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (!last || last.role !== 'assistant') return prev;
              const actions = [...(last.actions || [])];
              for (let i = actions.length - 1; i >= 0; i -= 1) {
                if (actions[i].tool === tool && actions[i].pending) {
                  actions[i] = { ...actions[i], result, pending: false };
                  break;
                }
              }
              return [...prev.slice(0, -1), { ...last, actions }];
            });
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
              const hasStreaming = prev.some((m) => m.streaming);
              if (hasStreaming) {
                return prev.map((m) => (m.streaming ? applyFinish(m) : m));
              }
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                return [...prev.slice(0, -1), applyFinish(last)];
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
        if (isUnmounted) return;
        setConnected(false);
        addLog('Disconnected from EidosBot Gateway. Reconnecting in 3s...');
        reconnectTimeoutRef.current = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      isUnmounted = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [token, addLog, loadHistory]);

  // Auto-sync when user returns to browser tab or phone wakes up
  useEffect(() => {
    /** Refresh when the tab or phone becomes visible again, so a backgrounded client is not stale. */
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && connected) {
        refreshSync();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleVisibilityChange);
    };
  }, [connected, refreshSync]);

  /** Show the user's message immediately, then send it to the agent. */
  const sendPrompt = useCallback(
    (promptText: string) => {
      const clean = promptText.trim();
      if (!clean) return;

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
    stopTask,
    triggerKill,
    resetKill,
    resolveConfirmation,
    speakMessage,
    clearHistory,
  };
}
