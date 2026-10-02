import React, { useState } from 'react';
import { ConfirmationRecord } from '../types/gateway';
import { ShieldAlert, Check, X, ShieldX, Terminal, Folder } from 'lucide-react';

interface SafetyModalProps {
  confirmations: ConfirmationRecord[];
  onResolve: (id: string, approved: boolean, allowlist: boolean) => void;
  onKill: () => void;
}

export const SafetyModal: React.FC<SafetyModalProps> = ({ confirmations, onResolve, onKill }) => {
  const [rememberMap, setRememberMap] = useState<Record<string, boolean>>({});

  if (!confirmations || confirmations.length === 0) return null;

  const current = confirmations[0];
  const isDestructive = current.risk_level === 3;
  const isLevel2 = current.risk_level === 2;

  const details = current.details || {};
  const command = details.command || details.rule || (typeof details === 'string' ? details : JSON.stringify(details));
  const cwd = details.cwd || details.directory || details.working_directory;

  const remember = !!rememberMap[current.id];

  const handleToggleRemember = () => {
    setRememberMap((prev) => ({ ...prev, [current.id]: !prev[current.id] }));
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-3 sm:p-4 animate-fadeIn">
      <div className="w-full max-w-lg bg-[#282a36] border-2 border-[#ffb86c] rounded-2xl p-5 shadow-2xl relative overflow-hidden">
        {/* Glow Header */}
        <div className="flex items-center justify-between pb-3 border-b border-[#44475a] mb-4">
          <div className="flex items-center gap-2">
            <div className={`p-2 rounded-xl ${isDestructive ? 'bg-[#ff5555]/20 text-[#ff5555]' : 'bg-[#ffb86c]/20 text-[#ffb86c]'}`}>
              <ShieldAlert className="w-6 h-6 animate-pulse" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-[#f8f8f2]">Safety Confirmation Required</h2>
              <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${isDestructive ? 'bg-[#ff5555]/20 text-[#ff5555]' : 'bg-[#ffb86c]/20 text-[#ffb86c]'}`}>
                Level {current.risk_level} • {isDestructive ? 'Destructive Operation' : 'Host Shell Execution'}
              </span>
            </div>
          </div>
          {confirmations.length > 1 && (
            <span className="text-xs bg-[#44475a] text-[#f8f8f2] px-2 py-0.5 rounded-full font-bold">
              1 of {confirmations.length}
            </span>
          )}
        </div>

        {/* Reason */}
        <p className="text-xs text-[#f1fa8c] font-medium mb-3">
          {current.reason}
        </p>

        {/* Command Box */}
        {command && (
          <div className="mb-3">
            <div className="flex items-center gap-1.5 text-[11px] text-[#6272a4] font-semibold mb-1">
              <Terminal className="w-3.5 h-3.5 text-[#50fa7b]" />
              Target Command:
            </div>
            <div className="p-3 bg-[#14151b] border border-[#44475a] rounded-xl font-mono text-xs text-[#50fa7b] break-all select-text max-h-36 overflow-y-auto">
              {command}
            </div>
          </div>
        )}

        {/* Working Directory */}
        {cwd && (
          <div className="flex items-center gap-2 text-xs text-[#6272a4] mb-3 bg-[#1e1f29] px-3 py-1.5 rounded-lg border border-[#44475a]/50">
            <Folder className="w-3.5 h-3.5 text-[#bd93f9]" />
            <span className="truncate">Directory: <code className="text-[#f8f8f2]">{cwd}</code></span>
          </div>
        )}

        {/* Remember / Always Allow Checkbox */}
        {isLevel2 && (
          <label className="flex items-center gap-2 text-xs text-[#f8f8f2] my-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={remember}
              onChange={handleToggleRemember}
              className="w-4 h-4 rounded border-[#44475a] bg-[#1e1f29] text-[#bd93f9] focus:ring-0 cursor-pointer"
            />
            <span className="text-[#6272a4]">Always allow this exact command in allowlist</span>
          </label>
        )}

        {/* Action Buttons */}
        <div className="grid grid-cols-2 gap-2 mt-4 pt-3 border-t border-[#44475a]">
          <button
            onClick={() => onResolve(current.id, true, remember)}
            className="flex items-center justify-center gap-1.5 py-3 rounded-xl bg-[#50fa7b] text-[#1e1f29] font-bold text-xs hover:bg-[#50fa7b]/90 transition active:scale-95 shadow-md"
          >
            <Check className="w-4 h-4" />
            Approve {remember ? '& Allow' : 'Once'}
          </button>

          <button
            onClick={() => onResolve(current.id, false, false)}
            className="flex items-center justify-center gap-1.5 py-3 rounded-xl bg-[#ff5555]/20 border border-[#ff5555] text-[#ff5555] font-bold text-xs hover:bg-[#ff5555]/30 transition active:scale-95"
          >
            <X className="w-4 h-4" />
            Reject Action
          </button>
        </div>

        {/* Emergency Stop secondary button */}
        <button
          onClick={onKill}
          className="w-full mt-2 py-2 rounded-xl bg-transparent text-[#6272a4] hover:text-[#ff5555] text-[11px] font-semibold flex items-center justify-center gap-1.5 transition"
        >
          <ShieldX className="w-3.5 h-3.5" />
          Abort & Trigger Emergency Kill Switch
        </button>
      </div>
    </div>
  );
};
