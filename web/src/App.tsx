import { useState, useEffect, useRef } from 'react';
import { useEidosWS } from './hooks/useEidosWS';
import { useAudioPlayback } from './hooks/useAudioPlayback';
import { Header } from './components/Header';
import { ChatView } from './components/ChatView';
import { ScreenViewport } from './components/ScreenViewport';
import { SafetyModal } from './components/SafetyModal';
import { ConversationsModal } from './components/ConversationsModal';
import { MessageSquare, Monitor, Terminal, ShieldAlert } from 'lucide-react';

type Tab = 'chat' | 'screen' | 'logs';

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chat');
  const [showConversationsModal, setShowConversationsModal] = useState(false);

  const {
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
  } = useEidosWS();

  const {
    isPlaying,
    playingMessageId,
    isBuffering,
    autoSpeak,
    toggleAutoSpeak,
    playbackError,
    playText,
    stopAudio,
  } = useAudioPlayback({ token });

  const handleKill = () => {
    stopAudio();
    triggerKill();
  };

  const handleStopCallOrTask = () => {
    stopAudio();
    stopTask();
  };

  const lastSpokenMsgIdRef = useRef<string | null>(
    localStorage.getItem('eidos_last_spoken_msg_id')
  );
  const initialLoadedRef = useRef<boolean>(false);

  // Auto-read finished assistant responses if autoSpeak is enabled
  useEffect(() => {
    if (messages.length === 0) return;
    const lastMsg = messages[messages.length - 1];

    // On initial mount or when first loading history, seed the last spoken message ref
    // so whatever past response is sitting in chat history is NEVER auto-read on open.
    if (!initialLoadedRef.current) {
      initialLoadedRef.current = true;
      if (lastMsg && lastMsg.role === 'assistant') {
        lastSpokenMsgIdRef.current = lastMsg.id;
        localStorage.setItem('eidos_last_spoken_msg_id', lastMsg.id);
      }
      return;
    }

    if (!autoSpeak) return;

    // Do not auto-speak past/historical messages loaded on connect or reconnect
    if (lastMsg.isHistorical) return;

    // If this exact message is already playing, do not interrupt it
    if (isPlaying && playingMessageId === lastMsg.id) return;

    if (
      lastMsg.role === 'assistant' &&
      !lastMsg.streaming &&
      lastMsg.content.trim() &&
      lastSpokenMsgIdRef.current !== lastMsg.id
    ) {
      lastSpokenMsgIdRef.current = lastMsg.id;
      localStorage.setItem('eidos_last_spoken_msg_id', lastMsg.id);
      playText(lastMsg.content, lastMsg.id);
    }
  }, [messages, autoSpeak, isPlaying, playingMessageId, playText]);

  const isThinking = ['thinking', 'executing', 'perceiving'].includes(agentState.toLowerCase());

  return (
    <div className="flex flex-col h-[100dvh] max-w-2xl mx-auto bg-[#1e1f29] text-[#f8f8f2] relative overflow-hidden select-none">
      {/* Sticky Header */}
      <Header
        connected={connected}
        state={agentState}
        stateDetails={stateDetails}
        isKilled={isKilled}
        token={token}
        conversationTitle={conversationTitle}
        isSyncing={isSyncing}
        isThinking={isThinking}
        isPlaying={isPlaying}
        onStopCallOrTask={handleStopCallOrTask}
        onRefreshSync={refreshSync}
        onNewChat={() => startNewChat('New Chat')}
        onOpenConversations={() => setShowConversationsModal(true)}
        onSetToken={setToken}
        onKill={handleKill}
        onReset={resetKill}
      />

      {/* Main View Area */}
      <main className="flex-1 overflow-hidden relative">
        {activeTab === 'chat' && (
          <ChatView
            messages={messages}
            sharedFiles={sharedFiles}
            onSendMessage={sendPrompt}
            token={token}
            isThinking={isThinking}
            isPlaying={isPlaying}
            playingMessageId={playingMessageId}
            isBuffering={isBuffering}
            autoSpeak={autoSpeak}
            onToggleAutoSpeak={toggleAutoSpeak}
            onPlayAudio={playText}
            onStopAudio={stopAudio}
            onStopTask={handleStopCallOrTask}
            playbackError={playbackError}
            conversationTitle={conversationTitle}
            onNewChat={() => startNewChat('New Chat')}
            onRefreshSync={refreshSync}
            isSyncing={isSyncing}
          />
        )}

        {activeTab === 'screen' && <ScreenViewport token={token} />}

        {activeTab === 'logs' && (
          <div className="flex flex-col h-full p-4 overflow-hidden bg-[#1e1f29]">
            <div className="flex items-center justify-between pb-3 border-b border-[#44475a] mb-3">
              <span className="text-xs font-bold text-[#f8f8f2] flex items-center gap-2">
                <Terminal className="w-4 h-4 text-[#50fa7b]" /> Live Event Logs
              </span>
              <span className="text-[11px] text-[#6272a4]">{logs.length} entries</span>
            </div>
            <div className="flex-1 overflow-y-auto font-mono text-[11px] space-y-1 text-[#6272a4] bg-[#14151b] p-3 rounded-xl border border-[#44475a] select-text">
              {logs.length === 0 ? (
                <div className="text-center py-8 text-[#6272a4]">No activity logs yet.</div>
              ) : (
                logs.map((log, i) => (
                  <div key={i} className="py-0.5 border-b border-[#44475a]/20 break-all">
                    {log}
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </main>

      {/* Bottom Tab Navigation Bar */}
      <nav className="bg-[#1e1f29] border-t border-[#44475a] px-4 py-2 flex items-center justify-around z-20">
        <button
          onClick={() => setActiveTab('chat')}
          className={`flex flex-col items-center gap-1 py-1 px-4 rounded-xl transition ${
            activeTab === 'chat' ? 'text-[#bd93f9]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
          }`}
        >
          <MessageSquare className="w-4 h-4" />
          <span className="text-[10px] font-bold">Chat</span>
        </button>

        <button
          onClick={() => setActiveTab('screen')}
          className={`flex flex-col items-center gap-1 py-1 px-4 rounded-xl transition ${
            activeTab === 'screen' ? 'text-[#bd93f9]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
          }`}
        >
          <Monitor className="w-4 h-4" />
          <span className="text-[10px] font-bold">Screen</span>
        </button>

        <button
          onClick={() => setActiveTab('logs')}
          className={`flex flex-col items-center gap-1 py-1 px-4 rounded-xl transition ${
            activeTab === 'logs' ? 'text-[#bd93f9]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
          }`}
        >
          <Terminal className="w-4 h-4" />
          <span className="text-[10px] font-bold">Logs</span>
        </button>

        {confirmations.length > 0 && (
          <button
            onClick={() => {}}
            className="flex flex-col items-center gap-1 py-1 px-4 rounded-xl text-[#ffb86c] animate-bounce"
            title="Pending Safety Confirmations"
          >
            <ShieldAlert className="w-4 h-4" />
            <span className="text-[10px] font-bold">{confirmations.length} Pending</span>
          </button>
        )}
      </nav>

      {/* Safety Confirmation Modal */}
      <SafetyModal
        confirmations={confirmations}
        onResolve={resolveConfirmation}
        onKill={handleKill}
      />

      {/* Past Conversations Modal */}
      <ConversationsModal
        isOpen={showConversationsModal}
        onClose={() => setShowConversationsModal(false)}
        token={token}
        activeConversationId={conversationId}
        onSelectConversation={switchConversation}
        onNewChat={() => startNewChat('New Chat')}
      />
    </div>
  );
}
