import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Brain, Copy, Check, Volume2, Square, Loader2 } from 'lucide-react';
import { copyToClipboard } from './MarkdownRenderer';

interface ThoughtAccordionProps {
  thoughts: string[];
  playbackId?: string;
  isPlaying?: boolean;
  isBuffering?: boolean;
  onPlayAudio?: (text: string, messageId: string) => void;
  onStopAudio?: () => void;
}

export const ThoughtAccordion: React.FC<ThoughtAccordionProps> = ({
  thoughts,
  playbackId,
  isPlaying = false,
  isBuffering = false,
  onPlayAudio,
  onStopAudio,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!thoughts || thoughts.length === 0) return null;

  const fullThoughtText = thoughts.join('\n\n');

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await copyToClipboard(fullThoughtText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSpeak = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (!onPlayAudio || !playbackId) return;
    if (isPlaying) {
      onStopAudio?.();
      return;
    }
    onPlayAudio(fullThoughtText, playbackId);
  };

  return (
    <div className="my-2 rounded-lg border border-[#44475a]/70 bg-[#1e1f29]/70 overflow-hidden text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-[#6272a4] hover:text-[#bd93f9] hover:bg-[#282a36]/50 transition text-left"
      >
        <div className="flex items-center gap-2">
          <Brain className="w-3.5 h-3.5 text-[#bd93f9]" />
          <span className="font-semibold text-[11px] uppercase tracking-wider text-[#bd93f9]">
            Reasoning Stream ({thoughts.length} step{thoughts.length > 1 ? 's' : ''})
          </span>
        </div>
        <div className="flex items-center gap-2">
          {onPlayAudio && playbackId && (
            <span
              onClick={handleSpeak}
              title={
                isBuffering
                  ? 'Synthesizing Kokoro speech...'
                  : isPlaying
                  ? 'Stop speech playback'
                  : 'Read reasoning aloud on this device (Kokoro TTS)'
              }
              className={`p-1 transition ${
                isPlaying
                  ? 'text-[#ff5555]'
                  : isBuffering
                  ? 'text-[#bd93f9]'
                  : 'hover:text-[#f8f8f2]'
              }`}
            >
              {isBuffering ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : isPlaying ? (
                <Square className="w-3 h-3 fill-current" />
              ) : (
                <Volume2 className="w-3 h-3" />
              )}
            </span>
          )}
          <span
            onClick={handleCopy}
            title="Copy reasoning"
            className="p-1 hover:text-[#f8f8f2] transition"
          >
            {copied ? <Check className="w-3 h-3 text-[#50fa7b]" /> : <Copy className="w-3 h-3" />}
          </span>
          {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </button>

      {expanded && (
        <div className="p-3 border-t border-[#44475a]/40 bg-[#14151b] font-mono text-[11px] leading-relaxed text-[#6272a4] max-h-60 overflow-y-auto whitespace-pre-wrap select-text">
          {fullThoughtText}
        </div>
      )}
    </div>
  );
};
