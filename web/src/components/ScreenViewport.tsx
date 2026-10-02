import React, { useState, useEffect, useRef } from 'react';
import { RefreshCw, Play, Pause, Maximize2, X, Monitor, MousePointer } from 'lucide-react';

interface ScreenViewportProps {
  token: string;
}

export const ScreenViewport: React.FC<ScreenViewportProps> = ({ token }) => {
  const [timestamp, setTimestamp] = useState<number>(Date.now());
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshIntervalMs, setRefreshIntervalMs] = useState(2000);
  const [interactiveMode, setInteractiveMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [clickRipple, setClickRipple] = useState<{ x: number; y: number } | null>(null);

  const imgRef = useRef<HTMLImageElement>(null);
  const timerRef = useRef<number | null>(null);

  const refreshScreenshot = () => {
    setLoading(true);
    setTimestamp(Date.now());
  };

  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = window.setInterval(() => {
        setTimestamp(Date.now());
      }, refreshIntervalMs);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [autoRefresh, refreshIntervalMs]);

  const handleImageClick = async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!interactiveMode) {
      setFullscreen(true);
      return;
    }

    const img = imgRef.current;
    if (!img) return;

    const rect = img.getBoundingClientRect();
    const naturalWidth = img.naturalWidth || 1920;
    const naturalHeight = img.naturalHeight || 1080;

    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    // Convert DOM pixels to screen coordinates
    const hostX = Math.round((clickX / rect.width) * naturalWidth);
    const hostY = Math.round((clickY / rect.height) * naturalHeight);

    // Show visual ripple effect at click point
    setClickRipple({ x: clickX, y: clickY });
    setTimeout(() => setClickRipple(null), 600);

    try {
      await fetch('/api/click', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ x: hostX, y: hostY, button: 'left' }),
      });
      // Refresh screen after a slight delay for UI render
      setTimeout(refreshScreenshot, 150);
    } catch (err) {
      console.debug('Click injection failed:', err);
    }
  };

  const imageUrl = `/api/screenshot?token=${encodeURIComponent(token)}&t=${timestamp}`;

  return (
    <div className="flex flex-col h-full bg-[#1e1f29] p-3 sm:p-4">
      {/* Top Controls Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3 bg-[#282a36] border border-[#44475a] p-2.5 rounded-xl">
        <div className="flex items-center gap-2">
          <Monitor className="w-4 h-4 text-[#bd93f9]" />
          <span className="text-xs font-bold text-[#f8f8f2]">Desktop Mirror</span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Interactive Tap-to-Click Toggle */}
          <button
            onClick={() => setInteractiveMode(!interactiveMode)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition active:scale-95 ${
              interactiveMode
                ? 'bg-[#ffb86c]/20 text-[#ffb86c] border border-[#ffb86c]'
                : 'bg-[#1e1f29] text-[#6272a4] border border-[#44475a]'
            }`}
            title={interactiveMode ? 'Tap-to-Click Active' : 'Enable Tap-to-Click'}
          >
            <MousePointer className="w-3 h-3" />
            <span>{interactiveMode ? 'Touch Click' : 'View Only'}</span>
          </button>

          {/* Auto Refresh Toggle */}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition active:scale-95 ${
              autoRefresh
                ? 'bg-[#50fa7b]/20 text-[#50fa7b] border border-[#50fa7b]/50'
                : 'bg-[#1e1f29] text-[#6272a4] border border-[#44475a]'
            }`}
          >
            {autoRefresh ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
            <span>{autoRefresh ? `${refreshIntervalMs / 1000}s` : 'Auto'}</span>
          </button>

          {/* Interval dropdown (when auto is active) */}
          {autoRefresh && (
            <select
              value={refreshIntervalMs}
              onChange={(e) => setRefreshIntervalMs(Number(e.target.value))}
              className="bg-[#1e1f29] text-[#f8f8f2] border border-[#44475a] text-[10px] rounded-md px-1.5 py-1 focus:outline-none"
            >
              <option value={1000}>1s</option>
              <option value={2000}>2s</option>
              <option value={5000}>5s</option>
            </select>
          )}

          {/* Manual Refresh button */}
          <button
            onClick={refreshScreenshot}
            className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition"
            title="Refresh screenshot"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-[#bd93f9]' : ''}`} />
          </button>

          {/* Fullscreen button */}
          <button
            onClick={() => setFullscreen(true)}
            className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition"
            title="Expand Fullscreen"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Screen Container */}
      <div className="flex-1 flex items-center justify-center bg-[#14151b] border border-[#44475a] rounded-xl overflow-hidden relative min-h-[300px]">
        <div className="relative inline-block max-w-full max-h-full">
          <img
            ref={imgRef}
            src={imageUrl}
            alt="Live Desktop View"
            onLoad={() => setLoading(false)}
            onError={() => setLoading(false)}
            onClick={handleImageClick}
            className={`max-w-full max-h-full object-contain select-none transition-opacity duration-200 ${
              interactiveMode ? 'cursor-crosshair' : 'cursor-pointer'
            }`}
          />

          {/* Click Ripple Indicator */}
          {clickRipple && (
            <span
              style={{ left: clickRipple.x - 12, top: clickRipple.y - 12 }}
              className="absolute w-6 h-6 rounded-full border-2 border-[#50fa7b] bg-[#50fa7b]/40 animate-ping pointer-events-none"
            />
          )}
        </div>

        {loading && (
          <div className="absolute top-3 right-3 bg-[#1e1f29]/90 border border-[#44475a] px-2 py-1 rounded-md text-[10px] text-[#6272a4] flex items-center gap-1.5 shadow-md">
            <RefreshCw className="w-3 h-3 animate-spin text-[#bd93f9]" />
            Updating...
          </div>
        )}

        {interactiveMode && (
          <div className="absolute bottom-3 left-3 bg-[#ffb86c]/20 border border-[#ffb86c] text-[#ffb86c] px-2 py-0.5 rounded-md text-[10px] font-semibold flex items-center gap-1 shadow-md">
            <MousePointer className="w-3 h-3" />
            Tap image to click on host
          </div>
        )}
      </div>

      {/* Fullscreen Modal View */}
      {fullscreen && (
        <div className="fixed inset-0 z-50 bg-black flex flex-col p-2">
          <div className="flex justify-between items-center px-4 py-2 bg-[#1e1f29] border-b border-[#44475a] rounded-t-lg">
            <span className="text-xs font-bold text-[#f8f8f2]">Desktop Fullscreen View</span>
            <button
              onClick={() => setFullscreen(false)}
              className="p-1 text-[#f8f8f2] hover:text-[#ff5555] rounded-md"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex-1 flex items-center justify-center overflow-auto p-1">
            <img
              src={imageUrl}
              alt="Fullscreen desktop"
              className="max-w-full max-h-full object-contain"
            />
          </div>
        </div>
      )}
    </div>
  );
};
