import { useState, useEffect, useRef } from 'react';
import { useEidosWS } from './hooks/useEidosWS';
import { usePinchTextScale } from './hooks/usePinchTextScale';
import { useAudioPlayback } from './hooks/useAudioPlayback';
import { useBackgroundKeepAlive } from './hooks/useBackgroundKeepAlive';
import { Header } from './components/Header';
import { ChatView } from './components/ChatView';
import { ScreenViewport } from './components/ScreenViewport';
import { SafetyModal } from './components/SafetyModal';
import { ConversationsModal } from './components/ConversationsModal';
import { BackgroundSettingsModal } from './components/BackgroundSettingsModal';
import { AudioPlayerBar } from './components/AudioPlayerBar';
import { MessageSquare, Monitor, Terminal, ShieldAlert } from 'lucide-react';

type Tab = 'chat' | 'screen' | 'logs';

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chat');
  const [showConversationsModal, setShowConversationsModal] = useState(false);
  const [showBackgroundSettings, setShowBackgroundSettings] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const { scale: textScale, showBadge: showTextScale } = usePinchTextScale();

  const sendNotificationRef = useRef<(title: string, opts?: NotificationOptions) => Promise<boolean> | void>(
    () => Promise.resolve(false)
  );

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
    editAndRetry,
    retryNotice,
    clearRetryNotice,
    stopTask,
    triggerKill,
    resetKill,
    resolveConfirmation,
  } = useEidosWS({
    onSendNotification: (title, opts) => sendNotificationRef.current?.(title, opts),
  });

  const {
    isPlaying,
    playbackActive,
    isPaused,
    playingMessageId,
    isBuffering,
    position,
    duration,
    autoSpeak,
    toggleAutoSpeak,
    playbackError,
    playText,
    stopAudio,
    pauseAudio,
    resumeAudio,
    seekBy,
    skipChunk,
    armBackgroundPlayback,
    releaseBackgroundArm,
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

  const {
    notificationPermission,
    requestNotificationPermission,
    sendNotification,
    testNotification,
    isWakeLockActive,
  } = useBackgroundKeepAlive({
    isThinking,
    isPlaying: isPlaying || isPaused || isBuffering,
    isRecording,
  });

  sendNotificationRef.current = sendNotification;

  return (
    <div className="flex flex-col h-[100dvh] max-w-2xl mx-auto bg-[#1e1f29] text-[#f8f8f2] relative overflow-hidden select-none">
      {showTextScale && (
        <div
          aria-live="polite"
          className="pointer-events-none fixed left-1/2 top-16 z-[60] -translate-x-1/2 rounded-full border border-[#44475a] bg-[#282a36]/95 px-3 py-1 text-xs font-semibold text-[#f8f8f2] shadow-lg"
        >
          Text {Math.round(textScale * 100)}%
        </div>
      )}
      {/* Sticky Header */}
      <Header
        connected={connected}
        state={agentState}
        stateDetails={stateDetails}
        isKilled={isKilled}
        token={token}
        conversationTitle={conversationTitle}
        isSyncing={isSyncing}
        notificationPermission={notificationPermission}
        onOpenBackgroundSettings={() => setShowBackgroundSettings(true)}
        onRefreshSync={refreshSync}
        onNewChat={() => startNewChat('New Chat')}
        onOpenConversations={() => setShowConversationsModal(true)}
        onSetToken={setToken}
        onKill={handleKill}
        onReset={resetKill}
      />

      {/* Main View Area - Views kept mounted to prevent killing active background audio/recording on tab switch */}
      <main className="flex-1 overflow-hidden relative">
        <div className={`h-full w-full ${activeTab === 'chat' ? 'flex flex-col' : 'hidden'}`}>
          <ChatView
            messages={messages}
            sharedFiles={sharedFiles}
            onSendMessage={sendPrompt}
            onEditRetry={editAndRetry}
            retryNotice={retryNotice}
            onDismissRetryNotice={clearRetryNotice}
            token={token}
            isThinking={isThinking}
            isPlaying={isPlaying}
            playbackActive={playbackActive}
            isPaused={isPaused}
            position={position}
            duration={duration}
            playingMessageId={playingMessageId}
            isBuffering={isBuffering}
            autoSpeak={autoSpeak}
            onToggleAutoSpeak={toggleAutoSpeak}
            onPlayAudio={playText}
            onStopAudio={stopAudio}
            onPauseAudio={pauseAudio}
            onResumeAudio={resumeAudio}
            onSeekBy={seekBy}
            onSkipChunk={skipChunk}
            onPreparePlayback={armBackgroundPlayback}
            onReleasePreparedPlayback={releaseBackgroundArm}
            onStopTask={handleStopCallOrTask}
            playbackError={playbackError}
            conversationTitle={conversationTitle}
            onNewChat={() => startNewChat('New Chat')}
            onRefreshSync={refreshSync}
            isSyncing={isSyncing}
            onRecordingChange={setIsRecording}
          />
        </div>

        <div className={`h-full w-full ${activeTab === 'screen' ? 'block' : 'hidden'}`}>
          <ScreenViewport token={token} isActive={activeTab === 'screen'} />
        </div>

        <div className={`h-full w-full ${activeTab === 'logs' ? 'flex flex-col' : 'hidden'}`}>
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
        </div>
      </main>

      {/* Persistent Audio Player Bar across ALL tabs (Chat, Screen, Logs) */}
      <AudioPlayerBar
        playbackActive={playbackActive}
        isPlaying={isPlaying}
        isPaused={isPaused}
        isBuffering={isBuffering}
        position={position}
        duration={duration}
        playbackError={playbackError}
        onPauseAudio={pauseAudio}
        onResumeAudio={resumeAudio}
        onStopAudio={stopAudio}
        onSeekBy={seekBy}
        onSkipChunk={skipChunk}
      />

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

      {/* Android Background Execution & Permissions Settings */}
      <BackgroundSettingsModal
        isOpen={showBackgroundSettings}
        onClose={() => setShowBackgroundSettings(false)}
        permission={notificationPermission}
        onRequestPermission={requestNotificationPermission}
        onTestNotification={testNotification}
        isWakeLockActive={isWakeLockActive}
      />
    </div>
  );
}
