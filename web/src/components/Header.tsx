import React, { useState } from 'react';
import { AgentState } from '../types/gateway';
import {
  Bot,
  ShieldAlert,
  Key,
  Wifi,
  WifiOff,
  Eye,
  EyeOff,
  Volume2,
  VolumeX,
  RefreshCw,
  Plus,
  History,
} from 'lucide-react';

interface HeaderProps {
  connected: boolean;
  state: AgentState;
  stateDetails: string;
  isKilled: boolean;
  token: string;
  conversationTitle?: string;
  isSyncing?: boolean;
  onRefreshSync?: () => void;
  onNewChat?: () => void;
  onOpenConversations?: () => void;
  autoSpeak?: boolean;
  onToggleAutoSpeak?: () => void;
  onSetToken: (token: string) => void;
  onKill: () => void;
  onReset: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  connected,
  state,
  stateDetails,
  isKilled,
  token,
  conversationTitle = 'New Chat',
  isSyncing = false,
  onRefreshSync,
  onNewChat,
  onOpenConversations,
  autoSpeak = false,
  onToggleAutoSpeak,
  onSetToken,
  onKill,
  onReset,
}) => {
  const [showTokenDialog, setShowTokenDialog] = useState(false);
  const [tempToken, setTempToken] = useState(token);
  const [showPassword, setShowPassword] = useState(false);

  const getStatusColor = () => {
    if (!connected) return 'bg-gray-500';
    if (isKilled) return 'bg-[#ff5555] shadow-[0_0_10px_#ff5555]';
    if (['thinking', 'executing', 'perceiving'].includes(state.toLowerCase())) {
      return 'bg-[#f1fa8c] shadow-[0_0_10px_#f1fa8c] animate-pulse';
    }
    return 'bg-[#50fa7b] shadow-[0_0_10px_#50fa7b]';
  };

  const getStatusLabel = () => {
    if (!connected) return 'Offline';
    if (isKilled) return 'STOPPED';
    return state.toUpperCase();
  };

  const handleSaveToken = () => {
    onSetToken(tempToken.trim());
    setShowTokenDialog(false);
  };

  const handlePurgeCache = async () => {
    if ((window as any).__clearPwaCacheAndReload) {
      await (window as any).__clearPwaCacheAndReload();
    } else {
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      window.location.reload();
    }
  };

  return (
    <>
      <header className="sticky top-0 z-30 bg-[#1e1f29]/95 backdrop-blur-md border-b border-[#44475a] px-3 sm:px-4 py-2.5 flex items-center justify-between gap-2">
        {/* Left: Brand, Title, and Active Conversation Pill */}
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-[#bd93f9]/20 border border-[#bd93f9]/40 flex items-center justify-center text-[#bd93f9] shrink-0">
            <Bot className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <h1 className="text-sm sm:text-base font-bold text-[#f8f8f2] tracking-tight">EidosBot</h1>
              <div className="flex items-center gap-1 bg-[#282a36] border border-[#44475a] px-1.5 py-0.5 rounded-full text-[10px] sm:text-[11px] font-semibold shrink-0">
                <span className={`w-1.5 h-1.5 rounded-full ${getStatusColor()}`} />
                <span className="text-[#f8f8f2]">{getStatusLabel()}</span>
              </div>
            </div>

            {/* Conversation Topic / Status Details */}
            <div className="flex items-center gap-1.5 text-[11px] truncate">
              {onOpenConversations ? (
                <button
                  onClick={onOpenConversations}
                  title="Click to view/switch past conversation threads"
                  className="text-[#bd93f9] font-medium hover:underline truncate max-w-[140px] sm:max-w-[200px]"
                >
                  {conversationTitle}
                </button>
              ) : (
                <span className="text-[#bd93f9] font-medium truncate max-w-[140px] sm:max-w-[200px]">
                  {conversationTitle}
                </span>
              )}
              {stateDetails && (
                <span className="text-[#6272a4] truncate hidden sm:inline">• {stateDetails}</span>
              )}
            </div>
          </div>
        </div>

        {/* Right: Actions (New Chat, Sync/Refresh, Past Chats, Audio, Auth, Kill) */}
        <div className="flex items-center gap-1.5 shrink-0">
          {/* New Chat Button */}
          {onNewChat && (
            <button
              onClick={onNewChat}
              title="Start a new conversation thread (syncs with desktop)"
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#50fa7b] text-[#1e1f29] font-bold text-xs hover:bg-[#50fa7b]/90 transition active:scale-95 shadow-sm"
            >
              <Plus className="w-3.5 h-3.5 stroke-[3]" />
              <span className="hidden xs:inline sm:inline">New</span>
            </button>
          )}

          {/* Sync / Refresh with Desktop Button */}
          {onRefreshSync && (
            <button
              onClick={onRefreshSync}
              disabled={isSyncing}
              title="Refresh / Sync chat and state with desktop app"
              className={`p-1.5 sm:px-2.5 sm:py-1.5 rounded-lg border transition active:scale-95 flex items-center gap-1 text-xs font-semibold ${
                isSyncing
                  ? 'bg-[#bd93f9]/20 border-[#bd93f9] text-[#bd93f9]'
                  : 'bg-[#282a36] border-[#44475a] text-[#f8f8f2] hover:border-[#bd93f9] hover:text-[#bd93f9]'
              }`}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">Sync</span>
            </button>
          )}

          {/* Past Chats Button */}
          {onOpenConversations && (
            <button
              onClick={onOpenConversations}
              title="Browse and resume past conversations"
              className="p-1.5 sm:p-2 rounded-lg bg-[#282a36] border border-[#44475a] text-[#8be9fd] hover:border-[#8be9fd] transition active:scale-95"
            >
              <History className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            </button>
          )}

          {/* Auto-Read responses toggle */}
          {onToggleAutoSpeak && (
            <button
              onClick={onToggleAutoSpeak}
              title={autoSpeak ? 'Auto-read aloud ON (Kokoro TTS)' : 'Auto-read aloud OFF'}
              className={`p-1.5 sm:p-2 rounded-lg border transition active:scale-95 ${
                autoSpeak
                  ? 'bg-[#50fa7b]/20 border-[#50fa7b] text-[#50fa7b]'
                  : 'bg-[#282a36] border-[#44475a] text-[#6272a4] hover:text-[#f8f8f2]'
              }`}
            >
              {autoSpeak ? <Volume2 className="w-3.5 h-3.5 sm:w-4 sm:h-4" /> : <VolumeX className="w-3.5 h-3.5 sm:w-4 sm:h-4" />}
            </button>
          )}

          {/* Key / Auth Button */}
          <button
            onClick={() => {
              setTempToken(token);
              setShowTokenDialog(true);
            }}
            title="Configure Auth Token"
            className="p-1.5 sm:p-2 rounded-lg bg-[#282a36] border border-[#44475a] text-[#bd93f9] hover:bg-[#44475a] transition active:scale-95"
          >
            <Key className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
          </button>

          {/* Connection status indicator */}
          <div
            title={connected ? 'Connected via Tailscale/Local' : 'Disconnected'}
            className="p-1.5 sm:p-2 rounded-lg bg-[#282a36] border border-[#44475a]"
          >
            {connected ? (
              <Wifi className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-[#50fa7b]" />
            ) : (
              <WifiOff className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-[#ff5555]" />
            )}
          </div>

          {/* Emergency Kill Switch Button */}
          {isKilled ? (
            <button
              onClick={onReset}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#ffb86c]/20 border border-[#ffb86c] text-[#ffb86c] font-bold text-xs hover:bg-[#ffb86c]/30 transition active:scale-95"
            >
              🔄 Reset
            </button>
          ) : (
            <button
              onClick={onKill}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#ff5555] text-white font-bold text-xs hover:bg-[#ff5555]/90 transition shadow-[0_0_12px_rgba(255,85,85,0.4)] active:scale-95"
            >
              <ShieldAlert className="w-3.5 h-3.5" />
              <span className="hidden xs:inline sm:inline">STOP</span>
            </button>
          )}
        </div>
      </header>

      {/* Auth Token Dialog */}
      {showTokenDialog && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#282a36] border border-[#44475a] rounded-xl p-5 w-full max-w-sm shadow-2xl">
            <h2 className="text-base font-bold text-[#bd93f9] mb-2 flex items-center gap-2">
              <Key className="w-4 h-4" /> Gateway Auth Token
            </h2>
            <p className="text-xs text-[#6272a4] mb-3">
              Enter the bearer token configured in your EidosBot <code className="bg-[#1e1f29] px-1 py-0.5 rounded text-[#f8f8f2]">config.yaml</code>.
            </p>
            <div className="relative mb-4">
              <input
                type={showPassword ? 'text' : 'password'}
                value={tempToken}
                onChange={(e) => setTempToken(e.target.value)}
                placeholder="eidos-secret-token"
                className="w-full bg-[#1e1f29] border border-[#44475a] rounded-lg pl-3 pr-10 py-2 text-sm text-[#f8f8f2] focus:outline-none focus:border-[#bd93f9]"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#6272a4] hover:text-[#f8f8f2]"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <div className="pt-3 border-t border-[#44475a]/50 flex items-center justify-between">
              <button
                type="button"
                onClick={handlePurgeCache}
                title="Force refresh app and clear PWA cache"
                className="flex items-center gap-1.5 text-[11px] text-[#ffb86c] hover:underline"
              >
                <RefreshCw className="w-3.5 h-3.5" /> Force Update App
              </button>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowTokenDialog(false)}
                  className="px-3 py-1.5 rounded-lg border border-[#44475a] text-xs font-semibold text-[#f8f8f2] hover:bg-[#44475a]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSaveToken}
                  className="px-4 py-1.5 rounded-lg bg-[#bd93f9] text-[#1e1f29] text-xs font-bold hover:bg-[#bd93f9]/90"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
