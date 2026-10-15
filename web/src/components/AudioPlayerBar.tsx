import React, { useState } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  RotateCw,
  SkipBack,
  SkipForward,
  ChevronDown,
  ChevronUp,
  Volume2,
  Square,
  AlertCircle,
} from 'lucide-react';

interface AudioPlayerBarProps {
  playbackActive: boolean;
  isPlaying: boolean;
  isPaused: boolean;
  isBuffering: boolean;
  position: number;
  duration: number;
  playbackError?: string | null;
  onPauseAudio?: () => void;
  onResumeAudio?: () => void;
  onStopAudio?: () => void;
  onSeekBy?: (seconds: number) => void;
  onSkipChunk?: (direction: -1 | 1) => void;
}

const formatClock = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(s / 60);
  const rem = s % 60;
  return `${mins}:${rem < 10 ? '0' : ''}${rem}`;
};

export const AudioPlayerBar: React.FC<AudioPlayerBarProps> = ({
  playbackActive,
  isPlaying,
  isPaused,
  isBuffering,
  position,
  duration,
  playbackError,
  onPauseAudio,
  onResumeAudio,
  onStopAudio,
  onSeekBy,
  onSkipChunk,
}) => {
  const [isPlayerCollapsed, setIsPlayerCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('eidos_audio_player_collapsed') === 'true';
  });

  const togglePlayerCollapsed = () => {
    setIsPlayerCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem('eidos_audio_player_collapsed', String(next));
      return next;
    });
  };

  if (!playbackActive) return null;

  const showPause = isPlaying && !isPaused;
  const stopEngaged = isBuffering || isPlaying || isPaused;
  const statusLabel = isBuffering ? 'Loading speech…' : showPause ? 'Playing voice' : isPaused ? 'Paused' : 'Finished';
  const expandedStatus = isBuffering ? 'Loading speech…' : showPause ? 'Kokoro Voice Stream' : isPaused ? 'Paused' : 'Finished';
  const togglePlayback = () => (showPause ? onPauseAudio?.() : onResumeAudio?.());

  return (
    <div className="px-3 pt-2 pb-1 bg-[#1e1f29] border-t border-[#44475a] shrink-0 z-30">
      <div className="rounded-xl border border-[#44475a] bg-[#282a36] overflow-hidden shadow-lg animate-fadeIn">
        {/* Top slim progress bar indicator (active in both collapsed and expanded states) */}
        <div className="h-1 bg-[#44475a]/50 w-full overflow-hidden">
          <div
            className="h-full bg-[#bd93f9] transition-all duration-200"
            style={{ width: `${duration > 0 ? Math.min(100, (position / duration) * 100) : 0}%` }}
          />
        </div>

        {isPlayerCollapsed ? (
          /* Collapsed compact audio bar */
          <div className="px-3 py-1.5 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <button
                type="button"
                onClick={togglePlayback}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#bd93f9] text-[#1e1f29] text-[11px] font-semibold active:scale-95 shrink-0 hover:bg-[#bd93f9]/90 transition"
                title={showPause ? 'Pause' : 'Play'}
              >
                {showPause ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                <span>{showPause ? 'Pause' : 'Play'}</span>
              </button>
              <div className="flex items-center gap-1.5 truncate">
                <span className="text-[11px] font-medium text-[#f8f8f2] truncate">
                  {statusLabel}
                </span>
                <span className="text-[11px] tabular-nums text-[#6272a4] shrink-0">
                  ({formatClock(position)} / {formatClock(duration)})
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                onClick={() => onSeekBy?.(-5)}
                className="px-1.5 py-0.5 rounded text-[10px] font-medium text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 active:scale-95"
                title="Rewind 5 seconds"
              >
                -5s
              </button>
              <button
                type="button"
                onClick={() => onSeekBy?.(5)}
                className="px-1.5 py-0.5 rounded text-[10px] font-medium text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 active:scale-95"
                title="Forward 5 seconds"
              >
                +5s
              </button>
              {onStopAudio && (
                <button
                  type="button"
                  onClick={() => { if (stopEngaged) onStopAudio(); }}
                  disabled={!stopEngaged}
                  className={`p-1 rounded transition ${
                    stopEngaged
                      ? 'text-[#ff5555] hover:bg-[#ff5555]/20 active:scale-95'
                      : 'text-[#6272a4]/40 cursor-default'
                  }`}
                  title={stopEngaged ? 'Stop playback' : 'Playback finished'}
                >
                  <Square className={`w-3 h-3 ${stopEngaged ? 'fill-current' : ''}`} />
                </button>
              )}
              <button
                type="button"
                onClick={togglePlayerCollapsed}
                className="p-1 rounded-md text-[#bd93f9] hover:bg-[#44475a]/60 active:scale-95 transition ml-0.5"
                title="Expand audio player"
              >
                <ChevronUp className="w-4 h-4" />
              </button>
            </div>
          </div>
        ) : (
          /* Expanded full-featured audio player */
          <div className="p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <Volume2 className="w-3.5 h-3.5 text-[#bd93f9]" />
                <span className="text-[11px] font-medium text-[#f8f8f2]">
                  {expandedStatus}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] tabular-nums text-[#6272a4]">
                  {formatClock(position)} / {formatClock(duration)}
                </span>
                <button
                  type="button"
                  onClick={togglePlayerCollapsed}
                  className="p-1 rounded-md text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/60 active:scale-95 transition"
                  title="Collapse audio player"
                >
                  <ChevronDown className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between gap-1 pt-0.5 overflow-x-auto">
              <button
                type="button"
                onClick={() => onSkipChunk?.(-1)}
                className="p-2 rounded-lg text-[#f8f8f2] hover:bg-[#44475a] active:scale-95"
                title="Previous chunk"
              >
                <SkipBack className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={() => onSeekBy?.(-5)}
                className="px-2 py-1.5 rounded-lg text-[11px] font-medium text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 inline-flex items-center gap-1"
                title="Rewind 5 seconds"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                5s
              </button>
              <button
                type="button"
                onClick={togglePlayback}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-full bg-[#bd93f9] text-[#1e1f29] text-[11px] font-semibold active:scale-95 hover:bg-[#bd93f9]/90 shadow-md shrink-0"
                title={showPause ? 'Pause' : 'Play'}
              >
                {showPause ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                <span>{showPause ? 'Pause' : 'Play'}</span>
              </button>
              <button
                type="button"
                onClick={() => onSeekBy?.(5)}
                className="px-2 py-1.5 rounded-lg text-[11px] font-medium text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 inline-flex items-center gap-1"
                title="Forward 5 seconds"
              >
                <RotateCw className="w-3.5 h-3.5" />
                5s
              </button>
              <button
                type="button"
                onClick={() => onSkipChunk?.(1)}
                className="p-2 rounded-lg text-[#f8f8f2] hover:bg-[#44475a] active:scale-95"
                title="Next chunk"
              >
                <SkipForward className="w-4 h-4" />
              </button>
              {onStopAudio && (
                <button
                  type="button"
                  onClick={() => { if (stopEngaged) onStopAudio(); }}
                  disabled={!stopEngaged}
                  className={`p-2 rounded-lg ml-1 transition ${
                    stopEngaged
                      ? 'text-[#ff5555] hover:bg-[#ff5555]/20 active:scale-95'
                      : 'text-[#6272a4]/40 cursor-default'
                  }`}
                  title={stopEngaged ? 'Stop playback' : 'Playback finished'}
                >
                  <Square className={`w-3.5 h-3.5 ${stopEngaged ? 'fill-current' : ''}`} />
                </button>
              )}
            </div>
          </div>
        )}

        {playbackError && (
          <div className="px-3 py-1.5 bg-[#ff5555]/15 border-t border-[#ff5555]/30 text-[11px] text-[#ff5555] flex items-center gap-1.5">
            <AlertCircle className="w-3 h-3 shrink-0" />
            <span className="truncate">{playbackError}</span>
          </div>
        )}
      </div>
    </div>
  );
};
