import React, { useState, useEffect, useCallback } from 'react';
import { ConversationItem } from '../types/gateway';
import { MessageSquare, Plus, Trash2, X, Search, Check, RefreshCw } from 'lucide-react';

interface ConversationsModalProps {
  isOpen: boolean;
  onClose: () => void;
  token: string;
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
}

export const ConversationsModal: React.FC<ConversationsModalProps> = ({
  isOpen,
  onClose,
  token,
  activeConversationId,
  onSelectConversation,
  onNewChat,
}) => {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchConversations = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const q = search.trim() ? `?search=${encodeURIComponent(search.trim())}` : '';
      const res = await fetch(`/api/conversations${q}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.conversations)) {
          setConversations(data.conversations);
        }
      }
    } catch (e) {
      console.error('Failed to load conversations:', e);
    } finally {
      setLoading(false);
    }
  }, [token, search]);

  useEffect(() => {
    if (isOpen) {
      fetchConversations();
    }
  }, [isOpen, fetchConversations]);

  if (!isOpen) return null;

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Are you sure you want to delete this conversation thread?')) return;
    setDeletingId(id);
    try {
      const res = await fetch(`/api/conversations/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setConversations((prev) => prev.filter((c) => c.id !== id));
      }
    } catch (err) {
      alert(`Failed to delete conversation: ${err}`);
    } finally {
      setDeletingId(null);
    }
  };

  const formatDate = (ms?: number) => {
    if (!ms) return '';
    const d = new Date(ms);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    if (isToday) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-4 animate-fadeIn">
      <div className="bg-[#1e1f29] border border-[#44475a] rounded-t-2xl sm:rounded-2xl w-full max-w-lg max-h-[85vh] flex flex-col shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="p-4 border-b border-[#44475a] flex items-center justify-between bg-[#282a36]">
          <div className="flex items-center gap-2">
            <MessageSquare className="w-5 h-5 text-[#bd93f9]" />
            <h2 className="text-base font-bold text-[#f8f8f2]">Past Conversations</h2>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={fetchConversations}
              disabled={loading}
              title="Refresh conversation list"
              className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 transition active:scale-95"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-[#bd93f9]' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 transition active:scale-95"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Action bar: Search + New Chat Button */}
        <div className="p-3 border-b border-[#44475a] flex items-center gap-2 bg-[#21232d]/60">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#6272a4]" />
            <input
              type="text"
              placeholder="Search conversations..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-[#181920] border border-[#44475a] rounded-xl pl-9 pr-3 py-1.5 text-xs text-[#f8f8f2] placeholder-[#6272a4] focus:outline-none focus:border-[#bd93f9]"
            />
          </div>
          <button
            onClick={() => {
              onNewChat();
              onClose();
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#50fa7b] text-[#1e1f29] font-bold text-xs hover:bg-[#50fa7b]/90 transition active:scale-95 shadow-sm whitespace-nowrap"
          >
            <Plus className="w-4 h-4" />
            <span>New Chat</span>
          </button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2 select-none">
          {conversations.length === 0 ? (
            <div className="text-center py-12 text-[#6272a4] text-xs">
              {loading ? (
                <div className="flex items-center justify-center gap-2">
                  <RefreshCw className="w-4 h-4 animate-spin text-[#bd93f9]" />
                  <span>Loading conversations...</span>
                </div>
              ) : (
                'No conversations found.'
              )}
            </div>
          ) : (
            conversations.map((c) => {
              const isActive = c.id === activeConversationId || c.is_active;
              return (
                <div
                  key={c.id}
                  onClick={() => {
                    onSelectConversation(c.id);
                    onClose();
                  }}
                  className={`p-3 rounded-xl border transition flex items-center justify-between gap-3 cursor-pointer ${
                    isActive
                      ? 'bg-[#282a36] border-[#bd93f9] shadow-[0_0_10px_rgba(189,147,249,0.15)]'
                      : 'bg-[#181920]/60 border-[#44475a]/40 hover:border-[#44475a] hover:bg-[#282a36]/40'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className={`text-xs font-semibold truncate ${
                          isActive ? 'text-[#bd93f9]' : 'text-[#f8f8f2]'
                        }`}
                      >
                        {c.title || 'Untitled Chat'}
                      </span>
                      {isActive && (
                        <span className="shrink-0 flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-[#bd93f9]/20 text-[#bd93f9] border border-[#bd93f9]/40">
                          <Check className="w-3 h-3" /> Active
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-[11px] text-[#6272a4]">
                      {c.updated_at && <span>{formatDate(c.updated_at)}</span>}
                      {c.message_count !== undefined && c.message_count > 0 && (
                        <span>• {c.message_count} msgs</span>
                      )}
                    </div>
                  </div>

                  <button
                    onClick={(e) => handleDelete(c.id, e)}
                    disabled={deletingId === c.id}
                    title="Delete conversation"
                    className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#ff5555] hover:bg-[#ff5555]/10 transition active:scale-95"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};
